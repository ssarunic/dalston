# M40: Model Registry & Aliases

| | |
|---|---|
| **Goal** | PostgreSQL-backed model registry with CLI management and alias resolution |
| **Duration** | 3-4 days |
| **Dependencies** | M39 (Model Cache & TTL Management) |
| **Deliverable** | `dalston model pull/ls/rm`, model aliases, capability profiles, HF card routing |
| **Status** | Planned |

## Overview

Transform model management from static JSON catalog to dynamic PostgreSQL registry with:

1. **Model Registry**: Track downloaded models, status, and metadata in database
2. **CLI Commands**: `dalston model pull/ls/rm/status` for model lifecycle
3. **Model Aliases**: Map friendly names like `whisper-1` to full model IDs
4. **Capability Profiles**: Preset pipeline configurations like `meeting` or `fast`
5. **HuggingFace Card Routing**: Auto-detect engine from model's `library_name`

### Why This Matters

- **Explicit model management**: Know exactly which models are available before running jobs
- **Better error messages**: "Model not downloaded" instead of timeout on first use
- **OpenAI compatibility**: `whisper-1` alias works with existing client code
- **Simplified configuration**: `profile=meeting` sets up full diarization pipeline
- **Dynamic model support**: Load any HuggingFace ASR model without catalog updates

---

## 40.1: Model Registry Database

### Current State

- Models defined in `generated_catalog.json` (static, built at image time)
- No tracking of download status
- No metadata caching

### Database Migration

**Create `alembic/versions/20260302_0024_create_models_table.py`:**

```python
"""Create models table for model registry.

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        # Identity
        sa.Column("id", sa.String(100), primary_key=True),  # "parakeet-tdt-1.1b"
        sa.Column("name", sa.String(200), nullable=True),   # "Parakeet TDT 1.1B"

        # Runtime mapping
        sa.Column("runtime", sa.String(50), nullable=False),  # "nemo", "faster-whisper"
        sa.Column("runtime_model_id", sa.String(200), nullable=False),  # HF model ID
        sa.Column("stage", sa.String(50), nullable=False),  # "transcribe", "diarize"

        # Download status
        sa.Column("status", sa.String(20), nullable=False, server_default="not_downloaded"),
        sa.Column("download_path", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("downloaded_at", sa.TIMESTAMP(timezone=True), nullable=True),

        # HuggingFace metadata
        sa.Column("source", sa.String(50), nullable=True),  # "huggingface", "local"
        sa.Column("library_name", sa.String(50), nullable=True),  # "ctranslate2", "nemo"
        sa.Column("languages", JSONB, nullable=True),  # ["en", "es", "fr"]

        # Capabilities
        sa.Column("word_timestamps", sa.Boolean, server_default="false"),
        sa.Column("punctuation", sa.Boolean, server_default="false"),
        sa.Column("streaming", sa.Boolean, server_default="false"),

        # Hardware requirements
        sa.Column("min_vram_gb", sa.Float, nullable=True),
        sa.Column("min_ram_gb", sa.Float, nullable=True),
        sa.Column("supports_cpu", sa.Boolean, server_default="true"),

        # Metadata cache
        sa.Column("metadata", JSONB, nullable=False, server_default="{}"),

        # Timestamps
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_index("ix_models_runtime", "models", ["runtime"])
    op.create_index("ix_models_stage", "models", ["stage"])
    op.create_index("ix_models_status", "models", ["status"])


def downgrade() -> None:
    op.drop_table("models")
```

### ORM Model

**Add to `dalston/db/models.py`:**

```python
class ModelRegistryModel(Base):
    """Model registry entry tracking available models and their status."""

    __tablename__ = "models"

    # Identity
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Runtime mapping
    runtime: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    runtime_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Download status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="not_downloaded", index=True
    )
    download_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloaded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # HuggingFace metadata
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    library_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    languages: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Capabilities
    word_timestamps: Mapped[bool] = mapped_column(Boolean, server_default="false")
    punctuation: Mapped[bool] = mapped_column(Boolean, server_default="false")
    streaming: Mapped[bool] = mapped_column(Boolean, server_default="false")

    # Hardware
    min_vram_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_ram_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    supports_cpu: Mapped[bool] = mapped_column(Boolean, server_default="true")

    # Metadata
    metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now()
    )
```

### Model Status Flow

```
not_downloaded → downloading → ready
                     ↓
                  failed
```

---

## 40.2: Model Registry Service

**Create `dalston/gateway/services/model_registry.py`:**

```python
"""Model registry service for managing model downloads and metadata."""
from __future__ import annotations
import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import snapshot_download, HfApi
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import structlog

from dalston.db.models import ModelRegistryModel
from dalston.engine_sdk.model_paths import HF_CACHE, get_hf_model_path, is_model_cached

logger = structlog.get_logger()


class ModelRegistryService:
    """Service for model download and registry management."""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.hf_api = HfApi()

    async def get_model(self, model_id: str) -> ModelRegistryModel | None:
        """Get model by ID."""
        result = await self.db.execute(
            select(ModelRegistryModel).where(ModelRegistryModel.id == model_id)
        )
        return result.scalar_one_or_none()

    async def list_models(
        self,
        stage: str | None = None,
        runtime: str | None = None,
        status: str | None = None,
    ) -> list[ModelRegistryModel]:
        """List models with optional filters."""
        query = select(ModelRegistryModel)
        if stage:
            query = query.where(ModelRegistryModel.stage == stage)
        if runtime:
            query = query.where(ModelRegistryModel.runtime == runtime)
        if status:
            query = query.where(ModelRegistryModel.status == status)
        result = await self.db.execute(query.order_by(ModelRegistryModel.id))
        return list(result.scalars().all())

    async def pull_model(
        self,
        model_id: str,
        force: bool = False,
    ) -> ModelRegistryModel:
        """
        Download a model from HuggingFace Hub.

        Args:
            model_id: Dalston model ID (e.g., "parakeet-tdt-1.1b")
            force: Re-download even if already present
        """
        model = await self.get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model: {model_id}")

        if model.status == "ready" and not force:
            logger.info("model_already_downloaded", model_id=model_id)
            return model

        # Update status to downloading
        await self.db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(status="downloading")
        )
        await self.db.commit()

        try:
            # Download from HuggingFace
            logger.info(
                "downloading_model",
                model_id=model_id,
                runtime_model_id=model.runtime_model_id,
            )

            local_path = await asyncio.to_thread(
                snapshot_download,
                model.runtime_model_id,
                cache_dir=str(HF_CACHE),
                force_download=force,
            )

            # Calculate size
            path = Path(local_path)
            size_bytes = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

            # Update registry
            await self.db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(
                    status="ready",
                    download_path=str(local_path),
                    size_bytes=size_bytes,
                    downloaded_at=datetime.now(timezone.utc),
                )
            )
            await self.db.commit()

            logger.info(
                "model_downloaded",
                model_id=model_id,
                size_mb=size_bytes / 1024 / 1024,
            )

        except Exception as e:
            # Update status to failed
            await self.db.execute(
                update(ModelRegistryModel)
                .where(ModelRegistryModel.id == model_id)
                .values(status="failed", metadata={"error": str(e)})
            )
            await self.db.commit()
            raise

        return await self.get_model(model_id)

    async def remove_model(self, model_id: str) -> None:
        """Remove a downloaded model from disk and update registry."""
        model = await self.get_model(model_id)
        if model is None:
            raise ValueError(f"Unknown model: {model_id}")

        if model.download_path:
            path = Path(model.download_path)
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)
                logger.info("model_files_removed", model_id=model_id, path=str(path))

        await self.db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(
                status="not_downloaded",
                download_path=None,
                size_bytes=None,
                downloaded_at=None,
            )
        )
        await self.db.commit()

    async def sync_from_disk(self) -> dict:
        """
        Sync registry with actual disk state.

        Updates status based on whether files exist on disk.
        """
        models = await self.list_models()
        synced = {"updated": 0, "unchanged": 0}

        for model in models:
            on_disk = is_model_cached(model.runtime_model_id)

            if on_disk and model.status != "ready":
                await self.db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(status="ready")
                )
                synced["updated"] += 1
            elif not on_disk and model.status == "ready":
                await self.db.execute(
                    update(ModelRegistryModel)
                    .where(ModelRegistryModel.id == model.id)
                    .values(status="not_downloaded", download_path=None)
                )
                synced["updated"] += 1
            else:
                synced["unchanged"] += 1

        await self.db.commit()
        return synced

    async def touch_model(self, model_id: str) -> None:
        """Update last_used_at timestamp."""
        await self.db.execute(
            update(ModelRegistryModel)
            .where(ModelRegistryModel.id == model_id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await self.db.commit()
```

---

## 40.3: CLI Commands

**Update `cli/dalston_cli/commands/models.py`:**

```python
"""Model management CLI commands."""
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from dalston_cli.client import get_client
from dalston_cli.state import state

app = typer.Typer(help="Model management commands")
console = Console()


@app.command("ls")
def list_models(
    stage: Optional[str] = typer.Option(None, help="Filter by stage"),
    runtime: Optional[str] = typer.Option(None, help="Filter by runtime"),
    downloaded: bool = typer.Option(False, "--downloaded", help="Only show downloaded"),
) -> None:
    """List available models."""
    client = get_client(state)
    params = {}
    if stage:
        params["stage"] = stage
    if runtime:
        params["runtime"] = runtime
    if downloaded:
        params["status"] = "ready"

    response = client.get("/v1/models", params=params)
    models = response.json()["data"]

    table = Table(title="Models")
    table.add_column("ID", style="cyan")
    table.add_column("Runtime", style="magenta")
    table.add_column("Stage")
    table.add_column("Status", style="green")
    table.add_column("Size")

    for model in models:
        status = model["status"]
        status_style = {
            "ready": "[green]ready[/green]",
            "downloading": "[yellow]downloading[/yellow]",
            "not_downloaded": "[dim]not downloaded[/dim]",
            "failed": "[red]failed[/red]",
        }.get(status, status)

        size = ""
        if model.get("size_bytes"):
            size_mb = model["size_bytes"] / 1024 / 1024
            size = f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb/1024:.1f} GB"

        table.add_row(
            model["id"],
            model["runtime"],
            model["stage"],
            status_style,
            size,
        )

    console.print(table)


@app.command("pull")
def pull_model(
    model_id: str = typer.Argument(..., help="Model ID to download"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-download"),
    wait: bool = typer.Option(True, "--wait/--no-wait", help="Wait for completion"),
) -> None:
    """Download a model to local cache."""
    client = get_client(state)

    # Start download
    response = client.post(f"/v1/models/{model_id}/pull", json={"force": force})
    if response.status_code == 404:
        console.print(f"[red]Model not found: {model_id}[/red]")
        raise typer.Exit(1)

    if not wait:
        console.print(f"Download started for {model_id}")
        return

    # Poll until complete
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Downloading {model_id}...", total=None)

        while True:
            status_response = client.get(f"/v1/models/{model_id}")
            model = status_response.json()

            if model["status"] == "ready":
                progress.update(task, description=f"[green]Downloaded {model_id}[/green]")
                break
            elif model["status"] == "failed":
                error = model.get("metadata", {}).get("error", "Unknown error")
                progress.update(task, description=f"[red]Failed: {error}[/red]")
                raise typer.Exit(1)

            time.sleep(2)

    # Show result
    size_mb = model.get("size_bytes", 0) / 1024 / 1024
    console.print(f"Model {model_id} ready ({size_mb:.1f} MB)")


@app.command("rm")
def remove_model(
    model_id: str = typer.Argument(..., help="Model ID to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a downloaded model from local cache."""
    if not force:
        confirm = typer.confirm(f"Remove model {model_id}?")
        if not confirm:
            raise typer.Abort()

    client = get_client(state)
    response = client.delete(f"/v1/models/{model_id}")

    if response.status_code == 404:
        console.print(f"[red]Model not found: {model_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Removed {model_id}[/green]")


@app.command("status")
def model_status(
    model_id: str = typer.Argument(..., help="Model ID to check"),
) -> None:
    """Show detailed status of a model."""
    client = get_client(state)
    response = client.get(f"/v1/models/{model_id}")

    if response.status_code == 404:
        console.print(f"[red]Model not found: {model_id}[/red]")
        raise typer.Exit(1)

    model = response.json()

    console.print(f"[bold]Model: {model['id']}[/bold]")
    console.print(f"  Name: {model.get('name', '-')}")
    console.print(f"  Runtime: {model['runtime']}")
    console.print(f"  Runtime Model ID: {model['runtime_model_id']}")
    console.print(f"  Stage: {model['stage']}")
    console.print(f"  Status: {model['status']}")

    if model["status"] == "ready":
        size_mb = model.get("size_bytes", 0) / 1024 / 1024
        console.print(f"  Size: {size_mb:.1f} MB")
        console.print(f"  Path: {model.get('download_path', '-')}")
        console.print(f"  Downloaded: {model.get('downloaded_at', '-')}")

    if model.get("languages"):
        console.print(f"  Languages: {', '.join(model['languages'])}")

    console.print(f"  Word Timestamps: {model.get('word_timestamps', False)}")
    console.print(f"  CPU Support: {model.get('supports_cpu', True)}")


@app.command("sync")
def sync_models() -> None:
    """Sync registry with disk state."""
    client = get_client(state)
    response = client.post("/v1/models/sync")
    result = response.json()
    console.print(f"Synced: {result['updated']} updated, {result['unchanged']} unchanged")
```

---

## 40.4: Model Aliases

### Configuration

**Create `config/aliases.yaml`:**

```yaml
# Model aliases for API compatibility and convenience
aliases:
  # OpenAI compatibility
  whisper-1: faster-whisper-large-v2
  gpt-4o-transcribe: faster-whisper-large-v3
  gpt-4o-mini-transcribe: faster-whisper-distil-large-v3

  # Friendly names
  whisper-turbo: faster-whisper-large-v3-turbo
  whisper-accurate: faster-whisper-large-v3
  parakeet: parakeet-tdt-1.1b
  parakeet-fast: parakeet-ctc-0.6b

  # Provider-prefixed
  openai/whisper-large-v3: faster-whisper-large-v3
  nvidia/parakeet: parakeet-tdt-1.1b
```

### Alias Resolution

**Create `dalston/gateway/services/model_resolver.py`:**

```python
"""Model alias and profile resolution."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.db.models import ModelRegistryModel
from dalston.gateway.services.model_registry import ModelRegistryService


@dataclass
class ResolvedModel:
    """Result of model resolution."""
    model_id: str           # Dalston model ID
    runtime: str            # Engine runtime
    runtime_model_id: str   # HuggingFace model ID
    parameters: dict        # Merged parameters from profile


class ModelResolver:
    """Resolves model aliases and capability profiles."""

    def __init__(self, config_dir: Path, db: AsyncSession):
        self.db = db
        self.registry = ModelRegistryService(db)

        # Load aliases
        aliases_path = config_dir / "aliases.yaml"
        if aliases_path.exists():
            with open(aliases_path) as f:
                self._aliases = yaml.safe_load(f).get("aliases", {})
        else:
            self._aliases = {}

        # Load profiles
        profiles_path = config_dir / "profiles.yaml"
        if profiles_path.exists():
            with open(profiles_path) as f:
                self._profiles = yaml.safe_load(f).get("profiles", {})
        else:
            self._profiles = {}

    async def resolve(
        self,
        model: str,
        profile: str | None = None,
        params: dict | None = None,
    ) -> ResolvedModel:
        """
        Resolve model alias and optional profile to concrete configuration.

        Args:
            model: Model ID or alias (e.g., "whisper-1", "parakeet-tdt-1.1b")
            profile: Optional profile name (e.g., "meeting", "fast")
            params: User-provided parameters (override profile defaults)
        """
        params = params or {}

        # 1. Resolve alias
        model_id = self._aliases.get(model, model)

        # 2. Get model from registry
        model_entry = await self.registry.get_model(model_id)
        if model_entry is None:
            raise ValueError(f"Unknown model: {model_id}")

        # 3. Check model is downloaded
        if model_entry.status != "ready":
            raise ValueError(
                f"Model {model_id} not downloaded. Run: dalston model pull {model_id}"
            )

        # 4. Apply profile if specified
        merged_params = dict(params)
        if profile:
            profile_config = self._profiles.get(profile)
            if profile_config is None:
                raise ValueError(f"Unknown profile: {profile}")

            # Profile values are defaults, user params override
            for key, value in profile_config.items():
                if key != "model" and key not in merged_params:
                    merged_params[key] = value

        return ResolvedModel(
            model_id=model_id,
            runtime=model_entry.runtime,
            runtime_model_id=model_entry.runtime_model_id,
            parameters=merged_params,
        )

    def get_aliases(self) -> dict[str, str]:
        """Return all configured aliases."""
        return dict(self._aliases)

    def get_profiles(self) -> dict[str, dict]:
        """Return all configured profiles."""
        return dict(self._profiles)
```

---

## 40.5: Capability Profiles

**Create `config/profiles.yaml`:**

```yaml
# Pipeline capability profiles
profiles:
  # Quick transcription, no diarization
  fast:
    model: faster-whisper-large-v3-turbo
    speaker_detection: none
    timestamps_granularity: segment

  # Full meeting transcription with speakers
  meeting:
    model: faster-whisper-large-v3-turbo
    speaker_detection: diarize
    min_speakers: 2
    max_speakers: 10
    timestamps_granularity: word

  # High accuracy English transcription
  accurate:
    model: parakeet-tdt-1.1b
    language: en
    timestamps_granularity: word

  # Podcast/interview with distinct speakers
  podcast:
    model: faster-whisper-large-v3
    speaker_detection: diarize
    min_speakers: 2
    max_speakers: 4
    timestamps_granularity: word

  # Call center with 2 speakers
  call:
    model: faster-whisper-large-v3-turbo
    speaker_detection: per_channel
    timestamps_granularity: word

  # PII-safe compliance mode
  compliant:
    model: faster-whisper-large-v3-turbo
    speaker_detection: diarize
    pii_detection: true
    pii_entity_types:
      - PERSON
      - EMAIL_ADDRESS
      - PHONE_NUMBER
      - CREDIT_CARD
    pii_redact_audio: true
    pii_redaction_mode: silence
```

### Profile Usage in Gateway

**Update `dalston/gateway/api/v1/transcription.py`:**

```python
@router.post("/transcriptions")
async def create_transcription(
    model: str = Form(None),
    profile: str = Form(None),  # NEW: profile parameter
    # ... other params
):
    """Create a transcription job."""
    # Resolve model and profile
    resolver = ModelResolver(config_dir, db)

    try:
        resolved = await resolver.resolve(
            model=model or "faster-whisper-large-v3-turbo",
            profile=profile,
            params=user_params,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Create job with resolved config
    job = await jobs_service.create_job(
        model_id=resolved.model_id,
        parameters=resolved.parameters,
        # ...
    )
```

---

## 40.6: HuggingFace Card Routing

Auto-detect engine from HuggingFace model card's `library_name` field.

**Create `dalston/gateway/services/hf_resolver.py`:**

```python
"""Resolve HuggingFace model metadata for engine routing."""
from __future__ import annotations
import asyncio
from functools import lru_cache

from huggingface_hub import HfApi, ModelInfo
import structlog

logger = structlog.get_logger()

# Mapping from HF library_name to Dalston runtime
LIBRARY_TO_RUNTIME = {
    "ctranslate2": "faster-whisper",
    "nemo": "nemo",
    "nemo-asr": "nemo",
    "transformers": "hf-asr",  # Generic HF pipeline
    "vllm": "vllm-asr",        # Audio LLMs
}

# Fallback by tags
TAG_TO_RUNTIME = {
    "faster-whisper": "faster-whisper",
    "ctranslate2": "faster-whisper",
    "nemo": "nemo",
    "whisper": "faster-whisper",
}


class HFResolver:
    """Resolve HuggingFace model metadata for engine routing."""

    def __init__(self):
        self.api = HfApi()

    async def get_model_info(self, model_id: str) -> ModelInfo | None:
        """Fetch model info from HuggingFace Hub."""
        try:
            return await asyncio.to_thread(self.api.model_info, model_id)
        except Exception as e:
            logger.warning("hf_model_info_failed", model_id=model_id, error=str(e))
            return None

    async def resolve_runtime(self, model_id: str) -> str | None:
        """
        Determine which runtime can load a HuggingFace model.

        Returns runtime name or None if cannot determine.
        """
        info = await self.get_model_info(model_id)
        if info is None:
            return None

        # 1. Check library_name (most reliable)
        library_name = getattr(info, "library_name", None)
        if library_name:
            runtime = LIBRARY_TO_RUNTIME.get(library_name.lower())
            if runtime:
                logger.info(
                    "runtime_resolved",
                    model_id=model_id,
                    library_name=library_name,
                    runtime=runtime,
                )
                return runtime

        # 2. Check tags as fallback
        tags = set(info.tags) if info.tags else set()
        for tag, runtime in TAG_TO_RUNTIME.items():
            if tag in tags:
                logger.info(
                    "runtime_resolved_by_tag",
                    model_id=model_id,
                    tag=tag,
                    runtime=runtime,
                )
                return runtime

        # 3. Check pipeline_tag for generic ASR
        pipeline_tag = getattr(info, "pipeline_tag", None)
        if pipeline_tag == "automatic-speech-recognition":
            # Default to HF transformers pipeline
            return "hf-asr"

        return None

    async def get_model_metadata(self, model_id: str) -> dict:
        """Get full metadata for caching in registry."""
        info = await self.get_model_info(model_id)
        if info is None:
            return {}

        return {
            "library_name": getattr(info, "library_name", None),
            "pipeline_tag": getattr(info, "pipeline_tag", None),
            "tags": list(info.tags) if info.tags else [],
            "languages": getattr(info, "language", []),
            "downloads": getattr(info, "downloads", 0),
            "likes": getattr(info, "likes", 0),
        }
```

### Integration with Model Resolver

**Update `dalston/gateway/services/model_resolver.py`:**

```python
class ModelResolver:
    async def resolve(self, model: str, ...) -> ResolvedModel:
        # 1. Check aliases
        model_id = self._aliases.get(model, model)

        # 2. Check registry
        model_entry = await self.registry.get_model(model_id)
        if model_entry is not None:
            return self._from_registry(model_entry, params)

        # 3. Try HuggingFace resolution for unknown models
        if "/" in model_id:  # Looks like HF model ID
            runtime = await self.hf_resolver.resolve_runtime(model_id)
            if runtime:
                # Auto-register in database for caching
                await self._auto_register(model_id, runtime)
                return ResolvedModel(
                    model_id=model_id,
                    runtime=runtime,
                    runtime_model_id=model_id,
                    parameters=params,
                )

        raise ValueError(f"Cannot resolve model: {model_id}")

    async def _auto_register(self, hf_model_id: str, runtime: str) -> None:
        """Auto-register a HF model in the registry."""
        metadata = await self.hf_resolver.get_model_metadata(hf_model_id)

        model = ModelRegistryModel(
            id=hf_model_id,  # Use HF ID as Dalston ID
            runtime=runtime,
            runtime_model_id=hf_model_id,
            stage="transcribe",
            source="huggingface",
            library_name=metadata.get("library_name"),
            languages=metadata.get("languages"),
            metadata=metadata,
        )
        self.db.add(model)
        await self.db.commit()
```

---

## API Endpoints

**Update `dalston/gateway/api/v1/models.py`:**

```python
@router.get("")
async def list_models(
    stage: str | None = None,
    runtime: str | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List available models."""
    service = ModelRegistryService(db)
    models = await service.list_models(stage=stage, runtime=runtime, status=status)
    return {
        "object": "list",
        "data": [model_to_dict(m) for m in models],
    }


@router.get("/{model_id}")
async def get_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Get model details."""
    service = ModelRegistryService(db)
    model = await service.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id}")
    return model_to_dict(model)


@router.post("/{model_id}/pull")
async def pull_model(
    model_id: str,
    request: PullRequest,
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = None,
) -> dict:
    """Download a model."""
    service = ModelRegistryService(db)

    # Start download in background
    background_tasks.add_task(service.pull_model, model_id, request.force)

    return {"message": "Download started", "model_id": model_id}


@router.delete("/{model_id}")
async def remove_model(
    model_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Remove a downloaded model."""
    service = ModelRegistryService(db)
    await service.remove_model(model_id)
    return {"message": "Model removed", "model_id": model_id}


@router.post("/sync")
async def sync_models(db: AsyncSession = Depends(get_db)) -> dict:
    """Sync registry with disk state."""
    service = ModelRegistryService(db)
    return await service.sync_from_disk()


@router.get("/aliases")
async def list_aliases(
    config_dir: Path = Depends(get_config_dir),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List configured model aliases."""
    resolver = ModelResolver(config_dir, db)
    return {"aliases": resolver.get_aliases()}


@router.get("/profiles")
async def list_profiles(
    config_dir: Path = Depends(get_config_dir),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """List configured capability profiles."""
    resolver = ModelResolver(config_dir, db)
    return {"profiles": resolver.get_profiles()}
```

---

## Files Summary

### New Files

| File | Description |
|------|-------------|
| `alembic/versions/20260302_0024_create_models_table.py` | Database migration |
| `dalston/gateway/services/model_registry.py` | Model registry service |
| `dalston/gateway/services/model_resolver.py` | Alias and profile resolution |
| `dalston/gateway/services/hf_resolver.py` | HuggingFace metadata resolution |
| `config/aliases.yaml` | Model alias definitions |
| `config/profiles.yaml` | Capability profile definitions |

### Modified Files

| File | Change |
|------|--------|
| `dalston/db/models.py` | Add ModelRegistryModel |
| `dalston/gateway/api/v1/models.py` | Add registry endpoints |
| `dalston/gateway/api/v1/transcription.py` | Use ModelResolver |
| `cli/dalston_cli/commands/models.py` | Add pull/rm/status commands |

---

## Verification

### Database

```bash
# Run migration
alembic upgrade head

# Seed registry from catalog
dalston model sync

# Verify table
docker compose exec postgres psql -U dalston -c "SELECT id, runtime, status FROM models LIMIT 5"
```

### CLI

```bash
# List all models
dalston model ls

# Download a model
dalston model pull parakeet-tdt-1.1b

# Check status
dalston model status parakeet-tdt-1.1b

# Remove model
dalston model rm parakeet-tdt-1.1b
```

### Alias Resolution

```bash
# Test OpenAI alias
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.wav" \
  -F "model=whisper-1"
# Should resolve to faster-whisper-large-v2
```

### Profile Usage

```bash
# Test meeting profile
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.wav" \
  -F "profile=meeting"
# Should enable diarization with configured settings
```

---

## Checkpoint

- [ ] **40.1**: `models` table migration applied
- [ ] **40.2**: ModelRegistryService implemented
- [ ] **40.3**: CLI `pull/ls/rm/status` commands working
- [ ] **40.4**: Alias resolution working (whisper-1 → faster-whisper-large-v2)
- [ ] **40.5**: Profile resolution working (profile=meeting)
- [ ] **40.6**: HF card routing auto-detects runtime
- [ ] Job creation validates model is downloaded
- [ ] Error messages guide user to `dalston model pull`

**Next**: M41 (New Engine Types)

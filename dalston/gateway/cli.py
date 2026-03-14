"""Dalston Gateway CLI commands.

Usage:
    # API Key Management
    python -m dalston.gateway.cli create-key --name "My Key"
    python -m dalston.gateway.cli create-key --name "Admin" --scopes admin
    python -m dalston.gateway.cli list-keys
    python -m dalston.gateway.cli revoke-key <key-id>

    # Model Management
    python -m dalston.gateway.cli model ls
    python -m dalston.gateway.cli model pull parakeet-tdt-1.1b
    python -m dalston.gateway.cli model status parakeet-tdt-1.1b
    python -m dalston.gateway.cli model rm parakeet-tdt-1.1b
    python -m dalston.gateway.cli model sync

Note: Models are seeded automatically from YAML files on gateway startup (M46).
"""

import asyncio
import sys
from typing import Annotated

import typer

from dalston.db.session import DEFAULT_TENANT_ID
from dalston.gateway.services.auth import DEFAULT_SCOPES, AuthService, Scope

app = typer.Typer(help="Dalston Gateway CLI.")
model_app = typer.Typer(help="Model management commands.")

# Valid scope values for CLI help
VALID_SCOPES = [s.value for s in Scope]


async def _create_key(name: str, scopes: list[Scope]) -> tuple[str, str, list[Scope]]:
    """Create an API key with specified scopes.

    Args:
        name: Human-readable name for the key
        scopes: List of permission scopes

    Returns:
        Tuple of (raw_key, key_id, scopes)
    """
    from dalston.common.redis import get_redis
    from dalston.db.session import async_session, init_db

    # Initialize database (creates tables if needed)
    await init_db()

    redis = await get_redis()
    async with async_session() as db:
        auth_service = AuthService(db, redis)

        raw_key, api_key = await auth_service.create_api_key(
            name=name,
            tenant_id=DEFAULT_TENANT_ID,
            scopes=scopes,
            rate_limit=None,
        )

        return raw_key, str(api_key.id), api_key.scopes


def parse_scopes(scopes_str: str | None) -> list[Scope]:
    """Parse comma-separated scopes string into Scope list.

    Args:
        scopes_str: Comma-separated scopes (e.g., "jobs:read,jobs:write,admin")

    Returns:
        List of Scope enums

    Raises:
        typer.BadParameter: If invalid scope provided
    """
    if not scopes_str:
        return list(DEFAULT_SCOPES)

    scopes = []
    for s in scopes_str.split(","):
        s = s.strip()
        try:
            scopes.append(Scope(s))
        except ValueError:
            raise typer.BadParameter(
                f"Invalid scope '{s}'. Valid scopes: {', '.join(VALID_SCOPES)}"
            ) from None

    return scopes


@app.command("create-key")
def create_key(
    name: Annotated[
        str,
        typer.Option(
            "--name",
            "-n",
            help="Human-readable name for the API key",
        ),
    ] = "API Key",
    scopes: Annotated[
        str | None,
        typer.Option(
            "--scopes",
            "-s",
            help=f"Comma-separated scopes. Valid: {', '.join(VALID_SCOPES)}. "
            f"Default: {', '.join(s.value for s in DEFAULT_SCOPES)}",
        ),
    ] = None,
) -> None:
    """Create an API key with specified scopes.

    The key is displayed once and cannot be retrieved later.
    Store it securely!

    Examples:
        # Default scopes (jobs:read, jobs:write, realtime)
        python -m dalston.gateway.cli create-key --name "My Key"

        # Admin key (full access)
        python -m dalston.gateway.cli create-key --name "Admin" --scopes admin

        # Read-only key
        python -m dalston.gateway.cli create-key --name "Reader" --scopes jobs:read

        # Multiple scopes
        python -m dalston.gateway.cli create-key --name "Worker" --scopes jobs:read,jobs:write
    """
    try:
        parsed_scopes = parse_scopes(scopes)
    except typer.BadParameter as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    scope_names = ", ".join(s.value for s in parsed_scopes)
    typer.echo(f"Creating API key '{name}' with scopes: {scope_names}...")

    try:
        raw_key, key_id, actual_scopes = asyncio.run(_create_key(name, parsed_scopes))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    typer.echo()
    typer.echo("=" * 60)
    typer.echo("API key created successfully!")
    typer.echo("=" * 60)
    typer.echo()
    typer.echo(f"Key ID:  {key_id}")
    typer.echo(f"API Key: {raw_key}")
    typer.echo(f"Scopes:  {', '.join(s.value for s in actual_scopes)}")
    typer.echo()
    typer.echo("IMPORTANT: Store this key securely!")
    typer.echo("It cannot be retrieved later.")
    typer.echo()
    typer.echo("=" * 60)
    typer.echo("Usage Examples")
    typer.echo("=" * 60)
    typer.echo()
    typer.echo("# Set as environment variable")
    typer.echo(f'export DALSTON_API_KEY="{raw_key}"')
    typer.echo()
    typer.echo("# Use with curl")
    typer.echo("curl -X POST http://localhost:8000/v1/audio/transcriptions \\")
    typer.echo(f'  -H "Authorization: Bearer {raw_key}" \\')
    typer.echo('  -F "file=@audio.mp3"')
    typer.echo()
    typer.echo("# Use with dalston-cli")
    typer.echo(f'dalston --api-key "{raw_key}" transcribe audio.mp3')
    typer.echo()


@app.command("list-keys")
def list_keys(
    include_revoked: Annotated[
        bool,
        typer.Option(
            "--include-revoked",
            "-r",
            help="Include revoked keys in the list",
        ),
    ] = False,
) -> None:
    """List all API keys for the default tenant."""

    async def _list_keys():
        from dalston.common.redis import get_redis
        from dalston.db.session import async_session

        redis = await get_redis()
        async with async_session() as db:
            auth_service = AuthService(db, redis)
            return await auth_service.list_api_keys(
                DEFAULT_TENANT_ID,
                include_revoked=include_revoked,
            )

    try:
        keys = asyncio.run(_list_keys())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not keys:
        typer.echo("No API keys found.")
        return

    typer.echo(f"Found {len(keys)} API key(s):")
    typer.echo()

    for key in keys:
        status = " [REVOKED]" if key.is_revoked else ""
        typer.echo(f"  ID:      {key.id}{status}")
        typer.echo(f"  Prefix:  {key.prefix}...")
        typer.echo(f"  Name:    {key.name}")
        typer.echo(f"  Scopes:  {', '.join(s.value for s in key.scopes)}")
        typer.echo(f"  Created: {key.created_at.isoformat()}")
        if key.last_used_at:
            typer.echo(f"  Used:    {key.last_used_at.isoformat()}")
        typer.echo()


@app.command("revoke-key")
def revoke_key(
    key_id: Annotated[
        str,
        typer.Argument(help="Key ID to revoke"),
    ],
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation",
        ),
    ] = False,
) -> None:
    """Revoke an API key by ID."""
    from uuid import UUID

    try:
        key_uuid = UUID(key_id)
    except ValueError:
        typer.echo(f"Error: Invalid key ID format: {key_id}", err=True)
        sys.exit(1)

    if not yes:
        typer.confirm(f"Revoke API key {key_id}?", abort=True)

    async def _revoke_key():
        from dalston.common.redis import get_redis
        from dalston.db.session import async_session

        redis = await get_redis()
        async with async_session() as db:
            auth_service = AuthService(db, redis)
            return await auth_service.revoke_api_key(key_uuid)

    try:
        success = asyncio.run(_revoke_key())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if success:
        typer.echo(f"API key {key_id} revoked.")
    else:
        typer.echo(f"API key {key_id} not found.", err=True)
        sys.exit(1)


# =============================================================================
# Model Management Commands (M40)
# =============================================================================


async def _list_models(
    stage: str | None = None,
    engine_id: str | None = None,
    status: str | None = None,
) -> list:
    """List models from the registry."""
    from dalston.db.session import async_session
    from dalston.gateway.services.model_registry import ModelRegistryService

    async with async_session() as db:
        service = ModelRegistryService()
        models = await service.list_models(
            db, stage=stage, engine_id=engine_id, status=status
        )
        return [
            {
                "id": m.id,
                "name": m.name,
                "engine_id": m.engine_id,
                "stage": m.stage,
                "status": m.status,
                "size_bytes": m.size_bytes,
                "downloaded_at": m.downloaded_at,
            }
            for m in models
        ]


@model_app.command("ls")
def model_list(
    stage: Annotated[
        str | None,
        typer.Option("--stage", "-s", help="Filter by stage (e.g., 'transcribe')"),
    ] = None,
    engine_id: Annotated[
        str | None,
        typer.Option("--engine_id", "-r", help="Filter by engine_id (e.g., 'nemo')"),
    ] = None,
    downloaded: Annotated[
        bool,
        typer.Option("--downloaded", "-d", help="Only show downloaded models"),
    ] = False,
) -> None:
    """List available models in the registry.

    Examples:
        # List all models
        python -m dalston.gateway.cli model ls

        # List only downloaded models
        python -m dalston.gateway.cli model ls --downloaded

        # Filter by engine_id
        python -m dalston.gateway.cli model ls --engine_id nemo
    """
    status_filter = "ready" if downloaded else None

    try:
        models = asyncio.run(
            _list_models(stage=stage, engine_id=engine_id, status=status_filter)
        )
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if not models:
        typer.echo("No models found.")
        return

    typer.echo(f"Found {len(models)} model(s):")
    typer.echo()

    # Header
    typer.echo(f"{'ID':<35} {'RUNTIME':<18} {'STAGE':<12} {'STATUS':<15} {'SIZE':<10}")
    typer.echo("-" * 92)

    for model in models:
        status = model["status"]
        size = ""
        if model.get("size_bytes"):
            size_mb = model["size_bytes"] / 1024 / 1024
            size = f"{size_mb:.1f} MB" if size_mb < 1024 else f"{size_mb / 1024:.1f} GB"

        typer.echo(
            f"{model['id']:<35} {model['engine_id']:<18} {model['stage']:<12} "
            f"{status:<15} {size:<10}"
        )

    typer.echo()


async def _pull_model(model_id: str, force: bool = False) -> dict:
    """Pull (download) a model."""
    from dalston.db.session import async_session
    from dalston.gateway.services.model_registry import (
        ModelNotFoundError,
        ModelRegistryService,
    )

    async with async_session() as db:
        service = ModelRegistryService()
        try:
            model = await service.pull_model(db, model_id, force=force)
            return {
                "id": model.id,
                "status": model.status,
                "size_bytes": model.size_bytes,
                "download_path": model.download_path,
            }
        except ModelNotFoundError:
            raise ValueError(f"Model not found: {model_id}") from None


@model_app.command("pull")
def model_pull(
    model_id: Annotated[str, typer.Argument(help="Model ID to download")],
    force: Annotated[
        bool,
        typer.Option("--force", "-f", help="Force re-download even if already cached"),
    ] = False,
) -> None:
    """Download a model from HuggingFace Hub.

    Examples:
        # Download a model
        python -m dalston.gateway.cli model pull parakeet-tdt-1.1b

        # Force re-download
        python -m dalston.gateway.cli model pull parakeet-tdt-1.1b --force
    """
    typer.echo(f"Downloading model: {model_id}...")

    try:
        result = asyncio.run(_pull_model(model_id, force=force))
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if result["status"] == "ready":
        size_mb = (result.get("size_bytes") or 0) / 1024 / 1024
        typer.echo()
        typer.echo("Model downloaded successfully!")
        typer.echo(f"  ID:   {result['id']}")
        typer.echo(f"  Size: {size_mb:.1f} MB")
        typer.echo(f"  Path: {result['download_path']}")
    else:
        typer.echo(f"Download status: {result['status']}", err=True)
        sys.exit(1)


async def _model_status(model_id: str) -> dict | None:
    """Get detailed model status."""
    from dalston.db.session import async_session
    from dalston.gateway.services.model_registry import ModelRegistryService

    async with async_session() as db:
        service = ModelRegistryService()
        model = await service.get_model(db, model_id)
        if model is None:
            return None
        return {
            "id": model.id,
            "name": model.name,
            "engine_id": model.engine_id,
            "loaded_model_id": model.loaded_model_id,
            "stage": model.stage,
            "status": model.status,
            "download_path": model.download_path,
            "size_bytes": model.size_bytes,
            "downloaded_at": model.downloaded_at,
            "languages": model.languages,
            "word_timestamps": model.word_timestamps,
            "punctuation": model.punctuation,
            "capitalization": model.capitalization,
            "native_streaming": model.native_streaming,
            "supports_cpu": model.supports_cpu,
            "min_vram_gb": model.min_vram_gb,
            "min_ram_gb": model.min_ram_gb,
            "last_used_at": model.last_used_at,
            "created_at": model.created_at,
        }


@model_app.command("status")
def model_status(
    model_id: Annotated[str, typer.Argument(help="Model ID to check")],
) -> None:
    """Show detailed status of a model.

    Examples:
        python -m dalston.gateway.cli model status parakeet-tdt-1.1b
    """
    try:
        model = asyncio.run(_model_status(model_id))
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if model is None:
        typer.echo(f"Model not found: {model_id}", err=True)
        sys.exit(1)

    typer.echo(f"Model: {model['id']}")
    typer.echo()
    typer.echo(f"  Name:            {model.get('name') or '-'}")
    typer.echo(f"  Runtime:         {model['engine_id']}")
    typer.echo(f"  Runtime Model:   {model['loaded_model_id']}")
    typer.echo(f"  Stage:           {model['stage']}")
    typer.echo(f"  Status:          {model['status']}")
    typer.echo()

    if model["status"] == "ready":
        size_mb = (model.get("size_bytes") or 0) / 1024 / 1024
        typer.echo(f"  Size:            {size_mb:.1f} MB")
        typer.echo(f"  Path:            {model.get('download_path') or '-'}")
        if model.get("downloaded_at"):
            typer.echo(f"  Downloaded:      {model['downloaded_at'].isoformat()}")

    typer.echo()
    typer.echo("Capabilities:")
    if model.get("languages"):
        typer.echo(f"  Languages:       {', '.join(model['languages'])}")
    else:
        typer.echo("  Languages:       multilingual")
    typer.echo(f"  Word Timestamps: {model.get('word_timestamps', False)}")
    typer.echo(f"  Punctuation:     {model.get('punctuation', False)}")
    typer.echo(f"  Capitalization:  {model.get('capitalization', False)}")
    typer.echo(f"  Native Streaming: {model.get('native_streaming', False)}")

    typer.echo()
    typer.echo("Hardware:")
    typer.echo(f"  CPU Support:     {model.get('supports_cpu', True)}")
    if model.get("min_vram_gb"):
        typer.echo(f"  Min VRAM:        {model['min_vram_gb']} GB")
    if model.get("min_ram_gb"):
        typer.echo(f"  Min RAM:         {model['min_ram_gb']} GB")

    if model.get("last_used_at"):
        typer.echo()
        typer.echo(f"  Last Used:       {model['last_used_at'].isoformat()}")


async def _remove_model(model_id: str) -> None:
    """Remove a downloaded model."""
    from dalston.db.session import async_session
    from dalston.gateway.services.model_registry import (
        ModelNotFoundError,
        ModelRegistryService,
    )

    async with async_session() as db:
        service = ModelRegistryService()
        try:
            await service.remove_model(db, model_id)
        except ModelNotFoundError:
            raise ValueError(f"Model not found: {model_id}") from None


@model_app.command("rm")
def model_remove(
    model_id: Annotated[str, typer.Argument(help="Model ID to remove")],
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation"),
    ] = False,
) -> None:
    """Remove a downloaded model from local cache.

    Examples:
        # Remove with confirmation
        python -m dalston.gateway.cli model rm parakeet-tdt-1.1b

        # Remove without confirmation
        python -m dalston.gateway.cli model rm parakeet-tdt-1.1b --yes
    """
    if not yes:
        typer.confirm(f"Remove model {model_id}?", abort=True)

    try:
        asyncio.run(_remove_model(model_id))
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    typer.echo(f"Model {model_id} removed.")


async def _sync_models() -> dict:
    """Sync registry with disk state."""
    from dalston.db.session import async_session
    from dalston.gateway.services.model_registry import ModelRegistryService

    async with async_session() as db:
        service = ModelRegistryService()
        return await service.sync_from_disk(db)


@model_app.command("sync")
def model_sync() -> None:
    """Sync registry with disk state.

    Updates the database registry to match actual files on disk.
    Models found on disk will be marked as 'ready', missing models
    will be marked as 'not_downloaded'.

    Examples:
        python -m dalston.gateway.cli model sync
    """
    typer.echo("Syncing model registry with disk...")

    try:
        result = asyncio.run(_sync_models())
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        sys.exit(1)

    typer.echo(
        f"Sync complete: {result['updated']} updated, {result['unchanged']} unchanged"
    )


# Register model subcommand
app.add_typer(model_app, name="model")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()

"""Generic composite engine that dispatches to child engines via HTTP.

A composite engine orchestrates multiple leaf engines (each running as a
separate service with M79 HTTP endpoints) by calling their ``/v1/{stage}``
endpoints and merging results into a stage-keyed envelope.

Children are declared in ``engine.yaml`` under the ``compose`` key and
resolved to HTTP URLs via environment variables or convention.

This implements Layer 2 of the ENGINE_COMPOSABILITY spec (§5).
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from dalston.common.engine_yaml import load_engine_yaml
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineCapabilities, TaskRequest, TaskResponse

logger = structlog.get_logger()


@dataclass
class ChildEngine:
    """Resolved child engine with its HTTP base URL and stage coverage."""

    engine_id: str
    url: str
    stages: list[str]
    submit_endpoints: dict[
        str, str
    ]  # stage → POST path, e.g. {"transcribe": "/v1/transcribe"}


# Stage → default HTTP submit path (matching M79 conventions).
_DEFAULT_SUBMIT_PATHS: dict[str, str] = {
    "transcribe": "/v1/transcribe",
    "diarize": "/v1/diarize",
}

# Stage → default port (matches docker-compose conventions).
_DEFAULT_PORTS: dict[str, int] = {
    "transcribe": 9100,
    "diarize": 9100,
}


def _resolve_child_url(engine_id: str, stages: list[str]) -> str:
    """Resolve child engine URL from env var or Docker-DNS convention.

    Lookup order:
    1. ``DALSTON_CHILD_URL_{ENGINE_ID}`` (explicit override)
    2. ``http://{engine_id}:9100`` (Docker-DNS convention)

    The engine_id is normalised: dots and underscores become hyphens.
    """
    env_key = (
        f"DALSTON_CHILD_URL_{engine_id.upper().replace('-', '_').replace('.', '_')}"
    )
    url = os.environ.get(env_key)
    if url:
        return url.rstrip("/")

    # Convention: Docker service name == engine_id with dots→hyphens
    hostname = engine_id.replace(".", "-").replace("_", "-")
    port = _DEFAULT_PORTS.get(stages[0], 9100) if stages else 9100
    return f"http://{hostname}:{port}"


def _parse_compose_block(card: dict[str, Any]) -> list[ChildEngine]:
    """Parse the ``compose`` block from an engine.yaml into ChildEngine list."""
    compose = card.get("compose", [])
    if not compose:
        raise ValueError("Composite engine.yaml must have a 'compose' block")

    children: list[ChildEngine] = []
    for entry in compose:
        engine_id = entry["engine"]
        stages = entry.get("stages", [])
        url = entry.get("url") or _resolve_child_url(engine_id, stages)

        submit_endpoints: dict[str, str] = {}
        for stage in stages:
            submit_endpoints[stage] = entry.get("submit", {}).get(
                stage, _DEFAULT_SUBMIT_PATHS.get(stage, f"/v1/{stage}")
            )

        children.append(
            ChildEngine(
                engine_id=engine_id,
                url=url,
                stages=stages,
                submit_endpoints=submit_endpoints,
            )
        )

    # Validate: no stage covered by more than one child
    seen: dict[str, str] = {}
    for child in children:
        for stage in child.stages:
            if stage in seen:
                raise ValueError(
                    f"Stage '{stage}' covered by both '{seen[stage]}' and "
                    f"'{child.engine_id}' — each stage must have exactly one child"
                )
            seen[stage] = child.engine_id

    return children


class CompositeEngine(Engine):
    """Generic composite engine that fans out to children via HTTP.

    On each ``process()`` call the engine:

    1. Identifies which children need to run (based on requested stage
       or all children for ``stage="combined"``).
    2. Calls each child's HTTP endpoint in parallel.
    3. Merges results into a stage-keyed envelope.

    Children are configured in ``engine.yaml``::

        type: composite
        compose:
          - engine: faster-whisper
            stages: [transcribe]
          - engine: pyannote-4.0
            stages: [diarize]
    """

    def __init__(self) -> None:
        super().__init__()

        card = load_engine_yaml() or {}
        self._engine_id = card.get("engine_id") or card.get("id", "composite")
        self._children = _parse_compose_block(card)
        self._pipeline_config = card.get("pipeline", {})

        # Build lookup: stage → child
        self._stage_to_child: dict[str, ChildEngine] = {}
        for child in self._children:
            for stage in child.stages:
                self._stage_to_child[stage] = child

        self.logger.info(
            "composite_engine_init",
            engine_id=self._engine_id,
            children=[c.engine_id for c in self._children],
            stages=list(self._stage_to_child.keys()),
        )

    # ------------------------------------------------------------------
    # HTTP client helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _http_client():
        """Lazily import httpx to avoid hard dependency at import time."""
        import httpx

        return httpx

    def _call_child(
        self,
        child: ChildEngine,
        stage: str,
        audio_path: Path,
        config: dict[str, Any],
        previous_results: dict[str, dict[str, Any]] | None = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Call a child engine's HTTP endpoint and return the JSON response.

        Sends audio as a file upload (multipart) with config as form fields.
        For stages that depend on prior output (e.g. align needs transcribe),
        ``previous_results`` is serialised into form fields that the child's
        HTTP endpoint expects.
        """
        httpx = self._http_client()
        endpoint = child.submit_endpoints.get(stage, f"/v1/{stage}")
        url = f"{child.url}{endpoint}"

        # Build form data from config.
        # Internal config keys use loaded_model_id; the HTTP API uses "model".
        _KEY_REMAP = {"loaded_model_id": "model"}

        form_data: dict[str, Any] = {}
        for key, value in config.items():
            if key.startswith("_"):
                continue
            form_key = _KEY_REMAP.get(key, key)
            if isinstance(value, bool):
                form_data[form_key] = str(value).lower()
            elif isinstance(value, list):
                form_data[form_key] = ",".join(str(v) for v in value)
            elif value is not None:
                form_data[form_key] = str(value)

        # Inject previous stage outputs as form fields.
        # The align endpoint expects a "transcript" JSON string.
        if previous_results and stage == "align" and "transcribe" in previous_results:
            form_data["transcript"] = json.dumps(previous_results["transcribe"])

        self.logger.info(
            "calling_child_engine",
            child=child.engine_id,
            stage=stage,
            url=url,
        )

        with open(audio_path, "rb") as f:
            files = {"file": (audio_path.name, f, "audio/wav")}
            resp = httpx.post(
                url,
                data=form_data,
                files=files,
                timeout=timeout,
            )

        if resp.status_code != 200:
            raise RuntimeError(
                f"Child engine {child.engine_id} returned {resp.status_code}: "
                f"{resp.text[:500]}"
            )

        return resp.json()

    def _call_child_health(
        self, child: ChildEngine, timeout: float = 5.0
    ) -> dict[str, Any]:
        """Call a child engine's /health endpoint."""
        httpx = self._http_client()
        try:
            resp = httpx.get(f"{child.url}/health", timeout=timeout)
            return resp.json()
        except Exception as e:
            return {"status": "unreachable", "error": str(e)}

    # ------------------------------------------------------------------
    # Engine interface
    # ------------------------------------------------------------------

    def process(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Dispatch to child engines based on stage."""
        stage = task_request.config.get("_stage", task_request.stage)

        if stage == "combined" or stage == self._engine_id:
            return self._run_all(task_request, ctx)

        # Single-stage dispatch
        child = self._stage_to_child.get(stage)
        if child is None:
            raise ValueError(
                f"Stage '{stage}' not covered by any child engine. "
                f"Available stages: {list(self._stage_to_child.keys())}"
            )
        return self._run_single(child, stage, task_request, ctx)

    def _run_single(
        self,
        child: ChildEngine,
        stage: str,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Run a single child engine for one stage."""
        config = {k: v for k, v in task_request.config.items() if k != "_stage"}
        result = self._call_child(
            child,
            stage,
            task_request.audio_path,
            config,
            previous_results=task_request.previous_responses,
        )
        return TaskResponse(data=result)

    def _run_all(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> TaskResponse:
        """Run all children in parallel and merge results."""
        self._set_runtime_state(status="processing")

        # Determine which children to run in parallel vs sequentially
        parallel_stages = set(self._pipeline_config.get("parallel", []))
        sequential_after = self._pipeline_config.get("sequential_after", [])

        # If no pipeline config, run everything in parallel
        if not parallel_stages and not sequential_after:
            parallel_stages = set(self._stage_to_child)

        results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []

        try:
            # Phase 1: parallel children
            parallel_children = [
                (child, stage)
                for stage, child in self._stage_to_child.items()
                if stage in parallel_stages
            ]

            if parallel_children:
                self._run_parallel(
                    parallel_children,
                    task_request,
                    results,
                    errors,
                )

            # Phase 2: sequential tail (after parallel completes)
            # Sequential stages may depend on prior results (e.g. align
            # needs the transcribe output).
            for stage in sequential_after:
                child = self._stage_to_child.get(stage)
                if child is None:
                    continue
                config = self._extract_stage_config(stage, task_request.config)
                try:
                    result = self._call_child(
                        child,
                        stage,
                        task_request.audio_path,
                        config,
                        previous_results=results,
                    )
                    results[stage] = result
                    self.logger.info("child_stage_completed", stage=stage)
                except Exception:
                    self.logger.exception("child_stage_failed", stage=stage)
                    errors.append(stage)

            merged = self._merge_results(results, errors)
            return TaskResponse(data=merged)
        finally:
            self._set_runtime_state(status="idle")

    def _run_parallel(
        self,
        children: list[tuple[ChildEngine, str]],
        task_request: TaskRequest,
        results: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> None:
        """Execute multiple children concurrently."""
        with ThreadPoolExecutor(max_workers=len(children)) as pool:
            futures = {}
            for child, stage in children:
                config = self._extract_stage_config(stage, task_request.config)
                future = pool.submit(
                    self._call_child,
                    child,
                    stage,
                    task_request.audio_path,
                    config,
                )
                futures[future] = stage

            for future in as_completed(futures):
                stage = futures[future]
                try:
                    results[stage] = future.result()
                    self.logger.info("child_stage_completed", stage=stage)
                except Exception:
                    self.logger.exception("child_stage_failed", stage=stage)
                    errors.append(stage)

    # ------------------------------------------------------------------
    # Config extraction
    # ------------------------------------------------------------------

    # Keys that belong to specific stages.  Anything not listed here is
    # passed through to all children.
    _TRANSCRIBE_KEYS = {
        "loaded_model_id",
        "language",
        "word_timestamps",
        "vocabulary",
        "channel",
        "beam_size",
        "vad_filter",
        "temperature",
        "task",
        "prompt",
    }
    _DIARIZE_KEYS = {
        "num_speakers",
        "min_speakers",
        "max_speakers",
        "exclusive",
        "diarize_model_id",
    }
    _ALIGN_KEYS = {
        "return_char_alignments",
        "align_model_id",
    }

    def _extract_stage_config(
        self, stage: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract config keys relevant to a specific stage."""
        out: dict[str, Any] = {}

        if stage == "transcribe":
            for key in self._TRANSCRIBE_KEYS:
                if key in config:
                    out[key] = config[key]
        elif stage == "diarize":
            for key in self._DIARIZE_KEYS:
                if key in config:
                    out[key] = config[key]
            # Map diarize_model_id → loaded_model_id for the child
            if "diarize_model_id" in out:
                out["loaded_model_id"] = out.pop("diarize_model_id")
        elif stage == "align":
            for key in self._ALIGN_KEYS:
                if key in config:
                    out[key] = config[key]
            # Map align_model_id → loaded_model_id for the child
            if "align_model_id" in out:
                out["loaded_model_id"] = out.pop("align_model_id")
        else:
            # Unknown stage: pass everything (minus internal keys)
            out = {k: v for k, v in config.items() if not k.startswith("_")}

        return out

    # ------------------------------------------------------------------
    # Result merging
    # ------------------------------------------------------------------

    def _merge_results(
        self,
        results: dict[str, dict[str, Any]],
        errors: list[str],
    ) -> dict[str, Any]:
        """Merge child results into a stage-keyed envelope (§3.3)."""
        merged: dict[str, Any] = {
            "engine_id": self._engine_id,
            "stages_completed": list(results.keys()),
        }

        for stage, data in results.items():
            merged[stage] = data

        for stage in errors:
            merged[stage] = {
                "skipped": True,
                "skip_reason": f"{stage} child engine failed",
            }

        if errors:
            merged["warnings"] = [f"Stage failed: {s}" for s in errors]

        return merged

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create_http_server(self, port: int = 9100):  # type: ignore[override]
        """Return a ``CombinedHTTPServer`` with stage-specific POST endpoints."""
        from dalston.engine_sdk.http_combined import CombinedHTTPServer

        return CombinedHTTPServer(engine=self, port=port)

    def get_capabilities(self) -> EngineCapabilities:
        """Return the union of child capabilities."""
        card = load_engine_yaml() or {}
        all_stages = list(self._stage_to_child.keys())
        hardware = card.get("hardware", {})
        performance = card.get("performance", {})
        caps = card.get("capabilities", {})

        return EngineCapabilities(
            engine_id=self._engine_id,
            version=card.get("version", "1.0.0"),
            stages=all_stages,
            supports_word_timestamps="transcribe" in all_stages,
            includes_diarization="diarize" in all_stages,
            supports_native_streaming=False,
            gpu_required=hardware.get("gpu_required", False),
            supports_cpu=hardware.get("supports_cpu", True),
            gpu_vram_mb=(
                hardware.get("min_vram_gb", 0) * 1024
                if hardware.get("min_vram_gb")
                else None
            ),
            min_ram_gb=hardware.get("min_ram_gb"),
            rtf_gpu=performance.get("rtf_gpu"),
            rtf_cpu=performance.get("rtf_cpu"),
            max_concurrency=caps.get("max_concurrency"),
        )

    def health_check(self) -> dict[str, Any]:
        """Aggregate health from all children via HTTP."""
        health: dict[str, Any] = {
            "status": "healthy",
            "engine_id": self._engine_id,
            "children": {},
        }

        for child in self._children:
            child_health = self._call_child_health(child)
            health["children"][child.engine_id] = child_health
            if child_health.get("status") not in ("healthy",):
                health["status"] = "degraded"

        return health

    def shutdown(self) -> None:
        """Nothing to shut down — children are independent services."""
        self.logger.info("composite_engine_shutdown")

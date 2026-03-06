"""Capability-driven engine selection for pipeline stages (M31).

This module replaces hardcoded engine defaults with dynamic selection based on:
- Running engine capabilities from the registry
- Job requirements (language, streaming, etc.)
- Engine ranking by capabilities (word timestamps, diarization, speed)

Example:
    requirements = extract_requirements(job_parameters)
    selection = await select_engine("transcribe", requirements, registry, catalog)
    # selection.runtime = "parakeet"
    # selection.capabilities.supports_word_timestamps = True
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

import dalston.telemetry
from dalston.common.model_selection_keys import (
    ENGINE_PARAM_TRANSCRIBE,
    MODEL_PARAM_ALIGN,
    MODEL_PARAM_DIARIZE,
    MODEL_PARAM_PII_DETECT,
    MODEL_PARAM_TRANSCRIBE,
)
from dalston.db.models import ModelRegistryModel
from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.registry import BatchEngineRegistry, BatchEngineState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# Stages that require explicit runtime model resolution from the registry.
MODEL_BACKED_STAGES = {"transcribe", "diarize", "align", "pii_detect"}


class NoDownloadedModelError(Exception):
    """No downloaded model available for the selected runtime.

    Raised when auto model selection is used but no models are downloaded
    for the selected transcription runtime.
    """

    def __init__(
        self,
        runtime: str,
        stage: str = "transcribe",
        attempted_runtimes: list[str] | None = None,
    ) -> None:
        self.runtime = runtime
        self.stage = stage
        self.attempted_runtimes = attempted_runtimes or [runtime]
        attempted_suffix = (
            f" Attempted runtimes: {', '.join(self.attempted_runtimes)}."
            if len(self.attempted_runtimes) > 1
            else ""
        )
        super().__init__(
            f"No downloaded models available for runtime '{runtime}'. "
            f"Please download a model from the Models page or specify a model explicitly."
            f"{attempted_suffix}"
        )


class ModelSelectionError(Exception):
    """Deterministic model-selection failure.

    Error codes:
    - model_not_found
    - model_stage_mismatch
    - model_not_ready
    - runtime_unavailable
    """

    def __init__(
        self,
        *,
        code: str,
        stage: str,
        model_id: str,
        runtime: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.model_id = model_id
        self.runtime = runtime
        self.detail = detail
        parts = [
            f"Model selection failed ({code})",
            f"stage={stage}",
            f"model_id={model_id}",
        ]
        if runtime:
            parts.append(f"runtime={runtime}")
        if detail:
            parts.append(detail)
        super().__init__(", ".join(parts))


@dataclass
class EngineSelectionResult:
    """Result of engine selection.

    Attributes:
        runtime: Selected engine identifier (runtime ID, e.g., "nemo", "faster-whisper")
        capabilities: Engine's declared capabilities
        selection_reason: Human-readable explanation of why this engine was selected
        runtime_model_id: Model ID to pass to the engine (e.g., "nvidia/parakeet-tdt-1.1b")
                         Only set when user requested a specific model variant.
    """

    runtime: str
    capabilities: EngineCapabilities
    selection_reason: str
    runtime_model_id: str | None = None


class NoCapableEngineError(Exception):
    """No running engine can handle job requirements.

    Provides structured context for actionable error messages including:
    - What was required
    - What engines are running and why they don't match
    - What alternatives exist in the catalog
    """

    def __init__(
        self,
        stage: str,
        requirements: dict,
        candidates: list[BatchEngineState],
        catalog_alternatives: list[CatalogEntry],
    ) -> None:
        """Initialize error with context for debugging.

        Args:
            stage: Pipeline stage that has no capable engine
            requirements: The requirements that couldn't be satisfied
            candidates: Running engines that were evaluated
            catalog_alternatives: Catalog engines that could work if started
        """
        self.stage = stage
        self.requirements = requirements
        self.candidates = candidates
        self.catalog_alternatives = catalog_alternatives
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        """Build human-readable error message."""
        lines = [
            "No running engine can handle this job.",
            f"  Stage: {self.stage}",
            f"  Required: {self.requirements}",
            "",
        ]

        if self.candidates:
            lines.append(f"  Running engines for '{self.stage}':")
            for engine in self.candidates:
                mismatch = self._explain_mismatch(engine)
                lines.append(f"    - {engine.runtime}: {mismatch}")
        else:
            lines.append(f"  No engines running for stage '{self.stage}'.")

        if self.catalog_alternatives:
            lines.append("")
            lines.append("  Available in catalog (not running):")
            for alt in self.catalog_alternatives:
                lines.append(f"    - {alt.runtime}")
                lines.append(
                    f"      Start: docker compose up stt-batch-{self.stage}-{alt.runtime}"
                )

        return "\n".join(lines)

    def _explain_mismatch(self, engine: BatchEngineState) -> str:
        """Explain why a running engine doesn't match requirements."""
        if engine.capabilities is None:
            return "no capabilities declared"

        caps = engine.capabilities
        reasons = []

        lang = self.requirements.get("language")
        if (
            lang
            and caps.languages
            and lang.lower() not in [lng.lower() for lng in caps.languages]
        ):
            reasons.append(f"language '{lang}' not supported (has: {caps.languages})")

        if self.requirements.get("streaming") and not caps.supports_streaming:
            reasons.append("streaming not supported")

        return "; ".join(reasons) if reasons else "unknown"

    def to_dict(self) -> dict:
        """Convert to structured dict for API responses."""
        return {
            "error": "no_capable_engine",
            "stage": self.stage,
            "requirements": self.requirements,
            "running_engines": [
                {"id": e.runtime, "reason": self._explain_mismatch(e)}
                for e in self.candidates
            ],
            "catalog_alternatives": [
                {"id": a.runtime, "languages": a.capabilities.languages}
                for a in self.catalog_alternatives
            ],
        }


def _resolve_runtime_model_id(model: ModelRegistryModel, stage: str) -> str:
    """Resolve the task-level runtime_model_id from a registry model."""
    # Transcribe uses source IDs for S3-backed artifact lookup compatibility.
    if stage == "transcribe":
        return model.source or model.id

    return model.runtime_model_id


async def _find_best_downloaded_model(
    runtime: str,
    stage: str,
    requirements: dict,
    db: AsyncSession,
) -> ModelRegistryModel | None:
    """Find the best downloaded model for a runtime.

    Queries the model registry for models with status='ready' for the given
    runtime, then ranks them by suitability for the job requirements.

    Ranking criteria:
    1. Language compatibility (if language specified)
    2. Model size (larger models generally better quality)

    Args:
        runtime: The runtime/engine ID (e.g., "faster-whisper", "nemo")
        requirements: Job requirements from extract_requirements()
        db: Database session for model registry lookup

    Returns:
        The best model entry, or None if no downloaded model is available.
    """
    from sqlalchemy import select

    # Query downloaded models for this runtime
    result = await db.execute(
        select(ModelRegistryModel).where(
            ModelRegistryModel.runtime == runtime,
            ModelRegistryModel.status == "ready",
            ModelRegistryModel.stage == stage,
        )
    )
    models = list(result.scalars().all())

    if not models:
        return None

    requested_language = requirements.get("language")

    # Hard filter for explicit language requests.
    if requested_language and requested_language.lower() != "auto":
        compatible = []
        for model in models:
            if model.languages is None or requested_language.lower() in [
                lng.lower() for lng in model.languages
            ]:
                compatible.append(model)
        models = compatible
        if not models:
            return None

    def score_model(model: ModelRegistryModel) -> tuple:
        """Score a model for ranking. Higher is better."""
        # Language compatibility
        if requested_language and requested_language.lower() != "auto":
            if model.languages:
                lang_match = (
                    1
                    if requested_language.lower()
                    in [lng.lower() for lng in model.languages]
                    else 0
                )
            else:
                # Unknown languages = assume universal
                lang_match = 1
        else:
            # Auto language detection - prefer multilingual models
            if model.languages is None:
                lang_match = 2  # Universal
            elif len(model.languages) > 10:
                lang_match = 2  # Multilingual
            else:
                lang_match = 1  # Limited languages

        # Model size as proxy for quality (larger = better, but penalize missing)
        size_score = model.size_bytes or 0

        return (lang_match, size_score)

    # Sort by score descending
    ranked = sorted(models, key=score_model, reverse=True)
    best = ranked[0]

    runtime_model_id = _resolve_runtime_model_id(best, stage)

    logger.info(
        "auto_model_selected",
        runtime=runtime,
        stage=stage,
        selected_model=best.id,
        runtime_model_id=runtime_model_id,
        model_id=best.id,
        candidates=len(models),
        language_requirement=requested_language,
    )

    return best


def extract_requirements(parameters: dict) -> dict:
    """Convert job parameters to selector requirements.

    Extracts the relevant parameters that affect engine selection.

    Args:
        parameters: Job parameters from API request

    Returns:
        Dict of requirements for engine selection
    """
    requirements: dict = {}

    # Language
    language = parameters.get("language") or parameters.get("language_code")
    if language and language.lower() != "auto":
        requirements["language"] = language

    # Streaming (realtime path only)
    if parameters.get("streaming"):
        requirements["streaming"] = True

    return requirements


def _meets_requirements(caps: EngineCapabilities, requirements: dict) -> bool:
    """Check if engine capabilities satisfy hard requirements.

    Args:
        caps: Engine capabilities to check
        requirements: Requirements dict

    Returns:
        True if all requirements are satisfied
    """
    # Language (hard requirement)
    lang = requirements.get("language")
    if lang and caps.languages is not None:
        if lang.lower() not in [lng.lower() for lng in caps.languages]:
            return False

    # Streaming (hard requirement)
    if requirements.get("streaming") and not caps.supports_streaming:
        return False

    return True


def _rank_capable_engines(
    capable: list[BatchEngineState],
    requirements: dict,
) -> list[BatchEngineState]:
    """Rank capable engines, best first.

    Ranking criteria (in order of priority):
    1. Native word timestamps (skips alignment stage)
    2. Native diarization (skips diarize stage)
    3. Language specificity (specialized > universal)
    4. Speed (lower RTF is better)

    Args:
        capable: List of engines that meet hard requirements
        requirements: Job requirements (for context in reason)

    Returns:
        Ranked list with best engine first.
    """

    requested_language = requirements.get("language")

    def score(engine: BatchEngineState) -> tuple:
        caps = engine.capabilities
        if caps is None:
            return (0, 0, 0, 0, -999.0)

        # For unknown language ("auto"), prioritize language-safe engines:
        # universal (None) > multilingual > single-language.
        if requested_language:
            unknown_lang_safety = 0
        elif caps.languages is None:
            unknown_lang_safety = 2
        elif len(caps.languages) > 1:
            unknown_lang_safety = 1
        else:
            unknown_lang_safety = 0

        # Prefer native word timestamps (skips alignment stage)
        native_ts = 1 if caps.supports_word_timestamps else 0

        # Prefer native diarization (skips diarize stage)
        native_diar = 1 if caps.includes_diarization else 0

        # Prefer language-specific engines over universal
        specific = 1 if caps.languages is not None else 0

        # Prefer faster (lower RTF is better, so negate)
        rtf = caps.rtf_gpu if caps.rtf_gpu else 999.0
        speed = -rtf

        return (unknown_lang_safety, native_ts, native_diar, specific, speed)

    return sorted(capable, key=score, reverse=True)


def _rank_and_select(
    capable: list[BatchEngineState],
    requirements: dict,
) -> EngineSelectionResult:
    """Rank capable engines and select best."""
    ranked = _rank_capable_engines(capable, requirements)
    winner = ranked[0]

    reasons = []
    if winner.capabilities:
        if winner.capabilities.supports_word_timestamps:
            reasons.append("native word timestamps")
        if winner.capabilities.includes_diarization:
            reasons.append("native diarization")
    if len(capable) > 1:
        reasons.append(f"ranked first of {len(capable)}")

    return EngineSelectionResult(
        runtime=winner.runtime,
        capabilities=winner.capabilities
        or EngineCapabilities(
            runtime=winner.runtime, version="unknown", stages=[winner.stage]
        ),
        selection_reason=", ".join(reasons) or "best available",
    )


async def select_engine(
    stage: str,
    requirements: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
    user_preference: str | None = None,
    db: AsyncSession | None = None,
    *,
    user_preference_is_model: bool = False,
) -> EngineSelectionResult:
    """Select best engine for a pipeline stage.

    Selection process:
    1. If user specified a model ID, resolve it to runtime + runtime_model_id
    2. If user specified an engine ID, validate it can handle requirements
    3. Get all running engines for the stage from registry
    4. Filter by hard requirements (language, streaming)
    5. Rank remaining engines by capabilities
    6. Return best match, or raise NoCapableEngineError with alternatives

    Args:
        stage: Pipeline stage (e.g., "transcribe", "diarize")
        requirements: Job requirements from extract_requirements()
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)
        user_preference: Optional model ID or runtime ID from user
        db: Optional database session for HF model lookup
        user_preference_is_model: If True, user preference must resolve to a
            model registry ID for this stage.

    Returns:
        EngineSelectionResult with selected engine and optional runtime_model_id

    Raises:
        NoCapableEngineError: If no running engine can handle requirements
    """
    # 1. Validate explicit user choice if provided
    if user_preference:
        # Look up model in database (the single source of truth)
        db_model = None
        if db is not None:
            from sqlalchemy import select

            from dalston.db.models import ModelRegistryModel

            result = await db.execute(
                select(ModelRegistryModel).where(
                    ModelRegistryModel.id == user_preference
                )
            )
            db_model = result.scalar_one_or_none()

        if db_model is not None:
            # Found in database: enforce stage and readiness invariants.
            if db_model.stage != stage:
                raise ModelSelectionError(
                    code="model_stage_mismatch",
                    stage=stage,
                    model_id=user_preference,
                    runtime=db_model.runtime,
                    detail=f"model stage is '{db_model.stage}'",
                )

            if db_model.status != "ready":
                raise ModelSelectionError(
                    code="model_not_ready",
                    stage=stage,
                    model_id=user_preference,
                    runtime=db_model.runtime,
                    detail=f"status is '{db_model.status}'",
                )

            runtime_id = db_model.runtime
            runtime_model_id = _resolve_runtime_model_id(db_model, stage)

            engine = await registry.get_engine(runtime_id)
            if engine is None or not engine.is_available:
                raise ModelSelectionError(
                    code="runtime_unavailable",
                    stage=stage,
                    model_id=user_preference,
                    runtime=runtime_id,
                )

            # Check model's language requirements
            if db_model.languages is not None:
                lang = requirements.get("language")
                if lang and lang.lower() not in [
                    lng.lower() for lng in db_model.languages
                ]:
                    catalog_alts = catalog.find_engines(stage, requirements)
                    raise NoCapableEngineError(
                        stage=stage,
                        requirements=requirements,
                        candidates=[engine],
                        catalog_alternatives=catalog_alts,
                    )

            logger.info(
                "engine_selected",
                stage=stage,
                selected_engine=runtime_id,
                runtime_model_id=runtime_model_id,
                selection_reason="database model lookup",
                original_model_id=user_preference,
            )

            return EngineSelectionResult(
                runtime=runtime_id,
                capabilities=engine.capabilities
                or EngineCapabilities(
                    runtime=runtime_id, version="unknown", stages=[stage]
                ),
                selection_reason="database model lookup",
                runtime_model_id=runtime_model_id,
            )

        if user_preference_is_model:
            raise ModelSelectionError(
                code="model_not_found",
                stage=stage,
                model_id=user_preference,
            )

        # Not a model ID, try as direct engine ID
        engine = await registry.get_engine(user_preference)
        if engine is None or not engine.is_available:
            # User-specified engine not running
            catalog_alts = catalog.find_engines(stage, requirements)
            raise NoCapableEngineError(
                stage=stage,
                requirements=requirements,
                candidates=[],
                catalog_alternatives=catalog_alts,
            )

        if engine.capabilities and not _meets_requirements(
            engine.capabilities, requirements
        ):
            # User-specified engine doesn't meet requirements
            catalog_alts = catalog.find_engines(stage, requirements)
            raise NoCapableEngineError(
                stage=stage,
                requirements=requirements,
                candidates=[engine],
                catalog_alternatives=catalog_alts,
            )

        return EngineSelectionResult(
            runtime=engine.runtime,
            capabilities=engine.capabilities
            or EngineCapabilities(
                runtime=engine.runtime, version="unknown", stages=[stage]
            ),
            selection_reason="user preference",
        )

    # 2. Get running engines for stage
    candidates = await registry.get_engines_for_stage(stage)

    # 3. Filter by hard requirements
    capable = [
        e
        for e in candidates
        if e.is_available
        and (
            e.capabilities is None or _meets_requirements(e.capabilities, requirements)
        )
    ]

    # 4. No capable engine - build helpful error
    if not capable:
        catalog_alts = catalog.find_engines(stage, requirements)
        raise NoCapableEngineError(
            stage=stage,
            requirements=requirements,
            candidates=candidates,
            catalog_alternatives=catalog_alts,
        )

    # 5. Select engine (single match or ranked), then resolve runtime_model_id if needed.
    ranked_capable = (
        capable if len(capable) == 1 else _rank_capable_engines(capable, requirements)
    )
    ranked_reason = (
        "only capable engine"
        if len(capable) == 1
        else _rank_and_select(capable, requirements).selection_reason
    )
    auto_model_id: str | None = None
    engine = ranked_capable[0]
    selection_reason = ranked_reason

    if stage in MODEL_BACKED_STAGES and db is not None:
        selected_model: ModelRegistryModel | None = None
        attempted_runtimes: list[str] = []
        for idx, candidate in enumerate(ranked_capable):
            attempted_runtimes.append(candidate.runtime)
            model = await _find_best_downloaded_model(
                runtime=candidate.runtime,
                stage=stage,
                requirements=requirements,
                db=db,
            )
            if model is None:
                continue

            engine = candidate
            selected_model = model
            if len(ranked_capable) > 1:
                selection_reason = (
                    ranked_reason
                    if idx == 0
                    else f"{ranked_reason}; fallback to runtime with ready model"
                )
            break

        if selected_model is None:
            raise NoDownloadedModelError(
                runtime=ranked_capable[0].runtime,
                stage=stage,
                attempted_runtimes=attempted_runtimes,
            )

        auto_model_id = _resolve_runtime_model_id(selected_model, stage)

    logger.info(
        "engine_selected",
        stage=stage,
        selected_engine=engine.runtime,
        runtime_model_id=auto_model_id,
        selection_reason=selection_reason,
        candidates_evaluated=len(candidates),
        capable_count=len(capable),
        requirements=requirements,
    )

    return EngineSelectionResult(
        runtime=engine.runtime,
        capabilities=engine.capabilities
        or EngineCapabilities(
            runtime=engine.runtime, version="unknown", stages=[stage]
        ),
        selection_reason=selection_reason,
        runtime_model_id=auto_model_id,
    )


def _should_add_alignment(
    parameters: dict, transcribe_selection: EngineSelectionResult
) -> bool:
    """Determine if alignment stage is needed.

    Alignment is needed when:
    - Job wants word timestamps (default: yes)
    - Transcriber doesn't produce native accurate timestamps

    Args:
        parameters: Job parameters
        transcribe_selection: Selected transcribe engine

    Returns:
        True if alignment stage should be added
    """
    # Check user preference for word timestamps
    if "word_timestamps" in parameters:
        wants_word_timestamps = parameters["word_timestamps"]
    elif "timestamps_granularity" in parameters:
        wants_word_timestamps = parameters["timestamps_granularity"] == "word"
    else:
        wants_word_timestamps = True  # Default: word timestamps on

    has_native = transcribe_selection.capabilities.supports_word_timestamps

    return wants_word_timestamps and not has_native


def _should_add_diarization(
    parameters: dict, transcribe_selection: EngineSelectionResult
) -> bool:
    """Determine if diarization stage is needed.

    Diarization is needed when:
    - Job requests speaker detection (speaker_detection="diarize")
    - Transcriber doesn't include diarization in output

    Args:
        parameters: Job parameters
        transcribe_selection: Selected transcribe engine

    Returns:
        True if diarization stage should be added
    """
    speaker_detection = parameters.get("speaker_detection", "none")
    wants_diarization = speaker_detection == "diarize"
    has_native = transcribe_selection.capabilities.includes_diarization

    return wants_diarization and not has_native


async def select_pipeline_engines(
    parameters: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
    db: AsyncSession | None = None,
) -> dict[str, EngineSelectionResult]:
    """Select engines for all required pipeline stages.

    This is the main entry point for capability-driven engine selection.
    It selects engines for all stages needed based on job parameters and
    engine capabilities.

    Args:
        parameters: Job parameters from API request
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)
        db: Optional database session for HF model lookup

    Returns:
        Dict mapping stage names to EngineSelectionResult

    Raises:
        NoCapableEngineError: If any required stage has no capable engine
    """
    requirements = extract_requirements(parameters)
    selections: dict[str, EngineSelectionResult] = {}

    with dalston.telemetry.create_span(
        "orchestrator.engine_selection",
        attributes={
            "dalston.language": requirements.get("language", ""),
            "dalston.model": parameters.get("model", ""),
        },
    ):
        # Prepare (always required)
        selections["prepare"] = await select_engine(
            "prepare",
            {},  # No special requirements for prepare
            registry,
            catalog,
            user_preference=parameters.get("engine_prepare"),
            db=db,
        )

        # Transcription (always required)
        selections["transcribe"] = await select_engine(
            "transcribe",
            requirements,
            registry,
            catalog,
            user_preference=parameters.get(ENGINE_PARAM_TRANSCRIBE)
            or parameters.get(MODEL_PARAM_TRANSCRIBE),
            db=db,
        )

        # Alignment (conditional on transcriber capabilities)
        if _should_add_alignment(parameters, selections["transcribe"]):
            # Alignment only needs language requirement
            align_requirements = (
                {"language": requirements.get("language")}
                if requirements.get("language")
                else {}
            )
            align_model_preference = parameters.get(MODEL_PARAM_ALIGN)
            try:
                selections["align"] = await select_engine(
                    "align",
                    align_requirements,
                    registry,
                    catalog,
                    user_preference=align_model_preference,
                    db=db,
                    user_preference_is_model=True,
                )
            except NoDownloadedModelError:
                # Keep explicit align-model pinning strict; otherwise degrade
                # to segment timestamps to preserve zero-config usability.
                if align_model_preference:
                    raise

                parameters["word_timestamps"] = False
                parameters["timestamps_granularity"] = "segment"
                logger.warning(
                    "align_model_missing_fallback_to_segment_timestamps",
                    reason="no_downloaded_align_model",
                    transcribe_runtime=selections["transcribe"].runtime,
                )

        # Diarization (conditional on parameters and transcriber capabilities)
        if _should_add_diarization(parameters, selections["transcribe"]):
            selections["diarize"] = await select_engine(
                "diarize",
                {},  # No special requirements for diarize
                registry,
                catalog,
                user_preference=parameters.get(MODEL_PARAM_DIARIZE),
                db=db,
                user_preference_is_model=True,
            )

        # PII detection (conditional on parameters)
        pii_detection_enabled = parameters.get("pii_detection", False)
        if pii_detection_enabled:
            selections["pii_detect"] = await select_engine(
                "pii_detect",
                {},  # No special requirements for PII detection
                registry,
                catalog,
                user_preference=parameters.get(MODEL_PARAM_PII_DETECT),
                db=db,
                user_preference_is_model=True,
            )

            # Audio redaction (conditional on parameters, requires PII detection)
            if parameters.get("redact_pii_audio", False):
                selections["audio_redact"] = await select_engine(
                    "audio_redact",
                    {},  # No special requirements for audio redaction
                    registry,
                    catalog,
                    user_preference=parameters.get("engine_audio_redact"),
                    db=db,
                )

        # Merge (always required)
        selections["merge"] = await select_engine(
            "merge",
            {},  # No special requirements for merge
            registry,
            catalog,
            user_preference=parameters.get("engine_merge"),
            db=db,
        )

        # Record selection results on span
        transcribe_sel = selections["transcribe"]
        dalston.telemetry.set_span_attribute("dalston.runtime", transcribe_sel.runtime)
        dalston.telemetry.set_span_attribute(
            "dalston.model", transcribe_sel.runtime_model_id or ""
        )
        dalston.telemetry.set_span_attribute(
            "dalston.selection.reason", transcribe_sel.selection_reason
        )
        dalston.telemetry.set_span_attribute(
            "dalston.dag.stages", list(selections.keys())
        )

    logger.info(
        "pipeline_engines_selected",
        stages=list(selections.keys()),
        engines={stage: sel.runtime for stage, sel in selections.items()},
        alignment_included="align" in selections,
        diarization_included="diarize" in selections,
        pii_detection_included="pii_detect" in selections,
        audio_redaction_included="audio_redact" in selections,
    )

    return selections

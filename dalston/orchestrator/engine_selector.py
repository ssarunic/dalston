"""Capability-driven engine selection for pipeline stages (M31).

This module replaces hardcoded engine defaults with dynamic selection based on:
- Running engine capabilities from the registry
- Job requirements (language, streaming, etc.)
- Engine ranking by capabilities (word timestamps, diarization, speed)

Example:
    requirements = extract_requirements(job_parameters)
    selection = await select_engine("transcribe", requirements, registry, catalog)
    # selection.engine_id = "parakeet"
    # selection.capabilities.supports_word_timestamps = True
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.registry import BatchEngineRegistry, BatchEngineState

logger = structlog.get_logger()


@dataclass
class EngineSelectionResult:
    """Result of engine selection.

    Attributes:
        engine_id: Selected engine identifier
        capabilities: Engine's declared capabilities
        selection_reason: Human-readable explanation of why this engine was selected
    """

    engine_id: str
    capabilities: EngineCapabilities
    selection_reason: str


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
                lines.append(f"    - {engine.engine_id}: {mismatch}")
        else:
            lines.append(f"  No engines running for stage '{self.stage}'.")

        if self.catalog_alternatives:
            lines.append("")
            lines.append("  Available in catalog (not running):")
            for alt in self.catalog_alternatives:
                lines.append(f"    - {alt.engine_id}")
                lines.append(
                    f"      Start: docker compose up stt-batch-{self.stage}-{alt.engine_id}"
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
                {"id": e.engine_id, "reason": self._explain_mismatch(e)}
                for e in self.candidates
            ],
            "catalog_alternatives": [
                {"id": a.engine_id, "languages": a.capabilities.languages}
                for a in self.catalog_alternatives
            ],
        }


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


def _rank_and_select(
    capable: list[BatchEngineState],
    requirements: dict,
) -> EngineSelectionResult:
    """Rank capable engines and select best.

    Ranking criteria (in order of priority):
    1. Native word timestamps (skips alignment stage)
    2. Native diarization (skips diarize stage)
    3. Language specificity (specialized > universal)
    4. Speed (lower RTF is better)

    Args:
        capable: List of engines that meet hard requirements
        requirements: Job requirements (for context in reason)

    Returns:
        EngineSelectionResult with the best engine
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

    ranked = sorted(capable, key=score, reverse=True)
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
        engine_id=winner.engine_id,
        capabilities=winner.capabilities
        or EngineCapabilities(
            engine_id=winner.engine_id, version="unknown", stages=[winner.stage]
        ),
        selection_reason=", ".join(reasons) or "best available",
    )


async def select_engine(
    stage: str,
    requirements: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
    user_preference: str | None = None,
) -> EngineSelectionResult:
    """Select best engine for a pipeline stage.

    Selection process:
    1. If user specified an engine, validate it can handle requirements
    2. Get all running engines for the stage from registry
    3. Filter by hard requirements (language, streaming)
    4. Rank remaining engines by capabilities
    5. Return best match, or raise NoCapableEngineError with alternatives

    Args:
        stage: Pipeline stage (e.g., "transcribe", "diarize")
        requirements: Job requirements from extract_requirements()
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)
        user_preference: Optional engine_id override from user

    Returns:
        EngineSelectionResult with selected engine

    Raises:
        NoCapableEngineError: If no running engine can handle requirements
    """
    # 1. Validate explicit user choice if provided
    if user_preference:
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
            engine_id=engine.engine_id,
            capabilities=engine.capabilities
            or EngineCapabilities(
                engine_id=engine.engine_id, version="unknown", stages=[stage]
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

    # 5. Single match - return directly
    if len(capable) == 1:
        engine = capable[0]
        logger.info(
            "engine_selected",
            stage=stage,
            selected_engine=engine.engine_id,
            selection_reason="only capable engine",
            candidates_evaluated=len(candidates),
            capable_count=1,
            requirements=requirements,
        )
        return EngineSelectionResult(
            engine_id=engine.engine_id,
            capabilities=engine.capabilities
            or EngineCapabilities(
                engine_id=engine.engine_id, version="unknown", stages=[stage]
            ),
            selection_reason="only capable engine",
        )

    # 6. Multiple matches - rank and select
    result = _rank_and_select(capable, requirements)

    logger.info(
        "engine_selected",
        stage=stage,
        selected_engine=result.engine_id,
        selection_reason=result.selection_reason,
        candidates_evaluated=len(candidates),
        capable_count=len(capable),
        requirements=requirements,
    )

    return result


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
) -> dict[str, EngineSelectionResult]:
    """Select engines for all required pipeline stages.

    This is the main entry point for capability-driven engine selection.
    It selects engines for all stages needed based on job parameters and
    engine capabilities.

    Args:
        parameters: Job parameters from API request
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)

    Returns:
        Dict mapping stage names to EngineSelectionResult

    Raises:
        NoCapableEngineError: If any required stage has no capable engine
    """
    requirements = extract_requirements(parameters)
    selections: dict[str, EngineSelectionResult] = {}

    # Prepare (always required)
    selections["prepare"] = await select_engine(
        "prepare",
        {},  # No special requirements for prepare
        registry,
        catalog,
        user_preference=parameters.get("engine_prepare"),
    )

    # Transcription (always required)
    selections["transcribe"] = await select_engine(
        "transcribe",
        requirements,
        registry,
        catalog,
        user_preference=parameters.get("engine_transcribe"),
    )

    # Alignment (conditional on transcriber capabilities)
    if _should_add_alignment(parameters, selections["transcribe"]):
        # Alignment only needs language requirement
        align_requirements = (
            {"language": requirements.get("language")}
            if requirements.get("language")
            else {}
        )
        selections["align"] = await select_engine(
            "align",
            align_requirements,
            registry,
            catalog,
            user_preference=parameters.get("engine_align"),
        )

    # Diarization (conditional on parameters and transcriber capabilities)
    if _should_add_diarization(parameters, selections["transcribe"]):
        selections["diarize"] = await select_engine(
            "diarize",
            {},  # No special requirements for diarize
            registry,
            catalog,
            user_preference=parameters.get("engine_diarize"),
        )

    # PII detection (conditional on parameters)
    pii_detection_enabled = parameters.get("pii_detection", False)
    if pii_detection_enabled:
        selections["pii_detect"] = await select_engine(
            "pii_detect",
            {},  # No special requirements for PII detection
            registry,
            catalog,
            user_preference=parameters.get("engine_pii_detect"),
        )

        # Audio redaction (conditional on parameters, requires PII detection)
        if parameters.get("redact_pii_audio", False):
            selections["audio_redact"] = await select_engine(
                "audio_redact",
                {},  # No special requirements for audio redaction
                registry,
                catalog,
                user_preference=parameters.get("engine_audio_redact"),
            )

    # Merge (always required)
    selections["merge"] = await select_engine(
        "merge",
        {},  # No special requirements for merge
        registry,
        catalog,
        user_preference=parameters.get("engine_merge"),
    )

    logger.info(
        "pipeline_engines_selected",
        stages=list(selections.keys()),
        engines={stage: sel.engine_id for stage, sel in selections.items()},
        alignment_included="align" in selections,
        diarization_included="diarize" in selections,
        pii_detection_included="pii_detect" in selections,
        audio_redaction_included="audio_redact" in selections,
    )

    return selections

# M31: Capability-Driven Engine Routing

|                  |                                                                                         |
| ---------------- | --------------------------------------------------------------------------------------- |
| **Goal**         | Orchestrator selects engines by capability, not hardcoded names; DAG shape adapts automatically |
| **Duration**     | 6 days                                                                                  |
| **Dependencies** | M30 (Engine Metadata Evolution)                                                         |
| **Deliverable**  | Engine selector module, capability-driven DAG builder, actionable error messages, auto-failover |
| **Status**       | Done                                                                                    |

## User Stories

> *"As an operator, I want to add a new transcription engine by deploying a container with engine.yaml, without modifying orchestrator code."*

> *"As an API client, I want immediate, actionable feedback when my job can't be processed, not a generic failure after queuing."*

> *"As a platform owner, I want jobs to automatically re-route to alternative engines when the originally selected one goes down."*

---

## Context

M30 established engine.yaml as the single source of truth and exposed capabilities via registry heartbeats and the catalog. But the orchestrator still doesn't *use* this metadata for routing decisions:

```python
# Current state (dag.py)
DEFAULT_ENGINES = {"transcribe": "faster-whisper", "diarize": "pyannote-4.0", ...}
NATIVE_WORD_TIMESTAMP_ENGINES = {"parakeet"}

# Engine selection is hardcoded
engine = parameters.get("engine_transcribe") or DEFAULT_ENGINES["transcribe"]
skip_alignment = engine in NATIVE_WORD_TIMESTAMP_ENGINES
```

**Problems this creates:**

1. **New engines require code changes** — Adding a transcriber means editing `DEFAULT_ENGINES` and possibly `NATIVE_WORD_TIMESTAMP_ENGINES`
2. **Silent misrouting** — Croatian audio routed to English-only Parakeet produces garbage
3. **Suboptimal pipelines** — Engine with native word timestamps still gets alignment stage if not in the hardcoded set
4. **Opaque failures** — "Engine unavailable" with no guidance on what's running or what to start
5. **No failover** — If selected engine dies, task fails permanently

This milestone makes the orchestrator capability-driven:

- Engine selection queries the registry by stage + requirements
- DAG shape determined by selected engine's capability flags
- Clear errors explain why no engine matched and what alternatives exist
- Automatic re-routing when engines disappear

### Architecture After M31

```
Job Submission
      │
      ▼
┌─────────────────┐
│ Extract         │  Job config → internal requirements
│ Requirements    │  {language: "hr", word_timestamps: true}
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│ Engine Selector │────►│ Registry     │  What's running?
│                 │     │ (Redis)      │
│ For each stage: │     └──────────────┘
│ - Filter by caps│     ┌──────────────┐
│ - Rank matches  │────►│ Catalog      │  What could run?
│ - Select best   │     │ (JSON)       │  (for error messages)
└────────┬────────┘     └──────────────┘
         │
         ▼
┌─────────────────┐
│ DAG Builder     │  Shape driven by capabilities:
│                 │  - transcriber.supports_word_timestamps → skip align
│                 │  - transcriber.includes_diarization → skip diarize
└────────┬────────┘
         │
         ▼
      Task Queue
```

---

## Outcomes

### O1: Zero-Code Engine Additions

**Before:** Adding a new engine requires modifying `DEFAULT_ENGINES` and `NATIVE_WORD_TIMESTAMP_ENGINES` in dag.py.

**After:** Deploy engine with engine.yaml → it participates in routing automatically based on declared capabilities.

**Verification:**

```bash
# Deploy new engine (no code changes)
docker compose up -d stt-batch-transcribe-canary

# Submit job - new engine is selected if it's the best match
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" -F "language=en"
# Response shows engine_id: canary-whisper
```

### O2: Correct Language Routing

**Before:** Croatian audio may be routed to English-only Parakeet, producing garbage output.

**After:** Jobs route to engines that support the requested language, or fail fast with clear error.

**Verification:**

```bash
# Only Parakeet (English-only) running
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@croatian.wav" -F "language=hr"
# 422: No engine supports language 'hr'. Running: parakeet (en only).
#      Available in catalog: faster-whisper (all languages)
```

### O3: Automatic Pipeline Optimization

**Before:** DAG always includes alignment stage unless engine is in hardcoded `NATIVE_WORD_TIMESTAMP_ENGINES` set.

**After:** DAG shape adapts based on selected engine's capabilities:

- `supports_word_timestamps: true` → skip alignment stage
- `includes_diarization: true` → skip diarize stage

**Verification:**

```bash
# With Parakeet selected (native word timestamps)
# DAG: prepare → transcribe → merge (no align stage)

# With faster-whisper selected (needs alignment)
# DAG: prepare → transcribe → align → merge
```

### O4: Actionable Error Messages

**Before:** "Engine unavailable" or generic 500 errors.

**After:** Errors explain what was required, what's running, why each failed, and what alternatives exist.

**Verification:**

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@croatian.wav" -F "language=hr"
# 422 response:
{
  "error": "no_capable_engine",
  "stage": "transcribe",
  "requirements": {"language": "hr"},
  "running_engines": [
    {"id": "parakeet", "reason": "language 'hr' not supported (has: ['en'])"}
  ],
  "catalog_alternatives": [
    {"id": "faster-whisper", "languages": null}
  ]
}
```

### O5: Automatic Failover

**Before:** If selected engine goes down between submission and execution, task fails permanently.

**After:** System re-selects alternative engine and re-routes task (up to retry budget).

**Verification:**

```bash
# Submit job, faster-whisper selected
# Kill faster-whisper mid-processing
docker compose stop stt-batch-transcribe-whisper

# Task automatically re-routes to parakeet (if language compatible)
# Logs show: engine_reselected, original=faster-whisper, new=parakeet
```

### O6: Selection Observability

**Before:** No visibility into why a particular engine was chosen.

**After:** Structured logs document every selection decision.

**Verification:**

```bash
docker compose logs gateway | grep engine_selected
# {"event": "engine_selected", "stage": "transcribe",
#  "selected_engine": "parakeet", "reason": "native word timestamps",
#  "candidates_evaluated": 2, "capable_count": 2}
```

---

## Strategy

### Phase 1: Foundation (Days 1-2)

Extend the data model and add prerequisite methods before building the selector.

1. Add missing field to `EngineCapabilities` (`includes_diarization`)
2. Update catalog loader to extract new fields
3. Add `find_engines()` method to catalog for requirement-based queries
4. Resolve faster-whisper word timestamps discrepancy

### Phase 2: Engine Selector (Days 2-3)

Build the core selection logic as an isolated, testable module.

1. Create `engine_selector.py` with `select_engine()` function
2. Implement requirements matching (hard filters)
3. Implement ranking logic (soft preferences)
4. Build `NoCapableEngineError` with structured context

### Phase 3: DAG Builder Integration (Days 3-4)

Replace hardcoded engine selection with capability-driven selection.

1. Update `build_task_dag()` signature to accept registry and catalog
2. Replace `DEFAULT_ENGINES` with selector calls
3. Replace `NATIVE_WORD_TIMESTAMP_ENGINES` with capability checks
4. Update all callers of `build_task_dag()`

### Phase 4: Error Handling & Failover (Days 5-6)

Make the system robust and observable.

1. Wire selector into job submission for fail-fast validation
2. Implement retry-with-reselection flow
3. Add structured logging for selection decisions
4. Add strangle-fig fallback during migration

---

## Tactical Plan

### 31.1: Extend EngineCapabilities Model

Add fields needed by the selector and DAG builder.

```python
# dalston/engine_sdk/types.py
class EngineCapabilities(BaseModel):
    # ... existing fields ...

    # NEW: For DAG shape decisions
    includes_diarization: bool = False    # Output includes speaker labels
```

**Files:**

- MODIFY: `dalston/engine_sdk/types.py`

**Tests:**

- MODIFY: `tests/unit/test_engine_capabilities.py`

---

### 31.2: Update Catalog Loader

Extract `includes_diarization` from engine.yaml into capabilities.

```python
# dalston/orchestrator/catalog.py
capabilities = EngineCapabilities(
    # ... existing ...
    includes_diarization=caps_data.get("includes_diarization", False),
)
```

**Files:**

- MODIFY: `dalston/orchestrator/catalog.py`

---

### 31.3: Add find_engines() to Catalog

Enable requirement-based queries for error messages.

```python
# dalston/orchestrator/catalog.py
def find_engines(
    self,
    stage: str,
    requirements: dict
) -> list[CatalogEntry]:
    """Find catalog engines that could satisfy requirements."""
    result = []
    for entry in self.get_engines_for_stage(stage):
        if self._matches_requirements(entry.capabilities, requirements):
            result.append(entry)
    return result

def _matches_requirements(
    self,
    caps: EngineCapabilities,
    requirements: dict
) -> bool:
    # Language check
    lang = requirements.get("language")
    if lang and caps.languages is not None:
        if lang.lower() not in [l.lower() for l in caps.languages]:
            return False

    return True
```

**Files:**

- MODIFY: `dalston/orchestrator/catalog.py`

**Tests:**

- NEW: `tests/unit/test_catalog_find_engines.py`

---

### 31.4: Clarify supports_word_timestamps Semantics

**Resolved:** The `supports_word_timestamps` field means "produces **accurate** word-level timestamps that don't require correction downstream."

- **Parakeet**: `true` — Native RNNT alignment produces accurate timestamps
- **faster-whisper**: `false` — Whisper's word timestamps are notoriously inaccurate; the alignment stage (WhisperX's wav2vec2 forced alignment) is required to fix them

This matches the current DAG builder behavior: faster-whisper is NOT in `NATIVE_WORD_TIMESTAMP_ENGINES`, so alignment is always added. The engine.yaml was incorrectly claiming `word_timestamps: true`.

**Note:** We don't need a dedicated WhisperX transcription engine. WhisperX functionality is achieved by combining faster-whisper + alignment stage + diarization stage — which is exactly what the capability-driven DAG builder does automatically.

**Files:**

- MODIFY: `engines/transcribe/faster-whisper/engine.yaml` — Set `word_timestamps: false` ✅ (done)

---

### 31.5: Create Engine Selector Module

New module with core selection logic.

```python
# dalston/orchestrator/engine_selector.py
from dataclasses import dataclass
from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.registry import BatchEngineRegistry, BatchEngineState
from dalston.orchestrator.catalog import EngineCatalog


@dataclass
class EngineSelectionResult:
    """Result of engine selection."""
    engine_id: str
    capabilities: EngineCapabilities
    selection_reason: str


class NoCapableEngineError(Exception):
    """No running engine can handle job requirements."""

    def __init__(
        self,
        stage: str,
        requirements: dict,
        candidates: list[BatchEngineState],
        catalog_alternatives: list,
    ):
        self.stage = stage
        self.requirements = requirements
        self.candidates = candidates
        self.catalog_alternatives = catalog_alternatives
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        # Human-readable error message
        ...

    def to_dict(self) -> dict:
        # Structured error for API responses
        ...


async def select_engine(
    stage: str,
    requirements: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
    user_preference: str | None = None,
) -> EngineSelectionResult:
    """Select best engine for a pipeline stage."""

    # 1. Validate explicit user choice if provided
    if user_preference:
        return await _validate_explicit_choice(
            user_preference, stage, requirements, registry
        )

    # 2. Get running engines for stage
    candidates = await registry.get_engines_for_stage(stage)

    # 3. Filter by hard requirements
    capable = [
        e for e in candidates
        if e.capabilities and _meets_requirements(e.capabilities, requirements)
    ]

    # 4. No capable engine
    if not capable:
        catalog_alts = catalog.find_engines(stage, requirements)
        raise NoCapableEngineError(stage, requirements, candidates, catalog_alts)

    # 5. Single match
    if len(capable) == 1:
        return EngineSelectionResult(
            engine_id=capable[0].engine_id,
            capabilities=capable[0].capabilities,
            selection_reason="only capable engine",
        )

    # 6. Multiple matches - rank and select
    return _rank_and_select(capable, requirements)


def _meets_requirements(caps: EngineCapabilities, requirements: dict) -> bool:
    """Check hard requirements only."""
    # Language (hard)
    lang = requirements.get("language")
    if lang and caps.languages is not None:
        if lang.lower() not in [l.lower() for l in caps.languages]:
            return False

    # Streaming (hard)
    if requirements.get("streaming") and not caps.supports_streaming:
        return False

    return True


def _rank_and_select(
    capable: list[BatchEngineState],
    requirements: dict,
) -> EngineSelectionResult:
    """Rank capable engines and select best."""

    def score(engine: BatchEngineState) -> tuple:
        caps = engine.capabilities

        # Prefer native word timestamps (skips alignment stage)
        native_ts = 1 if caps.supports_word_timestamps else 0

        # Prefer native diarization (skips diarize stage)
        native_diar = 1 if caps.includes_diarization else 0

        # Prefer faster (lower RTF)
        rtf = caps.rtf_gpu if caps.rtf_gpu else 999.0
        speed = -rtf

        # Prefer specificity
        specific = 1 if caps.languages is not None else 0

        return (native_ts, native_diar, specific, speed)

    ranked = sorted(capable, key=score, reverse=True)
    winner = ranked[0]

    reasons = []
    if winner.capabilities.supports_word_timestamps:
        reasons.append("native word timestamps")
    if winner.capabilities.includes_diarization:
        reasons.append("native diarization")
    if len(capable) > 1:
        reasons.append(f"ranked first of {len(capable)}")

    return EngineSelectionResult(
        engine_id=winner.engine_id,
        capabilities=winner.capabilities,
        selection_reason=", ".join(reasons) or "best available",
    )
```

**Files:**

- NEW: `dalston/orchestrator/engine_selector.py`

**Tests:**

- NEW: `tests/unit/test_engine_selector.py`

---

### 31.6: Implement NoCapableEngineError Messages

Detailed, actionable error formatting.

```python
# In engine_selector.py
def _build_message(self) -> str:
    lines = [
        f"No running engine can handle this job.",
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
            lines.append(f"      Start: docker compose up stt-batch-{self.stage}-{alt.engine_id}")

    return "\n".join(lines)

def _explain_mismatch(self, engine: BatchEngineState) -> str:
    if engine.capabilities is None:
        return "no capabilities declared"

    caps = engine.capabilities
    reasons = []

    lang = self.requirements.get("language")
    if lang and caps.languages and lang.lower() not in [l.lower() for l in caps.languages]:
        reasons.append(f"language '{lang}' not supported (has: {caps.languages})")

    if self.requirements.get("streaming") and not caps.supports_streaming:
        reasons.append("streaming not supported")

    return "; ".join(reasons) if reasons else "unknown"

def to_dict(self) -> dict:
    return {
        "error": "no_capable_engine",
        "stage": self.stage,
        "requirements": self.requirements,
        "running_engines": [
            {"id": e.engine_id, "reason": self._explain_mismatch(e)}
            for e in self.candidates
        ],
        "catalog_alternatives": [
            {"id": a.engine_id, "capabilities": a.capabilities.model_dump()}
            for a in self.catalog_alternatives
        ],
    }
```

**Files:**

- MODIFY: `dalston/orchestrator/engine_selector.py`

---

### 31.7: Extract Job Requirements

Convert user-facing config to internal requirements.

```python
# dalston/orchestrator/engine_selector.py
def extract_requirements(parameters: dict) -> dict:
    """Convert job parameters to selector requirements."""
    requirements = {}

    # Language
    language = parameters.get("language") or parameters.get("language_code")
    if language and language.lower() != "auto":
        requirements["language"] = language

    # Streaming (realtime path only)
    if parameters.get("streaming"):
        requirements["streaming"] = True

    return requirements
```

**Files:**

- MODIFY: `dalston/orchestrator/engine_selector.py`

---

### 31.8: Create select_all_engines() Function

All-or-nothing selection for complete pipeline.

```python
# dalston/orchestrator/engine_selector.py
async def select_all_engines(
    parameters: dict,
    requirements: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
) -> dict[str, EngineSelectionResult]:
    """Select engines for all required stages. All-or-nothing."""
    selections = {}

    # Transcription (always required)
    selections["transcribe"] = await select_engine(
        "transcribe",
        requirements,
        registry,
        catalog,
        user_preference=parameters.get("engine_transcribe"),
    )

    # Alignment (conditional)
    if _should_add_alignment(parameters, selections["transcribe"]):
        selections["align"] = await select_engine(
            "align",
            {"language": requirements.get("language")},
            registry,
            catalog,
        )

    # Diarization (conditional)
    if _should_add_diarization(parameters, selections["transcribe"]):
        selections["diarize"] = await select_engine(
            "diarize",
            {},
            registry,
            catalog,
        )

    # Prepare and merge (always required, no special requirements)
    selections["prepare"] = await select_engine("prepare", {}, registry, catalog)
    selections["merge"] = await select_engine("merge", {}, registry, catalog)

    return selections


def _should_add_alignment(parameters: dict, transcribe: EngineSelectionResult) -> bool:
    """Alignment needed when job wants word timestamps and transcriber lacks them."""
    # Check user preference
    if "word_timestamps" in parameters:
        wants = parameters["word_timestamps"]
    elif "timestamps_granularity" in parameters:
        wants = parameters["timestamps_granularity"] == "word"
    else:
        wants = True  # Default: word timestamps on

    has_native = transcribe.capabilities.supports_word_timestamps
    return wants and not has_native


def _should_add_diarization(parameters: dict, transcribe: EngineSelectionResult) -> bool:
    """Diarization stage needed when job wants it and transcriber doesn't include it."""
    speaker_detection = parameters.get("speaker_detection", "none")
    wants = speaker_detection == "diarize"
    has_native = transcribe.capabilities.includes_diarization
    return wants and not has_native
```

**Files:**

- MODIFY: `dalston/orchestrator/engine_selector.py`

**Tests:**

- NEW: `tests/unit/test_select_all_engines.py`

---

### 31.9: Refactor build_task_dag() Signature

Update to accept registry and catalog.

```python
# dalston/orchestrator/dag.py

# BEFORE
def build_task_dag(job_id: UUID, audio_uri: str, parameters: dict) -> list[Task]:

# AFTER
async def build_task_dag(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
) -> list[Task]:
```

**Breaking change:** All callers must be updated.

**Files:**

- MODIFY: `dalston/orchestrator/dag.py`

---

### 31.10: Find and Update All Callers

Search for `build_task_dag` usages and update each.

```bash
# Find all callers
grep -r "build_task_dag" dalston/ tests/
```

Expected locations:

- `dalston/orchestrator/scheduler.py` — main caller
- `tests/unit/test_dag.py` — unit tests
- `tests/integration/test_*.py` — integration tests

**Files:**

- MODIFY: `dalston/orchestrator/scheduler.py`
- MODIFY: `tests/unit/test_dag.py`
- MODIFY: Integration tests as needed

---

### 31.11: Replace DEFAULT_ENGINES with Selector

Remove hardcoded defaults, use selector instead.

```python
# dalston/orchestrator/dag.py

# DELETE these lines:
# DEFAULT_ENGINES = {...}
# NATIVE_WORD_TIMESTAMP_ENGINES = {...}

async def build_task_dag(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
) -> list[Task]:

    requirements = extract_requirements(parameters)

    # Select all engines (replaces DEFAULT_ENGINES)
    selections = await select_all_engines(
        parameters, requirements, registry, catalog
    )

    # Build engines dict from selections (for compatibility)
    engines = {stage: sel.engine_id for stage, sel in selections.items()}

    # DAG shape from capabilities (replaces NATIVE_WORD_TIMESTAMP_ENGINES)
    skip_alignment = "align" not in selections
    skip_diarization = "diarize" not in selections

    # ... rest of DAG building using engines dict ...
```

**Files:**

- MODIFY: `dalston/orchestrator/dag.py`

---

### 31.12: Update _build_per_channel_dag()

Apply same changes to per-channel DAG builder.

```python
# dalston/orchestrator/dag.py
def _build_per_channel_dag(
    tasks: list[Task],
    prepare_task: Task,
    job_id: UUID,
    selections: dict[str, EngineSelectionResult],  # Changed from engines dict
    transcribe_config: dict,
    word_timestamps: bool,
    num_channels: int = 2,
) -> list[Task]:

    # Skip alignment based on capabilities, not engine name
    skip_alignment = "align" not in selections

    # ... rest unchanged, using selections["transcribe"].engine_id etc ...
```

**Files:**

- MODIFY: `dalston/orchestrator/dag.py`

---

### 31.13: Wire Into Job Submission

Fail fast at submission if no capable engine.

```python
# dalston/orchestrator/scheduler.py (or wherever jobs are submitted)
from dalston.orchestrator.engine_selector import (
    select_all_engines,
    extract_requirements,
    NoCapableEngineError,
)

async def submit_job(job_id: UUID, audio_uri: str, parameters: dict):
    requirements = extract_requirements(parameters)

    try:
        selections = await select_all_engines(
            parameters, requirements, registry, catalog
        )
    except NoCapableEngineError as e:
        # Return structured error to gateway
        raise JobValidationError(
            status_code=422,
            error=e.to_dict(),
        )

    # Build DAG with validated selections
    dag = await build_task_dag(
        job_id, audio_uri, parameters, registry, catalog
    )

    # Queue tasks...
```

**Files:**

- MODIFY: `dalston/orchestrator/scheduler.py`
- MODIFY: `dalston/gateway/api/v1/transcriptions.py` (error handling)

---

### 31.14: Implement Retry-with-Reselection

> **DEFERRED**: This task is deferred to a future milestone. The core capability-driven selection at job submission time covers the primary use case. Retry-with-reselection adds complexity for an edge case (engine dying mid-job) that can be handled by simple job resubmission for now.

Handle engine disappearance during execution.

```python
# dalston/orchestrator/task_runner.py (or equivalent)
MAX_RESELECTIONS = 2

async def execute_task(task: Task, job: Job):
    engine = await registry.get_engine(task.engine_id)

    if engine is None or not engine.is_available:
        await _handle_engine_disappeared(task, job)
        return

    # Normal execution...


async def _handle_engine_disappeared(task: Task, job: Job):
    """Re-select engine when original is unavailable."""

    if task.reselection_count >= MAX_RESELECTIONS:
        await job.fail(
            error="engine_reselection_exhausted",
            message=f"Task re-routed {MAX_RESELECTIONS} times. Engines may be unstable.",
        )
        return

    requirements = extract_requirements(job.parameters)

    try:
        new_selection = await select_engine(
            stage=task.stage,
            requirements=requirements,
            registry=registry,
            catalog=catalog,
            # Don't pass user_preference - that engine is dead
        )

        logger.info(
            "engine_reselected",
            job_id=str(job.id),
            task_id=str(task.id),
            original_engine=task.engine_id,
            new_engine=new_selection.engine_id,
        )

        task.engine_id = new_selection.engine_id
        task.reselection_count += 1
        await requeue_task(task)

    except NoCapableEngineError:
        await job.fail(
            error="engine_unavailable",
            message=(
                f"Engine '{task.engine_id}' unavailable and no alternative "
                f"for stage '{task.stage}'. Start a compatible engine and resubmit."
            ),
        )
```

**Requires adding `reselection_count` to Task model.**

**Files:**

- MODIFY: `dalston/common/models.py` (add reselection_count to Task)
- MODIFY: `dalston/orchestrator/task_runner.py` (or equivalent)

---

### 31.15: Add Structured Logging

Log selection decisions for observability.

```python
# dalston/orchestrator/engine_selector.py
import structlog
logger = structlog.get_logger()

async def select_engine(...) -> EngineSelectionResult:
    # ... selection logic ...

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
```

```python
# dalston/orchestrator/dag.py
async def build_task_dag(...) -> list[Task]:
    # ... after selection ...

    logger.info(
        "dag_shape_decided",
        job_id=str(job_id),
        transcriber=selections["transcribe"].engine_id,
        alignment_included="align" in selections,
        diarization_included="diarize" in selections,
        stages=[s for s in selections.keys()],
    )
```

**Files:**

- MODIFY: `dalston/orchestrator/engine_selector.py`
- MODIFY: `dalston/orchestrator/dag.py`

---

### 31.16: Add Strangle-Fig Fallback

Keep DEFAULT_ENGINES during migration for safety.

```python
# dalston/orchestrator/dag.py
_LEGACY_DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
    "align": "whisperx-align",
    "diarize": "pyannote-4.0",
    "merge": "final-merger",
}

async def build_task_dag(...) -> list[Task]:
    try:
        selections = await select_all_engines(...)
    except Exception as e:
        logger.warning(
            "engine_selector_fallback",
            error=str(e),
            fallback="LEGACY_DEFAULT_ENGINES",
        )
        # Fall back to hardcoded defaults
        selections = _create_legacy_selections()

    # ... rest of function ...
```

**Remove after one milestone cycle in production.**

**Files:**

- MODIFY: `dalston/orchestrator/dag.py`

---

### 31.17: Update Unit Tests

Comprehensive tests for selector logic.

```python
# tests/unit/test_engine_selector.py
import pytest
from dalston.orchestrator.engine_selector import (
    select_engine,
    _meets_requirements,
    _rank_and_select,
    NoCapableEngineError,
)

class TestMeetsRequirements:
    def test_no_requirements_always_matches(self):
        caps = mock_capabilities(languages=["en"])
        assert _meets_requirements(caps, {}) is True

    def test_language_filters_correctly(self):
        caps = mock_capabilities(languages=["en"])
        assert _meets_requirements(caps, {"language": "en"}) is True
        assert _meets_requirements(caps, {"language": "hr"}) is False

    def test_null_languages_means_all(self):
        caps = mock_capabilities(languages=None)
        assert _meets_requirements(caps, {"language": "hr"}) is True

class TestRanking:
    def test_prefers_native_word_timestamps(self):
        engines = [
            mock_engine("a", word_timestamps=False),
            mock_engine("b", word_timestamps=True),
        ]
        result = _rank_and_select(engines, {})
        assert result.engine_id == "b"

    def test_prefers_native_diarization(self):
        engines = [
            mock_engine("a", includes_diarization=False),
            mock_engine("b", includes_diarization=True),
        ]
        result = _rank_and_select(engines, {})
        assert result.engine_id == "b"

    def test_prefers_faster_rtf(self):
        engines = [
            mock_engine("slow", rtf_gpu=0.1),
            mock_engine("fast", rtf_gpu=0.01),
        ]
        result = _rank_and_select(engines, {})
        assert result.engine_id == "fast"


class TestSelectEngine:
    @pytest.mark.asyncio
    async def test_single_capable_engine_selected(self):
        registry = mock_registry([mock_engine("only-one", languages=["en"])])
        result = await select_engine("transcribe", {"language": "en"}, registry, catalog)
        assert result.engine_id == "only-one"

    @pytest.mark.asyncio
    async def test_raises_when_no_capable_engine(self):
        registry = mock_registry([mock_engine("parakeet", languages=["en"])])
        catalog = mock_catalog([mock_catalog_entry("faster-whisper", languages=None)])

        with pytest.raises(NoCapableEngineError) as exc:
            await select_engine("transcribe", {"language": "hr"}, registry, catalog)

        assert exc.value.stage == "transcribe"
        assert "parakeet" in str(exc.value)
        assert "faster-whisper" in str(exc.value)
```

**Files:**

- NEW: `tests/unit/test_engine_selector.py`

---

### 31.18: Update Integration Tests

Test end-to-end DAG building with selector.

```python
# tests/integration/test_dag_capability_driven.py
@pytest.mark.asyncio
async def test_dag_skips_alignment_with_native_timestamps(registry, catalog):
    """Parakeet has native timestamps → no align stage."""
    # Setup: only parakeet running
    await register_engine(registry, "parakeet", word_timestamps=True)

    dag = await build_task_dag(
        job_id=uuid4(),
        audio_uri="s3://test/audio.wav",
        parameters={"language": "en"},
        registry=registry,
        catalog=catalog,
    )

    stages = [t.stage for t in dag]
    assert "transcribe" in stages
    assert "align" not in stages


@pytest.mark.asyncio
async def test_dag_adds_alignment_without_native_timestamps(registry, catalog):
    """faster-whisper needs alignment → align stage added."""
    await register_engine(registry, "faster-whisper", word_timestamps=False)
    await register_engine(registry, "whisperx-align", stage="align")

    dag = await build_task_dag(...)

    stages = [t.stage for t in dag]
    assert "align" in stages


@pytest.mark.asyncio
async def test_new_engine_works_without_code_changes(registry, catalog):
    """New engine routes correctly based purely on capabilities."""
    # Register engine that doesn't exist in any hardcoded list
    await register_engine(
        registry,
        "brand-new-engine",
        languages=["en"],
        word_timestamps=True,
    )

    dag = await build_task_dag(
        ...,
        parameters={"language": "en"},
    )

    transcribe_task = next(t for t in dag if t.stage == "transcribe")
    assert transcribe_task.engine_id == "brand-new-engine"
    assert "align" not in [t.stage for t in dag]  # Native timestamps → no align
```

**Files:**

- NEW: `tests/integration/test_dag_capability_driven.py`

---

### 31.19: Documentation

Update architecture docs to reflect capability-driven routing.

**Files:**

- MODIFY: `docs/specs/batch/ORCHESTRATOR.md`
- MODIFY: `docs/specs/batch/ENGINES.md`

---

## What NOT to Do

- **Don't implement late binding** — Select at submission, not execution. Retry-with-reselection handles failures.
- **Don't add GPU/CPU preference to ranking** — We don't run both variants simultaneously yet.
- **Don't change pipeline stages** — This is routing within existing stages, not new stages.
- **Don't touch realtime path** — Batch only. Realtime uses different registry (WorkerRegistry).
- **Don't delete DEFAULT_ENGINES immediately** — Keep as fallback for one milestone cycle.
- **Don't over-engineer ranking** — 3 criteria are enough for 2-3 engines per stage.

---

## Verification

```bash
# 1. Unit tests pass
pytest tests/unit/test_engine_selector.py -v

# 2. Integration tests pass
pytest tests/integration/test_dag_capability_driven.py -v

# 3. New engine works without code changes
# Deploy test engine, verify it's selected appropriately

# 4. Language routing works
docker compose up -d stt-batch-transcribe-parakeet  # English only
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@croatian.wav" -F "language=hr"
# Should return 422 with actionable error

# 5. Pipeline optimization works
# With parakeet: DAG has no align stage
# With faster-whisper: DAG has align stage

# 6. Failover works
# Submit job, kill engine mid-flight, verify re-routing in logs

# 7. Observability works
docker compose logs orchestrator | grep engine_selected
docker compose logs orchestrator | grep dag_shape_decided
```

---

## Checkpoint

- [x] `includes_diarization` added to EngineCapabilities
- [x] Catalog loader extracts new fields
- [x] `find_engines()` method added to catalog
- [x] faster-whisper word timestamps discrepancy resolved
- [x] `engine_selector.py` created with `select_engine()`
- [x] `NoCapableEngineError` with structured messages
- [x] `extract_requirements()` converts job config
- [x] `select_pipeline_engines()` handles full pipeline
- [x] `build_task_dag_async()` added with capability-driven selection
- [x] Job handler wired to use `build_task_dag_async()`
- [x] DAG shape adapts based on engine capabilities
- [x] `_build_per_channel_dag_from_selections()` uses capabilities
- [x] Job submission wired with fail-fast validation
- [ ] ~~Retry-with-reselection implemented~~ (DEFERRED)
- [x] Structured logging added
- [x] Strangle-fig fallback in place (sync `build_task_dag()` preserved)
- [x] Unit tests for selector
- [x] Integration tests for DAG building
- [x] Documentation updated

---

## Files Changed

| File | Change |
|------|--------|
| `dalston/engine_sdk/types.py` | MODIFY (add fields) |
| `dalston/orchestrator/catalog.py` | MODIFY (add find_engines, extract new fields) |
| `dalston/orchestrator/engine_selector.py` | NEW |
| `dalston/orchestrator/dag.py` | MODIFY (major refactor) |
| `dalston/orchestrator/scheduler.py` | MODIFY (wire selector) |
| `dalston/orchestrator/task_runner.py` | MODIFY (retry logic) |
| `dalston/common/models.py` | MODIFY (add reselection_count) |
| `dalston/gateway/api/v1/transcriptions.py` | MODIFY (error handling) |
| `engines/transcribe/faster-whisper/engine.yaml` | MODIFY (if word_timestamps wrong) |
| `docs/specs/batch/ORCHESTRATOR.md` | MODIFY |
| `docs/specs/batch/ENGINES.md` | MODIFY |
| `tests/unit/test_engine_selector.py` | NEW |
| `tests/unit/test_catalog_find_engines.py` | NEW |
| `tests/unit/test_select_all_engines.py` | NEW |
| `tests/unit/test_dag.py` | MODIFY |
| `tests/integration/test_dag_capability_driven.py` | NEW |

---

## Implementation Order

| Step | Scope | Effort |
|------|-------|--------|
| 31.1-31.3 | Extend EngineCapabilities + catalog | 0.5 day |
| 31.4 | Resolve faster-whisper discrepancy | 0.5 day |
| 31.5-31.8 | Engine selector module | 1.5 days |
| 31.9-31.12 | DAG builder refactor | 1.5 days |
| 31.13 | Job submission wiring | 0.5 day |
| 31.14 | Retry-with-reselection | 0.5 day |
| 31.15-31.16 | Logging + fallback | 0.5 day |
| 31.17-31.19 | Tests + docs | 0.5 day |

**Total: ~6 days**

---

## Enables Next

- **Auto-scaling**: Catalog + selector = "boot this image when no running engine matches"
- **Load-aware routing**: Add queue depth to ranking when `max_concurrent_jobs` is known
- **Cost optimization**: Route by GPU/CPU preference when both available
- **Multi-region routing**: Extend selector to consider engine location

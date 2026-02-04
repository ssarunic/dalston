# DAG Builder Patterns

## Task Creation Helper

```python
def create_task(
    stage: str,
    engine_id: str,
    dependencies: list[str],
    required: bool = True,
    config: dict | None = None,
) -> Task:
    """Create a task with standard fields."""
    return Task(
        id=f"task_{uuid.uuid4().hex[:12]}",
        stage=stage,
        engine_id=engine_id,
        dependencies=dependencies,
        required=required,  # If False, failure won't fail the job
        config=config or {},
        status=TaskStatus.PENDING,
    )
```

---

## Core Pipeline Construction

The core pipeline is always: `prepare → transcribe → [align] → [diarize]`

```python
def build_task_dag(job: Job) -> list[Task]:
    params = job.parameters
    tasks = []

    # === CORE PIPELINE (always runs) ===

    prepare = create_task("prepare", "audio-prepare", [])
    transcribe = create_task("transcribe", "faster-whisper", [prepare.id])
    tasks.extend([prepare, transcribe])

    # Track what the "core output" tasks are for downstream dependencies
    core_deps = []

    # Alignment (optional but common)
    if params.get("word_timestamps", True):
        align = create_task("align", "whisperx-align", [transcribe.id])
        tasks.append(align)
        core_deps.append(align.id)
    else:
        core_deps.append(transcribe.id)

    # Diarization (parallel with alignment, depends on prepare)
    if params.get("speaker_detection") == "diarize":
        diarize = create_task("diarize", "pyannote-3.1", [prepare.id])
        tasks.append(diarize)
        core_deps.append(diarize.id)

    return tasks, core_deps
```

---

## Optional/Parallel Enrichment

Enrichment tasks are:

- **Optional** (`required=False`): Don't fail job if they fail
- **Parallel**: Can run alongside each other
- **Post-core**: Wait for core pipeline to complete

```python
def add_enrichment_tasks(tasks: list, core_deps: list, params: dict) -> list[str]:
    """Add enrichment tasks, return their IDs for merge dependencies."""
    enrichment_tasks = []

    if params.get("detect_emotions"):
        emotion = create_task(
            "detect_emotions",
            "emotion2vec",
            core_deps,          # Wait for align/transcribe + diarize
            required=False,     # Don't fail job if this fails
        )
        enrichment_tasks.append(emotion)
        tasks.append(emotion)

    if params.get("detect_events"):
        events = create_task(
            "detect_events",
            "panns-events",
            [prepare.id],       # Only needs audio, can start early
            required=False,
        )
        enrichment_tasks.append(events)
        tasks.append(events)

    return [t.id for t in enrichment_tasks]
```

---

## LLM Cleanup (Waits for Enrichment)

LLM cleanup should wait for enrichment to complete so it can incorporate those results:

```python
def add_llm_cleanup(tasks: list, core_deps: list, enrichment_ids: list, params: dict) -> str | None:
    """Add LLM cleanup task, return its ID."""
    if not params.get("llm_cleanup"):
        return None

    # Wait for both core and enrichment
    llm_deps = enrichment_ids + core_deps if enrichment_ids else core_deps

    llm = create_task(
        "refine",
        "llm-cleanup",
        llm_deps,
        required=False,
        config={
            "tasks": [
                "fix_transcription_errors",
                "identify_speakers" if params.get("speaker_detection") == "diarize" else None,
                "generate_summary" if params.get("generate_summary") else None,
            ]
        }
    )
    tasks.append(llm)
    return llm.id
```

---

## Final Merge Task

Merge always runs last and collects all outputs:

```python
def add_merge_task(tasks: list, all_deps: list[str]):
    """Add final merge task that depends on everything."""
    merge = create_task("merge", "final-merger", all_deps)
    tasks.append(merge)
```

---

## Complete DAG Builder

```python
def build_task_dag(job: Job) -> list[Task]:
    params = job.parameters
    tasks = []

    # 1. Core pipeline
    tasks, core_deps = build_core_pipeline(params)

    # 2. Enrichment (optional, parallel)
    enrichment_ids = add_enrichment_tasks(tasks, core_deps, params)

    # 3. LLM cleanup (waits for enrichment)
    llm_id = add_llm_cleanup(tasks, core_deps, enrichment_ids, params)

    # 4. Determine merge dependencies
    if llm_id:
        merge_deps = [llm_id]
    elif enrichment_ids:
        merge_deps = enrichment_ids + core_deps
    else:
        merge_deps = core_deps

    # 5. Final merge
    add_merge_task(tasks, merge_deps)

    return tasks
```

---

## Example DAG Shapes

### Minimal (transcribe only)

```
prepare → transcribe → merge
```

### With alignment

```
prepare → transcribe → align → merge
```

### With diarization (parallel)

```
prepare → transcribe → align ──┐
    └──→ diarize ──────────────┴─→ merge
```

### Full enrichment

```
prepare → transcribe → align ──┬─→ emotions ──┐
    │                          │              │
    └──→ diarize ──────────────┤              ├─→ llm-cleanup → merge
    │                          │              │
    └──→ events ───────────────┴──────────────┘
```

---

## Task Status Transitions

```
PENDING ──→ READY ──→ RUNNING ──→ COMPLETED
   ↑                     │
   └─────────────────────┘ (retry)
                         │
                         └──→ FAILED ──→ SKIPPED (if not required)
```

**Transition rules:**

1. Task moves `PENDING` → `READY` when all dependencies are `COMPLETED`
2. `READY` tasks are pushed to engine-specific Redis queues
3. Engine pulls task, moves to `RUNNING`, processes, moves to `COMPLETED`
4. On `COMPLETED`, orchestrator checks dependents and advances them
5. On `FAILED`: retry if under limit, else `SKIPPED` if optional or fail job

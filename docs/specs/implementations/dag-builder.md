# DAG builder implementation

Current implementation: [`dalston/orchestrator/dag.py`](../../../dalston/orchestrator/dag.py).

The builder creates execution tasks only. Final transcript assembly is handled
by orchestrator completion handlers; it is not represented by a merge task.

## Inputs

The builder receives:

- job ID and audio artifact URI;
- normalized request parameters;
- selected engine IDs by stage;
- selected loaded-model IDs; and
- capability-derived decisions to skip alignment/diarization.

## Mono graph

```text
prepare → transcribe → optional align
   │
   └─────────────────► optional diarize
```

Dependency rules:

1. `prepare` has no dependency.
2. `transcribe` depends on `prepare`.
3. `align` depends on `transcribe`.
4. `diarize` depends on `prepare`, allowing it to run in parallel with the
   transcription/alignment branch.

Alignment is omitted when word timestamps were not requested or the selected
transcriber supplies them. Diarization is omitted when it was not requested or
the selected transcriber already includes speaker labels.

## Per-channel graph

```text
prepare
  ├──► transcribe_ch0 ──► optional align_ch0
  └──► transcribe_ch1 ──► optional align_ch1
```

`prepare` produces channel artifacts. Each transcription depends on prepare;
each alignment depends on its matching transcription. Completion code merges
the channel timelines chronologically without a queue-backed merge engine.

## Post-processing

PII detection and audio redaction are scheduled by post-processing lifecycle
code after core transcript assembly. They are intentionally absent from the
core DAG returned by `build_task_dag()`.

## Invariants

- Every non-prepare task has at least one valid dependency.
- Dependency IDs refer to tasks in the same job.
- No distributed graph contains a `merge` task.
- Diarization can become ready immediately after prepare.
- Per-channel branches do not depend on one another.
- Loaded model IDs propagate only to their corresponding stage configs.

The invariant tests live in
[`tests/unit/test_dag.py`](../../../tests/unit/test_dag.py).

# M52 Local Runner Readiness

Date: 2026-03-05
Milestone: M52 (Engine SDK Local Runner DX Clean-Cut)

## Goal

Validate that the M52 local runner command is usable as the pre-refactor harness for representative non-transcribe stages (`align`, `diarize`, `pii_detect`) and capture any readiness gaps.

## Command Under Test

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine <engine-ref> \
  --stage <stage> \
  --config <config.json> \
  --output <output.json>
```

## Stage Dry Runs

1. `align` (`engines.stt-align.phoneme-align.engine:PhonemeAlignEngine`)

- Result: Passed.
- Notes: Command completed, produced canonical `output.json`, and emitted aligned segment/word data.

2. `diarize` (`engines.stt-diarize.pyannote-4.0.engine:PyannoteEngine`)

- Result: Passed (with `DALSTON_DIARIZATION_DISABLED=true`).
- Notes: Command completed and produced canonical diarization output envelope in local mode.

3. `pii_detect` (`engines.stt-detect.pii-presidio.engine:PIIDetectionEngine`)

- Result: Blocked.
- Error: `ModuleNotFoundError: No module named 'presidio_analyzer'`.
- Impact: Local `pii_detect` dry runs require Presidio dependencies in the local environment.

## Readiness Findings

1. Local runner CLI contract is functional for representative alignment and diarization flows.
2. Engine reference resolution now supports filesystem-backed refs used by hyphenated runtime paths.
3. `pii_detect` local readiness is environment-dependent; missing Presidio dependency is the current blocker.

## Follow-Up

1. Ensure local dev dependency set includes `presidio-analyzer`/`presidio-anonymizer` (and any required models) for full `pii_detect` local-run parity.
2. Keep `pii_detect` dependency validation as a pre-flight check in future local-run docs/scripts.

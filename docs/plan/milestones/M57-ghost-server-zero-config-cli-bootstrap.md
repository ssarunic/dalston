# M57: Ghost Server + Zero-Config CLI Bootstrap (Clean-Cut)

| | |
|---|---|
| **Goal** | Make first-run UX truly zero-config: `dalston transcribe <file>` automatically boots local engine_id prerequisites and returns output without manual server/model steps |
| **Duration** | 5-8 days |
| **Dependencies** | M56 (lite infra backends), M13 (CLI baseline), M36/M40 (engine_id model management + model registry) |
| **Deliverable** | CLI bootstrap state machine that auto-checks/starts local server, auto-ensures default model, runs transcription immediately, and presents clear progress/error UX |
| **Status** | Completed |

Dependency clarification:

1. M56 enables local non-Docker execution path (`lite` mode) required for ghost-server startup.
2. M13 provides the CLI command surface that M57 upgrades from "thin proxy" to "self-bootstrapping UX."
3. M36/M40 provide model lifecycle primitives that M57 invokes automatically in first-run flow.
4. Delivery estimate assumes M56 has landed; if M56 slips, M57 start date and timeline slip with it.

## Intended Outcomes

### Functional outcomes

1. `dalston transcribe <file>` works on a fresh local install without requiring manual server startup.
2. If no local server is running, CLI starts one in background (local-only ghost server behavior).
3. If the requested/default model is missing, CLI automatically pulls/ensures it before submit.
4. Command returns transcript output directly after bootstrap and processing.
5. Default model for zero-config local flow is explicit and deterministic: `distil-small` unless overridden.

### UX outcomes

1. Bootstrap steps are visible and understandable:
   - preflight
   - server ready
   - model ready
   - transcription running
2. User does not need to learn backend internals (`uvicorn`, Redis, model registry, etc.) for first success.
3. Errors are actionable (which step failed, what to do next), not generic connection failures.

### Operational outcomes

1. No duplicate local server processes on repeated CLI calls.
2. Stale PID/lock state is cleaned safely.
3. Explicit remote-server usage (`--server`, `DALSTON_SERVER`) bypasses local ghost-server behavior by default.
4. Ghost server has explicit lifecycle controls (`idle timeout` + `dalston server stop`).
5. Crash diagnostics are recoverable on next CLI call (PID metadata + last log tail).

### Clean-start outcomes

1. No manual `dalston models pull ...` requirement for first transcription.
2. No forced Docker requirement for first-run local usage.
3. No automatic remote/production server mutation from local CLI bootstrap.

### Success criteria

1. Fresh local path:
   - `pip install ...`
   - `dalston transcribe sample.wav --format json`
   - succeeds without manual pre-steps
2. Missing-server path auto-starts local server and completes request in one command.
3. Missing-model path auto-pulls default model and completes request in one command.
4. Second run is faster and skips already-satisfied bootstrap steps.
5. Existing explicit remote-server workflow remains unchanged.
6. `DALSTON_BOOTSTRAP=false` with missing local prerequisites fails fast with explicit remediation (no auto-start/pull).
7. Non-Dalston process on configured local port returns a specific conflict error (not stale-PID recovery).

---

## Strategy To Reach Outcomes

### Strategy 1: Freeze one canonical first-run contract

Define one deterministic bootstrap state machine in CLI:

1. preflight checks
2. local server readiness
3. model readiness
4. transcription submit/wait/output

Avoid ad hoc per-command bootstrap logic.

### Strategy 1.1: Treat bootstrap as a serialized state transition

First-run bootstrap must run under a single host-local lock so concurrent `dalston transcribe` invocations do not race server start/model pull.

### Strategy 2: Local bootstrap only, explicit remote safety

Ghost-server behavior applies only when target is local (localhost/127.0.0.1) and no explicit remote contract is requested. Remote environments must not be auto-mutated.

### Strategy 3: Integrate model readiness into transcribe flow

`transcribe` becomes the main user path and internally guarantees model availability. Keep explicit `models pull` for power users, but remove first-run dependency on it.

### Strategy 4: Fail by step, not by stack trace

Every bootstrap stage returns typed outcomes and actionable user messaging (for example: local server failed to start, model pull failed, ffmpeg missing).

### Strategy 4.1: Separate local port ownership outcomes

Local endpoint probe distinguishes:

1. healthy Dalston server
2. Dalston-owned process unhealthy/crashed
3. non-Dalston process occupying configured port

Each maps to different recovery/error behavior.

### Strategy 5: Keep distributed behavior stable

M57 is a UX layer over existing control-plane behavior. Distributed mode semantics are preserved.

---

## What We Will Not Do In M57

1. Do not implement full cross-platform service-manager installation (systemd/launchd/Windows service).
2. Do not auto-provision GPU drivers/CUDA/engine_id stacks.
3. Do not auto-start remote servers.
4. Do not expand lite-mode feature parity beyond the scoped M56 path.
5. Do not bundle one-line binary packaging (deferred to M60).

---

## Tactical Plan

### Phase 0: Freeze Bootstrap UX Contract

1. Freeze command semantics for local-first flow:
   - `dalston transcribe <file>`
   - optional overrides (`--server`, `--model`)
2. Define bootstrap state machine and output contract.
3. Define env/config controls:
   - `DALSTON_BOOTSTRAP=true|false` (default `true`)
   - `DALSTON_DEFAULT_MODEL=distil-small`
   - `DALSTON_LOCAL_SERVER_URL` / local server endpoint + port
   - `DALSTON_SERVER_START_TIMEOUT_SECONDS=30`
   - `DALSTON_BOOTSTRAP_LOCK_TIMEOUT_SECONDS=30`
   - `DALSTON_GHOST_IDLE_TIMEOUT_SECONDS=900`
4. Define failure taxonomy and exit codes by stage.
5. Freeze bootstrap artifacts and locations:
   - PID file: `~/.dalston/run/ghost-server.pid`
   - lock file: `~/.dalston/run/bootstrap.lock`
   - logs: `~/.dalston/logs/ghost-server.log`

Expected files:

- `cli/dalston_cli/main.py`
- `cli/dalston_cli/commands/transcribe.py`
- `docs/specs/batch/API.md`

### Phase 1: Bootstrap Preflight and Guardrails

1. Add local preflight checks before any server/model side effects:
   - input file exists/readable
   - writable cache/run/log dirs
   - required tool presence
   - free disk space against model-required bytes (+ safety margin)
2. Respect `DALSTON_BOOTSTRAP=false`:
   - if prerequisites missing, fail fast with remediation
   - do not auto-start server or auto-pull model
3. Keep preflight deterministic and cheap.

Expected files:

- `cli/dalston_cli/bootstrap/preflight.py` (new)
- `tests/unit/test_cli_preflight.py` (new)

### Phase 2: Local Server Lifecycle Manager ("Ghost Server")

1. Add CLI-side local server manager:
   - classify port ownership (healthy Dalston / unhealthy Dalston / non-Dalston)
   - start detached local server if missing
   - wait for readiness timeout (`DALSTON_SERVER_START_TIMEOUT_SECONDS`, default 30s)
   - handle stale PID/lock recovery
   - acquire bootstrap lock before start path (`bootstrap.lock`)
2. Guarantee single local instance behavior for default endpoint.
3. Add crash-aware diagnostics:
   - detect PID file exists but process missing
   - surface short "last server log tail" hint from `ghost-server.log`
4. Add termination policy:
   - explicit `dalston server stop`
   - idle timeout shutdown (default 15 minutes)
5. Add explicit bypass when target is remote.
6. Handle Ctrl+C cleanly during startup wait:
   - release lock
   - avoid orphan bootstrap metadata

Expected files:

- `cli/dalston_cli/bootstrap/server_manager.py` (new)
- `cli/dalston_cli/commands/transcribe.py`
- `cli/dalston_cli/commands/status.py`
- `cli/dalston_cli/commands/server.py` (new, if stop/start grouped under `dalston server ...`)
- `tests/unit/test_cli_server_manager.py` (new)

### Phase 3: Model Readiness Auto-Ensure

1. Add model readiness check in bootstrap pipeline.
2. If model missing/not-ready:
   - trigger pull/ensure flow
   - surface progress in CLI
   - download to temp/partial artifact and atomically promote on success
3. Respect explicit `--model`; otherwise use deterministic default model (`distil-small`).
4. Keep existing `dalston models pull` command as advanced/manual path.
5. Handle Ctrl+C during pull:
   - remove/mark partial files
   - keep cache metadata consistent for next run

Expected files:

- `cli/dalston_cli/bootstrap/model_manager.py` (new)
- `cli/dalston_cli/commands/transcribe.py`
- `cli/dalston_cli/commands/models.py`
- `tests/unit/test_cli_model_bootstrap.py` (new)
- `tests/integration/test_cli_autopull_flow.py` (new)

### Phase 4: Wire End-to-End Transcribe Bootstrap

1. Integrate state machine into `dalston transcribe`.
2. Ensure idempotent behavior:
   - if server already healthy, skip start
   - if model already ready, skip pull
3. Preserve remote-server behavior and advanced flags.
4. Ensure output consistency for `--json`, text formats, and wait/no-wait paths.
5. Run bootstrap state machine before broad catch-all exception handlers so typed bootstrap failures are not swallowed.

Expected files:

- `cli/dalston_cli/commands/transcribe.py`
- `cli/dalston_cli/output.py`
- `tests/integration/test_cli_zero_config_transcribe.py` (new)
- `tests/integration/test_cli_remote_server_passthrough.py` (new)

### Phase 5: Docs, Metrics, and Acceptance Gate

1. Update first code block in README to zero-config flow.
2. Add troubleshooting section for bootstrap stages, including:
   - port already used by non-Dalston process
   - server crash recovery/log location
   - disabled bootstrap mode behavior
3. Add acceptance checks for first-run and repeat-run paths.
4. Update plan index and milestone references.

Expected files:

- `README.md`
- `docs/README.md`
- `docs/guides/self-hosted-deployment-tutorial.md`
- `docs/plan/README.md`
- `docs/reports/M57-zero-config-cli-bootstrap.md` (new)

---

## Testing Plan

### Automated tests

1. Unit tests:
   - bootstrap lock acquisition/release and concurrent invocation serialization
   - server detection/start/ready logic
   - port ownership classification (Dalston healthy/unhealthy/non-Dalston)
   - stale PID/lock handling
   - crash diagnostic mapping from PID/log metadata
   - idle timeout/explicit stop behavior
   - model readiness decision logic
   - partial-download cleanup on interrupt
   - preflight checks and error mapping
   - bootstrap disabled behavior
2. Integration tests:
   - server down + model missing -> single command success
   - server up + model ready -> bootstrap steps skipped
   - remote server target -> no local server spawn
   - local port occupied by non-Dalston -> explicit conflict error
   - timeout while waiting for server readiness -> explicit timeout error
   - bootstrap disabled + missing server/model -> fail with remediation
   - model pull failure -> actionable failure output
3. Regression tests:
   - existing CLI command options remain functional
   - distributed path unchanged

Suggested command sets:

```bash
pytest tests/unit/test_cli_server_manager.py \
       tests/unit/test_cli_model_bootstrap.py \
       tests/unit/test_cli_preflight.py \
       tests/integration/test_cli_autopull_flow.py \
       tests/integration/test_cli_zero_config_transcribe.py \
       tests/integration/test_cli_remote_server_passthrough.py -q
```

```bash
pytest -q
```

### Manual verification

1. Fresh machine/profile simulation: run one transcribe command and confirm no manual pre-steps needed.
2. Repeat run: confirm bootstrap steps are skipped and command is faster.
3. Remote mode: confirm no local ghost server side effects when explicit remote URL is used.
4. Concurrent local invocations: confirm only one bootstrap path executes while others wait/reuse.
5. Interrupt during startup/pull: confirm cleanup leaves next run recoverable.
6. Ghost idle timeout + explicit stop: confirm process exits deterministically.

---

## Exit Criteria

1. One-command local transcription path works from fresh install for scoped lite mode.
2. Local ghost-server lifecycle is explicit and recoverable (start, crash diagnostics, stop, idle timeout).
3. Local bootstrap concurrency, timeout, and interrupt paths are tested and deterministic.
4. Remote/distributed workflows remain intact.
5. User-facing docs prioritize zero-config first-run flow.

# M57 Zero-Config CLI Bootstrap Implementation Report

Date: 2026-03-06

## Scope Delivered

Implemented a CLI bootstrap state machine for `dalston transcribe` with local-first behavior:

1. Preflight checks before side effects.
2. Local ghost server readiness management.
3. Model readiness auto-ensure.
4. Remote-server passthrough (no local mutation).
5. Explicit local lifecycle command: `dalston server stop`.

## Implemented Behavior

### Bootstrap state machine (`dalston transcribe`)

When target server is local (`localhost` / `127.0.0.1` / `::1`):

1. Run preflight checks (input files, writable run/log dirs, disk floor, `uvicorn` availability when bootstrap is enabled).
2. Ensure local server:
   - classify endpoint state (`ready`, `not_running`, `dalston_unhealthy`, `port_conflict`)
   - recover stale PID metadata
   - acquire bootstrap lock (`~/.dalston/run/bootstrap.lock`)
   - start detached `uvicorn dalston.gateway.main:app` in lite mode with auth disabled for local path
   - wait for `/health` readiness within timeout
3. Ensure model readiness:
   - resolve default local model (`distil-small`) when `--model auto`
   - `GET /v1/models/{id}` status probe
   - trigger `POST /v1/models/{id}/pull` when needed
   - poll until `ready` or fail with actionable error

When bootstrap is disabled (`DALSTON_BOOTSTRAP=false`):

1. Fail fast if local server is not healthy.
2. Fail fast if model status is not `ready`.
3. Provide explicit remediation text.

When target server is remote:

1. Skip local bootstrap state machine.
2. Keep request model behavior unchanged (passthrough).

### Ghost server lifecycle controls

- PID metadata: `~/.dalston/run/ghost-server.pid`
- Log file: `~/.dalston/logs/ghost-server.log`
- Stop command: `dalston server stop`
- Idle timeout field tracked in PID metadata and enforced on next bootstrap invocation (expired idle metadata causes restart).

## CLI Surface Added

- `dalston server status`
- `dalston server stop`

## Configuration Added/Used

- `DALSTON_BOOTSTRAP` (default `true`)
- `DALSTON_DEFAULT_MODEL` (default `distil-small`)
- `DALSTON_LOCAL_SERVER_URL` (default current CLI server URL or `http://127.0.0.1:8000`)
- `DALSTON_SERVER_START_TIMEOUT_SECONDS` (default `30`)
- `DALSTON_BOOTSTRAP_LOCK_TIMEOUT_SECONDS` (default `30`)
- `DALSTON_GHOST_IDLE_TIMEOUT_SECONDS` (default `900`)
- `DALSTON_MODEL_ENSURE_TIMEOUT_SECONDS` (default `900`)
- `DALSTON_BOOTSTRAP_MIN_FREE_BYTES` (default `268435456`)

## Tests Added

### Unit

- `tests/unit/test_cli_preflight.py`
- `tests/unit/test_cli_server_manager.py`
- `tests/unit/test_cli_model_bootstrap.py`

### Integration

- `tests/integration/test_cli_zero_config_transcribe.py`
- `tests/integration/test_cli_remote_server_passthrough.py`

## Validation Run

Executed:

```bash
.venv/bin/ruff check cli/dalston_cli/bootstrap cli/dalston_cli/commands/transcribe.py cli/dalston_cli/commands/server.py cli/dalston_cli/main.py tests/unit/test_cli_preflight.py tests/unit/test_cli_server_manager.py tests/unit/test_cli_model_bootstrap.py tests/integration/test_cli_zero_config_transcribe.py tests/integration/test_cli_remote_server_passthrough.py
```

```bash
.venv/bin/pytest -q tests/unit/test_cli_preflight.py tests/unit/test_cli_server_manager.py tests/unit/test_cli_model_bootstrap.py tests/integration/test_cli_zero_config_transcribe.py tests/integration/test_cli_remote_server_passthrough.py
```

# M56 Lite Mode Foundation

Implemented in this milestone:

- Explicit runtime mode contract via `DALSTON_MODE` (`distributed` default, `lite` optional).
- Mode-aware lazy DB initialization with SQLite bootstrap for lite.
- Queue abstraction with `RedisStreamsQueue` and `InMemoryQueue`.
- Storage abstraction with S3 and local filesystem adapters.
- Lite orchestrator entrypoint (`dalston/orchestrator/lite_main.py`) and mode dispatch in `dalston/orchestrator/main.py`.

Validated scope: lite prepare -> transcribe -> merge path via in-process pipeline.

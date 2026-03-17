# M79: Leaf Engine HTTP API

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Add HTTP endpoints (`/health`, `/v1/capabilities`, `/v1/transcribe`) to leaf engines so they are individually addressable — the foundation for composability, sidecar topology, and push-based dispatch |
| **Duration**       | 5–7 days                                                     |
| **Dependencies**   | M63 (Engine Unification), M51 (Engine Runtime Context)       |
| **Deliverable**    | Engine SDK HTTP server base class; three leaf engines with HTTP API; integration test suite that validates the interface contract against any engine |
| **Status**         | Not Started                                                  |

## User Story

> *"As a platform developer, I want each engine to be a standalone HTTP service with a well-defined API, so that the orchestrator, sidecars, and future composites can talk to engines via a standard protocol instead of in-process Python calls."*

---

## Motivation

Today, engines have no HTTP surface. They expose `health_check()`,
`get_capabilities()`, and `process()` as Python methods called by the
`EngineRunner` via Redis queue polling. There is no way to reach an engine
from outside its process — no health probe, no capability query, no direct
job submission.

This matters for three reasons:

1. **Sidecar topology (M72).** The inference server sidecar pattern requires
   engines to be addressable via network protocol. Without HTTP endpoints,
   a sidecar can't delegate work to an engine.

2. **Composability (ENGINE_COMPOSABILITY spec, Layer 1).** The spec defines
   that every engine must support `/health`, `/v1/capabilities`, and
   `/v1/transcribe` (or equivalent stage endpoint). Composites need to call
   children via HTTP. We can't build Layer 2 composites until leaf engines
   have a network interface.

3. **Push-based dispatch (M80).** M80 proposes the orchestrator placing work
   directly on engines via typed HTTP APIs. That requires engines to *have*
   HTTP APIs. M79 builds the foundation; M80 changes who calls it.

This milestone implements Layer 1 of the ENGINE_COMPOSABILITY rollout:
prove the interface contract on a minimum set of engines, then expand.

### What exists vs what's needed

| Concern | Today | After M79 |
|---------|-------|-----------|
| Health check | Python method, reported via Redis heartbeat | `GET /health` — HTTP probe, k8s/compose native |
| Capabilities | Python method, read at startup by runner | `GET /v1/capabilities` — runtime introspection over HTTP |
| Job submission | Redis Stream → runner polls → `process()` | `POST /v1/transcribe` — synchronous HTTP (Redis dispatch stays, HTTP is additive) |
| Addressability | None — engines are queue consumers | `http://engine-host:9100` — individually addressable |

**Redis dispatch is not removed.** The existing queue-based dispatch continues
to work. The HTTP API is additive — it gives each engine a network identity
that sidecars, composites, and (later) the push-based orchestrator can use.
The runner can optionally start the HTTP server alongside its queue poll loop.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                Engine Container                       │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │  EngineHTTPServer (engine SDK base class)        │ │
│  │                                                   │ │
│  │  GET  /health          → engine.health_check()   │ │
│  │  GET  /v1/capabilities → engine.get_capabilities()│ │
│  │  POST /v1/transcribe   → engine.process(...)     │ │
│  │                                                   │ │
│  │  Port: 9100 (configurable via DALSTON_HTTP_PORT) │ │
│  └──────────────────────┬──────────────────────────┘ │
│                          │                            │
│  ┌──────────────────────▼──────────────────────────┐ │
│  │  Engine instance (existing)                      │ │
│  │  health_check(), get_capabilities(), process()   │ │
│  └──────────────────────────────────────────────────┘ │
│                                                       │
│  ┌──────────────────────────────────────────────────┐ │
│  │  EngineRunner (existing, unchanged)              │ │
│  │  Redis Stream polling, heartbeats                │ │
│  └──────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

The HTTP server and the queue runner coexist in the same process. They share
the same `Engine` instance. The HTTP server is optional — engines that don't
need it (e.g., simple batch-only workers) can skip it. When enabled, the
runner starts the HTTP server in a background task.

---

## Steps

### 79.1: Engine SDK HTTP Server Base Class

**Files modified:**

- `dalston/engine_sdk/http_server.py` *(new)* — `EngineHTTPServer` using FastAPI
- `dalston/engine_sdk/runner.py` — optionally start HTTP server in runner lifespan

**Deliverables:**

A reusable HTTP server that wraps any `Engine` instance:

```python
# dalston/engine_sdk/http_server.py

class EngineHTTPServer:
    """Lightweight HTTP server exposing the engine interface contract.

    Wraps an Engine instance and serves:
    - GET  /health          → engine.health_check()
    - GET  /v1/capabilities → engine.get_capabilities()
    - POST /v1/transcribe   → synchronous transcription

    Started by the EngineRunner when DALSTON_HTTP_PORT is set.
    """

    def __init__(
        self,
        engine: Engine,
        port: int = 9100,
        host: str = "0.0.0.0",
    ):
        self._engine = engine
        self._port = port
        self._host = host
        self._app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI(
            title=f"Dalston Engine: {self._engine.engine_id}",
            docs_url=None,   # no Swagger in production
            redoc_url=None,
        )

        @app.get("/health")
        async def health():
            return await asyncio.to_thread(self._engine.health_check)

        @app.get("/v1/capabilities")
        async def capabilities():
            caps = self._engine.get_capabilities()
            return caps.model_dump() if hasattr(caps, "model_dump") else asdict(caps)

        # Stage-specific endpoints added per engine type (see 79.2)
        self._register_stage_endpoints(app)

        return app

    @abstractmethod
    def _register_stage_endpoints(self, app: FastAPI) -> None:
        """Subclasses register their stage-specific POST endpoints."""
        ...

    async def serve(self) -> None:
        """Run the HTTP server (called as asyncio task by runner)."""
        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        await server.serve()
```

Key decisions:

- **`asyncio.to_thread` for `health_check()`.** Engine health checks may
  touch GPU state (e.g., `torch.cuda.memory_allocated()`), which is blocking.
  The FastAPI server runs async, so we offload.

- **No auth on engine endpoints.** Engines run inside the compose/k8s
  network. Auth is at the gateway boundary, not between internal services.

- **Port 9100 by default.** Configurable via `DALSTON_HTTP_PORT`. This
  doesn't conflict with the gateway (8000), Prometheus (9090), or inference
  servers (50052+).

- **Optional startup.** The runner only starts the HTTP server if
  `DALSTON_HTTP_PORT` is set or `DALSTON_ENABLE_HTTP=true`. Existing
  deployments are unaffected.

**Runner integration:**

```python
# In EngineRunner.run() — add alongside existing Redis poll loop

if os.environ.get("DALSTON_HTTP_PORT") or os.environ.get("DALSTON_ENABLE_HTTP"):
    port = int(os.environ.get("DALSTON_HTTP_PORT", "9100"))
    http_server = self._engine.create_http_server(port=port)
    asyncio.create_task(http_server.serve())
```

Gate: HTTP server starts, `/health` and `/v1/capabilities` return correct
JSON for any engine.

---

### 79.2: Transcription Engine HTTP Endpoint

**Files modified:**

- `dalston/engine_sdk/http_transcribe.py` *(new)* — `TranscribeHTTPServer` subclass
- `dalston/engine_sdk/base_transcribe.py` — add `create_http_server()` method

**Deliverables:**

A transcription-specific HTTP server that adds `POST /v1/transcribe`:

```python
# dalston/engine_sdk/http_transcribe.py

class TranscribeHTTPServer(EngineHTTPServer):
    """HTTP server for transcription engines."""

    def _register_stage_endpoints(self, app: FastAPI) -> None:

        @app.post("/v1/transcribe")
        async def transcribe(request: TranscribeHTTPRequest):
            # Build TaskRequest from HTTP request
            task_request = self._to_task_request(request)
            ctx = BatchTaskContext.for_http(
                task_id=request.task_id or str(uuid4()),
                job_id=request.job_id or "http",
            )

            result = await asyncio.to_thread(
                self._engine.process, task_request, ctx
            )

            return self._to_http_response(result)
```

The `TranscribeHTTPRequest` model:

```python
class TranscribeHTTPRequest(BaseModel):
    """HTTP request for transcription.

    Accepts either an audio_uri (S3 path) for batch-style requests,
    or inline audio bytes via multipart form for direct submission.
    """
    task_id: str | None = None
    job_id: str | None = None
    audio_uri: str                       # S3 URI to prepared audio
    loaded_model_id: str | None = None   # Model to use
    language: str | None = None
    word_timestamps: bool = True
    vocabulary: list[str] | None = None
    channel: int | None = None
    timeout_seconds: int = 300
```

For this milestone, `audio_uri` is required — the engine fetches from S3
just like it does today via the queue path. File upload (multipart) is
out of scope; it's a convenience that can be added later.

Gate: `POST /v1/transcribe` with an S3 URI returns a `Transcript` JSON
response identical to what the queue-based path produces.

---

### 79.3: First Engine — `onnx-asr` (Parakeet)

**Files modified:**

- `engines/stt-unified/onnx-asr/batch_engine.py` — add `create_http_server()` returning `TranscribeHTTPServer`
- `engines/stt-unified/onnx-asr/engine.yaml` — add `interface` block
- `docker-compose.yml` — expose port 9100 on the onnx-asr service

**Deliverables:**

The Parakeet ONNX engine becomes the first leaf engine with HTTP API:

```yaml
# engine.yaml additions
interface:
  protocol: dalston-native
  health: /health
  capabilities: /v1/capabilities
  submit: /v1/transcribe
  port: 9100
```

```yaml
# docker-compose.yml — add to existing onnx-asr service
environment:
  DALSTON_HTTP_PORT: 9100
ports:
  - "9100:9100"   # Only for dev; production uses internal network
```

The engine's `create_http_server()` is trivial — it instantiates
`TranscribeHTTPServer` with `self`:

```python
def create_http_server(self, port: int = 9100) -> TranscribeHTTPServer:
    return TranscribeHTTPServer(engine=self, port=port)
```

Gate: Full integration test — start ONNX engine with HTTP enabled, call
`GET /health`, `GET /v1/capabilities`, `POST /v1/transcribe` with test
audio, verify response matches queue-based output.

---

### 79.4: Second Engine — `faster-whisper`

**Files modified:**

- `engines/stt-unified/faster-whisper/batch_engine.py` — add `create_http_server()`
- `engines/stt-unified/faster-whisper/engine.yaml` — add `interface` block
- `docker-compose.yml` — expose port 9101 on faster-whisper service

**Deliverables:**

Same pattern as 79.3. The faster-whisper engine gets the identical HTTP
surface. The only difference is the port (9101 to avoid conflicts when
both run on the same host).

Gate: Same integration test suite passes for faster-whisper. The test
cannot distinguish which engine produced the output — this validates
the interface contract.

---

### 79.5: Third Engine — `diarize-pyannote`

**Files modified:**

- `dalston/engine_sdk/http_diarize.py` *(new)* — `DiarizeHTTPServer` subclass
- `engines/stt-diarize/pyannote/engine.py` — add `create_http_server()`
- `engines/stt-diarize/pyannote/engine.yaml` — add `interface` block
- `docker-compose.yml` — expose port 9102

**Deliverables:**

First non-transcription engine. This validates that the HTTP server pattern
works for a fundamentally different stage type. The endpoint is
`POST /v1/diarize` instead of `/v1/transcribe`:

```python
class DiarizeHTTPServer(EngineHTTPServer):
    """HTTP server for diarization engines."""

    def _register_stage_endpoints(self, app: FastAPI) -> None:

        @app.post("/v1/diarize")
        async def diarize(request: DiarizeHTTPRequest):
            task_request = self._to_task_request(request)
            ctx = BatchTaskContext.for_http(
                task_id=request.task_id or str(uuid4()),
                job_id=request.job_id or "http",
            )
            result = await asyncio.to_thread(
                self._engine.process, task_request, ctx
            )
            return self._to_http_response(result)
```

```python
class DiarizeHTTPRequest(BaseModel):
    task_id: str | None = None
    job_id: str | None = None
    audio_uri: str
    loaded_model_id: str | None = None
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    timeout_seconds: int = 180
```

Gate: Diarization result returned via HTTP matches queue-based output.
The `/v1/capabilities` response correctly shows `stages: [diarisation]`.

---

### 79.6: Interface Contract Test Suite

**Files modified:**

- `tests/integration/test_engine_http_contract.py` *(new)*

**Deliverables:**

A parametrized test suite that validates the interface contract against any
engine with an HTTP endpoint. The same tests run against all three engines
from 79.3–79.5:

```python
@pytest.fixture(params=[
    ("onnx-asr", "http://localhost:9100"),
    ("faster-whisper", "http://localhost:9101"),
    ("diarize-pyannote", "http://localhost:9102"),
])
def engine_endpoint(request):
    return request.param


class TestEngineHTTPContract:
    """Validates the engine interface contract from ENGINE_COMPOSABILITY §3."""

    def test_health_returns_status(self, engine_endpoint):
        name, url = engine_endpoint
        resp = httpx.get(f"{url}/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] in ("healthy", "unhealthy")

    def test_capabilities_returns_stages(self, engine_endpoint):
        name, url = engine_endpoint
        resp = httpx.get(f"{url}/v1/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert "stages" in data
        assert isinstance(data["stages"], list)
        assert len(data["stages"]) > 0

    def test_capabilities_returns_engine_id(self, engine_endpoint):
        name, url = engine_endpoint
        resp = httpx.get(f"{url}/v1/capabilities")
        data = resp.json()
        assert "engine_id" in data
        assert data["engine_id"] == name

    def test_submit_returns_structured_result(self, engine_endpoint):
        """Submit test audio and verify the result format."""
        name, url = engine_endpoint
        caps = httpx.get(f"{url}/v1/capabilities").json()
        stages = caps["stages"]

        if "transcription" in stages or "transcribe" in stages:
            endpoint = "/v1/transcribe"
            request = {
                "audio_uri": "s3://dalston-artifacts/test/test-audio.wav",
                "language": "en",
            }
        elif "diarisation" in stages or "diarize" in stages:
            endpoint = "/v1/diarize"
            request = {
                "audio_uri": "s3://dalston-artifacts/test/test-audio.wav",
            }
        else:
            pytest.skip(f"No test for stages: {stages}")

        resp = httpx.post(f"{url}{endpoint}", json=request, timeout=60)
        assert resp.status_code == 200
        data = resp.json()
        # Verify the result has the engine_id field
        assert "engine_id" in data
```

This test suite becomes the executable specification for all future engines.
Any engine that passes these tests correctly implements the contract.

Gate: All three engines pass the full test suite. The test suite can be
pointed at any future engine with zero changes.

---

## Non-Goals

- **Removing Redis dispatch** — Queue-based dispatch stays. HTTP is additive. Replacing queues with push is M80.
- **File upload via multipart** — Engines accept `audio_uri` (S3 path). Direct file upload is a convenience for later.
- **Stage-keyed result envelope** — The HTTP response uses existing `Transcript` / `DiarizationResponse` types. The stage-keyed envelope from ENGINE_COMPOSABILITY §3.3 is a separate step (Layer 2).
- **Realtime/WebSocket endpoints** — Only batch-style synchronous HTTP. Realtime WebSocket on engines is a separate concern.
- **Push-based dispatch** — The orchestrator doesn't call these endpoints yet. That's M80.
- **Auth between services** — Engine HTTP is internal-network-only.
- **All engines** — Only three engines get HTTP in this milestone. Horizontal expansion to remaining engines follows after the contract is validated.

---

## Deployment

All changes are additive. Engines that don't set `DALSTON_HTTP_PORT` are
completely unaffected — the HTTP server simply doesn't start.

Recommended rollout:

1. Deploy 79.1 (SDK base class) — no behavior change, just new code
2. Deploy 79.2 (transcribe endpoint) — still no change unless env var set
3. Enable HTTP on one engine at a time (79.3 → 79.4 → 79.5)
4. Run contract tests (79.6) against each engine as it's enabled

---

## Verification

```bash
make dev

# 1. Verify health endpoint
curl -s http://localhost:9100/health | jq .

# 2. Verify capabilities
curl -s http://localhost:9100/v1/capabilities | jq '.stages'

# 3. Submit a transcription job via HTTP
curl -s -X POST http://localhost:9100/v1/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "audio_uri": "s3://dalston-artifacts/test/test-audio.wav",
    "language": "en",
    "word_timestamps": true
  }' | jq '.text'

# 4. Verify diarization engine
curl -s http://localhost:9102/v1/capabilities | jq '.stages'
curl -s -X POST http://localhost:9102/v1/diarize \
  -H "Content-Type: application/json" \
  -d '{
    "audio_uri": "s3://dalston-artifacts/test/test-audio.wav"
  }' | jq '.turns | length'

# 5. Run contract test suite
pytest tests/integration/test_engine_http_contract.py -v
```

---

## Checkpoint

- [ ] `EngineHTTPServer` base class in engine SDK
- [ ] `TranscribeHTTPServer` with `POST /v1/transcribe`
- [ ] `DiarizeHTTPServer` with `POST /v1/diarize`
- [ ] Runner optionally starts HTTP server when `DALSTON_HTTP_PORT` set
- [ ] `onnx-asr` engine serves `/health`, `/v1/capabilities`, `/v1/transcribe`
- [ ] `faster-whisper` engine serves the same endpoints
- [ ] `diarize-pyannote` engine serves `/health`, `/v1/capabilities`, `/v1/diarize`
- [ ] Responses match queue-based output (parity verified)
- [ ] `engine.yaml` updated with `interface` block for all three engines
- [ ] Contract test suite passes for all three engines
- [ ] Existing queue-based dispatch unaffected (`make test` passes)
- [ ] Docker compose services expose HTTP ports in dev profile

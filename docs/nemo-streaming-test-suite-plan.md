# NeMo Streaming Test Suite — Design Plan

## Problem

Debugging real-time streaming with NeMo models (RNNT, TDT, Nemotron) is painful:

- Building Docker images on every code change is slow
- Mac CPU inference is unusably slow for iteration
- No way to replay known audio with controlled timing
- No systematic way to test failure scenarios (network drops, slow GPU, buffer overflow)
- Existing unit tests mock everything — no actual model inference validation
- No tooling to compare streaming output against known-good transcripts

## Architecture Overview

Three tiers, each independently useful:

```
┌─────────────────────────────────────────────────────────────────┐
│  Tier 1: Nemo Test Container (local, source-mounted)            │
│  Fast iteration: edit .py → re-run. No rebuild needed.          │
│  CPU or GPU. Standalone — no Redis/Gateway/Orchestrator.         │
├─────────────────────────────────────────────────────────────────┤
│  Tier 2: Scenario Runner (audio replay + fault injection)       │
│  Replays known audio files with controlled timing.              │
│  Simulates network errors, dropped packets, slow inference.     │
│  Validates output against reference transcripts.                │
├─────────────────────────────────────────────────────────────────┤
│  Tier 3: AWS Test Harness (CPU + GPU instances)                 │
│  Same container, deployed to EC2 for real GPU testing.          │
│  Scripts to launch/teardown instances with model cache.         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Tier 1: Nemo Test Container

### Goal
Edit Python source → re-run instantly. No `docker build` cycle.

### Design

**Dockerfile** (`tests/nemo-streaming/Dockerfile`):
- Based on the existing `engines/stt-unified/nemo/Dockerfile` but optimized for development
- Installs all dependencies (NeMo, torch, etc.) in the image
- Does NOT copy source code — source is bind-mounted at runtime
- Includes extra dev tools: `ipython`, `pytest`, `rich` (for pretty logging)
- Pre-downloads one default model at build time (optional, can also use model cache volume)

**docker-compose.nemo-test.yml**:
```yaml
services:
  nemo-test:
    build:
      context: .
      dockerfile: tests/nemo-streaming/Dockerfile
      args:
        DEVICE: cpu  # or cuda
    volumes:
      # Source mount — edit locally, changes reflect instantly
      - ./dalston:/opt/dalston/dalston
      - ./engines:/opt/dalston/engines
      - ./tests/nemo-streaming:/opt/dalston/tests/nemo-streaming
      # Persistent model cache — survives container restarts
      - nemo-model-cache:/models
      # Test audio files
      - ./tests/audio:/opt/dalston/tests/audio:ro
    environment:
      DALSTON_DEVICE: cpu
      DALSTON_LOG_LEVEL: DEBUG
      DALSTON_LOG_FORMAT: console  # human-readable for debugging
      DALSTON_RNNT_CHUNK_MS: "160"
      HF_TOKEN: ${HF_TOKEN}
    ports:
      - "9000:9000"   # WebSocket for manual testing
      - "8888:8888"   # Jupyter (optional)
    # Interactive mode by default — drop into shell
    stdin_open: true
    tty: true

volumes:
  nemo-model-cache:
```

**GPU variant** via compose profile or override:
```yaml
# docker-compose.nemo-test.gpu.yml
services:
  nemo-test:
    build:
      args:
        DEVICE: cuda
    environment:
      DALSTON_DEVICE: cuda
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

**Makefile targets**:
```makefile
nemo-test:           # Start CPU test container with shell
nemo-test-gpu:       # Start GPU test container with shell
nemo-test-run:       # Run specific test script inside container
nemo-test-jupyter:   # Start Jupyter inside container
```

### Usage Patterns

```bash
# Start container, drop into shell
make nemo-test

# Inside container — run a quick streaming test
python tests/nemo-streaming/cli.py stream \
  --model parakeet-rnnt-0.6b \
  --audio tests/audio/test1_speaker1.wav \
  --chunk-ms 160

# Edit dalston/engine_sdk/inference/nemo_inference.py locally
# Re-run immediately — no rebuild
python tests/nemo-streaming/cli.py stream \
  --model nemotron-streaming-rnnt-0.6b \
  --audio tests/audio/test1_speaker1.wav

# Run the full scenario suite
python tests/nemo-streaming/cli.py scenarios --all
```

---

## Tier 2: Scenario Runner

### Goal
Systematically test every streaming scenario with controlled, reproducible inputs.

### Test Audio Corpus

**Reference files** (stored in `tests/nemo-streaming/audio/`):

| File | Duration | Content | Purpose |
|------|----------|---------|---------|
| `short_sentence.wav` | ~3s | Single clear sentence | Basic sanity |
| `long_monologue.wav` | ~60s | Extended speech | Buffer management, memory |
| `multi_pause.wav` | ~20s | Speech with 2-5s pauses | VAD endpoint detection |
| `noisy_speech.wav` | ~10s | Speech + background noise | Robustness |
| `silence_only.wav` | ~5s | Pure silence | No false positives |
| `rapid_speech.wav` | ~10s | Fast-paced speech | Throughput stress |
| `whisper_speech.wav` | ~5s | Very quiet speech | VAD sensitivity |

Each file has a companion `*.expected.json` with reference transcript:
```json
{
  "text": "the full expected transcript",
  "words": [
    {"word": "the", "start": 0.1, "end": 0.2},
    ...
  ],
  "tolerance": {
    "wer_max": 0.15,
    "timing_tolerance_ms": 500,
    "word_count_tolerance": 2
  }
}
```

### Scenario Definitions

Scenarios are YAML files in `tests/nemo-streaming/scenarios/`:

```yaml
# scenarios/basic_streaming.yaml
name: "Basic streaming — normal conditions"
audio: "short_sentence.wav"
model: "parakeet-rnnt-0.6b"
chunk_ms: 160
send_rate: "realtime"  # 1x speed
expect:
  wer_max: 0.15
  must_contain: ["expected", "key", "words"]
  max_latency_ms: 500
  min_words: 3
```

```yaml
# scenarios/slow_sender.yaml
name: "Slow client — 3x slower than realtime"
audio: "short_sentence.wav"
model: "nemotron-streaming-rnnt-0.6b"
chunk_ms: 160
send_rate: 0.33  # 3x slower
expect:
  completes: true
  no_timeout: true
```

```yaml
# scenarios/burst_then_silence.yaml
name: "Burst of audio then long silence"
audio: "short_sentence.wav"
model: "parakeet-rnnt-0.6b"
chunk_ms: 160
send_rate: "realtime"
post_audio_silence_ms: 5000
expect:
  final_transcript_received: true
  vad_speech_end_received: true
```

### Core Scenarios to Implement

#### A. Happy Path
1. **basic_streaming** — Normal audio at realtime pace, each model variant
2. **vad_segmentation** — Multi-pause audio, verify correct utterance boundaries
3. **long_form** — 60s audio, verify no memory growth or buffer overflow
4. **word_timestamps** — Verify timestamps are monotonically increasing and roughly correct

#### B. Timing Variations
5. **slow_sender** — Client sends chunks 3x slower than realtime
6. **fast_sender** — Client sends chunks as fast as possible (burst mode)
7. **irregular_chunks** — Variable chunk sizes (50ms, 200ms, 500ms mixed)
8. **tiny_chunks** — 20ms chunks (many chunks, high overhead)
9. **large_chunks** — 1000ms chunks (few chunks, high latency)

#### C. Audio Edge Cases
10. **silence_only** — No speech, verify no false transcripts
11. **leading_silence** — 3s silence then speech
12. **trailing_silence** — Speech then 3s silence, verify final emitted
13. **very_short_utterance** — 0.5s of speech, below min_speech threshold
14. **near_vad_threshold** — Quiet speech near VAD threshold boundary
15. **dc_offset** — Audio with DC bias (common from cheap mics)
16. **clipped_audio** — Audio with digital clipping

#### D. Network/Transport Failures
17. **dropped_chunks** — Skip every Nth chunk (simulates UDP packet loss)
18. **duplicate_chunks** — Same chunk sent twice (simulates retransmission)
19. **out_of_order** — Chunks arrive out of sequence
20. **connection_reset_mid_stream** — WebSocket closes during audio
21. **reconnect_resume** — Disconnect and reconnect, verify session recovery
22. **zero_length_chunks** — Empty audio buffers interspersed

#### E. Model Loading
23. **cold_start** — First inference after model load (measures full latency)
24. **warm_start** — Second inference (model already in memory)
25. **model_switch** — Switch models mid-session (if supported)
26. **model_not_found** — Request non-existent model, verify error handling

#### F. Backpressure / Lag
27. **simulated_slow_gpu** — Inject artificial delay into inference (verify lag warnings)
28. **lag_hard_limit** — Exceed lag_hard_seconds, verify session termination
29. **lag_recovery** — Approach warning threshold then recover
30. **concurrent_sessions** — Multiple simultaneous streams at max_sessions

#### G. Model-Specific
31. **rnnt_cache_continuity** — Verify encoder cache carries across chunks (Nemotron)
32. **rnnt_hypothesis_stability** — Check that partial hypotheses don't regress excessively
33. **tdt_offline_accumulate** — Verify TDT uses VAD-accumulate path correctly
34. **ctc_rejects_streaming** — Verify CTC model raises RuntimeError on streaming call
35. **rnnt_vs_tdt_quality** — Compare WER between RNNT and TDT on same audio

### CLI Tool

`tests/nemo-streaming/cli.py` — Single entry point for all test operations:

```
Usage: python cli.py <command> [options]

Commands:
  stream          Run a single streaming transcription
  batch           Run a single batch transcription (comparison baseline)
  scenario        Run a single named scenario
  scenarios       Run all scenarios (or filtered subset)
  compare         Compare streaming vs batch output for same audio
  benchmark       Measure latency/throughput metrics
  record          Record a new test audio file
  inspect-model   Load model and print architecture details
  wait-ready      Block until model is loaded (for scripting)

Options:
  --model, -m     Model ID (default: parakeet-rnnt-0.6b)
  --audio, -a     Path to audio file
  --chunk-ms      Chunk duration in ms (default: 160)
  --device        cpu or cuda (default: auto)
  --verbose, -v   Verbose logging
  --json          Output results as JSON
  --timeout       Max seconds to wait (default: 120)
```

### Implementation: Audio Replay Engine

```python
class AudioReplayEngine:
    """Replays audio files as streaming chunks with controlled timing."""

    def __init__(
        self,
        audio_path: str,
        chunk_ms: int = 160,
        send_rate: float = 1.0,     # 1.0 = realtime, 2.0 = 2x speed
        sample_rate: int = 16000,
    ):
        ...

    def chunks(self) -> Iterator[np.ndarray]:
        """Yield audio chunks at controlled rate."""
        ...

    def chunks_with_faults(
        self,
        drop_rate: float = 0.0,      # fraction of chunks to drop
        duplicate_rate: float = 0.0,  # fraction to send twice
        jitter_ms: float = 0.0,      # random timing jitter
        pause_after_chunk: int | None = None,  # pause N ms after chunk M
        pause_duration_ms: int = 0,
    ) -> Iterator[np.ndarray]:
        """Yield chunks with simulated faults."""
        ...
```

### Implementation: Result Validator

```python
class TranscriptValidator:
    """Validates streaming output against expected results."""

    def validate(
        self,
        actual_words: list[dict],
        expected: dict,  # from *.expected.json
    ) -> ValidationResult:
        """
        Checks:
        - WER within tolerance
        - Word count within tolerance
        - Timestamps monotonically increasing
        - Timing within tolerance of expected
        - Required keywords present
        - No false positives during silence
        """
        ...
```

### Implementation: Inference Wrapper with Fault Injection

```python
class FaultInjector:
    """Wraps inference to simulate slow/failing GPU."""

    def __init__(
        self,
        target: NemoInference,
        latency_ms: float = 0,          # add artificial latency per chunk
        latency_jitter_ms: float = 0,    # random latency variation
        fail_after_n_chunks: int | None = None,  # raise after N chunks
        oom_after_n_chunks: int | None = None,    # simulate CUDA OOM
    ):
        ...
```

---

## Tier 3: AWS Test Harness

### Goal
Run the same test suite on real GPU hardware without manual setup.

### Instance Types

| Instance | GPU | VRAM | Use Case | Cost/hr |
|----------|-----|------|----------|---------|
| `g4dn.xlarge` | T4 | 16GB | Basic GPU testing, nemotron-0.6b | ~$0.53 |
| `g5.xlarge` | A10G | 24GB | Larger models, multi-session testing | ~$1.01 |
| `c6i.2xlarge` | None | 0 | CPU-only testing, CI baseline | ~$0.34 |

### Scripts

`tests/nemo-streaming/aws/`:

```
aws/
├── launch.sh           # Launch EC2 instance with GPU + Docker
├── setup-instance.sh   # Install Docker, nvidia-container-toolkit
├── deploy.sh           # Build & push test container to instance
├── run-tests.sh        # SSH + run scenario suite
├── teardown.sh         # Terminate instance
├── run-full.sh         # launch → setup → deploy → test → teardown
└── instance-config.yaml
```

**launch.sh** workflow:
1. Launch EC2 instance (from AMI with NVIDIA drivers pre-installed, e.g. `Deep Learning Base OSS Nvidia Driver GPU AMI`)
2. Attach model cache EBS volume (persistent across runs, avoids re-downloading models)
3. Wait for instance ready
4. Run `setup-instance.sh` via SSH
5. Print connection details

**deploy.sh** workflow:
1. rsync project source to instance (not full `docker build` — same source-mount approach)
2. Build test container on instance (only first time; deps cached in image)
3. Attach model cache volume

**run-tests.sh** workflow:
1. SSH into instance
2. Start test container with GPU
3. Wait for model load (`cli.py wait-ready`)
4. Run scenario suite (`cli.py scenarios --all --json`)
5. Download results JSON

**Model Cache Volume**:
- Persistent EBS volume (50GB) with pre-downloaded models
- Survives instance termination
- Mount at `/models` inside container
- Saves ~10-15 min model download per run

### Cost Control
- Instances auto-terminate after 2 hours (safety net)
- `run-full.sh` includes teardown
- Spot instances for cost reduction (with retry)
- Model cache volume is only ~$5/month to keep

---

## Logging & Observability

### Structured Debug Logging

The test container runs with `DALSTON_LOG_FORMAT=console` for human-readable output. Every significant event is logged:

```
[DEBUG] chunk_received      chunk_idx=0 size=2560 elapsed_ms=0
[DEBUG] vad_state           state=SILENCE prob=0.02
[DEBUG] chunk_received      chunk_idx=1 size=2560 elapsed_ms=160
[DEBUG] vad_state           state=SPEECH_START prob=0.87
[DEBUG] streaming_step      chunk_idx=1 prev_text="" curr_text="hello"
[INFO]  word_emitted        word="hello" start=0.160 end=0.320
[DEBUG] chunk_received      chunk_idx=2 size=2560 elapsed_ms=320
[DEBUG] streaming_step      chunk_idx=2 prev_text="hello" curr_text="hello world"
[INFO]  word_emitted        word="world" start=0.320 end=0.480
```

### Metrics Collection

Each scenario run produces a metrics JSON:

```json
{
  "scenario": "basic_streaming",
  "model": "parakeet-rnnt-0.6b",
  "device": "cpu",
  "metrics": {
    "total_audio_duration_s": 3.2,
    "total_processing_time_s": 1.8,
    "rtf": 0.56,
    "first_word_latency_ms": 340,
    "avg_chunk_latency_ms": 45,
    "p95_chunk_latency_ms": 89,
    "max_chunk_latency_ms": 120,
    "peak_memory_mb": 1840,
    "gpu_memory_mb": 0,
    "word_count": 8,
    "wer": 0.0,
    "words_per_second": 4.4
  },
  "transcript": {
    "text": "hello world this is a test",
    "words": [...]
  },
  "validation": {
    "passed": true,
    "checks": {...}
  }
}
```

### Timeline Visualization

`cli.py` can output a timeline view for debugging timing issues:

```
Time (s)  0.0   0.5   1.0   1.5   2.0   2.5   3.0
Audio     ████████████████████████████░░░░░░░░░░░░
VAD       ░░████████████████████████░░░░░░░░░░░░░░
Words          hello  world  this   is    a   test
Chunks    |  |  |  |  |  |  |  |  |  |  |  |  |  |
Latency   ·  ·  ■  ·  ·  ■  ·  ·  ·  ■  ·  ·  ·  ·
```

---

## File Structure

```
tests/nemo-streaming/
├── Dockerfile              # Dev container (deps only, no source)
├── Dockerfile.gpu          # GPU variant
├── docker-compose.yml      # Local compose with source mounts
├── docker-compose.gpu.yml  # GPU override
├── cli.py                  # Main CLI entry point
├── README.md               # Usage guide
│
├── core/
│   ├── __init__.py
│   ├── audio_replay.py     # AudioReplayEngine
│   ├── fault_injector.py   # FaultInjector for inference
│   ├── validator.py        # TranscriptValidator
│   ├── metrics.py          # Metrics collection
│   ├── timeline.py         # Timeline visualization
│   └── runner.py           # ScenarioRunner orchestrator
│
├── scenarios/
│   ├── __init__.py
│   ├── happy_path.yaml
│   ├── timing_variations.yaml
│   ├── audio_edge_cases.yaml
│   ├── network_failures.yaml
│   ├── model_loading.yaml
│   ├── backpressure.yaml
│   └── model_specific.yaml
│
├── audio/                  # Test corpus (git LFS or S3)
│   ├── short_sentence.wav
│   ├── short_sentence.expected.json
│   ├── long_monologue.wav
│   ├── long_monologue.expected.json
│   └── ...
│
├── aws/
│   ├── launch.sh
│   ├── setup-instance.sh
│   ├── deploy.sh
│   ├── run-tests.sh
│   ├── teardown.sh
│   ├── run-full.sh
│   └── instance-config.yaml
│
└── results/                # .gitignored, test output
    └── ...
```

---

## Implementation Priority

### Phase 1: Foundation (immediate value)
1. **Test container with source mounts** — eliminates rebuild cycle
2. **CLI tool** with `stream` and `batch` commands — manual debugging
3. **AudioReplayEngine** — controlled chunk playback
4. **`wait-ready` command** — blocks until model loaded (prevents buffer overflow on cold start)

### Phase 2: Automated Scenarios
5. **ScenarioRunner** + YAML scenario format
6. **TranscriptValidator** with WER + timing checks
7. **Happy path scenarios** (basic_streaming, vad_segmentation, word_timestamps)
8. **Timing variation scenarios** (slow/fast sender, irregular chunks)

### Phase 3: Fault Injection
9. **FaultInjector** wrapper
10. **Network failure scenarios** (dropped chunks, duplicates, reconnect)
11. **Backpressure scenarios** (simulated slow GPU, lag limits)
12. **Audio edge case scenarios** (silence, clipping, DC offset)

### Phase 4: AWS Harness
13. **AWS launch/teardown scripts**
14. **Model cache EBS volume management**
15. **Automated full test run** (launch → test → report → teardown)
16. **GPU-specific scenarios** (cold start timing, multi-session, CUDA OOM)

### Phase 5: CI Integration (optional)
17. **GitHub Actions workflow** for GPU tests on merge to main
18. **Regression tracking** — compare metrics across commits
19. **Benchmark dashboard** — latency/WER trends over time

---

## Key Design Decisions

### Why source-mount instead of rebuild?
NeMo + PyTorch install takes 5-10 minutes. Source mount means zero rebuild time for Python changes. Only rebuild when `requirements.txt` changes.

### Why standalone (no Redis/Gateway)?
The test suite targets the inference layer directly — `NemoInference`, `NemoRealtimeEngine`, and `SessionHandler`. No need for the full Dalston stack. This keeps the test environment simple and the feedback loop fast.

### Why YAML scenarios?
Declarative scenarios are easy to read, add, and version control. The runner interprets them into test execution. Non-developers can contribute scenarios.

### Why separate Tier 3 (AWS)?
- GPU inference is 100x+ faster than CPU — essential for realistic latency testing
- Mac doesn't have NVIDIA GPUs
- EC2 spot instances are cheap for occasional testing
- Model cache volume avoids repeated 10-min downloads

### Why `wait-ready` before tests?
NeMo model loading takes 10-30s on GPU, longer on CPU. Without this, the first test chunks arrive before the model is loaded, causing either buffer overflow or a misleading cold-start measurement. The `wait-ready` command polls `NemoInference` until the preloaded model is ready, then signals tests to begin.

### Why not use the existing WebSocket protocol?
For Tier 1/2, we bypass WebSocket entirely and call `NemoInference` / `NemoRealtimeEngine` directly. This removes WebSocket protocol complexity from the debugging surface. We test the inference and VAD layers in isolation first, then add WebSocket integration as a separate scenario type.

For WebSocket-level testing (scenarios 20-21), we use a lightweight WebSocket client that speaks the Dalston protocol — but this is a later phase concern.

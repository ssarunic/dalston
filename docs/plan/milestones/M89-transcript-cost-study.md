# M89: Transcript Cost Study — The Economy of a Transcript

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Measure end-to-end $/transcript-hour for Dalston across engines, GPUs, and deployment topologies, and publish it as a case study. |
| **Duration**       | 5–8 days |
| **Dependencies**   | M76 (engine telemetry depth), M78 (infrastructure topology view), M80 (engine control plane) |
| **Deliverable**    | (a) Schema + metric extensions persisting hardware/cold-start context per task; (b) reproducible benchmark harness; (c) a 20–30 podcast corpus with measured results; (d) `docs/reports/transcript-cost-study.md` with break-even curves and topology comparisons. |
| **Status**         | Not Started |

## User Story

> *"As an operator deciding how to run Dalston, I want to know the true $/hour of transcription for each engine + GPU + topology choice — including cold-start cost and break-even duty cycle — so I can pick a configuration that matches my speed/price target and compare it honestly to ElevenLabs."*

---

## Motivation

We have rough RTF numbers in `docs/reports/engines-architecture-analysis.md` and spot/on-demand prices sprinkled through the AWS guides, but no end-to-end cost model. Concretely, we cannot answer today:

1. How much does the **first transcript** cost after a cold boot (spot vs on-demand, g4dn vs g5 vs g6)? Where is the break-even against a hosted API?
2. How much does **Pyannote diarization** (RTF ≈ 0.15 GPU, ≈1.2 CPU — roughly an order of magnitude slower than Parakeet/ONNX) dominate wall-time and cost, and how should we lay out the DAG to optimise for speed vs price?
3. How does a self-hosted Parakeet+Pyannote stack compare to **ElevenLabs** on $/hour of finished transcript?
4. How does the picture change when we add the **Control Plane** (M80) and, later, **hosted Postgres + Redis**?
5. How does **faster-whisper** (what most people use today) compare, with and without word-level alignment?

### What we have (good enough to start)

From the telemetry survey:

- `TaskModel` stores `ready_at`, `started_at`, `completed_at`, `engine_id`, `stage` (`dalston/db/models.py:241-287`) — we can compute **wait / task / wall** per task.
- `JobModel` stores `audio_duration` — we can compute **RTF** for any job.
- `NodeIdentity` (`dalston/common/node_identity.py:33-41`) already discovers `instance_type`, `region`, `deploy_env`, `hostname` via IMDSv2.
- Prometheus histograms exist for `engine_model_load_seconds`, `engine_queue_wait_seconds`, `engine_task_duration_seconds`, `engine_recognize_seconds`, `engine_realtime_factor_ratio` (`dalston/metrics.py`).
- Durable event stream `dalston:events:stream` replays lifecycle — but has a 24h TTL, so **historical** jobs from offline instances cannot be recovered this way.

### What's missing (why a milestone, not just a script)

- `TaskModel` does **not** persist which instance type / GPU / spot-flag the task ran on. `engine_id` alone is ambiguous (same engine id, different hardware → different cost).
- `engine_model_load_seconds` captures *model* load, not **container cold start** (pull + venv + CUDA init + first-token). We conflate them today.
- No “**first task after boot**” marker, so cold-start amortisation curves cannot be drawn from existing data.
- No `audio_prepare` cost column — FFmpeg time and egress can matter for short files.
- No CLI for submitting a benchmark corpus and emitting a results CSV; only `tests/benchmarks/test_mixed_load.py` which measures QoS, not $.

We therefore need a small schema + metric delta, a one-shot CLI, and a corpus. Everything else (ElevenLabs, control-plane projections) is pure analysis on top.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Transcript Cost Study Harness                         │
│                                                                          │
│  podcast corpus (S3)                                                     │
│        │                                                                 │
│        ▼                                                                 │
│   dalston bench run ──▶ Gateway ──▶ Orchestrator ──▶ Engines (GPU/CPU)  │
│        │                                    │                            │
│        │                                    ▼                            │
│        │                              TaskModel (+ hw_ctx JSON)          │
│        │                              JobModel  (+ boot_ctx JSON)        │
│        │                              Prom metrics (+ instance labels)   │
│        ▼                                                                 │
│   results.csv  ◀── dalston bench export (joins DB + EC2 pricing table)   │
│        │                                                                 │
│        ▼                                                                 │
│   docs/reports/transcript-cost-study.md                                  │
│   (break-even curves, topology comparison, ElevenLabs benchmark)         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 89.1: Persist hardware + cold-start context on every task

**Files modified:**

- `dalston/db/models.py` — add nullable columns to `TaskModel`:
  - `instance_type: str | None` (e.g. `g5.xlarge`)
  - `gpu_model: str | None` (e.g. `NVIDIA A10G`)
  - `region: str | None`
  - `spot: bool | None`
  - `node_id: str | None` (EC2 instance id or hostname)
  - `cold_start: bool` (first task this worker ran after boot)
  - `boot_latency_s: float | None` (process start → first task picked up; worker-local)
  - `container_image_digest: str | None`
- `dalston/db/migrations/versions/` — append-only migration adding the columns.
- `dalston/engine_sdk/runner.py` — at task completion, set the fields from the worker’s cached `NodeIdentity` + first-task flag.
- `dalston/metrics.py` — add `instance_type`, `gpu_model`, `spot` labels to the engine histograms. Keep cardinality bounded (≤ 10 types × 3 profiles).

**Deliverables:** one task row = one fully-costable data point.

---

### 89.2: Instrument container cold start separately from model load

**Files modified:**

- `dalston/engine_sdk/runner.py` — record three timestamps: `process_start`, `model_loaded`, `first_task_started`.
- `dalston/metrics.py` — two new histograms:
  - `dalston_engine_container_boot_seconds` (process_start → first heartbeat to orchestrator)
  - `dalston_engine_first_task_latency_seconds` (process_start → first task `started_at`)
- Emit a one-shot `engine.boot` span with attributes `image_digest`, `instance_type`, `model_id`, `cache_hit`.

**Deliverables:** ability to decompose the cold-boot cost into image pull, CUDA init, model load, and queue idle.

---

### 89.3: `dalston bench` CLI

**Files modified:**

- `cli/dalston_cli/commands/bench.py` *(new)* — three subcommands:

```
dalston bench corpus sync      # sync 20–30 podcast clips from s3://dalston-bench-corpus
dalston bench run --profile <engine-matrix>.yaml --machine <g5.xlarge|...>
dalston bench export --since <ts> --out results.csv
```

- `cli/dalston_cli/bench/corpus.yaml` *(new)* — manifest of 20–30 podcast audio files covering: mono/stereo, 16/44.1 kHz, 1/2/3+ speakers, lengths 2 / 15 / 60 / 120 min, clean/noisy.
- `cli/dalston_cli/bench/profiles/` *(new)* — YAML matrices for the runs below (one profile per experiment).
- `dalston/tools/pricing.py` *(new)* — static table of AWS EC2 on-demand and spot prices for `g4dn.{xlarge,2xlarge}`, `g5.{xlarge,2xlarge,12xlarge}`, `g6.{xlarge,2xlarge}`, `g6e.xlarge`, plus ElevenLabs list price. Keyed by `(instance_type, region, spot)`.

`bench export` joins `TaskModel`/`JobModel` with `pricing.py` and produces, per task: `engine_id, instance_type, spot, audio_s, wait_s, task_s, wall_s, rtf, cold_start, $_compute, $_per_audio_hour`.

**Deliverables:** one command → one CSV per experiment → one chart.

---

### 89.4: Experiment matrix (what we actually run)

All runs use the same 20–30 clip corpus. Each row is a profile YAML in 89.3.

| # | Experiment                              | Engines                                                   | Machines                                                   | What we measure                          |
|---|-----------------------------------------|-----------------------------------------------------------|------------------------------------------------------------|------------------------------------------|
| A | **Cold boot cost**                      | nothing (just boot + pull + model load)                   | g4dn.xl, g5.xl, g5.2xl, g6.xl, g6e.xl — spot **and** on-demand | boot_s, first_task_latency_s, $cost     |
| B | **Single-engine RTF sweep**             | parakeet-nemo-rnnt-0.6b, parakeet-onnx-tdt-0.6b-v3, parakeet-onnx-ctc-1.1b, faster-whisper-large-v3 | g4dn.xl, g5.xl, g6.xl                                    | rtf, $/audio-hour                        |
| C | **Diarization RTF sweep**               | pyannote-4.0, nemo-msdd, nemo-sortformer                  | g4dn.xl, g5.xl, g6.xl, (g5.xl CPU fallback)                | rtf, $/audio-hour                        |
| D | **Pipeline topologies** (speed vs price) | parakeet-onnx + pyannote in: (i) parallel on one GPU, (ii) parallel on two GPUs, (iii) sequential on one GPU, (iv) whisper-align-pyannote combo | g5.xl ×1 and ×2                                           | wall_s, $/transcript                     |
| E | **faster-whisper comparison**           | faster-whisper-large-v3 with/without `phoneme-align`      | g5.xl                                                      | rtf, accuracy (WER spot-check), $        |
| F | **ElevenLabs baseline**                 | ElevenLabs STT via `/v1/speech-to-text` (hosted)          | —                                                          | latency, $ from their pricing page       |

Break-even analysis is derived, not measured: for each `(engine, instance, spot)` combo,

```
$_cold = boot_cost + first_task_latency * hourly_rate
$_warm_hour = hourly_rate * rtf
break_even_hours = $_cold / ($_elevenlabs_hour - $_warm_hour)
```

### 89.5: Historical backfill (optional — only for instances we spin back up)

**Files modified:**

- `cli/dalston_cli/commands/bench.py` — add `dalston bench backfill --db-url ...` which:
  1. Reads `TaskModel` rows where `instance_type IS NULL`.
  2. Joins to any on-disk audit logs / NodeIdentity cache on the worker FS (if the instance is live).
  3. Falls back to cross-referencing the task `created_at` against the EC2 console’s instance-run history (manual CSV the operator pastes in).

**Honest scope:** for jobs older than 24h the durable Redis stream is gone, and we never wrote `instance_type` to the DB. We will get `engine_id`, the three timestamps, and `audio_duration`. We will **not** get GPU/spot/boot context unless the operator hand-supplies a `(node_id → instance_type, spot?)` map. Mark backfilled rows `is_backfilled=true` and exclude them from cold-start analysis.

---

### 89.6: Control-plane & hosted-infra projection

Pure analysis on top of 89.4 data, no new code.

- **+ Control Plane (M80):** add one `t3.small` on-demand for the control plane, constant regardless of audio volume. Output: break-even audio-hours-per-day for a self-hosted CP to be cheaper than paying a provider.
- **+ Hosted Postgres (RDS `db.t4g.small`) and Redis (ElastiCache `cache.t4g.micro`):** add their $/month to the fixed cost. Re-plot total $/transcript-hour at 1, 10, 100, 1000 audio-hours/day.
- Publish as a table in the final report.

---

### 89.7: Write the case study

**Files modified:**

- `docs/reports/transcript-cost-study.md` *(new)* — the deliverable. Sections:
  1. Methodology (corpus, pricing table, what we measure/don’t).
  2. Cold boot cost per instance × spot/on-demand (Experiment A).
  3. RTF and $/audio-hour per engine × machine (Experiments B, C).
  4. Pipeline topology trade-offs for Parakeet + Pyannote (Experiment D).
  5. Speed-optimal vs price-optimal configuration (recommendation).
  6. Faster-whisper comparison with/without word alignment (Experiment E).
  7. ElevenLabs comparison (Experiment F) + break-even curves.
  8. Control plane and hosted Postgres/Redis overlay (89.6).
  9. Limitations and reproducibility notes.

---

## Non-Goals

- **WER / accuracy study** — we spot-check, but this milestone is about $ and wall time. A separate doc should compare quality.
- **Real-time / streaming cost** — batch only. Streaming has a different economic shape (long-lived sessions, GPU slicing) and deserves its own milestone.
- **Egress, S3, and audit storage costs** — include a flat estimate in the final table, don’t instrument them. They are a rounding error relative to GPU-hour.
- **Multi-region / cross-AZ networking cost** — single-region only.
- **Auto-scaling policies** — we measure the components; the operator chooses the policy.

---

## Deployment

No production deploy. Migration in 89.1 is additive and backwards compatible. The `bench` CLI is operator-only.

---

## Verification

```bash
make dev

# 89.1 verify: a completed task has hardware context
psql "$DATABASE_URL" -c "select engine_id, instance_type, gpu_model, spot, cold_start, boot_latency_s from tasks order by completed_at desc limit 5;"

# 89.2 verify: boot metrics present
curl -s http://localhost:9464/metrics | grep -E "dalston_engine_(container_boot|first_task_latency)_seconds"

# 89.3 verify: bench CLI works end-to-end on a 3-clip smoke corpus
dalston bench corpus sync --profile smoke
dalston bench run --profile smoke --machine local
dalston bench export --since -1h --out /tmp/results.csv
test $(wc -l < /tmp/results.csv) -ge 4   # header + >=3 rows
```

---

## Checkpoint

- [ ] 89.1 TaskModel has `instance_type`, `gpu_model`, `region`, `spot`, `node_id`, `cold_start`, `boot_latency_s` populated on new tasks.
- [ ] 89.2 `dalston_engine_container_boot_seconds` and `dalston_engine_first_task_latency_seconds` exported.
- [ ] 89.3 `dalston bench {corpus,run,export}` commands ship with a 20–30 clip corpus manifest and a pricing table.
- [ ] 89.4 Experiments A–F executed and raw CSVs checked into `docs/reports/data/transcript-cost-study/`.
- [ ] 89.6 Control-plane and hosted-infra overlay computed.
- [ ] 89.7 `docs/reports/transcript-cost-study.md` published with break-even curves and topology recommendation.

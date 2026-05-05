# GPU Topology & Cost Benchmarks

Living document. Each section is additive — when new engines, GPU types, or
topologies are tested, append a row to the relevant table rather than rewriting.

Last updated: **2026-05-05**
Region: **eu-west-2 (London)** unless otherwise noted.

---

## 1. Purpose

Quantify the cost-per-audio-hour and end-to-end latency of running Dalston's
transcribe + diarize pipeline across:

- Different GPU instance families (g4dn / g6 / g6e / g5 / etc.).
- Different engine combinations (parakeet, faster-whisper, ONNX, vLLM, hf-asr,
  pyannote-3 vs 4, etc.).
- Different deployment topologies (single co-located box, split transcribe +
  diarize fleets, mixed on-demand + spot).
- Different account quotas (the binding constraint for most users).

The goal is to give an operator a defensible answer to "for my workload, my
quota, and my latency target, what should I deploy?"

---

## 2. Methodology

### 2.1 Source of truth

Measurements come from two places:

1. **Postgres** on the control plane (`jobs` + `tasks` tables) — gives audio
   duration, per-stage `started_at` / `completed_at`, and end-to-end wall time.
2. **Engine container logs** (structured JSON) — gives `processing_time`
   per task, traceable across stages by `trace_id`.
3. **GPU watcher** (`/tmp/dalston-gpu-watch.sh` on the box, run as a systemd
   transient service) — 1 Hz `nvidia-smi` polling with per-container
   attribution by mapping cgroup→docker container name. Used during a session
   to capture peak VRAM and util.

### 2.2 Standard queries

Aggregate per-stage RTF for a session:

```sql
WITH bucket AS (
  SELECT
    j.id, j.audio_duration AS audio_s,
    EXTRACT(EPOCH FROM (j.completed_at - j.started_at)) AS wall_s
  FROM jobs j
  WHERE j.status = 'completed'
    AND j.audio_duration IS NOT NULL
    AND j.started_at >= '<session-start>'
)
SELECT
  COUNT(*) AS n_jobs,
  ROUND(SUM(b.audio_s)::numeric / 3600, 2) AS audio_h,
  ROUND((SUM(CASE WHEN t.stage = 'transcribe' THEN
       EXTRACT(EPOCH FROM (t.completed_at - t.started_at)) ELSE 0 END)
     / (SUM(b.audio_s) / 3600))::numeric, 1) AS xcribe_per_h,
  ROUND((SUM(CASE WHEN t.stage = 'diarize' THEN
       EXTRACT(EPOCH FROM (t.completed_at - t.started_at)) ELSE 0 END)
     / (SUM(b.audio_s) / 3600))::numeric, 1) AS diar_per_h,
  ROUND((SUM(b.wall_s) / (SUM(b.audio_s) / 3600))::numeric, 1) AS wall_per_h
FROM bucket b
LEFT JOIN tasks t ON t.job_id = b.id
                 AND t.completed_at IS NOT NULL
                 AND t.started_at IS NOT NULL;
```

Per-stage RTF is `(stage_seconds / audio_seconds)`. RTF = 0.06 means 1 hour
of audio takes 216 seconds of GPU compute (≈17× faster than real-time).

### 2.3 GPU memory monitoring

Watcher script that captures total + per-container GPU memory, mapping
process PIDs to container names via cgroup:

```bash
#!/bin/bash
declare -A NAME_BY_HASH
last_refresh=0
while true; do
  ts=$(date +%s)
  if (( ts - last_refresh > 30 )); then
    NAME_BY_HASH=()
    while IFS=" " read -r id name; do
      NAME_BY_HASH[$id]=$name
    done < <(docker ps --no-trunc --format "{{.ID}} {{.Names}}")
    last_refresh=$ts
  fi
  IFS=, read mem util <<<$(nvidia-smi \
    --query-gpu=memory.used,utilization.gpu \
    --format=csv,noheader,nounits | tr -d " ")
  apps=$(nvidia-smi --query-compute-apps=pid,used_memory \
    --format=csv,noheader,nounits 2>/dev/null)
  # Loop apps, parse cgroup, attribute to container ...
  echo "$ts $mem $util ..."
  sleep 1
done
```

Run via `systemd-run --unit=dalston-gpu-watch --collect /bin/bash <script>`
so it survives the SSH session.

### 2.4 Throughput model

For a topology with N transcribe-capable boxes and M diarize-capable boxes
(boxes can appear in both pools when co-located):

- Transcribe pool capacity = Σᵢ (1 / transcribe_RTFᵢ) audio-hours per wall-hour
- Diarize pool capacity = Σᵢ (1 / diarize_RTFᵢ) audio-hours per wall-hour
- Co-located box contribution = 1 / (transcribe_RTF + diarize_RTF)
- System throughput = min(transcribe_pool, diarize_pool)

Empirical throughput is ~60% of theoretical due to queue dwell, S3
round-trips, and stage-handoff overhead. Apply this factor when projecting
cost-per-audio-hour for a new topology.

---

## 3. Engine RTF table

Compute time per audio-hour, single-engine SOLO measurements (the other
container stopped to avoid co-location pollution of `pynvml.memory.used`).

Append a row when a new engine + GPU pair is benchmarked. Cite the
calibration profile JSON if one exists in `dalston/tools/vram_profiles/`.

| Engine | Model | GPU | Stage | RTF (s of compute / s of audio) | Speedup vs realtime | VRAM peak (solo) | Calibration profile | Date | Notes |
|---|---|---|---|---:|---:|---:|---|---|---|
| nemo | nvidia/parakeet-tdt-0.6b-v3 | L4 (g6.xlarge) | transcribe | 0.0061 | 165× | ~7.4 GB working set; 5.2 GB resident | `transcribe-nemo-L4.json` | 2026-05-05 | `vad_batch_size` form param ignored by engine — calibrator's batch sweep was inert. |
| pyannote-4.0 | pyannote/speaker-diarization-community-1 | L4 (g6.xlarge) | diarize | 0.0185 | 54× | ~1.16 GB peak (flat across 60–900 s) | `diarize-pyannote-4.0-L4.json` | 2026-05-05 | Duration-independent peak. |
| pyannote-4.0 | pyannote/speaker-diarization-community-1 | T4 (g4dn.xlarge) | diarize | ~0.0241 (population) | 41× | 1.4 GB peak | `diarize-pyannote-4.0-T4.json` | 2026-04-01 | Solo calibration. |
| nemo | nvidia/parakeet-tdt-0.6b-v3 | T4 (g4dn.xlarge) | transcribe | ~0.0094 (population) | 106× | TBD | none | 2026-04 | Inferred from prior 302 jobs population stats; not solo-calibrated. |
| prepare | (audio convert) | CPU | prepare | ~0.0008 | — | — | n/a | — | Stage runs on CPU; audio length-driven. |

### 3.1 RTF by audio length (g6.xlarge co-loc, 2026-05-05)

| Audio bucket | n | xcribe RTF | diarize RTF | Compute RTF | Wall RTF |
|---|---:|---:|---:|---:|---:|
| 5–15 min | 1 | 0.017 | 0.041 | 0.062 | 0.50 (queue-bound) |
| 15–30 min | 5 | 0.018 | 0.046 | 0.067 | 0.17 |
| 30–60 min | 8 | 0.019 | 0.054 | 0.075 | 0.15 |
| 60–120 min | 4 | 0.018 | 0.061 | 0.082 | 0.12 |

Diarize RTF grows mildly with audio length (clustering scales worse than
streaming transcribe).

### 3.2 RTF by audio length (g4dn.xlarge split, prior 302 jobs)

| Audio bucket | n | xcribe RTF | diarize RTF | Compute RTF | Wall RTF |
|---|---:|---:|---:|---:|---:|
| 0–5 min | 9 | 0.27 | 0.001 | 0.62 | 0.64 (likely transcribe-only configs in this bucket) |
| 5–15 min | 4 | 0.070 | 0.057 | 0.13 | 0.40 |
| 15–30 min | 31 | 0.040 | 0.061 | 0.099 | 0.26 |
| 30–60 min | 119 | 0.030 | 0.070 | 0.10 | 0.15 |
| 60–120 min | 129 | 0.027 | 0.074 | 0.10 | 0.13 |
| >120 min | 10 | 0.022 | 0.078 | 0.10 | 0.10 |

---

## 4. Pricing snapshot

Pull command:

```bash
# On-demand (eu-west-2)
aws pricing get-products --region us-east-1 \
  --service-code AmazonEC2 \
  --filters \
    "Type=TERM_MATCH,Field=instanceType,Value=<INSTANCE>" \
    "Type=TERM_MATCH,Field=regionCode,Value=<REGION>" \
    "Type=TERM_MATCH,Field=tenancy,Value=Shared" \
    "Type=TERM_MATCH,Field=operatingSystem,Value=Linux" \
    "Type=TERM_MATCH,Field=preInstalledSw,Value=NA" \
    "Type=TERM_MATCH,Field=capacitystatus,Value=Used" \
  --query 'PriceList[0]' --output text | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
for k,v in data['terms']['OnDemand'].items():
  for p,q in v['priceDimensions'].items():
    print(q['pricePerUnit']['USD']); break"

# Spot (7-day history per AZ)
aws ec2 describe-spot-price-history --region <REGION> \
  --instance-types <INSTANCE> \
  --product-descriptions "Linux/UNIX" \
  --start-time $(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ)
```

### 4.1 eu-west-2 — 2026-05-05

| Instance | vCPU | GPU | VRAM | On-demand | Spot — cheapest AZ | Spot — average |
|---|---:|---|---:|---:|---:|---:|
| g4dn.xlarge | 4 | T4 | 16 GB | $0.615/h | $0.181/h (eu-west-2a) | ~$0.195/h |
| g6.xlarge | 4 | L4 | 24 GB | $1.022/h | $0.299/h (eu-west-2b) | ~$0.318/h |

g6 spot has been quoted in all 3 eu-west-2 AZs every hour for the prior 7
days, but request fulfillment can still fail under capacity pressure.
g4dn spot pricing is stable.

To track: g6e.xlarge (L40S, 48 GB), g5.xlarge (A10G, 24 GB), p4d/p5
families, ARM-based g5g.

---

## 5. Topology cost-throughput catalog

Cost-per-audio-hour at theoretical full utilization, then with the
empirical 60% efficiency factor applied.

Append a row when a new topology is tested. Use the same pricing snapshot
date as section 4.

### 5.1 Single-stage capacities (audio-h per wall-h)

|  | Transcribe-only | Diarize-only | Co-located |
|---|---:|---:|---:|
| L4 (g6.xlarge) | 163.6 | 54.1 | 12.8 |
| T4 (g4dn.xlarge) | 106.2 | 41.5 | 9.9 |

### 5.2 Topology cost catalog (eu-west-2, prices from 4.1)

| Option | Topology | OD vCPU | Spot vCPU | $/h | Theoretical thrpt | $/audio-hour (theory) | $/audio-hour (empirical) |
|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | 1× g6 OD co-loc | 4 | 0 | $1.022 | 12.8 | $0.080 | $0.133 |
| 2 | 1× g4dn OD co-loc | 4 | 0 | $0.615 | 9.9 | $0.062 | $0.103 |
| 3 | 3× g4dn spot co-loc | 0 | 12 | $0.543 | 29.7 | $0.018 | $0.030 |
| 4 | 3× g4dn spot split (1 T + 2 D) | 0 | 12 | $0.543 | ~26.5 (D-bound) | $0.020 | $0.034 |
| 5 | 1× g6 OD T-only + 3× g4dn spot D-only | 4 | 12 | $1.565 | 124.5 (D-bound) | $0.013 | $0.021 |
| 6 | 1× g6 OD co-loc + 3× g4dn spot co-loc | 4 | 12 | $1.565 | 42.5 | $0.037 | $0.061 |
| **7** | **1× g6 OD co-loc + 3× g4dn spot D-only** | **4** | **12** | **$1.565** | **~158 (T-bound by g6)** | **$0.010** | **$0.017** |
| 8 | 1× g4dn OD T-only + 3× g4dn spot D-only | 4 | 12 | $1.158 | 106 (T-bound) | $0.011 | $0.018 |

Throughput notes:

- "T-bound" / "D-bound" indicates which stage is the bottleneck.
- Co-located boxes contribute to *both* pools; transcribe-only or
  diarize-only boxes contribute only to one.
- Empirical column = theoretical × ~0.6, derived from the 2026-05-05
  session where measured wall RTF was ~62% of the sum-of-stages compute RTF.

### 5.3 Recommendations by quota

| Quota | Best $/audio-h topology |
|---|---|
| 4 vCPU OD only | Option 2 (1× g4dn OD co-loc) — $0.10/h, 9.9 thrpt |
| 12 vCPU spot only | Option 3 (3× g4dn spot co-loc) — $0.030/h, 29.7 thrpt |
| 4 OD + 12 spot (typical post-quota-bump) | **Option 7** — $0.017/h, 158 thrpt |
| Larger spot quota & g6 spot reliable | Multiple g6 spot co-loc — needs benchmarking |

---

## 6. Sessions

Append a section for each benchmark run. Each entry = one stable topology
exercised over a representative workload.

### Session 2026-05-05 — g6.xlarge co-located (1× on-demand)

- **Window**: 18:30–19:30 UTC (active workload), idle thereafter.
- **Topology**: single g6.xlarge with `--engines nemo,pyannote`, co-located
  on a single L4. Profiles `transcribe-nemo-L4.json` +
  `diarize-pyannote-4.0-L4.json` calibrated SOLO during this session.
- **VRAM budget overrides**:
  `DALSTON_OVERRIDE__nemo__VRAM_BUDGET_MB=22000`,
  `DALSTON_OVERRIDE__pyannote__VRAM_BUDGET_MB=2000`,
  `DALSTON_OVERRIDE__nemo__BATCH_MAX_INFLIGHT=2`,
  `DALSTON_OVERRIDE__nemo__NEMO_MAX_CHUNK_S=600`.
- **Jobs**: 17 completed, 36.30 hours of audio, avg 42.7 min per job.
- **Per audio-hour**: transcribe 22.0 s, diarize 66.5 s, prepare 2.9 s;
  total compute 91.4 s, wall 545.7 s.
- **Wall RTF**: 0.152 (~6.6× faster than real-time, end-to-end).
- **VRAM observed**: NeMo 7.4 GB peak / 22 GB budget (34%);
  pyannote 1.93 GB peak / 2 GB budget (96% — flag for OOM if longer audio).
- **Findings**:
  - Diarize is the long pole; queue dwell dominated wall time on bursts
    (some jobs spent 8 of 9 minutes waiting).
  - Single-job execution achieves wall RTF 0.048 (21× real-time) — the
    floor of what this topology can do without concurrency wins.
  - Pyannote 96% budget peak comes from allocator caching activations
    across calls, not the model itself (~1.16 GB solo). Bumping the
    override to 4000 MB removes the OOM risk for long audio.

### Session 2026-04-30 + 2026-04-28 — g4dn.xlarge split (3× spot)

- **Topology**: 1× g4dn (transcribe, NeMo) + 2× g4dn (diarize, pyannote)
  on Tesla T4 spot instances.
- **Jobs**: 102 completed, 105.0 hours of audio.
- **Per audio-hour**: transcribe 26.8 s, diarize 93.5 s, prepare 3.5 s;
  total compute 123.8 s, wall 392.3 s.
- **Wall RTF**: 0.109 (~9.2× real-time end-to-end). Beats the single g6
  on wall time despite slower compute, due to 2× diarize parallelism.

### Session population — all g4dn.xlarge split (Apr 1 → Apr 30, 2026)

- **Jobs**: 302 completed, 895.3 hours of audio.
- **Per audio-hour**: transcribe 33.9 s, diarize 86.8 s; compute 123.6 s,
  wall 490.8 s.
- **Wall RTF**: 0.136.

---

## 7. Comparison: g6 co-loc (today) vs g4dn split (prior population)

| Metric | g6 co-loc | g4dn split | Δ |
|---|---:|---:|---:|
| Transcribe per audio-hour | 22.0 s | 33.9 s | **−35%** |
| Diarize per audio-hour | 66.5 s | 86.8 s | **−23%** |
| Compute per audio-hour | 91.4 s | 123.6 s | **−26%** |
| Wall per audio-hour | 545.7 s | 490.8 s | **+11%** |

L4 wins on every per-stage compute metric. g4dn split wins on wall time
because of 2-replica diarize parallelism. The implication: **single-box
g6 is compute-efficient but throughput-limited**; combine g6 + g4dn pool
(Option 7) to get the L4 compute advantage *and* parallelism.

---

## 8. Open questions / next benchmarks

Each item is a candidate for a future session.

- [ ] g6.xlarge spot fulfillment in eu-west-2 — empirical interruption rate
      over a 24-hour window. Critical for whether Option 7 can be made all-spot.
- [ ] Pyannote on g6.xlarge with 2 replicas in the same container
      (`DALSTON_MAX_SESSIONS=2`, budget bumped). Does it beat 2× g4dn
      pyannote pool on cost-per-audio-hour given L4's faster compute?
- [ ] Faster-whisper transcribe on L4 (g6) vs parakeet — RTF + WER.
      Calibration profile for `transcribe-faster-whisper-L4.json`.
- [ ] vLLM ASR transcribe on g6 — already has L4 compute capability ≥ 8.0.
- [ ] g6e.xlarge (L40S, 48 GB) for fatter parakeet variants
      (parakeet-tdt-1.1b, parakeet-rnnt-1.1b). VRAM budget 40000/8000.
- [ ] g5.xlarge (A10G, 24 GB) — interpolates between T4 and L4 pricing,
      worth a baseline.
- [ ] ARM g5g family — compute capability and Dalston's torch wheel
      compatibility.
- [ ] NeMo's `vad_batch_size` propagation bug — calibrator's form-field
      did not affect the engine. Investigate
      `engines/stt-transcribe/nemo/batch_engine.py` for the actual VAD
      batching env var, then patch
      `dalston/tools/calibrate_vram.py:516` to set it via
      `docker exec ... -e VAR=N` or fix the engine to honor the form field.
- [ ] Audio-length aware routing — currently jobs are FIFO. Long audio
      blocks the pyannote queue for short audio (today's 14.8-min job
      waited 7+ minutes behind a 73-min job). SJF or audio-length-bucketed
      queues would smooth wall RTF for short audio.
- [ ] Cross-region: us-east-1 / us-west-2 / eu-central-1 spot price
      and capacity comparison.
- [ ] Mixed-precision / quantized model variants.
- [ ] Realtime engine capacity comparison — this report covers batch only.

---

## 9. Reference

- Calibration tool: [dalston/tools/calibrate_vram.py](../../dalston/tools/calibrate_vram.py)
- VRAM budget calculator: [dalston/engine_sdk/vram_budget.py](../../dalston/engine_sdk/vram_budget.py)
- AWS deployment script: [infra/scripts/dalston-aws](../../infra/scripts/dalston-aws)
- Co-located calibration helper: [infra/scripts/calibrate-coloc-gpu.sh](../../infra/scripts/calibrate-coloc-gpu.sh)
- Engine presets: [dalston-aws:81-137](../../infra/scripts/dalston-aws#L81-L137)
- Per-engine VRAM budget overrides via shell:
  `DALSTON_OVERRIDE__<engine>__<KEY>` (see [dalston-aws:1259-1278](../../infra/scripts/dalston-aws#L1259-L1278))

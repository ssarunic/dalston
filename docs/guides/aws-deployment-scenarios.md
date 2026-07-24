# AWS deployment scenarios

The current repository implements three full-stack templates and one separate
single-engine mode. Choose by operational shape, then check current AWS
availability and pricing for your region.

## Supported matrix

| Shape | Command | What runs |
| --- | --- | --- |
| CPU single host | `setup -t cpu` | Control plane and CPU-oriented Compose services |
| GPU single host | `setup -t gpu` | Control plane and GPU profile on one host |
| Split | `setup -t split` | Persistent CPU control plane plus replaceable GPU workers |
| Naked engine | `engine up PRESET` | One synchronous engine and local Redis, no gateway |

Every full-stack template uses `setup` followed by `launch`. The repository
does not currently implement Terraform, ECS, EKS, an ALB deployment, or a
queue-driven EC2 autoscaler.

## Scenario 1: CPU single host

```bash
dalston-aws setup -t cpu
dalston-aws launch control-plane
```

The checked-in template uses `t3.xlarge`, on-demand, with the `local-infra`
profile. It is useful for evaluation and workloads whose selected engines are
acceptable on CPU. It does not define a GPU worker, so this is invalid:

```text
dalston-aws launch gpu
```

To move from CPU-only to split mode, use a deliberate state/infrastructure
migration or a fresh deployment based on the split template; rerunning setup
with a different template is not a general-purpose topology migration.

## Scenario 2: GPU single host

```bash
dalston-aws setup -t gpu
dalston-aws launch control-plane
```

The checked-in template uses a spot `g4dn.xlarge` and the local-infra/GPU
profiles. This is compact but couples API availability and GPU lifecycle.
Because a one-time spot instance can be terminated rather than stopped, it is
less suitable for an always-available control plane than split mode.

The host has a persistent EBS data volume, and a replacement control plane can
reattach it in the same availability zone.

## Scenario 3: split control plane and GPU

```bash
dalston-aws setup -t split
dalston-aws launch control-plane --observability
dalston-aws launch gpu --engines nemo,pyannote --spot
```

The checked-in template uses an on-demand `t3.large` control plane and a spot
`g6.xlarge` worker. This keeps the gateway, job database, Redis, and
orchestrator available while GPU workers are rotated.

Add specialized or replicated workers:

```bash
dalston-aws launch gpu --engines onnx --spot
dalston-aws launch gpu --engines onnx --spot
dalston-aws launch gpu \
  --engines vllm-asr \
  --gpu-type g6.xlarge \
  --on-demand
```

Replicas can share an engine label; the tool records distinct Tailscale
hostnames and worker IDs.

## Scenario 4: naked engine

```bash
dalston-aws setup -t gpu
dalston-aws engine up faster-whisper
```

This mode reuses setup’s key pair, security group, and instance profile, but
not its S3 bucket or control-plane services. It runs one engine with
capacity one and an engine-local Redis container.

Transcription presets expose native, OpenAI-compatible, and
ElevenLabs-compatible synchronous HTTP routes. There are no durable jobs,
gateway authentication, webhooks, exports, DAG assembly, or automatic
multi-engine routing.

Terminate it when finished:

```bash
dalston-aws engine down
```

## Selecting and co-locating engines

`dalston-aws` currently knows these GPU presets:

| Preset | Stage | Important constraint |
| --- | --- | --- |
| `onnx` | transcribe | Lightweight default |
| `faster-whisper` | transcribe | Multilingual model family |
| `nemo` | transcribe | NeMo model family; VRAM budget is GPU-aware |
| `pyannote` | diarize | Requires `HF_TOKEN` |
| `vllm-asr` | transcribe | Requires compute capability at least 8.0 |
| `hf-asr` | transcribe | Hugging Face model family |

The worker launch code has tuned co-location budgets for `nemo` and
`pyannote` on known GPUs. Other multi-engine combinations require capacity
testing. Engine containers may load or preload different models, so image
presence alone does not determine peak VRAM.

The script validates known compute-capability incompatibilities. It does not
prove that an arbitrary list passed through `--engines` fits concurrently.

## Spot lifecycle

- Split-mode GPU workers are disposable; S3 and the control plane retain
  durable job state and artifacts.
- A terminated/reclaimed worker must be recreated with `launch gpu`.
- `up` restarts retained instances but cannot resurrect a terminated one-time
  spot instance.
- Single-engine mode is explicitly ephemeral and removes its local
  `engine-state.yaml` record after successful termination.

Use on-demand workers for workloads that cannot tolerate spot replacement:

```bash
dalston-aws launch gpu --engines nemo --on-demand
```

## Storage

| Data | Location |
| --- | --- |
| Full-stack job artifacts/audio | S3 bucket created by setup |
| Control-plane PostgreSQL/Redis | Persistent `/data` EBS volume |
| Control-plane operational files | Persistent `/data` EBS volume |
| GPU worker models and containers | Replaceable worker-local storage |
| Naked-engine model cache | Ephemeral instance storage/root fallback |

## Observability

Pass `--observability` when launching the control plane:

```bash
dalston-aws launch control-plane --observability
```

This adds the repository’s Jaeger, Prometheus, Grafana, Loki, and metrics
exporter services and configures worker log/metric connectivity where
supported. It is a single-control-plane observability stack, not a managed AWS
monitoring service.

## Costs and sizing

The templates are executable defaults, not pricing promises. AWS instance,
spot, EBS, S3, data-transfer, and logging prices change by region and time.
Use:

- the current template files for selected instance types;
- AWS Service Quotas and instance-type offerings for availability;
- the AWS Pricing Calculator for forecasts; and
- `dalston-cost-correlate` for measured deployment cost.

Avoid copying historical monthly estimates from old documentation.

## See also

- [Deploying Dalston on AWS](aws-deploy.md)
- [Single-engine Tailscale mode](11-single-engine-tailscale-mode.md)
- [Spot interruption recovery](13-spot-interruptions-recovery.md)
- [Cost correlation](52-cost-correlate-tool.md)

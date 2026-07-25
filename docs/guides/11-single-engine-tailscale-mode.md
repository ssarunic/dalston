# Single-engine Tailscale mode

Single-engine mode launches one GPU engine on EC2 and exposes its synchronous
HTTP interface only to your Tailscale network. It does not run the Dalston
gateway, orchestrator, PostgreSQL, or shared job system.

## What it provides

- one selected GPU engine;
- a local Redis sidecar required by the engine runner;
- native stage HTTP on port 9100;
- minimal OpenAI- and ElevenLabs-compatible routes for transcription engines;
- capacity one, with caller-managed concurrency; and
- explicit `engine up` / `engine down` lifecycle.

It does not provide durable jobs, API-key authentication, webhooks, exports,
multi-stage DAGs, PII processing, or realtime compatibility WebSockets.

## Prerequisites

- Python 3.11+ with the repository dependencies;
- working AWS credentials;
- Tailscale on the operator machine with MagicDNS enabled; and
- `/dalston/tailscale-auth-key` stored as an SSM SecureString in the target
  region.

Export `HF_TOKEN` before launching the `pyannote` preset.

## One-time AWS setup

```bash
dalston-aws setup -t gpu
```

This provisions shared AWS resources but does not launch an instance.
Single-engine mode reuses its key pair, security group, and IAM instance
profile. The S3 bucket is not used by the naked engine.

## Launch

```bash
dalston-aws engine up faster-whisper
```

Available preset keys:

```text
faster-whisper  hf-asr  nemo  onnx  pyannote  vllm-asr
```

Spot is the default. Optional overrides:

```bash
dalston-aws engine up nemo --on-demand
dalston-aws engine up vllm-asr --gpu-type g6.xlarge
```

`vllm-asr` requires compute capability 8.0 or newer, so the script rejects
T4-backed `g4dn` for that preset.

## Status and readiness

```bash
dalston-aws engine status
```

The output reports the preset, instance, instance type, pricing mode, region,
EC2 state, Tailscale hostname, and engine URL. It does not probe model health.
After EC2 reaches `running`, cloud-init still needs time to join Tailscale,
prepare storage, pull the image, and load the model.

Probe readiness from a tailnet member:

```bash
ENGINE=http://dalston-engine-faster-whisper:9100

curl "$ENGINE/health"
curl "$ENGINE/v1/capabilities"
```

## Call the engine

Native transcription:

```bash
curl -X POST "$ENGINE/v1/transcribe" \
  -F file=@meeting.wav \
  -F language=en \
  -F word_timestamps=true
```

OpenAI-compatible synchronous transcription:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://dalston-engine-faster-whisper:9100/v1",
    api_key="not-used",
)

with open("meeting.wav", "rb") as audio:
    result = client.audio.transcriptions.create(
        model="large-v3-turbo",
        file=audio,
        response_format="verbose_json",
        timestamp_granularities=["word"],
    )

print(result.text)
```

ElevenLabs-compatible synchronous transcription:

```python
from elevenlabs import ElevenLabs

client = ElevenLabs(
    base_url="http://dalston-engine-faster-whisper:9100",
    api_key="not-used",
)

with open("meeting.wav", "rb") as audio:
    result = client.speech_to_text.convert(
        file=audio,
        model_id="scribe_v1",
        language_code="en",
    )

print(result.text)
```

These compatibility adapters intentionally omit gateway features. In
particular, `diarize=true`, webhook mode, SRT/VTT response formats, durable
jobs, and additional formats are not supported.

A diarization preset exposes `POST /v1/diarize`, not the transcription
compatibility routes.

## SSH and bootstrap diagnostics

The top-level `dalston-aws ssh --name ...` command only selects GPU workers
recorded in `aws-state.yaml`; it does not select the separate naked-engine
record.

Use the key and hostname reported by the state files:

```bash
ssh -i ~/.dalston/dalston-key.pem \
  ubuntu@dalston-engine-faster-whisper
```

If setup used an existing key pair via `--key`, provide the corresponding
private key yourself; `dalston-aws` cannot download existing AWS key
material.

On the instance:

```bash
sudo tail -f /var/log/user-data.log
sudo systemctl status dalston-tailscale
docker ps
docker logs stt-transcribe-faster-whisper
nvidia-smi
```

## Teardown

```bash
dalston-aws engine down
```

This cancels an associated spot request, terminates the instance, and removes
the local engine deployment record after AWS accepts termination. Model and
container caches are ephemeral and may need to be downloaded again next time.

Single-engine state is stored at:

```text
~/.dalston/engine-state.yaml
```

Full deployment state remains separately at:

```text
~/.dalston/aws-state.yaml
```

Do not delete engine state merely because an operation failed. If AWS
termination fails, the tool deliberately retains the record so a billable
instance is not orphaned. Retry `engine down` or inspect the instance in AWS
before force-removing state.

## Common problems

### “Engine is already deployed”

Run:

```bash
dalston-aws engine status
dalston-aws engine down
```

Stopped instances count as existing deployments because launching another
would orphan the first. Only remove `engine-state.yaml` manually after
confirming the recorded EC2 instance is terminated or absent.

### Hostname does not resolve

Confirm the SSM auth key is in the instance’s region, wait for cloud-init, and
inspect the Tailscale service through SSH/public EC2 diagnostics if necessary.

### Pyannote fails during bootstrap

Export `HF_TOKEN` before `engine up` and confirm the token has access to the
configured gated model.

### Second request fails

This is expected at capacity one. Serialize calls in the client or use the
full control plane for queueing and horizontal routing.

## When to use the full control plane

Use [the AWS control-plane deployment](21-control-plane-aws-deploy.md) when
you need async jobs, webhooks, transcript exports, speaker pipelines,
realtime WebSockets, API keys, audit history, or multiple engines.

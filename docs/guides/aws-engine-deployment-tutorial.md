# Deploying Dalston engines on AWS

There are two supported engine deployment shapes in `dalston-aws`:

1. a naked single engine, called directly over Tailscale; or
2. one or more GPU workers attached to the full Dalston control plane.

The old manual Compose instructions and `*-cpu` service names are not part of
the current AWS automation.

## Option A: one naked engine

Use this for synchronous transcription without the gateway, database, job
DAG, webhooks, or exports:

```bash
dalston-aws setup -t gpu
dalston-aws engine up faster-whisper
dalston-aws engine status
```

Available presets:

```text
faster-whisper  hf-asr  nemo  onnx  pyannote  vllm-asr
```

Spot is the default. Override it or the GPU type at launch:

```bash
dalston-aws engine up vllm-asr \
  --gpu-type g6.xlarge \
  --on-demand
```

`vllm-asr` requires a GPU family with compute capability 8.0 or newer; the
script rejects incompatible T4 instances. Export `HF_TOKEN` before launching
`pyannote`.

Call a transcription engine directly:

```bash
ENGINE=http://dalston-engine-faster-whisper:9100

curl -X POST "$ENGINE/v1/transcribe" \
  -F file=@meeting.wav \
  -F language=en \
  -F word_timestamps=true
```

Transcription presets also expose minimal OpenAI- and
ElevenLabs-compatible synchronous endpoints:

```bash
curl -X POST "$ENGINE/v1/audio/transcriptions" \
  -F file=@meeting.wav \
  -F model=large-v3-turbo \
  -F response_format=verbose_json

curl -X POST "$ENGINE/v1/speech-to-text" \
  -F file=@meeting.wav \
  -F model_id=scribe_v1 \
  -F language_code=en
```

See [Single-engine Tailscale mode](11-single-engine-tailscale-mode.md) for
state, access, and teardown details.

## Option B: engines behind the control plane

Use split mode for async jobs, routing, persistence, speaker pipelines,
webhooks, exports, API keys, and the web console:

```bash
dalston-aws setup -t split
dalston-aws launch control-plane
dalston-aws launch gpu --engines onnx --spot
```

Add a diarization/transcription worker:

```bash
export HF_TOKEN=hf_...
dalston-aws launch gpu \
  --engines nemo,pyannote \
  --gpu-type g6.xlarge \
  --spot
```

The worker bootstrap:

1. launches a Deep Learning AMI;
2. joins Tailscale;
3. prepares replaceable local model/container storage;
4. pulls the selected GHCR images;
5. runs each preset on ports beginning at 9100; and
6. connects it to control-plane Redis and S3.

Workers register automatically; the orchestrator selects them by stage,
capabilities, model, language, and current readiness.

## Choosing presets

| Preset | Stage | Notes |
| --- | --- | --- |
| `onnx` | transcribe | Lightweight Parakeet runtime |
| `faster-whisper` | transcribe | Multilingual CTranslate2 runtime |
| `nemo` | transcribe | NeMo Parakeet runtime |
| `hf-asr` | transcribe | Hugging Face ASR runtime |
| `vllm-asr` | transcribe | GPU audio-language-model runtime |
| `pyannote` | diarize | Pyannote 4.0; requires `HF_TOKEN` |

Alignment, preparation, PII, and redaction remain control-plane services in
the generated split deployment. A distributed merge worker is not required;
the orchestrator assembles completed transcripts.

## Inspect and operate

```bash
dalston-aws status
dalston-aws ssh gpu --name onnx
dalston-aws terminate gpu --name onnx
dalston-aws launch gpu --engines onnx --spot
```

For a worker that has not yet resolved its tailnet hostname:

```bash
dalston-aws reconcile
```

On the worker:

```bash
sudo systemctl status dalston-gpu
sudo journalctl -u dalston-gpu -n 100
docker ps
nvidia-smi
```

## Building images

AWS automation pulls public GHCR images. When developing an engine locally,
use the actual Compose service name:

```bash
make build-engine ENGINE=stt-transcribe-onnx
make build-engine ENGINE=stt-diarize-pyannote-4.0
```

Base engine images use `docker/Dockerfile.base-engine`; there is no
`docker/Dockerfile.engine-base`.

## See also

- [Deploying Dalston on AWS](aws-deploy.md)
- [AWS deployment scenarios](aws-deployment-scenarios.md)
- [Engine presets catalog](12-engine-presets-catalog.md)

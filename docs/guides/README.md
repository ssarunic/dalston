# Dalston user guides

> **Self-host an ElevenLabs- and OpenAI-compatible speech-to-text API.**
> Run it on your laptop or private cloud. Use spot/on-demand engines when they
> fit your workload. You own the models, the cache, and the bill.

This is the user-facing guide section. For engineering deep-dives (DAG
internals, schema specs, etc.), see [`docs/specs/`](../specs/).

---

## Start here

- **[01-quickstart.md](01-quickstart.md)** — first transcript in 5 minutes (curl, SDK, CLI)
- **[02-pick-your-deployment.md](02-pick-your-deployment.md)** — laptop / spot GPU / split mode / multi-engine

---

## Engines on the go (the headline)

- **[10-engines-spot-and-on-demand.md](10-engines-spot-and-on-demand.md)** — pricing model, when to pick which
- **[11-single-engine-tailscale-mode.md](11-single-engine-tailscale-mode.md)** — `dalston-aws engine up` walkthrough
- **[12-engine-presets-catalog.md](12-engine-presets-catalog.md)** — the six presets, side by side
- **[13-spot-interruptions-recovery.md](13-spot-interruptions-recovery.md)** — what happens when AWS reclaims

## Whole-system mode (control plane)

- **[20-control-plane-tour.md](20-control-plane-tour.md)** — what's running and why
- **[21-control-plane-aws-deploy.md](21-control-plane-aws-deploy.md)** — split mode in 4 commands
- **[22-using-the-web-console.md](22-using-the-web-console.md)** — every page, what it's for
- **[23-using-the-cli.md](23-using-the-cli.md)** — `dalston transcribe`, `listen`, `jobs`, …
- **[24-using-the-python-sdk.md](24-using-the-python-sdk.md)** — sync, async, real-time, webhooks

## Deployment references

- **[self-hosted-deployment-tutorial.md](self-hosted-deployment-tutorial.md)** — current local/private deployment
- **[aws-deploy.md](aws-deploy.md)** — current AWS script and templates
- **[gcp-deploy-experimental.md](gcp-deploy-experimental.md)** — experimental GCP script, with explicit gaps
- **[configuration reference](../reference/configuration.md)** — generated gateway/orchestrator environment settings

## Principles — how it works under the hood

- **[30-how-models-are-fetched.md](30-how-models-are-fetched.md)** — S3-first, HF fallback, on-disk cache
- **[31-pipeline-stages-explained.md](31-pipeline-stages-explained.md)** — prepare/transcribe/align/diarize, transcript assembly, then optional PII detection/audio redaction
- **[32-diarization-vs-transcription.md](32-diarization-vs-transcription.md)** — picking the right combo

## Real-time streaming

- **[40-realtime-overview.md](40-realtime-overview.md)** — three protocols, one backend
- **[41-realtime-elevenlabs-compatible.md](41-realtime-elevenlabs-compatible.md)** — ElevenLabs SDK drop-in
- **[42-realtime-openai-compatible.md](42-realtime-openai-compatible.md)** — OpenAI Realtime SDK drop-in
- **[43-realtime-dalston-native.md](43-realtime-dalston-native.md)** — binary frames, lowest overhead

## Performance & cost

- **[50-performance-and-rtf.md](50-performance-and-rtf.md)** — RTF math, sizing worksheet
- **[51-aws-cost-estimator.md](51-aws-cost-estimator.md)** — what each setup costs
- **[52-cost-correlate-tool.md](52-cost-correlate-tool.md)** — daily cost-per-episode reports

---

## Additional engineering references

Use these for deeper or scenario-specific context.

- [aws-deploy.md](aws-deploy.md) — comprehensive AWS deployment reference
- [aws-deployment-scenarios.md](aws-deployment-scenarios.md) — scenario-by-scenario tradeoffs
- [aws-engine-deployment-tutorial.md](aws-engine-deployment-tutorial.md) — engineering walkthrough
- [aws-cost-correlation.md](aws-cost-correlation.md) — cost-correlate tool reference
- [new-transcription-engine-tutorial.md](new-transcription-engine-tutorial.md) — adding a new engine
- [TYPED_ENGINE_CONTRACTS.md](TYPED_ENGINE_CONTRACTS.md) — engine SDK contracts

---

## At a glance

- Three public realtime protocols: Dalston native, ElevenLabs, and OpenAI.
- Batch stages for preparation, transcription, alignment, diarization, PII
  detection, and audio redaction.
- Local, AWS, and experimental GCP deployment tooling.
- Pluggable authored engines and a shared model registry/cache.

Performance and cloud cost depend on dated hardware/price inputs. Use
[50-performance-and-rtf.md](50-performance-and-rtf.md) and
[51-aws-cost-estimator.md](51-aws-cost-estimator.md) rather than copying a
headline number from the navigation page.

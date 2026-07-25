# Dalston documentation

Dalston is a modular, self-hosted speech-to-text server with native, OpenAI-
compatible, and ElevenLabs-compatible batch/realtime APIs.

## Choose your path

- New user: [Quickstart](guides/01-quickstart.md), then
  [pick a deployment](guides/02-pick-your-deployment.md).
- Application developer:
  [REST API](specs/batch/API.md),
  [Python SDK](guides/24-using-the-python-sdk.md), or
  [realtime overview](guides/40-realtime-overview.md).
- Operator:
  [self-hosted deployment](guides/self-hosted-deployment-tutorial.md),
  [AWS deployment](guides/aws-deploy.md),
  [experimental GCP](guides/gcp-deploy-experimental.md), and
  [configuration reference](reference/configuration.md).
- Engine author:
  [new transcription engine tutorial](guides/new-transcription-engine-tutorial.md)
  and [typed engine contracts](guides/TYPED_ENGINE_CONTRACTS.md).
- Contributor:
  [architecture](specs/ARCHITECTURE.md),
  [project structure](specs/PROJECT_STRUCTURE.md), and repository `AGENTS.md`.

## User guides

[The guide index](guides/README.md) organizes task-oriented documentation into
getting started, deployment, console/CLI/SDK, model/pipeline concepts,
realtime, and performance/cost.

## Current references

- [Glossary](GLOSSARY.md)
- [Architecture](specs/ARCHITECTURE.md)
- [Project structure](specs/PROJECT_STRUCTURE.md)
- [REST API](specs/batch/API.md)
- [OpenAI-compatible API](specs/openai/API.md)
- [WebSocket API](specs/realtime/WEBSOCKET_API.md)
- [Model selection and registry](specs/MODEL_SELECTION.md)
- [Data retention](specs/DATA_RETENTION.md)
- [PII detection/redaction](specs/PII_DETECTION.md)
- [Observability](specs/OBSERVABILITY.md)
- [Audit log](specs/AUDIT_LOG.md)
- [Configuration](reference/configuration.md)

The running gateway's `/openapi.json`, installed CLI `--help`, SDK types, and
effective Compose configuration remain authoritative where generated/runtime
surfaces and prose disagree.

## Engineering and history

- `decisions/`: architecture decision records.
- `specs/implementations/`: internal engineering patterns, not public API
  guarantees.
- `plan/`, `reports/`, milestone documents, and test plans: delivery history
  and design context. They may intentionally describe superseded behavior.
- `testing/`: contributor testing playbooks.

When updating user documentation, verify current behavior against code or a
generated surface and avoid treating plans/reports as current references.

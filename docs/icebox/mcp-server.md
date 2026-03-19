# MCP Server for Dalston

|              |                                                                          |
| ------------ | ------------------------------------------------------------------------ |
| **Idea**     | Expose Dalston capabilities as an MCP server so LLMs/agents can use it   |
| **Priority** | High — likely more important than the web console long-term              |
| **Status**   | Icebox                                                                   |

## Thesis

Classic interfaces — web consoles, dashboards, CLIs — are transitional. As
LLMs and agents become the primary operators of software infrastructure, tools
like Dalston will be consumed primarily through agent-native protocols like
MCP rather than human-facing UIs.

The web console we built in M10/M15/M78 is valuable today, but the future
control plane for Dalston is an MCP server (and eventually a Claude Code skill)
that lets an agent submit transcription jobs, manage realtime sessions, inspect
infrastructure, and react to results — all without a human in the loop.

**MCP is more important than web in the long run.** The web console becomes a
fallback for debugging and edge cases; the MCP server becomes the primary
interface.

## Architectural Position

The MCP server sits in the control plane, alongside the web console, CLI, and
Python SDK. It is a client of the Gateway HTTP API — not a lower-level
component.

```
┌────────────────────── Control Plane ──────────────────────┐
│  Web Console   CLI   Python SDK   MCP Server   Skill     │
└──────────────────────────┬────────────────────────────────┘
                           │  HTTP / WebSocket
                   ┌───────▼────────┐
                   │    Gateway      │
                   │   (FastAPI)     │
                   └───────┬────────┘
                           │
             ┌─────────────┼─────────────┐
             │             │             │
        Orchestrator    Session      Redis/Postgres
                        Router
```

**Why not lower?** The gateway already handles auth, rate limiting, validation,
and API versioning. Bypassing it would create a parallel gateway to maintain
and would couple the MCP server to internal queue formats and event schemas.

## Proposed Tool Surface

### Batch transcription

| Tool               | Maps to                               | Notes                                    |
| ------------------ | ------------------------------------- | ---------------------------------------- |
| `transcribe`       | `POST /v1/audio/transcriptions`       | Blocking convenience: submit + poll + return result |
| `transcribe_async` | `POST /v1/audio/transcriptions`       | Returns job ID immediately               |
| `get_job`          | `GET /v1/audio/transcriptions/{id}`   | Status + transcript if complete          |
| `list_jobs`        | `GET /v1/audio/transcriptions`        | Recent jobs, filterable                  |

### Realtime session management

Realtime transcription does not fit the MCP request/response model. Audio
streaming requires a real client (browser, mobile, CLI) connected via
WebSocket. The MCP server manages sessions; it does not stream audio.

| Tool              | What it does                                              |
| ----------------- | --------------------------------------------------------- |
| `start_session`   | Creates a realtime session, returns WebSocket URL + ID    |
| `get_session`     | Status + accumulated transcript so far                    |
| `stop_session`    | Ends the session                                          |

This matches ElevenLabs' approach: their MCP server creates conversational AI
agents and retrieves transcripts, but actual voice streaming happens on their
infra, not through MCP.

### Operational

| Tool           | Maps to                      |
| -------------- | ---------------------------- |
| `list_engines` | `GET /api/console/engines`   |
| `system_status`| `GET /api/console/dashboard` |

~8 tools total. Minimal surface, each one a thin translation from MCP protocol
to Dalston HTTP API.

## Authentication

**API key**, same as every other Dalston client. The `DALSTON_API_KEY` env var
is provided to the MCP server via its configuration. No OAuth needed unless
Dalston goes multi-tenant.

Works identically in all deployment modes:

- **Local stdio**: Claude Desktop spawns the MCP server process, key from config
- **Docker sidecar**: Container with `DALSTON_API_URL` + `DALSTON_API_KEY` env vars
- **AWS**: Same container, gateway URL points at the remote instance

## Claude Code Skill

Beyond the MCP server, we should build a **Claude Code skill** — a prompt
document that teaches Claude how to use Dalston effectively. The skill would:

- Know the available MCP tools and when to use each
- Understand pipeline stages and engine selection
- Know how to interpret job statuses and error codes
- Guide multi-step workflows (e.g., transcribe → check quality → re-run with
  different engine if poor)
- Handle realtime session orchestration (start session → give user the WS URL
  → monitor → fetch transcript)

The skill turns Dalston from a tool with documentation into a tool an agent
can use fluently.

## Prior Art

**ElevenLabs MCP** (`elevenlabs-mcp`): ~20 tools wrapping their Python SDK.
Batch STT is a single sync call (`speech_to_text`). Realtime conversational
AI is managed (create agent, fetch transcript) but not streamed through MCP.
Auth is API key via env var. Transport is stdio only.

Dalston's MCP would be similar in shape but differs in:

- Async pipeline (multi-stage DAG) vs single API call
- Self-hosted vs cloud — file access and deployment are different
- Engine selection as a first-class concept
- Session management for realtime (ElevenLabs has agent management instead)

## Open Questions

- **File transfer**: When the MCP server runs in Docker, how does the LLM
  provide audio files? Options: shared volume mount, upload via multipart API,
  presigned URL (M77).
- **Blocking vs async**: Should `transcribe` block until the job completes
  (simpler for LLMs) or always return a job ID? Probably offer both.
- **Progress**: Can we use MCP notifications/SSE to stream job progress, or
  must the LLM poll?
- **Skill scope**: Should the skill be Dalston-specific or generic
  "audio transcription" that happens to use Dalston?

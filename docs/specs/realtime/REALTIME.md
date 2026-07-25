# Realtime transcription architecture

Dalston exposes three WebSocket adapters over the same realtime worker pool:

| Protocol | Endpoint |
| --- | --- |
| Dalston native | `/v1/audio/transcriptions/stream` |
| ElevenLabs compatible | `/v1/speech-to-text/realtime` |
| OpenAI compatible | `/v1/realtime?intent=transcription` |

The gateway authenticates the client, applies the per-tenant concurrent-session
limit, resolves a requested model to an engine, and asks the embedded
`SessionCoordinator` for a compatible worker. It then proxies protocol messages
between client and worker. Protocol translation remains in the gateway; engine
workers use Dalston's internal session protocol.

```text
client
  -> gateway adapter
  -> embedded SessionCoordinator (Redis-backed allocation)
  -> realtime engine WebSocket
  -> partial/final transcript events
  -> gateway adapter
  -> client
```

There is no standalone session-router service. There is also no hybrid
`enhance_on_end` mode: ending a realtime session does not automatically submit
a batch job. Applications that need a second-pass batch result must submit one
explicitly.

## Allocation and routing

Realtime engines heartbeat into the unified engine registry with endpoint,
engine ID, loaded models, languages, capacity, and active-session count.
Allocation filters incompatible workers and then reserves capacity atomically.
No capacity closes the client connection with `4503`.

When a specific model is requested, its registry entry determines the
`engine_id`. With automatic selection, Dalston prefers the largest ready
streaming-capable model that has a live compatible worker. An explicit language
is checked against model metadata where that metadata is available.

Session reservations have a Redis TTL and the gateway refreshes it during a
connection. The coordinator's health loop marks stale workers offline and
reconciles reservations left by crashed gateway processes.

## Audio and transcription

Realtime engines combine streaming ASR with voice activity detection and
utterance assembly. Native clients normally send binary PCM frames. Provider
adapters accept their provider-specific JSON/base64 form and translate it.

Partial results are provisional. Only final/committed transcript events should
be appended permanently by clients. Word timestamps, log probabilities,
characters, language detection, and VAD events are capability- and
protocol-dependent.

Backpressure uses an audio-lag budget rather than queue length alone. The worker
warns when lag enters the warning region and terminates at the hard budget with
close code `4010`. See
[Latency budget and backpressure](./LATENCY_BUDGET_BACKPRESSURE.md).

## Persistence

Dalston-native and OpenAI-compatible sessions can create a PostgreSQL session
record through the shared proxy lifecycle. Retention controls whether audio and
transcript artifacts are stored. `resume_session_id` links a new native session
to a previous one; it does not resume decoder state or merge transcripts.

ElevenLabs-compatible realtime currently uses the shared allocation/proxy
lifecycle without creating the same persisted session record. Do not promise
management-API history for every compatibility connection.

See [Session persistence](./SESSION_PERSISTENCE.md) for the current REST
surface.

## Failure model

- Invalid or missing API key: `4001`.
- Missing scope: `4003`.
- API-key request-rate rejection: `4029`.
- Concurrent-session limit: `4429`.
- Invalid realtime parameters: `4400`.
- Lag budget exceeded: `4010`.
- No compatible capacity/service unavailable: `4503`.

Provider adapters also send protocol-shaped error events when possible.
Clients must still inspect the WebSocket close code because an error event
cannot always be delivered.

## Capacity planning

Worker capacity is declared by each engine and enforced during allocation.
Observed concurrency depends on hardware, model, audio characteristics, and
latency objective. Any published capacity or RTF measurement must include at
least the GPU/CPU, model, engine version or commit, audio corpus, concurrency,
and test date.

## Related references

- [WebSocket API](./WEBSOCKET_API.md)
- [Realtime engines](./REALTIME_ENGINES.md)
- [Realtime overview guide](../../guides/40-realtime-overview.md)
- [Dalston-native guide](../../guides/43-realtime-dalston-native.md)
- [OpenAI-compatible guide](../../guides/42-realtime-openai-compatible.md)
- [ElevenLabs-compatible guide](../../guides/41-realtime-elevenlabs-compatible.md)

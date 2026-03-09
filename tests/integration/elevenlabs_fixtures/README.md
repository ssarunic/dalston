# ElevenLabs Contract Fixtures (M62)

These fixtures pin the ElevenLabs STT compatibility contract used by Dalston.

- Contract date: `2026-03-08`
- Primary SDK: `elevenlabs==2.38.1`
- Scope:
  - `speech_to_text.convert(...)` sync and async
  - `speech_to_text.get(...)`
  - `speech_to_text.delete(...)`
  - `POST /v1/single-use-token/{token_type}`
  - realtime `session_started` envelope

The files intentionally freeze request/response schemas used by integration tests.
When the public contract is updated, edit these fixtures and keep tests table-driven.

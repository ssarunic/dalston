# OpenAI Contract Fixtures (M61)

These fixtures pin the OpenAI STT compatibility contract used by Dalston.

- Contract date: `2026-03-08`
- Primary SDK: `openai==1.93.2`
- Scope:
  - `audio.transcriptions.create(...)`
  - `audio.translations.create(...)`
  - `audio.transcriptions.with_raw_response.create(...)`
  - `beta.realtime.transcription_sessions.create(...)`

The repository currently stores canonical fixture shapes and request expectations for
integration tests. As real trace captures are expanded, update these fixtures in-place
instead of mutating test assertions ad hoc.

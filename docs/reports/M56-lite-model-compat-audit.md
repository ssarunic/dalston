# M56 Lite Model Compatibility Audit

Scoped lite-path entities (`tenants`, `jobs`, `tasks`) use a SQLite baseline schema.

Type mitigations in lite mode:

- Postgres UUID columns are represented as text UUID strings generated in application code.
- JSONB payloads are stored as JSON text.
- Postgres-only types (ARRAY/INET) are out of scope for the M56 lite batch slice.

This keeps distributed migrations untouched while enabling lite startup without Postgres.

# Internal implementation references

> These documents explain engineering patterns and historical implementation
> decisions. They are not public API contracts and may lag the running code.
> For user-visible behavior, use the guides, generated OpenAPI document, CLI
> `--help`, and current SDK types.

This folder contains references for non-obvious patterns used across Dalston.
They are not exhaustive code listings, but focused examples of patterns that:

- Establish conventions used throughout the codebase
- Have subtle requirements that aren't obvious from specs
- Show integration points between components

## Contents

| File | Description |
|------|-------------|
| [auth-patterns.md](auth-patterns.md) | API key auth, middleware, scopes, rate limiting |
| [dag-builder.md](dag-builder.md) | Task DAG construction with optional/parallel tasks |
| [enrichment-engines.md](enrichment-engines.md) | Emotion, events, and LLM cleanup engine patterns |
| [console-api.md](console-api.md) | Console API aggregation patterns |
| [python-sdk.md](python-sdk.md) | Python SDK package structure, patterns, testing |

## When to Use These

- **During implementation**: Reference these patterns when building the corresponding features
- **For consistency**: Follow established patterns when adding similar functionality
- **Not as copy-paste**: Actual implementations may evolve; these show the approach, not final code

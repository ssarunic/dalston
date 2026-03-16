# M{N}: {Title}

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | One-sentence description of what this milestone achieves     |
| **Duration**       | X–Y days                                                     |
| **Dependencies**   | M{N} (Name), M{N} (Name)                                     |
| **Deliverable**    | Comma-separated list of concrete outputs                     |
| **Status**         | Not Started / In Progress / Completed                        |

## User Story

> *"As a [role], I want [capability], so that [outcome]."*

---

## Outcomes

Use this table when the milestone changes observable behaviour. Skip if Motivation (below) is a better fit.

| Scenario | Current | After M{N} |
| -------- | ------- | ---------- |
| Describe a concrete situation | What happens today | What happens after |

---

## Motivation

Use this section instead of (or in addition to) the Outcomes table when the milestone addresses a less visible problem — tech debt, architectural correctness, performance, etc.

Explain the current pain point, why it matters now, and what goes wrong if it is not addressed.

---

## Architecture

Include an ASCII diagram when the milestone introduces or meaningfully changes a data flow, component interaction, or state machine. Skip for purely internal refactors where the external behaviour is unchanged.

```
┌─────────────────────────────────────────────────────┐
│                   COMPONENT NAME                     │
│                                                      │
│   Component A ──────────────▶ Component B           │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## Steps

Steps are independently deployable or testable slices. Each step should leave the system in a working state. Number them `{N}.1`, `{N}.2`, etc.

### {N}.1: {Step Name}

**Files modified:**

- `path/to/file.py` — brief description of change
- `path/to/new_file.py` *(new)*

**Deliverables:**

Describe what is built. Use code blocks for interface definitions, schema changes, or config diffs that need to be precise.

```python
# Example: new function signature
def example(arg: str) -> None:
    ...
```

---

### {N}.2: {Step Name}

**Files modified:**

- `path/to/file.py`

**Deliverables:**

...

---

## Non-Goals

Explicitly list things that are out of scope to prevent scope creep and answer "why didn't you also do X?" questions.

- **Thing that sounds related but isn't** — one-line reason
- **Obvious extension** — why it belongs in a separate milestone

---

## Deployment

Fill in only if the rollout has ordering constraints, requires a coordinated deploy of multiple services, or has a migration step that cannot be rolled back. Skip for standard rolling deploys.

---

## Verification

Bash commands that confirm the milestone is working correctly in a running stack. Aim for commands that can be copy-pasted and produce a clear PASS/FAIL signal.

```bash
make dev

# Describe what you are verifying
curl -s http://localhost:8000/v1/... \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '...'
```

---

## Checkpoint

One checkbox per independently verifiable deliverable. This list drives the "done" criteria.

- [ ] Item 1
- [ ] Item 2
- [ ] Item 3

# LLM-Based Speaker Consolidation

|              |                                                                              |
| ------------ | ---------------------------------------------------------------------------- |
| **Idea**     | Use LLM cleanup phase to consolidate fragmented diarization speakers         |
| **Priority** | Medium — depends on LLM cleanup milestone                                    |
| **Status**   | Icebox                                                                       |

## Problem

Diarizers (especially nemo-sortformer) sometimes fragment a single speaker into
multiple speaker IDs. The symptom is rapid alternation between two speaker labels
at turn boundaries that line up exactly (e.g., SPEAKER_00 ends at 135.36,
SPEAKER_02 starts at 135.36), producing a ping-pong effect in the transcript.

Observed in job `ae711622-41e2-4661-a1d9-7651a0075275` (nemo-sortformer, 4
detected speakers where 2 were likely the same person, plus a ghost speaker with
<1s total).

## Indicators

- Two speakers whose turns alternate with near-zero gap at matching boundaries
- A speaker with negligible total speaking time (< 2s) likely being noise
- Diarizer reporting more speakers than actually present

## Proposed Approach

Handle this in the LLM cleanup stage rather than heuristics in the merger:

- LLM can read the transcript text and judge whether adjacent segments with
  different speaker labels sound like the same person (same voice, continuing
  the same thought, mid-sentence splits)
- Merge speaker IDs and relabel segments accordingly
- Collapse ghost speakers into their nearest neighbor

## Why Not the Merger

Pure timing heuristics in the merger would be brittle. The LLM can use semantic
context (continuation of a sentence, topic coherence, speech patterns) to make a
much more reliable decision.

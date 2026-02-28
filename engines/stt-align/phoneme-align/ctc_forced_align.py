"""CTC forced alignment via dynamic programming.

Implements the trellis-based forced alignment algorithm used to align
a known character sequence to CTC emission probabilities from a wav2vec2
model. Based on the approach from the PyTorch forced alignment tutorial
and the WhisperX paper (Bain et al., 2023).

Algorithm overview:
    1. Build a trellis (DP lattice) scoring how well each token aligns to
       each time frame, considering both "stay on same token" (blank) and
       "advance to next token" transitions.
    2. Backtrack through the trellis using beam search to find the optimal
       alignment path.
    3. Merge consecutive frames assigned to the same token into character
       segments with start/end frame indices and confidence scores.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True)
class CharSegment:
    """A character-level alignment result."""

    label: str
    start: int  # Start frame index (inclusive)
    end: int  # End frame index (exclusive)
    score: float  # Average probability across frames


@dataclass(frozen=True, slots=True)
class _Point:
    """A single point on the alignment path."""

    token_index: int
    time_index: int
    score: float


def build_trellis(
    emission: torch.Tensor,
    tokens: list[int],
    blank_id: int = 0,
) -> torch.Tensor:
    """Build the CTC alignment trellis (dynamic programming lattice).

    The trellis has shape (T, N) where T is the number of emission frames
    and N is the number of tokens. Each cell (t, n) holds the best cumulative
    log-probability of aligning tokens[0..n] to frames[0..t].

    Args:
        emission: Log-softmax emission matrix of shape (T, V) where V is vocab size.
        tokens: Token indices to align. Use -1 for wildcard (unknown character).
        blank_id: CTC blank token index.

    Returns:
        Trellis tensor of shape (T, N).
    """
    num_frames = emission.size(0)
    num_tokens = len(tokens)

    trellis = torch.zeros((num_frames, num_tokens))

    # First column: cumulative blank probability (staying at first token)
    trellis[1:, 0] = torch.cumsum(emission[1:, blank_id], 0)
    # First row after (0,0): impossible to be at token>0 at frame 0
    trellis[0, 1:] = -float("inf")
    # Prevent staying at first token too long when there are many tokens
    trellis[-num_tokens + 1 :, 0] = float("inf")

    for t in range(num_frames - 1):
        trellis[t + 1, 1:] = torch.maximum(
            # Stay at same token (emit blank)
            trellis[t, 1:] + emission[t, blank_id],
            # Advance to next token
            trellis[t, :-1] + _token_emission(emission[t], tokens[1:], blank_id),
        )

    return trellis


def backtrack(
    trellis: torch.Tensor,
    emission: torch.Tensor,
    tokens: list[int],
    blank_id: int = 0,
    beam_width: int = 2,
) -> list[_Point] | None:
    """Beam-search backtracking through the trellis.

    Starting from the last frame and last token, traces backwards through
    the trellis to find the best alignment path.

    Args:
        trellis: The alignment trellis of shape (T, N).
        emission: Log-softmax emission matrix of shape (T, V).
        tokens: Token indices that were aligned.
        blank_id: CTC blank token index.
        beam_width: Number of candidate paths to keep at each step.

    Returns:
        Alignment path as a list of Points from first to last frame,
        or None if alignment failed.
    """
    T = trellis.size(0) - 1  # noqa: N806
    J = trellis.size(1) - 1  # noqa: N806

    initial = _BeamState(
        token_index=J,
        time_index=T,
        score=trellis[T, J],
        path=[_Point(J, T, emission[T, blank_id].exp().item())],
    )
    beams = [initial]

    while beams and beams[0].token_index > 0:
        next_beams: list[_BeamState] = []

        for beam in beams:
            t, j = beam.time_index, beam.token_index
            if t <= 0:
                continue

            p_stay = emission[t - 1, blank_id]
            p_change = _token_emission(emission[t - 1], [tokens[j]], blank_id)[0]

            stay_score = trellis[t - 1, j]
            change_score = trellis[t - 1, j - 1] if j > 0 else float("-inf")

            if not torch.isinf(torch.tensor(stay_score)):
                new_path = beam.path.copy()
                new_path.append(_Point(j, t - 1, p_stay.exp().item()))
                next_beams.append(
                    _BeamState(
                        token_index=j,
                        time_index=t - 1,
                        score=stay_score,
                        path=new_path,
                    )
                )

            if j > 0 and not torch.isinf(torch.tensor(change_score)):
                new_path = beam.path.copy()
                new_path.append(_Point(j - 1, t - 1, p_change.exp().item()))
                next_beams.append(
                    _BeamState(
                        token_index=j - 1,
                        time_index=t - 1,
                        score=change_score,
                        path=new_path,
                    )
                )

        beams = sorted(next_beams, key=lambda x: x.score, reverse=True)[:beam_width]
        if not beams:
            break

    if not beams:
        return None

    best = beams[0]
    t = best.time_index
    j = best.token_index
    while t > 0:
        prob = emission[t - 1, blank_id].exp().item()
        best.path.append(_Point(j, t - 1, prob))
        t -= 1

    return best.path[::-1]


def merge_repeats(path: list[_Point], transcript: str) -> list[CharSegment]:
    """Merge consecutive frames assigned to the same token into segments.

    Args:
        path: Alignment path from backtracking.
        transcript: The clean character sequence that was aligned.

    Returns:
        List of CharSegment with label, start/end frame, and score.
    """
    segments: list[CharSegment] = []
    i1 = 0
    while i1 < len(path):
        i2 = i1
        while i2 < len(path) and path[i1].token_index == path[i2].token_index:
            i2 += 1
        score = sum(path[k].score for k in range(i1, i2)) / (i2 - i1)
        segments.append(
            CharSegment(
                label=transcript[path[i1].token_index],
                start=path[i1].time_index,
                end=path[i2 - 1].time_index + 1,
                score=score,
            )
        )
        i1 = i2
    return segments


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _BeamState:
    """Mutable state for a single beam during backtracking."""

    token_index: int
    time_index: int
    score: float
    path: list[_Point]


def _token_emission(
    frame_emission: torch.Tensor,
    tokens: list[int],
    blank_id: int,
) -> torch.Tensor:
    """Get emission scores for tokens, treating -1 as wildcard.

    Wildcard tokens (characters not in the model dictionary) receive the
    maximum non-blank emission score for the frame, allowing alignment to
    proceed even when some characters are missing from the vocabulary.

    Args:
        frame_emission: Emission vector for a single frame, shape (V,).
        tokens: Token indices; -1 means wildcard.
        blank_id: CTC blank token index.

    Returns:
        Tensor of emission scores, one per token.
    """
    tokens_t = torch.tensor(tokens) if not isinstance(tokens, torch.Tensor) else tokens
    wildcard_mask = tokens_t == -1

    # Regular scores (clamp to avoid -1 indexing)
    regular_scores = frame_emission[tokens_t.clamp(min=0).long()]

    # Wildcard score: max non-blank emission
    masked = frame_emission.clone()
    masked[blank_id] = float("-inf")
    max_score = masked.max()

    return torch.where(wildcard_mask, max_score, regular_scores)

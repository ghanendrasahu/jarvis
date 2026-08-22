"""Raw STT metrics: no RapidFuzz, no NLU, no correction layers."""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def word_accuracy(expected: str, transcript: str) -> float:
    ref = _WORD_RE.findall(expected.lower())
    hyp = _WORD_RE.findall(transcript.lower())
    if not ref:
        return 1.0 if not hyp else 0.0
    dist = _levenshtein(ref, hyp)
    return max(0.0, 1.0 - dist / len(ref))


def _levenshtein(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i]
        for j, h in enumerate(hyp, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def score(expected: str, transcript: str) -> dict:
    return {
        "accuracy": round(word_accuracy(expected, transcript), 4),
        "exact_match": normalize(expected) == normalize(transcript),
    }

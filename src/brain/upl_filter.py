"""Deterministic UPL output filter — backstop for LLM responses."""
from __future__ import annotations

import re

UPL_PATTERNS = [
    (re.compile(r"\b(you have a (strong|good|great|solid) case)\b", re.I), "That's exactly what the attorney will assess."),
    (re.compile(r"\b(worth|value).{0,20}\$[\d,]+", re.I), "Case value depends on details only the attorney should evaluate."),
    (re.compile(r"\b(you('ll| will) (win|lose))\b", re.I), "The attorney is the one who can assess outcomes."),
    (re.compile(r"\bstatute of limitations.{0,30}(is|expires|deadline)\b", re.I), "The attorney will review all relevant deadlines for your case."),
    (re.compile(r"\b\d+\s*(year|month|day)s?\s*(left|remaining)\b", re.I), "The attorney will review all relevant deadlines for your case."),
]


def filter_upl_violations(text: str) -> str:
    """Strip or replace UPL-violating phrases from outbound text."""
    result = text
    for pattern, replacement in UPL_PATTERNS:
        if pattern.search(result):
            result = pattern.sub(replacement, result)
    return result

"""Lightweight substring / pattern checks for benchmark answers (fallback only)."""

from __future__ import annotations

import math
import re
from typing import Any


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _nums_in_text(text: str) -> list[float]:
    out: list[float] = []
    for m in re.finditer(
        r"(?P<n>\d+(?:\.\d+)?)\s*(?P<k>[kK])\b|(?P<plain>\d+(?:\.\d+)?)",
        text,
    ):
        if m.group("k"):
            out.append(float(m.group("n")) * 1000.0)
        elif m.group("plain"):
            out.append(float(m.group("plain")))
    return out


def _number_match(expected: float, response: str) -> bool:
    t = response.lower()
    fv = float(expected)
    candidates: set[str] = set()
    if abs(fv - round(fv)) < 1e-9:
        candidates.add(str(int(round(fv))))
    for nd in (6, 5, 4, 3, 2, 1, 0):
        s = f"{fv:.{nd}f}".rstrip("0").rstrip(".")
        if s:
            candidates.add(s.lower())
    for c in candidates:
        if c in t:
            return True
    for n in _nums_in_text(t):
        denom = max(abs(fv), 1e-9)
        if abs(n - fv) / denom <= 0.04 or abs(n - fv) <= 0.35:
            return True
    return False


def score_answer(response: str, answer: Any) -> bool:
    """Heuristic pass/fail (not entailment). Used when LLM judge is unavailable or errors."""
    if answer is None:
        return True
    r = norm_text(response)
    if isinstance(answer, bool):
        return ("true" if answer else "false") in r
    if isinstance(answer, int) and not isinstance(answer, bool):
        return _number_match(float(answer), response)
    if isinstance(answer, float):
        if math.isnan(answer):
            return True
        return _number_match(answer, response)
    if isinstance(answer, str):
        a = norm_text(answer)
        if len(a) < 2:
            return a in r
        return a in r or a[: min(40, len(a))] in r
    if isinstance(answer, list):
        if not answer:
            return True
        ok = 0
        for item in answer:
            if isinstance(item, str) and norm_text(item) in r:
                ok += 1
            elif isinstance(item, dict):
                if score_answer(response, item):
                    ok += 1
        return ok >= max(1, (len(answer) + 1) // 2)
    if isinstance(answer, dict):
        hits = 0
        total = 0
        for _k, v in answer.items():
            total += 1
            if score_answer(response, v):
                hits += 1
        return hits >= max(1, (total + 1) // 2) if total else True
    return norm_text(str(answer)) in r

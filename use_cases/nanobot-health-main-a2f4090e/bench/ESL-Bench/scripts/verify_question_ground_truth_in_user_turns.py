#!/usr/bin/env python3
"""Verify every ``ground_truth`` path in ``question_sparse40.json`` resolves and is recoverable from ``user_turn_mixed.json``.

Resolution (multi-file):
- Paths without ``.json`` are read from ``profile.json`` under ``--construction-dir``.
- Paths like ``timeline_26_filtered_sparse40.json.entries[456].value`` load the named JSON from
  ``--construction-dir`` (same basenames as in ``ground_truth``).
- ``question_sparse40.json`` and ``user_turn_mixed.json`` default under ``--eval-dir``.

Matching (heuristic, not entailment):
- Builds a **corpus** from ``user_turn_mixed.json``: every ``user`` message plus every timeline
  turn's ``date`` (so ``ground_truth`` times matchable via calendar day).
- Numbers: literal forms + fuzzy match to numbers parsed from corpus (~3% or abs 0.25).
- ``None`` from timeline: expects ``(no reading)`` in corpus (AutoSender convention).
- ISO ``.time`` fields: require the calendar date prefix ``YYYY-MM-DD`` to appear in corpus.
- Long strings: normalized substring; if that fails, ``;``-clause coverage; if that fails,
  token-prefix overlap (same spirit as ``verify_profile_in_user_turns.py``).

Exit 0 if all paths pass; 1 otherwise. Prints each failure.

Example::

    python3 verify_question_ground_truth_in_user_turns.py \\
        --construction-dir ../data/construction(2026-04)/user110_AT_demo \\
        --eval-dir ../data/evaluation
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"
_DEFAULT_EVALUATION = _BENCH / "data" / "evaluation"

STOPWORDS = frozenset(
    """
    the and for with from that this his her him are was has have been into about
    their they them while both over than then such there these those also only some
    very just like does did not but you your our its who how what when where which
    """.split()
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _split_path_segments(path: str) -> list[tuple[str, int | None]]:
    """Split ``a.b[3].c`` into [('a', None), ('b', 3), ('c', None)]."""
    parts: list[tuple[str, int | None]] = []
    for raw in path.split("."):
        if not raw:
            continue
        m = re.fullmatch(r"([^[\]]+)(?:\[(\d+)\])?", raw)
        if not m:
            raise ValueError(f"bad path segment: {raw!r} in {path!r}")
        key, idx_s = m.group(1), m.group(2)
        parts.append((key, int(idx_s) if idx_s is not None else None))
    return parts


def _walk(obj: Any, parts: list[tuple[str, int | None]]) -> Any:
    cur = obj
    for key, idx in parts:
        if not isinstance(cur, dict):
            raise TypeError(f"expected dict at {key!r}, got {type(cur).__name__}")
        if key not in cur:
            raise KeyError(key)
        cur = cur[key]
        if idx is not None:
            if not isinstance(cur, list):
                raise TypeError(f"expected list at index {idx}, got {type(cur).__name__}")
            cur = cur[idx]
    return cur


def _parse_ground_truth_path(full: str) -> tuple[str | None, str]:
    """Returns (json_filename or None for profile), dot_path_after_root."""
    m = re.match(r"^([^/\\]+\.json)\.(.+)$", full)
    if m:
        return m.group(1), m.group(2)
    return None, full


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


def _number_mentioned(v: int | float, text: str) -> bool:
    if isinstance(v, float) and math.isnan(v):
        return True
    fv = float(v)
    t = text.lower()
    candidates: set[str] = set()
    if abs(fv - round(fv)) < 1e-9:
        candidates.add(str(int(round(fv))))
    for nd in (8, 6, 5, 4, 3, 2, 1, 0):
        s = f"{fv:.{nd}f}".rstrip("0").rstrip(".")
        if s:
            candidates.add(s.lower())
    for c in candidates:
        if c in t:
            return True
    for n in _nums_in_text(text):
        denom = max(abs(fv), 1e-9)
        if abs(n - fv) / denom <= 0.03 or abs(n - fv) <= 0.25:
            return True
    return False


def _meaningful_tokens(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4 and t not in STOPWORDS]
    seen: set[str] = set()
    out: list[str] = []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _stem_hit(token: str, corpus_words: set[str]) -> bool:
    if token in corpus_words:
        return True
    if len(token) < 4:
        return False
    p = token[:4]
    for cw in corpus_words:
        if len(cw) >= 4 and (cw.startswith(p) or token.startswith(cw[:4])):
            return True
    return False


def _string_mentioned(s: str, corpus: str, corpus_words: set[str]) -> bool:
    n = _norm(s)
    if len(n) < 3:
        return n in corpus
    if n in corpus:
        return True
    if ";" in s:
        chunks = [c.strip() for c in s.split(";") if len(c.strip()) >= 8]
        if chunks and sum(1 for c in chunks if _norm(c) in corpus) >= max(1, int(0.7 * len(chunks))):
            return True
    toks = _meaningful_tokens(s)
    if not toks:
        return False
    hits = sum(1 for t in toks if _stem_hit(t, corpus_words))
    return hits >= max(2, int(0.35 * len(toks)))


def _primitive_findable(
    val: Any,
    *,
    path: str,
    corpus: str,
    corpus_words: set[str],
) -> tuple[bool, str]:
    if path.endswith(".time") and isinstance(val, str):
        day = val[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day.lower() in corpus:
            return True, "date_prefix_in_corpus"
        if _norm(val) in corpus:
            return True, "iso_in_corpus"
        return False, "time_date_not_in_corpus"

    if val is None:
        if "(no reading)" in corpus:
            return True, "null_as_no_reading"
        return False, "null_but_no_no_reading_phrase"

    if isinstance(val, bool):
        return (str(val).lower() in corpus), "bool"

    if isinstance(val, int) and not isinstance(val, bool):
        if _number_mentioned(val, corpus):
            return True, "int"
        # word-boundary for small ints that could be substrings
        if re.search(rf"(?<!\d){val}(?!\d)", corpus):
            return True, "int_wb"
        return False, "int_missing"

    if isinstance(val, float):
        if _number_mentioned(val, corpus):
            return True, "float"
        return False, "float_missing"

    if isinstance(val, str):
        if _string_mentioned(val, corpus, corpus_words):
            return True, "str"
        return False, "str_missing"

    if isinstance(val, list):
        if not val:
            return True, "empty_list"
        ok_all = True
        reasons: list[str] = []
        for i, item in enumerate(val):
            subp = f"{path}[{i}]"
            ok, why = _primitive_findable(item, path=subp, corpus=corpus, corpus_words=corpus_words)
            if not ok:
                ok_all = False
                reasons.append(why)
        return ok_all, ("list:" + ",".join(reasons[:3]))

    if isinstance(val, dict):
        ok_all = True
        bits: list[str] = []
        for k, v in val.items():
            ok, why = _primitive_findable(v, path=f"{path}.{k}", corpus=corpus, corpus_words=corpus_words)
            if not ok:
                ok_all = False
                bits.append(f"{k}({why})")
        return ok_all, "dict:" + ",".join(bits[:4])

    return False, f"unsupported_type_{type(val).__name__}"


def _build_corpus(mixed: dict[str, Any]) -> tuple[str, set[str]]:
    parts: list[str] = []
    for turn in mixed.get("turns", []):
        d = turn.get("date")
        if isinstance(d, str):
            parts.append(d)
        for msg in turn.get("messages", []):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                parts.append(msg["content"])
    raw = "\n".join(parts)
    return raw.lower(), set(re.findall(r"[a-z0-9]+", raw.lower()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--construction-dir",
        type=Path,
        default=_DEFAULT_CONSTRUCTION,
        help="Directory with profile.json and timeline *.json referenced in ground_truth",
    )
    ap.add_argument(
        "--eval-dir",
        type=Path,
        default=_DEFAULT_EVALUATION,
        help="Directory with question_sparse40.json and user_turn_mixed.json",
    )
    ap.add_argument("--question", type=Path, default=None, help="Override question path")
    ap.add_argument("--profile", type=Path, default=None, help="Override profile.json path")
    ap.add_argument("--mixed", type=Path, default=None, help="Override user_turn_mixed.json path")
    args = ap.parse_args()
    constr = args.construction_dir.resolve()
    evald = args.eval_dir.resolve()
    q_path = args.question or (evald / "question_sparse40.json")
    prof_path = args.profile or (constr / "profile.json")
    mixed_path = args.mixed or (evald / "user_turn_mixed.json")

    questions = json.loads(q_path.read_text(encoding="utf-8"))
    profile = json.loads(prof_path.read_text(encoding="utf-8"))
    mixed = json.loads(mixed_path.read_text(encoding="utf-8"))
    corpus, corpus_words = _build_corpus(mixed)

    file_cache: dict[str, Any] = {}

    def load_json(name: str) -> Any:
        if name not in file_cache:
            p = constr / name
            if not p.is_file():
                raise FileNotFoundError(p)
            file_cache[name] = json.loads(p.read_text(encoding="utf-8"))
        return file_cache[name]

    failures: list[tuple[int, str, Any, str]] = []

    for qi, item in enumerate(questions):
        for gt in item.get("ground_truth", []):
            if not isinstance(gt, str):
                failures.append((qi, str(gt), None, "ground_truth_not_string"))
                continue
            try:
                fname, rest = _parse_ground_truth_path(gt)
                if fname is None:
                    root = profile
                else:
                    root = load_json(fname)
                parts = _split_path_segments(rest)
                val = _walk(root, parts)
            except Exception as ex:  # pragma: no cover
                failures.append((qi, gt, None, f"resolve_error:{ex}"))
                continue

            ok, why = _primitive_findable(val, path=gt, corpus=corpus, corpus_words=corpus_words)
            if not ok:
                failures.append((qi, gt, val, why))

    n_gt = sum(len(item.get("ground_truth", [])) for item in questions)
    print("question file:", q_path)
    print("profile:", prof_path)
    print("mixed:", mixed_path)
    print("construction dir (profile + timeline json):", constr)
    print("eval dir:", evald)
    print("questions:", len(questions), "ground_truth path refs:", n_gt)
    print("pass" if not failures else f"FAIL: {len(failures)} path(s)")
    for qi, gt, val, why in failures[:80]:
        vrepr = repr(val)
        if len(vrepr) > 120:
            vrepr = vrepr[:117] + "..."
        print(f"  q[{qi}] {why}\n    path={gt!r}\n    value={vrepr}")
    if len(failures) > 80:
        print(f"  ... and {len(failures) - 80} more")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

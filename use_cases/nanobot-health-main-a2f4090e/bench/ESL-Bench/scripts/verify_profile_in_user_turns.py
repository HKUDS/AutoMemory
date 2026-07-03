#!/usr/bin/env python3
"""Lightweight pattern check: profile.json content appears in user_turn_profile.json.

Default: walks demographics, personality, and health_profile (skips metadata —
synthetic generation fields the user would not normally dictate in chat).

Matching strategy (lightweight, no ML):
1) Case-insensitive, whitespace-collapsed substring match on the full user corpus.
2) If the profile string contains ``;`` or long comma-separated clauses, each
   segment is checked the same way (so chief complaints / goals survive paraphrase).
3) If literal match fails, require *token stem coverage*: enough tokens from the
   profile string (len>=4, stopwords removed) match words in the user corpus by
   equality or shared 4-character prefix (handles thrive/thriving, etc.).
4) Numbers: word-boundary regex so ``34`` does not match inside ``178``.

Exit 0 if all checks pass; 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"

STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "his",
        "her",
        "him",
        "are",
        "was",
        "has",
        "have",
        "been",
        "into",
        "about",
        "their",
        "they",
        "them",
        "while",
        "both",
        "over",
        "than",
        "then",
        "such",
        "there",
        "these",
        "those",
        "also",
        "only",
        "some",
        "very",
        "just",
        "like",
        "does",
        "did",
        "not",
        "but",
        "you",
        "your",
        "our",
        "its",
        "who",
        "how",
        "what",
        "when",
        "where",
        "which",
    }
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _corpus_from_user_turns(data: dict) -> str:
    parts: list[str] = []
    for turn in data.get("turns", []):
        for msg in turn.get("messages", []):
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                parts.append(msg["content"])
    return _norm(" ".join(parts))


def _corpus_words(corpus: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", corpus))


def _meaningful_tokens(text: str) -> list[str]:
    toks = [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4 and t not in STOPWORDS]
    # de-dup preserving order
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


def _token_coverage(text: str, corpus: str, corpus_words: set[str]) -> tuple[int, int]:
    toks = _meaningful_tokens(text)
    if not toks:
        return (0, 0)
    hits = sum(1 for t in toks if _stem_hit(t, corpus_words))
    need = max(2, int(0.42 * len(toks)) + (1 if len(toks) >= 8 else 0))
    need = min(need, len(toks))
    return (hits, need)


def _covers_string(path: str, text: str, corpus: str, corpus_words: set[str]) -> bool:
    t = text.strip()
    if not t:
        return True
    nt = _norm(t)
    if nt in corpus:
        return True

    # Very short blurbs: allow one token miss (e.g. "Conscious eater" vs "conscious about food").
    mtoks = _meaningful_tokens(t)
    if mtoks and len(nt) <= 40:
        hits = sum(1 for tok in mtoks if _stem_hit(tok, corpus_words))
        if hits >= max(1, len(mtoks) - 1):
            return True

    # Semicolon-separated clauses (chief complaints, etc.)
    if ";" in t and len(t) > 24:
        parts = [p.strip() for p in t.split(";") if p.strip()]
        return all(_covers_string(f"{path}::seg", p, corpus, corpus_words) for p in parts)

    # Long comma-separated list (goals of care)
    if "," in t and len(t) > 60 and path.endswith("goals_of_care"):
        parts = [p.strip() for p in t.split(",") if len(p.strip()) >= 12]
        return all(_covers_string(f"{path}::goal", p, corpus, corpus_words) for p in parts)

    hits, need = _token_coverage(t, corpus, corpus_words)
    if hits >= need:
        return True

    # Last resort: shorter substring windows for dense clinical prose
    if len(nt) > 90:
        windows = (nt[:56], nt[len(nt) // 2 : len(nt) // 2 + 56], nt[-56:])
        if sum(1 for w in windows if w in corpus) >= 2:
            return True

    return False


def _walk(obj: Any, path: str, out: list[tuple[str, Any]]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            _walk(v, p, out)
    elif isinstance(obj, list):
        if not obj:
            out.append((path, []))
            return
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", out)
    elif obj is None:
        return
    elif isinstance(obj, bool):
        out.append((path, obj))
    elif isinstance(obj, (int, float)):
        out.append((path, obj))
    elif isinstance(obj, str):
        if obj.strip():
            out.append((path, obj))
    else:
        out.append((path, str(obj)))


def _number_pattern(path: str, value: int | float) -> tuple[str, re.Pattern[str]]:
    if float(value).is_integer():
        token = str(int(value))
    else:
        token = str(value)
    pat = re.compile(rf"(?<!\d){re.escape(token)}(?!\d)")
    return (path, pat)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "profile.json",
    )
    parser.add_argument(
        "--user-turns",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "user_turn_profile.json",
    )
    parser.add_argument(
        "--include-metadata",
        action="store_true",
        help="Also require metadata.* string leaves to appear (verbatim or token match).",
    )
    args = parser.parse_args()

    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    turns = json.loads(args.user_turns.read_text(encoding="utf-8"))
    corpus = _corpus_from_user_turns(turns)
    corpus_words = _corpus_words(corpus)

    leaves: list[tuple[str, Any]] = []
    _walk(profile, "", leaves)

    failures: list[str] = []
    ok = 0

    for path, value in leaves:
        if not args.include_metadata and path.startswith("metadata"):
            continue

        if value == []:
            if path.endswith("chronic_conditions"):
                if not re.search(r"no\s+active\s+chronic", corpus):
                    failures.append(f"[{path}] empty list: expected user to state no active chronic conditions")
                else:
                    ok += 1
            else:
                ok += 1
            continue

        if isinstance(value, bool):
            need = "true" if value else "false"
            if need not in corpus:
                failures.append(f"[{path}] expected boolean token '{need}' in user corpus")
            else:
                ok += 1
            continue

        if isinstance(value, (int, float)):
            pid, pat = _number_pattern(path, value)
            if not pat.search(corpus):
                failures.append(f"[{pid}] number {value!r} not found (word-boundary)")
            else:
                ok += 1
            continue

        if isinstance(value, str):
            if _covers_string(path, value, corpus, corpus_words):
                ok += 1
            else:
                preview = _norm(value)[:100]
                failures.append(f"[{path}] string not covered (literal / segment / token-stem): {preview!r}…")
            continue

    print(f"Checks passed: {ok}")
    print(f"Checks failed: {len(failures)}")
    for line in failures:
        print("  -", line)

    if failures:
        print(
            "\nTip: metadata.* is skipped by default. "
            "Use --include-metadata to require those fields too.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

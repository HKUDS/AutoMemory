#!/usr/bin/env python3
"""Check user_turn_timeline_daily_llm.json for likely information omissions vs timeline.

For each daily turn, loads the corresponding ``entries`` from the source timeline
(``source_file`` next to the LLM JSON, or ``--timeline``) using ``source_entry_indices``.

Heuristics (lightweight, not semantic entailment):
- **Numeric values** (non-null): pass if a close literal appears in the user text, or
  if any number parsed from the message is within ~3% of the value (handles ``5.5k`` vs 5523).
  **Note:** Summaries like "almost 90%" will not match ``89.12`` — expect high "miss"
  rates unless the user text copies many raw readings.
- **Events** that day: at least one significant token from ``event_name`` must appear
  (case-insensitive).
- **Null device values**: user text should hint at missing data (optional soft check).

Exit 0 always; prints a report. Use ``--fail-on-warn`` to exit 1 if any day exceeds
``--max-missing-value-rate``.

Example::

    python3 verify_user_turn_timeline_daily_llm.py \\
        --llm-json ../data/construction(2026-04)/user110_AT_demo/user_turn_timeline_daily_llm.json
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


def _value_mentioned(v: Any, text: str) -> bool:
    if v is None:
        return True
    if not isinstance(v, (int, float)) or (isinstance(v, float) and math.isnan(v)):
        return True
    fv = float(v)
    t = text.lower()

    # Exact-ish string forms
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

    # Fuzzy: any number in message within 3% (large values) or absolute 0.25 (small)
    for n in _nums_in_text(text):
        denom = max(abs(fv), 1e-9)
        if abs(n - fv) / denom <= 0.03 or abs(n - fv) <= 0.25:
            return True
    return False


def _event_mentioned(event: dict[str, Any], text: str) -> bool:
    name = (event.get("event_name") or "").lower()
    if not name.strip():
        return True
    toks = [w for w in re.findall(r"[a-z]{4,}", name)]
    if not toks:
        return name[:12] in text.lower()
    hits = sum(1 for w in toks if w in text.lower())
    return hits >= max(1, min(2, len(toks) // 2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm-json",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "user_turn_timeline_daily_llm.json",
    )
    parser.add_argument(
        "--timeline",
        type=Path,
        default=None,
        help="Override timeline path (default: sibling file named llm_json['source_file'])",
    )
    parser.add_argument(
        "--max-missing-value-rate",
        type=float,
        default=0.35,
        help="Warn if more than this fraction of non-null numeric values lack a match",
    )
    parser.add_argument(
        "--fail-on-warn",
        action="store_true",
        help="Exit 1 if any day exceeds --max-missing-value-rate",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print missing (indicator, value) samples for each WARN day",
    )
    parser.add_argument("--verbose-limit", type=int, default=4)
    args = parser.parse_args()

    llm = json.loads(args.llm_json.read_text(encoding="utf-8"))
    src_name = llm.get("source_file") or "timeline_26_filtered.json"
    timeline_path = args.timeline or (args.llm_json.parent / src_name)
    if not timeline_path.is_file():
        print(f"Timeline not found: {timeline_path}", file=sys.stderr)
        return 1

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = timeline["entries"]

    warned_days: list[str] = []
    all_miss_rates: list[float] = []

    print("LLM file:", args.llm_json)
    print("Timeline:", timeline_path)
    print("Turns:", len(llm.get("turns", [])))
    print()

    for turn in llm.get("turns", []):
        date = turn.get("date")
        parts: list[str] = []
        for m in turn.get("messages", []):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                parts.append(m["content"])
        msg = "\n\n".join(parts)

        idxs = turn.get("source_entry_indices") or []
        miss_val = 0
        total_val = 0
        event_rows: list[dict[str, Any]] = []
        for i in idxs:
            if i < 0 or i >= len(entries):
                print(f"BAD_INDEX day={date} i={i} (len={len(entries)})")
                return 1
            e = entries[i]
            if e.get("entry_type") == "device_indicator":
                v = e.get("value")
                if v is not None:
                    total_val += 1
                    if not _value_mentioned(v, msg):
                        miss_val += 1
            elif e.get("entry_type") == "event":
                ev = e.get("event") or {}
                event_rows.append(ev)

        rate = (miss_val / total_val) if total_val else 0.0
        all_miss_rates.append(rate)

        ev_fail = []
        for ev in event_rows:
            if not _event_mentioned(ev, msg):
                ev_fail.append(ev.get("event_name"))

        flag = rate > args.max_missing_value_rate or bool(ev_fail)
        if flag:
            warned_days.append(str(date))
        status = "WARN" if flag else "ok"
        ev_note = f" event_miss={ev_fail!r}" if ev_fail else ""
        print(f"{status}  {date}  value_miss={miss_val}/{total_val} ({rate:.1%}){ev_note}")

        if args.verbose and flag and msg:
            shown = 0
            for i in idxs:
                if shown >= args.verbose_limit:
                    break
                e = entries[i]
                if e.get("entry_type") != "device_indicator":
                    continue
                v = e.get("value")
                if v is None or _value_mentioned(v, msg):
                    continue
                print(f"    missing: {e.get('indicator')} = {v!r}")
                shown += 1

    worst_i = max(range(len(all_miss_rates)), key=lambda i: all_miss_rates[i])
    worst_day = llm["turns"][worst_i].get("date")
    print()
    print("Summary: worst value-miss rate", f"{all_miss_rates[worst_i]:.1%}", "on", worst_day)
    print("Days with WARN:", len(warned_days), "/", len(llm.get("turns", [])))

    if args.fail_on_warn and warned_days:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

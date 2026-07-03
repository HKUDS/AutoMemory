#!/usr/bin/env python3
"""Mix profile user turns with timeline user turns for ESL-Bench evaluation.

- All profile content is merged into calendar day **2026-01-01** (one turn for that day).
  Profile turns are **shuffled** (reproducible with ``--seed``).
- Timeline stays **one turn per calendar day**, sorted by date.
- On **2026-01-01**, profile ``messages`` come first, then that day's timeline ``messages``.
- **No** ``HH:MM`` prefixes are written; ingest-time ``NANOBOT_CURRENT_TIME`` is assigned by
  ``bench/esl_turn_timing.py`` (used from ``run_agent.py`` / ``eval_agent.py``).

The merged first-day turn includes ``profile_message_count`` so the driver can apply
30-minute spacing for profile lines and hourly spacing for timeline lines on that day.

Example::

    python3 mix_profile_timeline_user_turns.py \\
        --output ../data/evaluation/user_turn.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"
_DEFAULT_EVALUATION = _BENCH / "data" / "evaluation"

PROFILE_MERGE_DATE = "2026-01-01"


def _flatten_profile_messages(shuffled_profile_turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in shuffled_profile_turns:
        for m in t.get("messages") or []:
            if isinstance(m, dict):
                out.append(dict(m))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--profile",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "user_turn_profile.json",
        help="user_turn_profile.json",
    )
    ap.add_argument(
        "--timeline",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "user_turn_timeline.json",
        help="Daily timeline user turns (e.g. user_turn_timeline.json)",
    )
    ap.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_EVALUATION / "user_turn.json",
        help="Mixed JSON written here (default: evaluation/user_turn.json)",
    )
    ap.add_argument("--seed", type=int, default=42, help="RNG seed for shuffling profile turns")
    args = ap.parse_args()

    prof = json.loads(args.profile.read_text(encoding="utf-8"))
    tl = json.loads(args.timeline.read_text(encoding="utf-8"))

    profile_turns: list[dict[str, Any]] = list(prof.get("turns") or [])
    timeline_turns: list[dict[str, Any]] = list(tl.get("turns") or [])

    rng = random.Random(args.seed)
    shuffled_profiles = profile_turns[:]
    rng.shuffle(shuffled_profiles)

    flat_profile_msgs = _flatten_profile_messages(shuffled_profiles)
    by_date: dict[str, dict[str, Any]] = {}
    for t in timeline_turns:
        d = t.get("date")
        if isinstance(d, str) and d:
            by_date[d] = t

    all_dates = sorted(by_date.keys())
    if flat_profile_msgs and PROFILE_MERGE_DATE not in by_date:
        all_dates = sorted(set(all_dates) | {PROFILE_MERGE_DATE})

    mixed_turns: list[dict[str, Any]] = []

    for day in all_dates:
        tl_turn = by_date.get(day)
        tl_messages: list[dict[str, Any]] = []
        if tl_turn:
            tl_messages = [dict(m) for m in (tl_turn.get("messages") or []) if isinstance(m, dict)]

        if day == PROFILE_MERGE_DATE:
            combined = [dict(m) for m in flat_profile_msgs] + tl_messages
            if not combined:
                continue
            n_prof = len(flat_profile_msgs)
            if flat_profile_msgs and tl_messages:
                kind = "mixed"
            elif flat_profile_msgs:
                kind = "profile"
            else:
                kind = "timeline"
            out_turn: dict[str, Any] = {
                "date": day,
                "messages": combined,
                "turn_kind": kind,
            }
            if n_prof > 0:
                out_turn["profile_message_count"] = n_prof
            if tl_turn and "source_row_count" in tl_turn:
                out_turn["source_row_count"] = tl_turn["source_row_count"]
            mixed_turns.append(out_turn)
            continue

        if not tl_turn:
            continue
        if not tl_messages:
            continue
        out_turn = {
            "date": day,
            "messages": tl_messages,
            "turn_kind": "timeline",
        }
        if "source_row_count" in tl_turn:
            out_turn["source_row_count"] = tl_turn["source_row_count"]
        mixed_turns.append(out_turn)

    user_id = tl.get("user_id") or prof.get("user_id")

    out_doc: dict[str, Any] = {
        "user_id": user_id,
        "description": (
            f"Mixed user turns: profile merged into {PROFILE_MERGE_DATE} (shuffled, seed {args.seed}); "
            "timeline chronological; one turn per day. Timestamps for nanobot ingest are assigned in "
            "bench/esl_turn_timing.py."
        ),
        "sources": {
            "profile": args.profile.name,
            "timeline": args.timeline.name,
        },
        "mixing": {
            "random_seed": args.seed,
            "profile_merge_date": PROFILE_MERGE_DATE,
            "profile_order": "shuffled_turns_then_messages_in_turn_order",
            "ingest_time_assignment": "bench/esl_turn_timing.py",
            "layout": "one_turn_per_calendar_day",
        },
        "turns": mixed_turns,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} ({len(mixed_turns)} day-turns; "
        f"profile_msgs={len(flat_profile_msgs)}, timeline_days={len(by_date)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Randomly drop a fraction of per-day device_indicator rows from a timeline JSON.

For each calendar day, removes ``--fraction`` (default 0.4) of that day's
``device_indicator`` entries, **never** removing an entry index that appears in
``question_full_timeline_indices.json`` ``ground_truth`` paths (full-timeline indices), e.g.
``timeline_26_filtered.json.entries[123].value``.

``event`` rows are always kept. After removal, indices shift: this script
rewrites ``ground_truth`` entry indices (and optionally the timeline filename
prefix in those paths) to match the new ``entries`` array.

Example::

    python3 sparse_timeline_drop_indicators.py --seed 42

Writes by default::

    data/construction(2026-04)/user110_AT_demo/timeline_26_filtered_sparse40.json
    data/evaluation/question_sparse40.json

Reads questions by default from ``question_full_timeline_indices.json`` (construction), which
must reference ``timeline_26_filtered.json`` entry indices before remap.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"
_DEFAULT_EVALUATION = _BENCH / "data" / "evaluation"

GT_PATH_RE = re.compile(
    r"(?P<prefix>[^\"'\s]*timeline[^\"'\s]*\.json)\.entries\[(?P<idx>\d+)\](?P<suffix>\.(?:time|value))",
    re.IGNORECASE,
)


def collect_protected_indices(questions: list[dict[str, Any]], source_prefix: str) -> set[int]:
    """Indices referenced in ground_truth for the given timeline filename stem."""
    protected: set[int] = set()
    # Match literal paths containing the basename (e.g. timeline_26_filtered.json)
    basename = Path(source_prefix).name
    pat = re.compile(rf"{re.escape(basename)}\.entries\[(\d+)\]")
    for q in questions:
        for gt in q.get("ground_truth", []):
            if not isinstance(gt, str):
                continue
            for m in pat.finditer(gt):
                protected.add(int(m.group(1)))
    return protected


def remap_ground_truth_strings(
    questions: list[dict[str, Any]],
    old_to_new: dict[int, int],
    old_basename: str,
    new_basename: str,
) -> None:
    """In-place update ground_truth list strings."""

    def sub_one(s: str) -> str:
        def repl(m: re.Match[str]) -> str:
            prefix, idx_s, suffix = m.group("prefix"), m.group("idx"), m.group("suffix")
            if Path(prefix).name != old_basename:
                return m.group(0)
            old = int(idx_s)
            if old not in old_to_new:
                raise KeyError(f"ground_truth references removed index {old}: {m.group(0)!r}")
            new_i = old_to_new[old]
            new_prefix = str(Path(prefix).with_name(new_basename))
            return f"{new_prefix}.entries[{new_i}]{suffix}"

        return GT_PATH_RE.sub(repl, s)

    for q in questions:
        gts = q.get("ground_truth")
        if not isinstance(gts, list):
            continue
        q["ground_truth"] = [sub_one(x) if isinstance(x, str) else x for x in gts]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeline",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "timeline_26_filtered.json",
    )
    parser.add_argument(
        "--question",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "question_full_timeline_indices.json",
        help="Question set with ground_truth pointing at full timeline_26_filtered.json indices",
    )
    parser.add_argument(
        "--output-timeline",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "timeline_26_filtered_sparse40.json",
    )
    parser.add_argument(
        "--output-question",
        type=Path,
        default=_DEFAULT_EVALUATION / "question_sparse40.json",
    )
    parser.add_argument("--fraction", type=float, default=0.4, help="Fraction of device rows to drop per day")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--keep-question-path-basename",
        action="store_true",
        help="Do not rename timeline_*.json inside ground_truth (indices still remapped)",
    )
    args = parser.parse_args()

    if not 0 < args.fraction < 1:
        raise SystemExit("--fraction must be between 0 and 1")

    timeline = json.loads(args.timeline.read_text(encoding="utf-8"))
    questions = json.loads(args.question.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = timeline["entries"]

    old_basename = args.timeline.name
    protected = collect_protected_indices(questions, old_basename)

    by_day: dict[str, list[int]] = defaultdict(list)
    for i, e in enumerate(entries):
        if e.get("entry_type") != "device_indicator":
            continue
        t = e.get("time") or ""
        if len(t) >= 10:
            by_day[t[:10]].append(i)

    rng = random.Random(args.seed)
    to_remove: set[int] = set()

    for day in sorted(by_day):
        day_ix = by_day[day]
        n = len(day_ix)
        if n == 0:
            continue
        k = min(math.floor(n * args.fraction), n)
        eligible = [i for i in day_ix if i not in protected]
        if len(eligible) < k:
            raise RuntimeError(
                f"{day}: need to remove {k} device rows but only {len(eligible)} are "
                f"non-protected (protected_in_day={len(day_ix)-len(eligible)})"
            )
        to_remove.update(rng.sample(eligible, k))

    assert not (to_remove & protected), "logic bug: would remove protected index"

    new_entries = [e for i, e in enumerate(entries) if i not in to_remove]
    old_to_new: dict[int, int] = {}
    new_i = 0
    for old_i, e in enumerate(entries):
        if old_i in to_remove:
            continue
        old_to_new[old_i] = new_i
        new_i += 1

    new_basename = args.output_timeline.name
    if args.keep_question_path_basename:
        new_basename = old_basename

    remap_ground_truth_strings(questions, old_to_new, old_basename, new_basename)

    out_timeline = {
        "user_id": timeline.get("user_id"),
        "generated_at": timeline.get("generated_at"),
        "entry_count": len(new_entries),
        "entries": new_entries,
    }

    args.output_timeline.parent.mkdir(parents=True, exist_ok=True)
    args.output_question.parent.mkdir(parents=True, exist_ok=True)
    args.output_timeline.write_text(
        json.dumps(out_timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.output_question.write_text(
        json.dumps(questions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("Removed", len(to_remove), "device_indicator rows; kept", len(new_entries), "entries")
    print("protected_indices:", len(protected), "seed:", args.seed, "fraction:", args.fraction)
    print("Wrote", args.output_timeline)
    print("Wrote", args.output_question)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

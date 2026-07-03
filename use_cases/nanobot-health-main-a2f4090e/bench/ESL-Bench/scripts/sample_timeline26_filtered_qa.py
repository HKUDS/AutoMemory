#!/usr/bin/env python3
"""Sample 100 timeline entries from timeline_26_filtered.json with a fixed RNG seed.

Here "field" is interpreted as one timeline row in ``entries`` (one JSON object:
timestamp + device reading or event payload). This is the natural unit for QA
from a time-series file.

Outputs ``timeline26_filtered_qa.json`` next to the input by default.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

_BENCH = Path(__file__).resolve().parents[1]
_DEFAULT_CONSTRUCTION = _BENCH / "data" / "construction(2026-04)" / "user110_AT_demo"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=_DEFAULT_CONSTRUCTION / "timeline_26_filtered.json",
        help="Path to timeline_26_filtered.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (default: <input_dir>/timeline26_filtered_qa.json)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed (default: 42)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of entries to sample (default: 100)",
    )
    args = parser.parse_args()

    inp: Path = args.input
    out: Path = args.output or (inp.parent / "timeline26_filtered_qa.json")

    with inp.open(encoding="utf-8") as f:
        data = json.load(f)

    entries = data["entries"]
    n = len(entries)
    k = min(args.count, n)

    rng = random.Random(args.seed)
    sampled = rng.sample(entries, k=k)

    out_obj = {
        "user_id": data.get("user_id"),
        "source_file": inp.name,
        "random_seed": args.seed,
        "sample_size": k,
        "source_entry_count": n,
        "entries": sampled,
    }

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        json.dump(out_obj, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {k} entries to {out}")


if __name__ == "__main__":
    main()

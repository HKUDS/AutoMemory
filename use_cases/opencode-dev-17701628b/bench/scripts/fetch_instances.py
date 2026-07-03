#!/usr/bin/env python3
"""Fetch SWE-bench instance metadata for the memory evaluation showcase.

The harness uses the compact JSON emitted by this script to render prompts and,
when requested, feed predictions to the official SWE-bench verifier.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="SWE-bench/SWE-bench_Verified")
    parser.add_argument("--split", default="test")
    parser.add_argument("--ids", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    try:
        from datasets import load_dataset  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local Python env
        print(f"failed to import datasets: {exc}", file=sys.stderr)
        return 2

    rows = None
    try:
        data = load_dataset(args.dataset, split=args.split)
        rows = data
    except Exception as exc:  # pragma: no cover - network/cache dependent
        print(f"failed to load dataset {args.dataset}: {exc}", file=sys.stderr)
        # Older datasets/fsspec combinations can fail after downloading the
        # parquet shard. Fall back to reading the public shard directly.
        try:
            import pandas as pd  # type: ignore

            url = f"https://huggingface.co/datasets/{args.dataset}/resolve/main/data/{args.split}-00000-of-00001.parquet"
            rows = pd.read_parquet(url).to_dict("records")
        except Exception as fallback_exc:
            print(f"failed direct parquet fallback: {fallback_exc}", file=sys.stderr)
            return 3

    wanted = set(args.ids)
    found: dict[str, dict[str, Any]] = {}
    for row in rows:
        instance_id = row.get("instance_id")
        if instance_id not in wanted:
            continue
        found[instance_id] = {
            "instance_id": instance_id,
            "repo": row.get("repo"),
            "base_commit": row.get("base_commit"),
            "problem_statement": row.get("problem_statement"),
            "hints_text": row.get("hints_text"),
            "version": row.get("version"),
            "created_at": row.get("created_at"),
            "FAIL_TO_PASS": row.get("FAIL_TO_PASS"),
            "PASS_TO_PASS": row.get("PASS_TO_PASS"),
            "difficulty": row.get("difficulty"),
        }

    missing = sorted(wanted - set(found))
    if missing:
        print("missing instance ids: " + ", ".join(missing), file=sys.stderr)
        return 4

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {len(found)} instances to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

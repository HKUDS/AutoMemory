#!/usr/bin/env python3
"""
Refine runner for nanobot health memory system.
Usage:
    python runner.py --trace <trace_json> --data-dir <base_data_dir> [--results-dir <dir>]

Replays a single trace through the adapter, scores recall queries,
and writes results JSON to --results-dir.

For parallel execution, invoke once per trace with isolated data dirs.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

WORKTRACES = Path(__file__).parent
MEMORY_DIR = WORKTRACES.parent / "memory"
sys.path.insert(0, str(MEMORY_DIR.parent))

import memory as adapter

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "openrouter/minimax/minimax-m2.7"
API_BASE = "https://openrouter.ai/api/v1"


def build_context(turn_messages):
    """Wrap turn messages with API config for memorize()."""
    return {
        "messages": turn_messages,
        "model": MODEL,
        "api_key": OPENROUTER_API_KEY,
        "api_base": API_BASE,
    }


def flatten_recall(result):
    """Flatten recall result dict into a single searchable string."""
    parts = []
    if "health" in result:
        parts.append(result["health"])
    if "health_log" in result:
        parts.append(result["health_log"])
    if "health_log_matches" in result:
        for m in result["health_log_matches"]:
            parts.append(json.dumps(m) if isinstance(m, dict) else str(m))
    return "\n".join(parts)


def score_item(item, recall_text):
    """
    Score a single expected_information item against recall output.
    Returns 1 if matched, 0 otherwise.

    Supports:
      - Plain string: case-insensitive substring match
      - /regex/: regex search (case-insensitive)
    """
    if item.startswith("/") and item.endswith("/"):
        pattern = item[1:-1]
        return 1 if re.search(pattern, recall_text, re.IGNORECASE) else 0
    return 1 if item.lower() in recall_text.lower() else 0


def run_trace(trace_path, base_data_dir, results_dir):
    with open(trace_path) as f:
        trace = json.load(f)

    trace_id = trace["trace_id"]
    turns = trace["turns"]
    queries = trace["recall_queries"]

    data_dir = os.path.join(base_data_dir, trace_id)
    adapter.init(data_dir)

    # Validate quality gates
    n_turns = len(turns)
    assert n_turns >= 15, f"[{trace_id}] Needs >= 15 turns, got {n_turns}"

    eval_turns = [t for t in turns if t.get("eval_marker")]
    if eval_turns:
        assert len(eval_turns) / n_turns <= 0.30, \
            f"[{trace_id}] Eval turns fraction {len(eval_turns)/n_turns:.2f} > 0.30"
        positions = [i for i, t in enumerate(turns) if t.get("eval_marker")]
        spread = (max(positions) - min(positions)) / (n_turns - 1)
        assert spread >= 0.60, f"[{trace_id}] Eval spread {spread:.2f} < 0.60"

    assert len(queries) >= 5, f"[{trace_id}] Needs >= 5 recall queries"

    # Replay turns
    for i, turn in enumerate(turns):
        msgs = turn.get("messages", [])
        if not msgs:
            continue
        ctx = build_context(msgs)
        adapter.memorize(ctx)

    # Score recall queries
    query_results = []
    all_item_scores = []

    for q in queries:
        query_str = q["query"]
        expected = q["expected_information"]
        recall_result = adapter.recall(query_str)
        recall_text = flatten_recall(recall_result)

        item_scores = [score_item(item, recall_text) for item in expected]
        coverage = sum(item_scores) / len(item_scores) if item_scores else 0.0
        success = 1 if all(s == 1 for s in item_scores) else 0

        query_results.append({
            "query": query_str,
            "expected_information": expected,
            "item_scores": item_scores,
            "support_coverage": coverage,
            "query_success": success,
            "recall_snippet": recall_text[:500],
        })
        all_item_scores.extend(item_scores)

    overall_coverage = sum(all_item_scores) / len(all_item_scores) if all_item_scores else 0.0
    overall_success = sum(q["query_success"] for q in query_results) / len(query_results)

    result = {
        "trace_id": trace_id,
        "description": trace.get("description", ""),
        "n_turns": n_turns,
        "n_queries": len(queries),
        "scores": {
            "support_coverage": round(overall_coverage, 4),
            "query_success": round(overall_success, 4),
        },
        "query_results": query_results,
    }

    os.makedirs(results_dir, exist_ok=True)
    out_path = os.path.join(results_dir, f"result_{trace_id}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[{trace_id}] coverage={overall_coverage:.3f}  success={overall_success:.3f}  -> {out_path}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--results-dir", default=str(WORKTRACES / "traces" / "results"))
    args = parser.parse_args()

    run_trace(args.trace, args.data_dir, args.results_dir)


if __name__ == "__main__":
    main()

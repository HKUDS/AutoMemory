#!/usr/bin/env python3
"""
Rescore traces against already-stored memory files without re-memorizing.
Reads HEALTH.md and health_log.jsonl from data_dirs, applies updated query matchers.
"""
import json
import os
import re
import sys
from pathlib import Path

WORKTRACES = Path(__file__).parent
DATA_DIRS = WORKTRACES / "traces" / "data_dirs"


def read_file(path):
    if os.path.exists(path):
        with open(path) as f:
            return f.read()
    return ""


def flatten_recall(health_content, log_content, query):
    parts = [health_content, log_content]
    if query and log_content:
        for line in log_content.strip().split("\n"):
            if not line.strip():
                continue
            if query.lower() in line.lower():
                try:
                    parts.append(json.dumps(json.loads(line)))
                except json.JSONDecodeError:
                    parts.append(line)
    return "\n".join(parts)


def score_item(item, recall_text):
    if item.startswith("/") and item.endswith("/"):
        pattern = item[1:-1]
        return 1 if re.search(pattern, recall_text, re.IGNORECASE) else 0
    return 1 if item.lower() in recall_text.lower() else 0


def rescore_trace(trace_path, data_dir_base):
    with open(trace_path) as f:
        trace = json.load(f)

    trace_id = trace["trace_id"]
    data_dir = data_dir_base / trace_id
    health_path = data_dir / "HEALTH.md"
    log_path = data_dir / "health_log.jsonl"

    health_content = read_file(health_path)
    log_content = read_file(log_path)

    queries = trace["recall_queries"]
    query_results = []
    all_item_scores = []

    for q in queries:
        query_str = q["query"]
        expected = q["expected_information"]
        recall_text = flatten_recall(health_content, log_content, query_str)

        item_scores = [score_item(item, recall_text) for item in expected]
        coverage = sum(item_scores) / len(item_scores) if item_scores else 0.0
        success = 1 if all(s == 1 for s in item_scores) else 0

        query_results.append({
            "query": query_str,
            "expected_information": expected,
            "item_scores": item_scores,
            "support_coverage": round(coverage, 4),
            "query_success": success,
            "failed_items": [e for e, s in zip(expected, item_scores) if s == 0],
        })
        all_item_scores.extend(item_scores)

    overall_coverage = sum(all_item_scores) / len(all_item_scores) if all_item_scores else 0.0
    overall_success = sum(q["query_success"] for q in query_results) / len(query_results)

    print(f"\n=== {trace_id} ===")
    print(f"support_coverage: {overall_coverage:.4f}  query_success: {overall_success:.4f}")
    for r in query_results:
        status = "PASS" if r["query_success"] else "FAIL"
        print(f"  [{status}] {r['query'][:60]}")
        if r["failed_items"]:
            for fi in r["failed_items"]:
                print(f"    MISS: {fi}")

    return {
        "trace_id": trace_id,
        "scores": {
            "support_coverage": round(overall_coverage, 4),
            "query_success": round(overall_success, 4),
        },
        "query_results": query_results,
    }


if __name__ == "__main__":
    trace_files = sys.argv[1:] if len(sys.argv) > 1 else []
    if not trace_files:
        trace_files = sorted((WORKTRACES / "traces" / "data").glob("*.json"))

    results = []
    for tf in trace_files:
        results.append(rescore_trace(Path(tf), DATA_DIRS))

    all_cov = [r["scores"]["support_coverage"] for r in results]
    all_suc = [r["scores"]["query_success"] for r in results]
    print(f"\n=== AGGREGATE ===")
    print(f"support_coverage: {sum(all_cov)/len(all_cov):.4f}")
    print(f"query_success:    {sum(all_suc)/len(all_suc):.4f}")

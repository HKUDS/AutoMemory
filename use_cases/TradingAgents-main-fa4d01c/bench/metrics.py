#!/usr/bin/env python3
"""Generate the StockBench memory behavior report (English).

Writes ``bench/results.md`` only. This is not a trading-performance report:
it summarizes memory coverage and qualitative evidence from existing run artifacts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BENCH_DIR = Path(__file__).resolve().parent
STOCKBENCH = BENCH_DIR / "stockbench"
CONDITIONS = ("memory-on", "memory-off")

MEMORY_AGENT_REL = Path("stockbench/runs/memory-on/shared-memory/agent")


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def truncate(text: str, limit: int = 320) -> str:
    s = text.replace("\n", " ").strip()
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _agent_artifact_coverage(agent_root: Path) -> list[dict[str, Any]]:
    """Per-artifact measures with explicit units (items / chars / files)."""
    rows: list[dict[str, Any]] = []
    refl = agent_root / "reflections"
    if refl.exists():
        for p in sorted(refl.glob("*.jsonl")):
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            rows.append({"store": "reflections", "path": str(p.relative_to(BENCH_DIR)), "measure": f"{n} items"})
    tk = agent_root / "ticker_knowledge"
    if tk.exists():
        for p in sorted(tk.glob("*.md")):
            chars = len(p.read_text(encoding="utf-8"))
            rows.append(
                {
                    "store": "ticker_knowledge",
                    "path": str(p.relative_to(BENCH_DIR)),
                    "measure": f"{chars} chars",
                }
            )
    rec = agent_root / "recommendations"
    if rec.exists():
        for p in sorted(rec.glob("*.jsonl")):
            n = sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())
            rows.append({"store": "recommendations", "path": str(p.relative_to(BENCH_DIR)), "measure": f"{n} items"})
    macro = agent_root / "macro_notes.md"
    if macro.exists():
        chars = len(macro.read_text(encoding="utf-8"))
        rows.append(
            {
                "store": "macro_notes",
                "path": str(macro.relative_to(BENCH_DIR)),
                "measure": f"{chars} chars",
            }
        )
    ac = agent_root / "analyst_cache"
    if ac.exists():
        n_files = sum(1 for _ in ac.rglob("*.json"))
        rows.append({"store": "analyst_cache", "path": str(ac.relative_to(BENCH_DIR)), "measure": f"{n_files} files"})
    return rows


def _build_decision_comparison_table() -> str:
    """Optional behavioral table: same (date, symbol) memory-on vs memory-off rating token."""
    on_path = STOCKBENCH / "runs" / "memory-on" / "session-1" / "daily_decisions.json"
    off_path = STOCKBENCH / "runs" / "memory-off" / "session-1" / "daily_decisions.json"
    on_list = read_json(on_path, [])
    off_list = read_json(off_path, [])
    if not isinstance(on_list, list) or not isinstance(off_list, list):
        return "_No paired `daily_decisions.json` files found or invalid JSON._\n"

    def key(row: dict[str, Any]) -> tuple[str, str]:
        return (str(row.get("date", "")), str(row.get("symbol", "")).upper())

    def rating_token(row: dict[str, Any]) -> str:
        r = str(row.get("rating") or row.get("processed_signal") or "").strip().upper().split()
        return r[0] if r else "—"

    on_map = {key(r): r for r in on_list if key(r)[0] and key(r)[1]}
    off_map = {key(r): r for r in off_list if key(r)[0] and key(r)[1]}
    keys = sorted(set(on_map) | set(off_map))
    if not keys:
        return "_No decision rows to compare._\n"

    lines = [
        "| date | symbol | memory-on (rating) | memory-off (rating) |",
        "| --- | --- | --- | --- |",
    ]
    for ds, sym in keys[: min(40, len(keys))]:
        ro, rf = on_map.get((ds, sym)), off_map.get((ds, sym))
        lines.append(
            f"| {ds} | {sym} | {rating_token(ro) if ro else '—'} | {rating_token(rf) if rf else '—'} |"
        )
    if len(keys) > 40:
        lines.append(f"\n_Table truncated to 40 rows ({len(keys)} total decision rows)._")
    lines.append("")
    lines.append(
        "_Ratings are coarse labels from the simulator (`daily_decisions.json`); "
        "differences illustrate behavioral divergence under shared vs isolated memory, not performance._"
    )
    return "\n".join(lines) + "\n"


def _extract_msft_lesson_quote(full_states_path: Path) -> str:
    data = read_json(full_states_path, {})
    if not isinstance(data, dict):
        return ""
    debate = data.get("investment_debate_state") or {}
    if isinstance(debate, dict):
        cr = debate.get("current_response") or ""
        if isinstance(cr, str) and "Microsoft" in cr and "mistake" in cr.lower():
            return truncate(cr, 400)
    # Fallback: search nested strings
    blob = json.dumps(data, ensure_ascii=False)
    if "Microsoft" in blob and "mistake" in blob.lower():
        return "_Found MSFT/mistake references in full state log (see file)._"
    return ""


def _extract_memory_quote(full_states_path: Path, required_terms: tuple[str, ...]) -> str:
    data = read_json(full_states_path, {})
    if not isinstance(data, dict):
        return ""

    candidates: list[str] = []
    debate = data.get("investment_debate_state") or {}
    if isinstance(debate, dict):
        for key in ("current_response", "judge_decision"):
            value = debate.get(key)
            if isinstance(value, str):
                candidates.append(value)

    for key in ("investment_plan", "final_trade_decision", "trader_investment_decision"):
        value = data.get(key)
        if isinstance(value, str):
            candidates.append(value)

    lowered_terms = tuple(term.lower() for term in required_terms)
    for text in candidates:
        haystack = text.lower()
        if all(term in haystack for term in lowered_terms):
            return truncate(text, 430)
    return ""


def generate_report() -> str:
    snap_path = STOCKBENCH / "runs" / "memory-on" / "session-1" / "memory_snapshot.json"
    snap = read_json(snap_path, {})
    agent_root = BENCH_DIR / MEMORY_AGENT_REL

    lines: list[str] = [
        "# StockBench memory behavior report",
        "",
        "This report is generated by `bench/metrics.py` from artifacts under `bench/stockbench/runs/`. "
        "It documents **whether TradingAgents accumulates, persists, recalls, and uses memory** in the "
        "StockBench offline harness. It does **not** evaluate realized trading performance, returns, or "
        "directional accuracy.",
        "",
        "## Scope and limitations",
        "",
        "StockBench can support numeric backtests, but TradingAgents primarily emits narrative outputs plus "
        "a coarse five-way rating label rather than explicit trade sizing or execution prices. The "
        "TradingAgents paper does not define numeric trade execution targets. Therefore this benchmark "
        "**does not claim** to measure P&L or predictive skill; it only inspects memory-related signals "
        "and artifacts.",
        "",
    ]

    # Memory coverage from memory_snapshot.json
    lines.extend(
        [
            "## Memory-on snapshot (session-1)",
            "",
            f"- Source: `{snap_path.relative_to(BENCH_DIR)}`",
        ]
    )
    if snap.get("dry_run"):
        lines.append("- _Dry run only — execute the bench to populate snapshots._")
    else:
        rc = snap.get("reflection_counts") or {}
        kc_n = int(snap.get("kc_char_length") or 0)
        lines.extend(
            [
                f"- `kc_in_prompt`: **{snap.get('kc_in_prompt', False)}**",
                f"- `kc_char_length`: **{kc_n} chars**",
                f"- `decisions_log_count` (recent window): **{snap.get('decisions_log_count', 0)}** items",
                f"- `portfolio_manager_memory_count`: **{snap.get('portfolio_manager_memory_count', 0)}** items",
                "- `reflection_counts` (JSONL lines per bucket, **items** each):",
            ]
        )
        for bucket in sorted(rc.keys()):
            lines.append(f"  - `{bucket}`: **{rc[bucket]}** items")
        lines.append(f"- Analyst cache: hit flag **{snap.get('analyst_cache_hit', False)}** (hits/misses in snapshot).")

    lines.extend(["", "## Persistent store coverage (memory-on shared root)", ""])

    if agent_root.is_dir():
        cov = _agent_artifact_coverage(agent_root)
        lines.append(
            "_Units: **items** = non-empty JSONL lines (one memory record per line); "
            "**chars** = UTF-8 character count in Markdown; "
            "**files** = JSON leaf files under analyst cache._"
        )
        lines.append("")
        lines.append("| area | relative path | measure |")
        lines.append("| --- | --- | --- |")
        for row in cov:
            lines.append(f"| {row['store']} | `{row['path']}` | {row['measure']} |")
    else:
        lines.append(f"_Shared memory agent directory not found at `{agent_root.relative_to(BENCH_DIR)}`._")

    # Case study: AAPL full state MSFT lesson
    full_log = (
        STOCKBENCH
        / "runs"
        / "memory-on"
        / "session-1"
        / "tradingagents-results"
        / "AAPL"
        / "TradingAgentsStrategy_logs"
        / "full_states_log_2025-06-09.json"
    )
    lines.extend(
        [
            "",
            "## Case study 1: cross-ticker lesson in judge reasoning (AAPL, 2025-06-09)",
            "",
            f"Full-state log: `{full_log.relative_to(BENCH_DIR)}`",
            "",
        ]
    )
    q = _extract_msft_lesson_quote(full_log) if full_log.exists() else ""
    if q:
        lines.extend(["> " + q.replace("\n", "\n> "), ""])
    else:
        lines.append("_Could not extract a short quote; open the JSON for `investment_debate_state` text._\n")

    # Case study: AMZN applies the same historical MSFT lesson to a different ticker.
    amzn_log = (
        STOCKBENCH
        / "runs"
        / "memory-on"
        / "session-1"
        / "tradingagents-results"
        / "AMZN"
        / "TradingAgentsStrategy_logs"
        / "full_states_log_2025-06-27.json"
    )
    lines.extend(
        [
            "## Case study 2: cross-ticker lesson in judge reasoning (AMZN, 2025-06-27)",
            "",
            f"Full-state log: `{amzn_log.relative_to(BENCH_DIR)}`",
            "",
        ]
    )
    q = _extract_memory_quote(amzn_log, ("microsoft", "institutional accumulation")) if amzn_log.exists() else ""
    if q:
        lines.extend(["> " + q.replace("\n", "\n> "), ""])
    else:
        lines.append("_Could not extract a short quote; open the JSON for `investment_debate_state` text._\n")

    # Case study: MSFT explicitly reuses its own prior mistake as a decision lens.
    msft_log = (
        STOCKBENCH
        / "runs"
        / "memory-on"
        / "session-1"
        / "tradingagents-results"
        / "MSFT"
        / "TradingAgentsStrategy_logs"
        / "full_states_log_2025-06-27.json"
    )
    lines.extend(
        [
            "## Case study 3: prior mistake reused as a decision lens (MSFT, 2025-06-27)",
            "",
            f"Full-state log: `{msft_log.relative_to(BENCH_DIR)}`",
            "",
        ]
    )
    q = _extract_memory_quote(msft_log, ("past mistakes", "incorrect sell decision")) if msft_log.exists() else ""
    if q:
        lines.extend(["> " + q.replace("\n", "\n> "), ""])
    else:
        lines.append("_Could not extract a short quote; open the JSON for `investment_debate_state` text._\n")

    # Case study: knowledge card + recommendations continuity
    kc = agent_root / "ticker_knowledge" / "AAPL.md"
    rec = agent_root / "recommendations" / "AAPL.jsonl"
    lines.extend(
        [
            "## Case study 4: knowledge card and recommendations continuity (AAPL)",
            "",
        ]
    )
    if kc.exists():
        head = kc.read_text(encoding="utf-8").splitlines()[:12]
        lines.append(f"**`{kc.relative_to(BENCH_DIR)}`** (first lines):\n")
        lines.append("```")
        lines.extend(head)
        lines.append("```\n")
    else:
        lines.append(f"_Missing `{kc.relative_to(BENCH_DIR)}`._\n")

    if rec.exists():
        tail_lines = [ln for ln in rec.read_text(encoding="utf-8").splitlines() if ln.strip()][-3:]
        lines.append(f"**`{rec.relative_to(BENCH_DIR)}`** (last records):\n")
        lines.append("```")
        for ln in tail_lines:
            lines.append(truncate(ln, 200))
        lines.append("```\n")
    else:
        lines.append(f"_Missing `{rec.relative_to(BENCH_DIR)}`._\n")

    lines.extend(
        [
            "## Memory-on vs memory-off decisions (behavioral)",
            "",
            "Side-by-side **rating tokens** from `daily_decisions.json` (no return math):",
            "",
            _build_decision_comparison_table(),
            "",
            "---",
            "",
            "_End of report._",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=BENCH_DIR / "results.md",
        help="Output markdown path (default: bench/results.md)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    out = args.out.resolve()
    text = generate_report()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    try:
        display = out.relative_to(BENCH_DIR)
    except ValueError:
        display = out
    print(f"[metrics] wrote {display}")


if __name__ == "__main__":
    main()

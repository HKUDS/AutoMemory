#!/usr/bin/env python3
"""Refine trace runner for TradingAgents memory system.

Reusable across all phases. Directly imports component write/read methods
as declared in implemented.md. No adapter layer.

Usage:
    python run_traces.py [--trace TRACE_ID | --all] [--traces-dir DIR] [--output FILE]

Outputs structured JSON: list of {trace_id, eval_mode, queries/checks, scores}.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Repo root and path setup
# ---------------------------------------------------------------------------

_RUNNER_DIR = Path(__file__).resolve().parent  # worktraces/
_REPO_ROOT = _RUNNER_DIR.parents[1]           # TradingAgents/

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TRACES_DIR = _RUNNER_DIR / "traces"

# ---------------------------------------------------------------------------
# LLM setup (loaded once)
# ---------------------------------------------------------------------------

def _build_llm():
    """Build LLM from .env — returns None if not available."""
    env_file = _REPO_ROOT / ".env"
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(str(env_file))
        except ImportError:
            pass

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("[runner] WARNING: OPENROUTER_API_KEY not set — LLM components will fail", file=sys.stderr)
        return None

    try:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="qwen/qwen3.5-flash-02-23",
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            temperature=0,
        )
    except Exception as exc:
        print(f"[runner] WARNING: Could not build LLM: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Memory path patching for data isolation
# ---------------------------------------------------------------------------

def _patch_memory_paths(base_dir: Path) -> None:
    """Redirect all memory module path variables to base_dir for isolation."""
    from tradingagents.agents.utils import memory_stores as ms_mod
    from tradingagents.agents.utils import memory as mem_mod

    agent_dir = base_dir / "agent"
    user_dir = base_dir / "user"

    ms_mod._MEMORY_ROOT = base_dir
    ms_mod._AGENT_DIR = agent_dir
    ms_mod._USER_DIR = user_dir
    ms_mod._macro_refresh_done = False

    # Class-level paths (used via cls._BASE / cls._PATH)
    ms_mod.AnalystCache._BASE = agent_dir / "analyst_cache"
    ms_mod.KnowledgeCard._BASE = agent_dir / "ticker_knowledge"
    ms_mod.DecisionsLog._BASE = agent_dir / "recommendations"
    ms_mod.MacroNotes._PATH = agent_dir / "macro_notes.md"

    # Module-level var used in FinancialSituationMemory.__init__
    mem_mod._REFLECTIONS_DIR = agent_dir / "reflections"


# ---------------------------------------------------------------------------
# Turn execution dispatch
# ---------------------------------------------------------------------------

def _execute_turn(turn: Dict, llm, base_dir: Path) -> None:
    """Execute one trace turn: call the specified write method."""
    component = turn["component"]
    args = turn.get("args", {})

    if component == "DecisionsLog":
        from tradingagents.agents.utils.memory_stores import DecisionsLog
        DecisionsLog.append(args["ticker"], args["record"])

    elif component == "KnowledgeCardWriter":
        if llm is None:
            raise RuntimeError("LLM required for KnowledgeCardWriter but not available")
        from tradingagents.graph.self_reflection import KnowledgeCardWriter
        writer = KnowledgeCardWriter(llm)
        writer.merge(args["state"], args["ticker"], args["trade_date"])

    elif component == "SelfReflector":
        if llm is None:
            raise RuntimeError("LLM required for SelfReflector but not available")
        from tradingagents.graph.self_reflection import SelfReflector
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        reflector = SelfReflector(llm)
        bucket_names = [
            "bull_memory", "bear_memory", "trader_memory",
            "invest_judge_memory", "portfolio_manager_memory",
        ]
        memories = {name: FinancialSituationMemory(name) for name in bucket_names}
        reflector.run(args["state"], memories)

    elif component == "MacroRefresher":
        if llm is None:
            raise RuntimeError("LLM required for MacroRefresher but not available")
        import tradingagents.agents.utils.memory_stores as ms_mod
        # Reset per-process flag so multiple refresh calls work within one trace
        ms_mod._macro_refresh_done = False

        mock_news = args.get("mock_news", "Global markets saw moderate moves this week.")

        def _get_global_news(curr_date, look_back_days, limit):
            return mock_news

        from tradingagents.graph.self_reflection import MacroRefresher
        refresher = MacroRefresher(llm, _get_global_news)
        refresher.refresh_if_stale(args["trade_date"])

    elif component == "MacroNotes":
        # Direct write_section — no LLM, for idempotency/cap tests
        from tradingagents.agents.utils.memory_stores import MacroNotes
        MacroNotes.write_section(args["iso_week"], args["bullets"], args["trade_date"])

    elif component == "AnalystCache":
        from tradingagents.agents.utils.memory_stores import AnalystCache
        AnalystCache.write(**args)

    elif component == "UserContext_seed":
        # Fixture-only: seed portfolio.json and/or profile.md
        user_dir = base_dir / "user"
        user_dir.mkdir(parents=True, exist_ok=True)
        if "portfolio" in args:
            (user_dir / "portfolio.json").write_text(
                json.dumps(args["portfolio"], ensure_ascii=False), encoding="utf-8"
            )
        if "profile" in args:
            (user_dir / "profile.md").write_text(args["profile"], encoding="utf-8")

    else:
        print(f"[runner] WARNING: unknown component '{component}' — skipping turn", file=sys.stderr)


# ---------------------------------------------------------------------------
# Query execution dispatch
# ---------------------------------------------------------------------------

def _execute_query(query: Dict) -> Any:
    """Execute one recall query and return raw output."""
    component = query["component"]
    args = query.get("args", {})

    if component == "DecisionsLog":
        from tradingagents.agents.utils.memory_stores import DecisionsLog
        return DecisionsLog.read_last_n(args["ticker"], args.get("n", 3))

    elif component == "KnowledgeCard":
        from tradingagents.agents.utils.memory_stores import KnowledgeCard
        return KnowledgeCard.read(args["ticker"])

    elif component == "BM25Reflections":
        from tradingagents.agents.utils.memory import FinancialSituationMemory
        mem = FinancialSituationMemory(args["name"])
        return mem.get_memories(args["query"], n_matches=args.get("n_matches", 2))

    elif component == "MacroNotes":
        from tradingagents.agents.utils.memory_stores import MacroNotes
        return MacroNotes.read_recent(args.get("n_weeks", 2))

    elif component == "UserContext":
        from tradingagents.agents.utils.memory_stores import UserContext
        return UserContext.read(args.get("ticker", ""))

    else:
        print(f"[runner] WARNING: unknown query component '{component}'", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _to_str(output: Any) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return json.dumps(output, ensure_ascii=False, default=str)


def _score_item(output_str: str, item: Dict) -> bool:
    matcher = item.get("type", "substring")
    value = item.get("value", "")
    case_sensitive = item.get("case_sensitive", False)

    if matcher == "substring":
        if case_sensitive:
            return value in output_str
        return value.lower() in output_str.lower()

    elif matcher == "not_substring":
        if case_sensitive:
            return value not in output_str
        return value.lower() not in output_str.lower()

    elif matcher == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        return bool(re.search(value, output_str, flags))

    elif matcher == "none_or_empty":
        return output_str == "" or output_str == "[]" or output_str == "null"

    elif matcher == "non_empty":
        return output_str.strip() not in ("", "[]", "null", "{}")

    elif matcher == "count_exact":
        # Count regex occurrences and compare to expected count.
        # item must contain "value" (regex pattern) and "count" (expected int).
        pattern = value
        expected = int(item.get("count", 1))
        flags = 0 if case_sensitive else re.IGNORECASE
        found = len(re.findall(pattern, output_str, flags))
        return found == expected

    elif matcher == "count_max":
        # Count regex occurrences and verify <= max_count.
        # item must contain "value" (regex pattern) and "max_count" (int).
        pattern = value
        max_count = int(item.get("max_count", 1))
        flags = 0 if case_sensitive else re.IGNORECASE
        found = len(re.findall(pattern, output_str, flags))
        return found <= max_count

    return False


def _score_qa_query(output: Any, expected_information: List[Dict]) -> Tuple[float, float]:
    output_str = _to_str(output)
    if not expected_information:
        return 0.0, 0.0
    hits = sum(_score_item(output_str, item) for item in expected_information)
    total = len(expected_information)
    support_coverage = hits / total
    query_success = 1.0 if hits == total else 0.0
    return support_coverage, query_success


# ---------------------------------------------------------------------------
# Trace replay: qa mode
# ---------------------------------------------------------------------------

def _replay_qa(trace: Dict, llm, data_dir: Path) -> Dict:
    trace_id = trace["trace_id"]
    trace_data_dir = data_dir / trace_id
    # Clean before each run to enforce per-trace memory isolation (protocol requirement).
    if trace_data_dir.exists():
        shutil.rmtree(trace_data_dir)
    trace_data_dir.mkdir(parents=True, exist_ok=True)

    _patch_memory_paths(trace_data_dir)

    # Execute turns
    for i, turn in enumerate(trace.get("turns", [])):
        try:
            _execute_turn(turn, llm, trace_data_dir)
        except Exception as exc:
            print(f"[runner] ERROR in {trace_id} turn {i} ({turn.get('component', '?')}): {exc}", file=sys.stderr)

    # Execute recall queries and score
    query_results = []
    all_sc = []
    all_qs = []

    for q in trace.get("recall_queries", []):
        try:
            raw = _execute_query(q)
        except Exception as exc:
            print(f"[runner] ERROR in {trace_id} query ({q.get('component', '?')}): {exc}", file=sys.stderr)
            raw = None

        sc, qs = _score_qa_query(raw, q.get("expected_information", []))
        all_sc.append(sc)
        all_qs.append(qs)

        query_results.append({
            "query_label": q.get("label", ""),
            "component": q.get("component", ""),
            "raw_output_preview": _to_str(raw)[:300],
            "support_coverage": sc,
            "query_success": qs,
        })

    n = len(all_sc)
    agg_sc = sum(all_sc) / n if n else 0.0
    agg_qs = sum(all_qs) / n if n else 0.0

    return {
        "trace_id": trace_id,
        "eval_mode": "qa",
        "memory_path": trace.get("memory_path", ""),
        "query_results": query_results,
        "support_coverage": round(agg_sc, 4),
        "query_success": round(agg_qs, 4),
    }


# ---------------------------------------------------------------------------
# Trace replay: hit_rate mode
# ---------------------------------------------------------------------------

def _replay_hit_rate(trace: Dict, llm, data_dir: Path) -> Dict:
    trace_id = trace["trace_id"]
    trace_data_dir = data_dir / trace_id
    # Clean before each run to enforce per-trace memory isolation (protocol requirement).
    if trace_data_dir.exists():
        shutil.rmtree(trace_data_dir)
    trace_data_dir.mkdir(parents=True, exist_ok=True)

    _patch_memory_paths(trace_data_dir)

    hits = 0
    total = 0
    check_results = []

    for i, check in enumerate(trace.get("checks", [])):
        # Write step (optional — some checks test miss without prior write)
        if check.get("write_input"):
            try:
                _execute_turn({"component": "AnalystCache", "args": check["write_input"]}, llm, trace_data_dir)
            except Exception as exc:
                print(f"[runner] ERROR in {trace_id} check {i} write: {exc}", file=sys.stderr)

        # Read step
        read_args = check["read_input"]
        try:
            from tradingagents.agents.utils.memory_stores import AnalystCache
            result = AnalystCache.check(**read_args)
        except Exception as exc:
            print(f"[runner] ERROR in {trace_id} check {i} read: {exc}", file=sys.stderr)
            result = None

        expected_none = check.get("expected_none", False)
        expected_value = check.get("expected_output", "")
        hit = False
        if expected_none:
            hit = result is None
        else:
            result_str = _to_str(result)
            hit = expected_value.lower() in result_str.lower() if expected_value else result is not None

        hits += int(hit)
        total += 1
        check_results.append({
            "check_idx": i,
            "label": check.get("label", ""),
            "hit": hit,
            "result_preview": _to_str(result)[:200],
        })

    hit_rate = hits / total if total > 0 else 0.0

    return {
        "trace_id": trace_id,
        "eval_mode": "hit_rate",
        "memory_path": trace.get("memory_path", ""),
        "check_results": check_results,
        "hits": hits,
        "total": total,
        "hit_rate": round(hit_rate, 4),
    }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_trace(trace_path: Path, llm, data_dir: Path) -> Dict:
    with open(trace_path, encoding="utf-8") as f:
        trace = json.load(f)

    eval_mode = trace.get("eval_mode", "qa")
    print(f"[runner] Running {trace['trace_id']} ({eval_mode}) ...", file=sys.stderr)

    if eval_mode == "hit_rate":
        return _replay_hit_rate(trace, llm, data_dir)
    else:
        return _replay_qa(trace, llm, data_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", help="Single trace ID to run (no .json suffix needed)")
    parser.add_argument("--all", action="store_true", help="Run all traces in traces-dir")
    parser.add_argument("--traces-dir", default=str(_TRACES_DIR), help="Directory containing trace JSON files")
    parser.add_argument("--output", default="-", help="Output JSON path (- for stdout)")
    parser.add_argument("--workdir", default=str(_RUNNER_DIR / "_run_data"), help="Isolated data directory per trace")
    args = parser.parse_args()

    traces_dir = Path(args.traces_dir)
    data_dir = Path(args.workdir)
    data_dir.mkdir(parents=True, exist_ok=True)

    llm = _build_llm()

    # Collect trace files
    if args.trace:
        tid = args.trace if args.trace.endswith(".json") else args.trace + ".json"
        trace_files = [traces_dir / tid]
    elif args.all:
        trace_files = sorted(traces_dir.glob("*.json"))
    else:
        parser.print_help()
        sys.exit(1)

    results = []
    for tf in trace_files:
        if not tf.exists():
            print(f"[runner] ERROR: trace file not found: {tf}", file=sys.stderr)
            continue
        result = run_trace(tf, llm, data_dir)
        results.append(result)

    output_json = json.dumps(results, indent=2, ensure_ascii=False)

    if args.output == "-":
        print(output_json)
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")
        print(f"[runner] Results written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()

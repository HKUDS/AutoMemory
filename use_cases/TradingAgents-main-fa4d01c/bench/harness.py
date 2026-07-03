#!/usr/bin/env python3
"""Run the TradingAgents StockBench memory benchmark.

TradingAgents default package behavior is left untouched: data routing and memory
roots are patched only inside this process and restored after each session.
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
SHOWCASES = ("stockbench",)
CONDITIONS = ("memory-on", "memory-off")
MEMORY_BUCKETS = (
    "bull_memory",
    "bear_memory",
    "trader_memory",
    "invest_judge_memory",
    "portfolio_manager_memory",
)
ANALYST_TYPES = ("market", "social", "news", "fundamentals")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
except ImportError:
    pass


_started_at = time.time()


def progress(message: str, **details: Any) -> None:
    elapsed = int(time.time() - _started_at)
    attrs = ", ".join(f"{key}={value}" for key, value in details.items() if value is not None)
    suffix = f" ({attrs})" if attrs else ""
    print(f"[bench +{elapsed}s] {message}{suffix}", flush=True)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def truncate(text: Any, limit: int = 4000) -> str:
    if text is None:
        return ""
    value = str(text)
    if len(value) <= limit:
        return value
    return value[: limit - 14] + "\n...[truncated]"


def json_safe(value: Any, limit: int = 4000) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return truncate(value, limit) if isinstance(value, str) else value
    if isinstance(value, dict):
        return {str(key): json_safe(val, limit) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item, limit) for item in value]
    content = getattr(value, "content", None)
    if content is not None:
        return {"type": type(value).__name__, "content": truncate(content, limit)}
    return truncate(repr(value), limit)


def load_tasks(showcase: str) -> dict[str, Any]:
    path = BENCH_DIR / showcase / "tasks.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing tasks file: {path}")
    tasks = read_json(path)
    sessions = tasks.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError(f"{path} must contain a non-empty sessions list")
    if not tasks.get("symbols"):
        raise ValueError(f"{path} must define a non-empty symbols list for StockBench")
    return tasks


def select_showcases(value: str) -> list[str]:
    if value in ("all", "stockbench"):
        return ["stockbench"]
    raise ValueError(f"Unknown showcase '{value}'. Expected one of: all, stockbench")


def select_conditions(value: str) -> list[str]:
    if value == "all":
        return list(CONDITIONS)
    if value not in CONDITIONS:
        raise ValueError(f"Unknown condition '{value}'. Expected one of: all, {', '.join(CONDITIONS)}")
    return [value]


@dataclass
class CacheProbe:
    hits: dict[str, int]
    misses: dict[str, int]

    @classmethod
    def empty(cls) -> "CacheProbe":
        return cls(hits={name: 0 for name in ANALYST_TYPES}, misses={name: 0 for name in ANALYST_TYPES})


@contextmanager
def analyst_cache_probe() -> Iterable[CacheProbe]:
    from tradingagents.agents.utils.memory_stores import AnalystCache

    probe = CacheProbe.empty()
    original_check_descriptor = AnalystCache.__dict__["check"]

    def checked(cls, ticker: str, trade_date: str, analyst_type: str):
        original_check = original_check_descriptor.__get__(cls, cls)
        result = original_check(ticker, trade_date, analyst_type)
        key = analyst_type if analyst_type in probe.hits else "unknown"
        target = probe.hits if result else probe.misses
        target[key] = target.get(key, 0) + 1
        return result

    AnalystCache.check = classmethod(checked)
    try:
        yield probe
    finally:
        AnalystCache.check = original_check_descriptor


def memory_root_for(
    showcase: str,
    condition: str,
    session_dir: Path,
    condition_config: dict[str, Any],
    run_root: Path,
) -> Path:
    if condition == "memory-on":
        configured = condition_config.get("shared_memory_root")
        if configured:
            return (BENCH_DIR / configured).resolve() if not Path(configured).is_absolute() else Path(configured)
        return run_root / showcase / "runs" / "memory-on" / "shared-memory"
    return session_dir / "memory"


def build_graph_config(session_dir: Path, condition_config: dict[str, Any]) -> dict[str, Any]:
    from tradingagents.default_config import DEFAULT_CONFIG

    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(condition_config.get("tradingagents_config", {}))
    config["results_dir"] = str(session_dir / "tradingagents-results")
    config["data_cache_dir"] = str(BENCH_DIR / ".cache")
    return config


@contextmanager
def patched_memory_paths(base_dir: Path):
    """Redirect persistent memory stores to a benchmark-owned directory."""

    from tradingagents.agents.utils import memory as mem_mod
    from tradingagents.agents.utils import memory_stores as ms_mod

    saved = {
        "ms_memory_root": ms_mod._MEMORY_ROOT,
        "ms_agent_dir": ms_mod._AGENT_DIR,
        "ms_user_dir": ms_mod._USER_DIR,
        "ms_macro_refresh_done": ms_mod._macro_refresh_done,
        "analyst_base": ms_mod.AnalystCache._BASE,
        "kc_base": ms_mod.KnowledgeCard._BASE,
        "decisions_base": ms_mod.DecisionsLog._BASE,
        "macro_path": ms_mod.MacroNotes._PATH,
        "reflections_dir": mem_mod._REFLECTIONS_DIR,
    }

    agent_dir = base_dir / "agent"
    user_dir = base_dir / "user"
    ms_mod._MEMORY_ROOT = base_dir
    ms_mod._AGENT_DIR = agent_dir
    ms_mod._USER_DIR = user_dir
    ms_mod._macro_refresh_done = False
    ms_mod.AnalystCache._BASE = agent_dir / "analyst_cache"
    ms_mod.KnowledgeCard._BASE = agent_dir / "ticker_knowledge"
    ms_mod.DecisionsLog._BASE = agent_dir / "recommendations"
    ms_mod.MacroNotes._PATH = agent_dir / "macro_notes.md"
    mem_mod._REFLECTIONS_DIR = agent_dir / "reflections"
    try:
        yield
    finally:
        ms_mod._MEMORY_ROOT = saved["ms_memory_root"]
        ms_mod._AGENT_DIR = saved["ms_agent_dir"]
        ms_mod._USER_DIR = saved["ms_user_dir"]
        ms_mod._macro_refresh_done = saved["ms_macro_refresh_done"]
        ms_mod.AnalystCache._BASE = saved["analyst_base"]
        ms_mod.KnowledgeCard._BASE = saved["kc_base"]
        ms_mod.DecisionsLog._BASE = saved["decisions_base"]
        ms_mod.MacroNotes._PATH = saved["macro_path"]
        mem_mod._REFLECTIONS_DIR = saved["reflections_dir"]


def collect_memory_snapshot(memory_root: Path, ticker: str, trade_date: str, probe: CacheProbe | None = None) -> dict[str, Any]:
    card_path = memory_root / "agent" / "ticker_knowledge" / f"{ticker}.md"
    card = card_path.read_text(encoding="utf-8") if card_path.exists() else ""
    decisions_path = memory_root / "agent" / "recommendations" / f"{ticker}.jsonl"
    decisions: list[dict[str, Any]] = []
    if decisions_path.exists():
        for line in decisions_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                decisions.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    decisions.sort(key=lambda item: item.get("trade_date", ""))
    decisions = decisions[-10:]
    reflections_dir = memory_root / "agent" / "reflections"
    reflection_counts = {
        bucket: count_jsonl(reflections_dir / f"{bucket}.jsonl")
        for bucket in MEMORY_BUCKETS
    }
    snapshot: dict[str, Any] = {
        "ticker": ticker,
        "trade_date": trade_date,
        "memory_root": str(memory_root),
        "kc_char_length": len(card),
        "kc_in_prompt": bool(card and "## Identity" in card),
        "decisions_log_count": len(decisions),
        "recent_decisions": decisions,
        "reflection_counts": reflection_counts,
        "portfolio_manager_memory_count": reflection_counts["portfolio_manager_memory"],
    }
    if probe is not None:
        snapshot["analyst_cache_hits"] = probe.hits
        snapshot["analyst_cache_misses"] = probe.misses
        snapshot["analyst_cache_hit"] = any(count > 0 for count in probe.hits.values())
    return snapshot


def write_stockbench_meta(
    session_dir: Path,
    condition: str,
    session: dict[str, Any],
    tasks: dict[str, Any],
    dry_run: bool,
) -> None:
    write_json(
        session_dir / "session-meta.json",
        {
            "showcase": "stockbench",
            "condition": condition,
            "session": session.get("order"),
            "seed": session.get("seed", session.get("order")),
            "symbols": tasks.get("symbols", []),
            "start_date": tasks.get("start_date"),
            "end_date": tasks.get("end_date"),
            "window_trading_days": tasks.get("window_trading_days"),
            "dry_run": dry_run,
        },
    )


def reset_condition_if_requested(showcase: str, condition: str, force: bool) -> None:
    if not force:
        return
    run_dir = BENCH_DIR / showcase / "runs" / condition
    if run_dir.exists():
        shutil.rmtree(run_dir)


def reset_memory_if_requested(
    showcase: str,
    condition: str,
    condition_config: dict[str, Any],
    reset_memory: bool,
) -> None:
    if not reset_memory:
        return
    if condition == "memory-on":
        memory_root = memory_root_for(
            showcase,
            condition,
            BENCH_DIR / showcase / "runs" / condition / "session-1",
            condition_config,
            BENCH_DIR,
        )
        if memory_root.exists():
            shutil.rmtree(memory_root)
        progress("reset benchmark memory", showcase=showcase, condition=condition, path=memory_root)
        return

    condition_run_dir = BENCH_DIR / showcase / "runs" / condition
    for memory_dir in condition_run_dir.glob("session-*/memory"):
        shutil.rmtree(memory_dir, ignore_errors=True)
        progress("reset benchmark memory", showcase=showcase, condition=condition, path=memory_dir)


def run_stockbench_session(
    condition: str,
    tasks: dict[str, Any],
    session: dict[str, Any],
    condition_config: dict[str, Any],
    execute: bool,
    force: bool,
    run_root: Path,
) -> None:
    order = int(session["order"])
    session_dir = run_root / "stockbench" / "runs" / condition / f"session-{order}"
    if execute and force and session_dir.exists() and condition == "memory-off":
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    write_stockbench_meta(session_dir, condition, session, tasks, dry_run=not execute)

    memory_root = memory_root_for("stockbench", condition, session_dir, condition_config, run_root)
    if execute and condition == "memory-off" and force and memory_root.exists():
        shutil.rmtree(memory_root)
    memory_root.mkdir(parents=True, exist_ok=True)

    if not execute:
        write_json(
            session_dir / "session_state.json",
            {
                "dry_run": True,
                "seed": session.get("seed", order),
                "symbols": tasks.get("symbols", []),
                "start_date": tasks.get("start_date"),
                "end_date": tasks.get("end_date"),
            },
        )
        write_json(session_dir / "memory_snapshot.json", {"dry_run": True, "memory_root": str(memory_root)})
        progress("dry-run stockbench session prepared", condition=condition, session=order)
        return

    if (session_dir / "daily_decisions.json").exists() and not force:
        progress("stockbench session already exists; skipping", condition=condition, session=order)
        return

    from stockbench_adapter import StockBenchOfflineAdapter, StockBenchDataError, stockbench_data_routes
    from stockbench_portfolio import run_tradingagents_portfolio

    adapter = StockBenchOfflineAdapter(tasks)
    try:
        preflight = adapter.preflight()
        bars_by_symbol, calendar = adapter.load_window_bars()
    except StockBenchDataError as exc:
        write_json(
            session_dir / "preflight.json",
            {
                "ok": False,
                "error": str(exc),
                "hint": "Run StockBench pre-cache for daily bars first, then rerun this offline-only showcase.",
            },
        )
        raise

    write_json(session_dir / "preflight.json", preflight)
    progress("starting stockbench session", condition=condition, session=order, seed=session.get("seed", order))
    with stockbench_data_routes(adapter), analyst_cache_probe() as probe:
        result = run_tradingagents_portfolio(
            tasks=tasks,
            session=session,
            condition=condition,
            bars_by_symbol=bars_by_symbol,
            calendar=calendar,
            memory_root=memory_root,
            session_dir=session_dir,
            condition_config=condition_config,
            build_graph_config=build_graph_config,
            patched_memory_paths=patched_memory_paths,
            collect_memory_snapshot=collect_memory_snapshot,
        )

    memory_snapshot = result["memory_snapshot"]
    memory_snapshot["analyst_cache_hits"] = probe.hits
    memory_snapshot["analyst_cache_misses"] = probe.misses
    memory_snapshot["analyst_cache_hit"] = any(count > 0 for count in probe.hits.values())
    write_json(session_dir / "daily_decisions.json", result["decisions"])
    write_json(session_dir / "memory_snapshot.json", memory_snapshot)
    write_json(
        session_dir / "session_state.json",
        {
            "dry_run": False,
            "seed": session.get("seed", order),
            "symbols": tasks.get("symbols", []),
        },
    )
    progress("finished stockbench session", condition=condition, session=order)


def run_showcase(
    showcase: str,
    condition: str,
    execute: bool,
    force: bool,
    reset_memory: bool,
    run_root: Path,
) -> None:
    tasks = load_tasks(showcase)
    condition_config = read_json(BENCH_DIR / "config" / f"{condition}.json")
    if execute:
        reset_condition_if_requested(showcase, condition, force)
        reset_memory_if_requested(showcase, condition, condition_config, reset_memory)
    for session in sorted(tasks["sessions"], key=lambda item: int(item["order"])):
        run_stockbench_session(condition, tasks, session, condition_config, execute, force, run_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--showcase", default="all", help="stockbench or all (StockBench only)")
    parser.add_argument("--condition", default="all", help="memory-on, memory-off, or all")
    parser.add_argument("--execute", action="store_true", help="Run TradingAgentsGraph instead of dry-run validation")
    parser.add_argument("--force", action="store_true", help="Remove prior run outputs before executing selected sessions")
    parser.add_argument(
        "--reset-memory",
        action="store_true",
        help="Clear benchmark memory for the selected showcase/condition before an execute run",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    showcases = select_showcases(args.showcase)
    conditions = select_conditions(args.condition)
    if args.execute:
        run_root = BENCH_DIR
    else:
        if args.reset_memory:
            progress("ignoring --reset-memory during dry-run; no benchmark memory will be changed")
        dryrun_base = BENCH_DIR / ".dryrun"
        dryrun_base.mkdir(parents=True, exist_ok=True)
        run_root = Path(tempfile.mkdtemp(prefix="run-", dir=dryrun_base))
        progress("dry-run staging directory", path=run_root)
    for showcase in showcases:
        for condition in conditions:
            run_showcase(
                showcase,
                condition,
                execute=args.execute,
                force=args.force,
                reset_memory=args.reset_memory,
                run_root=run_root,
            )
    progress("bench complete")


if __name__ == "__main__":
    main()

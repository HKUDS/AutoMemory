"""TradingAgents-driven simulator for StockBench: independent accounts per symbol.

Execution: BUY/SELL = full position change; OVERWEIGHT/UNDERWEIGHT = use
``partial_equity_fraction`` of *current account equity* (same-day close) for
buy / sell notional, capped by available cash or shares.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd  # type: ignore[import-not-found]

ExecMode = Literal["buy", "sell", "hold", "buy_partial", "sell_partial"]

_CANONICAL_RATINGS = frozenset({"BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL"})
_VALID_EXEC_MODES = frozenset({"buy", "sell", "hold", "buy_partial", "sell_partial"})
_DEFAULT_EXEC_MAP = {
    "BUY": "buy",
    "OVERWEIGHT": "buy_partial",
    "HOLD": "hold",
    "UNDERWEIGHT": "sell_partial",
    "SELL": "sell",
}


def normalize_rating(value: Any, fallback_text: str = "") -> str:
    """Map analyst output to a canonical five-way rating."""
    raw = str(value or "").strip()
    if raw:
        first = re.split(r"\s+", raw.upper(), maxsplit=1)[0]
        if first in _CANONICAL_RATINGS:
            return first
    text = f"{fallback_text or ''}".upper()
    for rating in ("BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL"):
        if re.search(rf"\b{rating}\b", text):
            return rating
    return "HOLD"


def partial_equity_fraction(rules: dict[str, Any]) -> float:
    """Fraction of account equity for OVERWEIGHT buy / UNDERWEIGHT sell (default 30%)."""
    v = rules.get("partial_equity_fraction", 0.3)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.3
    return max(0.0, min(x, 1.0))


def rating_to_exec_mode(rating: str, rules: dict[str, Any]) -> ExecMode:
    """Map five-way rating to execution mode (full vs partial using equity fraction)."""
    custom = rules.get("rating_actions")
    if isinstance(custom, dict):
        key = rating.upper()
        if key in custom:
            m = str(custom[key]).lower().strip()
            if m in _VALID_EXEC_MODES:
                return m  # type: ignore[return-value]
    return _DEFAULT_EXEC_MAP.get(rating.upper(), "hold")  # type: ignore[return-value]


@dataclass
class SymbolAccount:
    """Single-symbol sleeve: cash + shares; trades only at decision close."""

    cash: float
    shares: float = 0.0

    def equity(self, price: float) -> float:
        if price <= 0:
            return float(self.cash)
        return float(self.cash + self.shares * price)


@dataclass
class PendingReflection:
    graph: Any
    symbol: str
    decision_date: str
    decision_price: float


def _price_lookup(bars_by_symbol: dict[str, pd.DataFrame], date: str, price_col: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol, bars in bars_by_symbol.items():
        row = bars[bars["date"] == date]
        if row.empty:
            continue
        prices[symbol] = float(row.iloc[0][price_col])
    return prices


def _execute_mode(
    account: SymbolAccount,
    symbol: str,
    date: str,
    mode: ExecMode,
    price: float,
    equity_fraction: float,
    trades: list[dict[str, Any]],
) -> None:
    """buy/sell: full cash or full exit; buy_partial/sell_partial: fraction of current equity."""
    if price <= 0:
        return
    equity = account.equity(price)

    if mode == "hold":
        return

    if mode == "buy":
        if account.cash <= 1e-9:
            return
        qty = account.cash / price
        cash_before = account.cash
        account.shares += qty
        account.cash = 0.0
        trades.append(
            {
                "date": date,
                "symbol": symbol,
                "execution": mode,
                "side": "buy",
                "qty": qty,
                "exec_price": price,
                "exec_value": cash_before,
                "equity_before": equity,
                "fraction": None,
                "cash_before": cash_before,
                "cash_after": account.cash,
                "shares_before": account.shares - qty,
                "shares_after": account.shares,
            }
        )
        return

    if mode == "sell":
        if account.shares <= 1e-12:
            return
        proceeds = account.shares * price
        shares_before = account.shares
        account.cash += proceeds
        account.shares = 0.0
        trades.append(
            {
                "date": date,
                "symbol": symbol,
                "execution": mode,
                "side": "sell",
                "qty": shares_before,
                "exec_price": price,
                "exec_value": proceeds,
                "equity_before": equity,
                "fraction": None,
                "cash_before": account.cash - proceeds,
                "cash_after": account.cash,
                "shares_before": shares_before,
                "shares_after": account.shares,
            }
        )
        return

    if mode == "buy_partial":
        if equity <= 1e-9 or account.cash <= 1e-9:
            return
        budget = min(equity_fraction * equity, account.cash)
        if budget <= 1e-9:
            return
        qty = budget / price
        cash_before = account.cash
        shares_before = account.shares
        account.shares += qty
        account.cash -= budget
        trades.append(
            {
                "date": date,
                "symbol": symbol,
                "execution": mode,
                "side": "buy",
                "qty": qty,
                "exec_price": price,
                "exec_value": budget,
                "equity_before": equity,
                "fraction": equity_fraction,
                "cash_before": cash_before,
                "cash_after": account.cash,
                "shares_before": shares_before,
                "shares_after": account.shares,
            }
        )
        return

    if mode == "sell_partial":
        if equity <= 1e-9 or account.shares <= 1e-12:
            return
        position_value = account.shares * price
        target_value = equity_fraction * equity
        sell_value = min(target_value, position_value)
        if sell_value <= 1e-9:
            return
        qty = sell_value / price
        shares_before = account.shares
        account.cash += sell_value
        account.shares -= qty
        trades.append(
            {
                "date": date,
                "symbol": symbol,
                "execution": mode,
                "side": "sell",
                "qty": qty,
                "exec_price": price,
                "exec_value": sell_value,
                "equity_before": equity,
                "fraction": equity_fraction,
                "cash_before": account.cash - sell_value,
                "cash_after": account.cash,
                "shares_before": shares_before,
                "shares_after": account.shares,
            }
        )


def flush_window_reflections(pending: list[PendingReflection], prices: dict[str, float]) -> int:
    """Force outcome-aware reflection for matured one-day decisions."""
    flushed = 0
    for item in list(pending):
        current_price = prices.get(item.symbol)
        if current_price is None or item.decision_price <= 0:
            pending.remove(item)
            continue
        realized_return = (float(current_price) / float(item.decision_price)) - 1.0
        try:
            item.graph.reflect_and_remember(realized_return)
            flushed += 1
        except Exception:
            pass
        pending.remove(item)
    return flushed


def compute_portfolio_metrics(
    nav_rows: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    initial_per_symbol: float,
    symbols: list[str],
) -> dict[str, Any]:
    """Aggregate + per-symbol returns; risk stats on combined NAV series."""
    n = len(symbols)
    total_initial = float(initial_per_symbol) * n
    if not nav_rows or n == 0:
        return {
            "final_return": 0.0,
            "mean_symbol_return_rate": 0.0,
            "max_drawdown": 0.0,
            "sortino": 0.0,
            "trades_count": 0,
            "trades_notional": 0.0,
            "per_symbol_return_rate": {},
            "per_symbol_final_value": {},
            "aggregate_initial_cash": total_initial,
            "aggregate_final_value": total_initial,
        }

    last = nav_rows[-1]
    per_sym = last.get("per_symbol") or {}
    per_symbol_final_value: dict[str, float] = {}
    per_symbol_return_rate: dict[str, float] = {}
    for sym in symbols:
        info = per_sym.get(sym) or {}
        fv = float(info.get("equity", initial_per_symbol))
        per_symbol_final_value[sym] = fv
        per_symbol_return_rate[sym] = (fv - float(initial_per_symbol)) / float(initial_per_symbol)

    aggregate_final = float(last.get("nav", sum(per_symbol_final_value.values())))
    mean_symbol_rr = sum(per_symbol_return_rate.values()) / max(len(per_symbol_return_rate), 1)
    aggregate_return = (aggregate_final - total_initial) / total_initial if total_initial > 0 else 0.0

    nav = pd.Series(
        [float(row["nav"]) / total_initial for row in nav_rows],
        index=pd.to_datetime([row["date"] for row in nav_rows]),
    )
    try:
        from stockbench.backtest.metrics import evaluate  # type: ignore[import-not-found]

        metrics = evaluate(nav, pd.DataFrame(trades))
        mdd = float(metrics.get("max_drawdown", 0.0))
        sortino = float(metrics.get("sortino", 0.0))
        tc = int(metrics.get("trades_count", len(trades)))
        tn = float(metrics.get("trades_notional", sum(t.get("exec_value", 0) for t in trades)))
    except Exception:
        ret = nav.pct_change().fillna(0.0)
        downside = ret[ret < 0]
        downside_vol = float(downside.std()) if len(downside) else 0.0
        mdd = float((nav / nav.cummax() - 1.0).min())
        sortino = float(ret.mean() / downside_vol) if downside_vol > 0 else 0.0
        tc = len(trades)
        tn = float(sum(t.get("exec_value", 0) for t in trades))

    return {
        "final_return": aggregate_return,
        "mean_symbol_return_rate": mean_symbol_rr,
        "max_drawdown": mdd,
        "sortino": sortino,
        "trades_count": tc,
        "trades_notional": tn,
        "per_symbol_return_rate": per_symbol_return_rate,
        "per_symbol_final_value": per_symbol_final_value,
        "aggregate_initial_cash": total_initial,
        "aggregate_final_value": aggregate_final,
    }


def _nav_snapshot(
    accounts: dict[str, SymbolAccount],
    prices: dict[str, float],
    symbols: list[str],
) -> dict[str, Any]:
    per_symbol: dict[str, Any] = {}
    total = 0.0
    for sym in symbols:
        acct = accounts[sym]
        p = float(prices.get(sym) or 0.0)
        eq = acct.equity(p)
        total += eq
        per_symbol[sym] = {
            "cash": acct.cash,
            "shares": acct.shares,
            "price": p,
            "equity": eq,
        }
    return {"nav": total, "per_symbol": per_symbol}


def run_tradingagents_portfolio(
    *,
    tasks: dict[str, Any],
    session: dict[str, Any],
    condition: str,
    bars_by_symbol: dict[str, pd.DataFrame],
    calendar: list[str],
    memory_root,
    session_dir,
    condition_config: dict[str, Any],
    build_graph_config,
    patched_memory_paths,
    collect_memory_snapshot,
) -> dict[str, Any]:
    """Independent $initial_cash per symbol; same-day close execution (full/partial by rating)."""
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    symbols = [str(s).upper() for s in tasks["symbols"]]
    seed = int(session.get("seed", session.get("order", 1)))
    rng = random.Random(seed)
    initial_per_symbol = float(tasks.get("initial_cash", 10_000.0))
    price_col = str(tasks.get("execution_price", "close"))
    rules = tasks.get("trading_rules") or {}
    selected_analysts = tasks.get("selected_analysts") or condition_config.get(
        "selected_analysts",
        ["market", "social", "news", "fundamentals"],
    )
    flush_cfg = tasks.get("flush_window", {})
    flush_enabled = bool(flush_cfg.get("enabled", True)) and condition == "memory-on"
    flush_frequency_days = max(1, int(flush_cfg.get("frequency_days", 1)))

    accounts: dict[str, SymbolAccount] = {s: SymbolAccount(cash=initial_per_symbol) for s in symbols}
    pending_reflections: list[PendingReflection] = []
    nav_rows: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    flush_events: list[dict[str, Any]] = []

    graph_config = build_graph_config(session_dir, condition_config)

    n_cal = len(calendar)
    print(
        f"[stockbench] start seed={seed} condition={condition} | "
        f"trading_days={n_cal} symbols={len(symbols)} | "
        f"initial_cash_per_symbol={initial_per_symbol} independent_accounts",
        flush=True,
    )

    for day_idx, date in enumerate(calendar):
        print(
            f"[stockbench] seed={seed} {condition} | day {day_idx + 1}/{n_cal} {date}",
            flush=True,
        )
        prices = _price_lookup(bars_by_symbol, date, price_col)

        should_flush = flush_enabled and (
            day_idx % flush_frequency_days == 0 or day_idx == len(calendar) - 1
        )
        if should_flush and pending_reflections:
            flushed = flush_window_reflections(pending_reflections, prices)
            flush_events.append({"date": date, "flushed_reflections": flushed})
            print(
                f"[stockbench] seed={seed} {condition} | {date} flush reflect_and_remember "
                f"pending_cleared={flushed}",
                flush=True,
            )

        if day_idx == len(calendar) - 1:
            snap = _nav_snapshot(accounts, prices, symbols)
            nav_rows.append({"date": date, **snap})
            print(
                f"[stockbench] seed={seed} {condition} | {date} last calendar day — "
                "no new propagate",
                flush=True,
            )
            continue

        symbols_today = list(symbols)
        rng.shuffle(symbols_today)
        for symbol in symbols_today:
            print(
                f"[stockbench] seed={seed} {condition} | {date} propagate {symbol}",
                flush=True,
            )
            decision_memory_root = memory_root
            if condition == "memory-off":
                decision_memory_root = session_dir / "memory" / "isolated" / f"{date}-{symbol}"
            with patched_memory_paths(decision_memory_root):
                graph = TradingAgentsGraph(selected_analysts=selected_analysts, debug=False, config=graph_config)
                final_state, processed_signal = graph.propagate(symbol, date)
            final_text = str(final_state.get("final_trade_decision", ""))
            rating = normalize_rating(processed_signal, final_text)
            exec_mode = rating_to_exec_mode(rating, rules)
            frac = partial_equity_fraction(rules)
            px = float(prices.get(symbol) or 0.0)
            _execute_mode(accounts[symbol], symbol, date, exec_mode, px, frac, trades)
            decisions.append(
                {
                    "date": date,
                    "symbol": symbol,
                    "rating": rating,
                    "execution": exec_mode,
                    "partial_equity_fraction": frac if exec_mode in ("buy_partial", "sell_partial") else None,
                    "processed_signal": str(processed_signal),
                    "final_decision_preview": final_text[:1000],
                    "exec_price": px,
                    "memory_root": str(decision_memory_root),
                }
            )
            if flush_enabled and symbol in prices:
                pending_reflections.append(
                    PendingReflection(
                        graph=graph,
                        symbol=symbol,
                        decision_date=date,
                        decision_price=px,
                    )
                )

        snap_after = _nav_snapshot(accounts, prices, symbols)
        nav_rows.append({"date": date, **snap_after})

    metrics = compute_portfolio_metrics(
        nav_rows, trades, initial_per_symbol=initial_per_symbol, symbols=symbols
    )
    metrics.update(
        {
            "seed": seed,
            "condition": condition,
            "symbols": symbols,
            "start_date": calendar[0] if calendar else None,
            "end_date": calendar[-1] if calendar else None,
            "trading_days": len(calendar),
            "initial_cash_per_symbol": initial_per_symbol,
            "final_nav": float(nav_rows[-1]["nav"]) if nav_rows else initial_per_symbol * len(symbols),
            "flush_events": flush_events,
            "simulation_mode": "independent_accounts_exec_modes",
        }
    )

    if condition == "memory-on":
        memory_snapshot = collect_memory_snapshot(memory_root, symbols[0], calendar[-1])
    else:
        memory_snapshot = {
            "symbols": symbols,
            "trade_date": calendar[-1] if calendar else None,
            "memory_strategy": "per-decision-isolated",
            "isolated_memory_root": str(session_dir / "memory" / "isolated"),
        }

    return {
        "nav_rows": nav_rows,
        "trades": trades,
        "decisions": decisions,
        "metrics": metrics,
        "memory_snapshot": memory_snapshot,
    }

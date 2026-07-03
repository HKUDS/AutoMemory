"""StockBench data adapter for the TradingAgents bench harness.

This module uses StockBench only as an offline data source. It does not call
StockBench agents or benchmark protocols.
"""

from __future__ import annotations

import sys
import types
import importlib.util
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

import pandas as pd  # type: ignore[import-not-found]


BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
STOCKBENCH_ROOT = REPO_ROOT / "stockbench"


class StockBenchDataError(RuntimeError):
    """Raised when required offline StockBench data is unavailable."""


def ensure_stockbench_importable() -> None:
    """Put the local StockBench project on sys.path."""
    root = str(STOCKBENCH_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    # Skip find_spec if we already injected the offline stub (or finnhub was imported).
    if "finnhub" in sys.modules:
        return
    try:
        has_finnhub = importlib.util.find_spec("finnhub") is not None
    except (ValueError, ModuleNotFoundError):
        # Broken/partial finnhub install can make find_spec raise (e.g. __spec__ is None).
        has_finnhub = False
    if not has_finnhub:
        stub = types.ModuleType("finnhub")

        class _OfflineFinnhubClient:
            DEFAULT_TIMEOUT = 30

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def company_news(self, *args: Any, **kwargs: Any) -> list[Any]:
                return []

        stub.Client = _OfflineFinnhubClient
        sys.modules["finnhub"] = stub


def _date_str(value: Any) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _coerce_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "vwap"])
    out = df.copy()
    if "date" not in out.columns:
        out["date"] = pd.to_datetime(out.index)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for col in ("open", "high", "low", "close", "volume", "vwap"):
        if col not in out.columns:
            out[col] = out["close"] if col == "vwap" and "close" in out.columns else 0.0
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
    out = out[["date", "open", "high", "low", "close", "volume", "vwap"]]
    return out.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


class StockBenchOfflineAdapter:
    """Read StockBench local cache/storage in offline-only mode."""

    def __init__(self, tasks: dict[str, Any]) -> None:
        ensure_stockbench_importable()
        from stockbench.core import data_hub  # type: ignore[import-not-found]

        data_hub.set_data_mode("offline_only")
        self.data_hub = data_hub
        self.tasks = tasks
        self.symbols = [str(s).upper() for s in tasks.get("symbols", [])]
        self.start_date = str(tasks.get("start_date", ""))
        self.end_date = str(tasks.get("end_date", ""))
        self.window_trading_days = int(tasks.get("window_trading_days", 25))
        self.cfg = {
            "data": {"mode": "offline_only"},
            "bars": {"day": {"adjusted": True}},
            "news": tasks.get(
                "news",
                {"lookback_days": 2, "page_limit": 100, "top_k_event_count": 5},
            ),
        }

    def get_bars(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        start = start or self.start_date
        end = end or self.end_date
        bars = self.data_hub.get_bars(
            symbol.upper(),
            start,
            end,
            multiplier=1,
            timespan="day",
            adjusted=True,
            cfg=self.cfg,
        )
        return _coerce_bars(bars)

    def load_window_bars(self) -> tuple[dict[str, pd.DataFrame], list[str]]:
        if not self.symbols:
            raise StockBenchDataError("bench/stockbench/tasks.json must define a non-empty symbols list.")
        if not self.start_date or not self.end_date:
            raise StockBenchDataError("bench/stockbench/tasks.json must define start_date and end_date.")

        bars_by_symbol = {symbol: self.get_bars(symbol) for symbol in self.symbols}
        missing = [symbol for symbol, bars in bars_by_symbol.items() if bars.empty]
        if missing:
            raise StockBenchDataError(
                "Missing offline StockBench OHLCV for "
                f"{', '.join(missing)} in {self.start_date}..{self.end_date}. "
                "Pre-cache StockBench daily bars first or adjust the window."
            )

        common_dates: set[str] | None = None
        for bars in bars_by_symbol.values():
            dates = set(bars["date"].astype(str).tolist())
            common_dates = dates if common_dates is None else common_dates & dates
        calendar = sorted(common_dates or [])
        if len(calendar) < self.window_trading_days:
            raise StockBenchDataError(
                f"Only {len(calendar)} common trading days are available for "
                f"{', '.join(self.symbols)}; need {self.window_trading_days}."
            )
        if not 2 <= self.window_trading_days <= 30:
            raise StockBenchDataError(
                "window_trading_days must be between 2 and 30 (use 20–30 for full benchmark windows)."
            )
        calendar = calendar[: self.window_trading_days]
        trimmed = {
            symbol: bars[bars["date"].isin(calendar)].reset_index(drop=True)
            for symbol, bars in bars_by_symbol.items()
        }
        return trimmed, calendar

    def preflight(self) -> dict[str, Any]:
        bars_by_symbol, calendar = self.load_window_bars()
        news_counts = {
            symbol: len(self._news_items(symbol, calendar[0], calendar[-1], limit=10))
            for symbol in self.symbols
        }
        indicator_counts = {
            symbol: len(self.data_hub.get_stock_indicators(symbol, calendar[0], cfg=self.cfg) or {})
            for symbol in self.symbols
        }
        return {
            "ok": True,
            "symbols": self.symbols,
            "start_date": calendar[0],
            "end_date": calendar[-1],
            "trading_days": len(calendar),
            "rows_by_symbol": {symbol: int(len(bars)) for symbol, bars in bars_by_symbol.items()},
            "news_items_by_symbol": news_counts,
            "indicator_fields_by_symbol": indicator_counts,
            "data_mode": "offline_only",
        }

    def format_stock_data(self, symbol: str, start_date: str, end_date: str) -> str:
        bars = self.get_bars(symbol, start_date, end_date)
        if bars.empty:
            return f"No offline StockBench price data for {symbol} from {start_date} to {end_date}."
        return (
            f"Offline StockBench OHLCV for {symbol} from {start_date} to {end_date}:\n"
            f"{bars.tail(60).to_string(index=False)}"
        )

    def format_indicator(self, symbol: str, indicator: str, curr_date: str, look_back_days: int = 30) -> str:
        start = _date_str(pd.to_datetime(curr_date) - timedelta(days=max(int(look_back_days) * 2, 40)))
        bars = self.get_bars(symbol, start, curr_date)
        if bars.empty or "close" not in bars:
            return f"No offline StockBench price data for indicator {indicator} on {symbol}."
        close = bars["close"].astype(float)
        ind = indicator.lower().strip()
        if "rsi" in ind:
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0.0, pd.NA)
            value = (100 - (100 / (1 + rs))).iloc[-1]
            return f"StockBench RSI(14) for {symbol} on {curr_date}: {float(value):.2f}"
        if "macd" in ind:
            macd = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
            signal = macd.ewm(span=9, adjust=False).mean()
            return (
                f"StockBench MACD for {symbol} on {curr_date}: "
                f"macd={float(macd.iloc[-1]):.4f}, signal={float(signal.iloc[-1]):.4f}"
            )
        ma5 = close.rolling(5).mean().iloc[-1]
        ma20 = close.rolling(20).mean().iloc[-1]
        ret = close.pct_change().tail(5).mean()
        return (
            f"StockBench technical snapshot for {symbol} on {curr_date}: "
            f"close={float(close.iloc[-1]):.2f}, ma5={float(ma5):.2f}, "
            f"ma20={float(ma20):.2f}, avg_5d_return={float(ret):.4f}"
        )

    def format_fundamentals(self, symbol: str, curr_date: str) -> str:
        data = self.data_hub.get_stock_indicators(symbol.upper(), curr_date, cfg=self.cfg) or {}
        if not data:
            return f"No offline StockBench fundamental indicators for {symbol} on {curr_date}."
        lines = [f"Offline StockBench fundamental indicators for {symbol} on {curr_date}:"]
        for key in sorted(data):
            lines.append(f"- {key}: {data[key]}")
        return "\n".join(lines)

    def _news_items(self, symbol: str, start_date: str, end_date: str, limit: int = 10) -> list[dict[str, Any]]:
        items, _ = self.data_hub.get_news(symbol.upper(), start_date, end_date, limit=limit, cfg=self.cfg)
        return list(items or [])[:limit]

    def format_news(self, symbol: str, start_date: str, end_date: str, limit: int = 10) -> str:
        items = self._news_items(symbol, start_date, end_date, limit=limit)
        if not items:
            return f"No cached StockBench news for {symbol} from {start_date} to {end_date}."
        lines = [f"Offline StockBench news for {symbol} from {start_date} to {end_date}:"]
        for item in items:
            title = item.get("title") or item.get("headline") or "(untitled)"
            published = item.get("published_utc") or item.get("datetime") or item.get("date") or ""
            description = item.get("description") or item.get("summary") or ""
            lines.append(f"- {published} {title}: {description}")
        return "\n".join(lines)

    def format_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 5) -> str:
        start = _date_str(pd.to_datetime(curr_date) - timedelta(days=max(int(look_back_days), 1)))
        lines: list[str] = []
        per_symbol_limit = max(1, int(limit))
        for symbol in self.symbols:
            for item in self._news_items(symbol, start, curr_date, limit=per_symbol_limit):
                title = item.get("title") or item.get("headline") or "(untitled)"
                description = item.get("description") or item.get("summary") or ""
                lines.append(f"- {symbol}: {title}: {description}")
                if len(lines) >= limit:
                    break
            if len(lines) >= limit:
                break
        if not lines:
            return f"No cached StockBench market news through {curr_date}."
        return "Offline StockBench cross-symbol news:\n" + "\n".join(lines)


@contextmanager
def stockbench_data_routes(adapter: StockBenchOfflineAdapter) -> Iterator[None]:
    """Route TradingAgents tools to StockBench offline data for one benchmark run."""
    from tradingagents.agents.utils import core_stock_tools
    from tradingagents.agents.utils import fundamental_data_tools
    from tradingagents.agents.utils import news_data_tools
    from tradingagents.agents.utils import technical_indicators_tools
    from tradingagents.dataflows import interface as data_interface

    originals = {
        "interface": data_interface.route_to_vendor,
        "core": core_stock_tools.route_to_vendor,
        "technical": technical_indicators_tools.route_to_vendor,
        "fundamental": fundamental_data_tools.route_to_vendor,
        "news": news_data_tools.route_to_vendor,
    }

    def route(method: str, *args: Any, **kwargs: Any) -> str:
        if method == "get_stock_data":
            symbol, start_date, end_date = args[:3]
            return adapter.format_stock_data(symbol, start_date, end_date)
        if method == "get_indicators":
            symbol, indicator, curr_date = args[:3]
            look_back_days = args[3] if len(args) > 3 else kwargs.get("look_back_days", 30)
            return adapter.format_indicator(symbol, indicator, curr_date, look_back_days)
        if method in {"get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"}:
            symbol = args[0]
            curr_date = args[-1] if len(args) > 1 else kwargs.get("curr_date")
            return adapter.format_fundamentals(symbol, curr_date or adapter.end_date)
        if method == "get_news":
            ticker, start_date, end_date = args[:3]
            return adapter.format_news(ticker, start_date, end_date)
        if method == "get_global_news":
            curr_date = args[0]
            look_back_days = args[1] if len(args) > 1 else kwargs.get("look_back_days", 7)
            limit = args[2] if len(args) > 2 else kwargs.get("limit", 5)
            return adapter.format_global_news(curr_date, look_back_days, limit)
        if method == "get_insider_transactions":
            return "StockBench offline adapter does not provide insider transaction data."
        return f"StockBench offline adapter does not support method {method}."

    data_interface.route_to_vendor = route
    core_stock_tools.route_to_vendor = route
    technical_indicators_tools.route_to_vendor = route
    fundamental_data_tools.route_to_vendor = route
    news_data_tools.route_to_vendor = route
    try:
        yield
    finally:
        data_interface.route_to_vendor = originals["interface"]
        core_stock_tools.route_to_vendor = originals["core"]
        technical_indicators_tools.route_to_vendor = originals["technical"]
        fundamental_data_tools.route_to_vendor = originals["fundamental"]
        news_data_tools.route_to_vendor = originals["news"]

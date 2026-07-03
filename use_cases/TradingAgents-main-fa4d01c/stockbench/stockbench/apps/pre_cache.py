from __future__ import annotations

from pathlib import Path
from typing import List, Optional
import os
import re
import time

import typer
import yaml
import pandas as pd

from stockbench.core import data_hub as hub


app = typer.Typer(add_completion=False)


def _parse_symbols_arg(symbols: str | None) -> List[str]:
    if not symbols:
        return []
    return [s for s in re.split(r"[\s,]+", symbols.strip()) if s]


def _discover_symbols_from_config(cfg: dict) -> List[str]:
    try:
        items = cfg.get("symbols_universe", []) or []
        return list(items)
    except Exception:
        return []


def _discover_symbols_from_storage(project_root: Path) -> List[str]:
    # Prefer directories under storage/parquet/* as symbols
    base = project_root / "storage" / "parquet"
    if not base.is_dir():
        return []
    symbols: List[str] = []
    for p in sorted(base.iterdir()):
        try:
            if p.is_dir() and re.fullmatch(r"[A-Z]{1,6}", p.name):
                symbols.append(p.name)
        except Exception:
            continue
    return symbols


def _fallback_default_symbols() -> List[str]:
    # Default 20-stock universe matching config.yaml
    return [
        "GS", "MSFT", "HD", "V", "SHW", "CAT", "MCD", "UNH", "AXP", "AMGN",
        "TRV", "CRM", "JPM", "IBM", "HON", "BA", "AMZN", "AAPL", "PG", "JNJ"
    ]


def _resolve_symbols(cfg: dict, symbols_arg: str | None, project_root: Path) -> List[str]:
    # Priority: CLI -> config.symbols_universe -> storage/parquet -> fallback
    cli_syms = _parse_symbols_arg(symbols_arg)
    if cli_syms:
        return cli_syms
    cfg_syms = _discover_symbols_from_config(cfg)
    if cfg_syms:
        return cfg_syms
    stor_syms = _discover_symbols_from_storage(project_root)
    if stor_syms:
        return stor_syms
    return _fallback_default_symbols()


def _project_root() -> Path:
    # stockbench/apps/ -> project root = ../../
    return (Path(__file__).resolve().parent.parent.parent)


def _safe_api_call(func, *args, api_delay: float = 1.2, batch_delay: float = 60.0, max_retries: int = 3, **kwargs):
    """Safe API call with retry and rate limiting mechanism"""
    for attempt in range(max_retries + 1):
        try:
            # Wait before each API call to avoid exceeding rate limits
            if attempt > 0 or api_delay > 0:
                time.sleep(api_delay)
            
            result = func(*args, **kwargs)
            return result, None  # Return result on success and None for error
            
        except Exception as e:
            error_str = str(e).lower()
            
            # Check if it's an API rate limit error (429)
            if "429" in error_str or "api limit" in error_str or "rate limit" in error_str:
                if attempt < max_retries:
                    typer.echo(f"    ⏳ API rate limit (attempt {attempt + 1}/{max_retries + 1}): waiting {batch_delay}s before retry...")
                    time.sleep(batch_delay)
                    continue
                else:
                    return None, f"API rate limit: {e}"
            
            # Other error types
            elif "timeout" in error_str or "network" in error_str:
                if attempt < max_retries:
                    wait_time = min(api_delay * (2 ** attempt), 30)  # Exponential backoff, max 30s
                    typer.echo(f"    🔄 Network error (attempt {attempt + 1}/{max_retries + 1}): waiting {wait_time}s before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    return None, f"Network error: {e}"
            
            # Immediate failure errors (e.g., authentication errors)
            else:
                return None, str(e)
    
    return None, "Maximum retry attempts reached"


@app.command()
def main(
    cfg: Path = typer.Option(Path("config.yaml"), exists=True, readable=True, help="Path to config file"),
    start: str = typer.Option("2025-03-01", help="Start date YYYY-MM-DD"),
    end: str = typer.Option("2025-07-31", help="End date YYYY-MM-DD (inclusive)"),
    symbols: Optional[str] = typer.Option(None, "--symbols", "-s", help="Symbol list (comma or space separated, overrides config)"),
    include_prices: bool = typer.Option(True, help="Pre-cache daily price bars"),
    include_news: bool = typer.Option(True, help="Pre-cache news (per day)"),
    include_financials: bool = typer.Option(True, help="Pre-cache financials (annual/quarterly/all)"),
    include_indicators: bool = typer.Option(True, help="Pre-cache key indicators (per day)"),
    include_corp_actions: bool = typer.Option(True, help="Pre-cache dividends and splits"),
    # Rate limiting options
    api_delay: float = typer.Option(1.2, help="API call interval in seconds (Finnhub free tier recommends 1.2s)"),
    batch_delay: float = typer.Option(60.0, help="Batch wait time in seconds (when encountering 429 errors)"),
    max_retries: int = typer.Option(3, help="Maximum retry attempts for failed API calls"),
):
    """One-click pre-caching of required data to ensure offline operation."""
    # Load config
    with cfg.open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    project_root = _project_root()
    sym_list = _resolve_symbols(config, symbols, project_root)
    typer.echo(f"[PRE-CACHE] Symbols: {len(sym_list)} -> {sym_list}")
    typer.echo(f"[PRE-CACHE] Range: {start} ~ {end}")

    # Read news parameters
    news_cfg = (config.get("news", {}) or {})
    lookback_days = int(news_cfg.get("lookback_days", 2))
    page_limit = int(news_cfg.get("page_limit", 100))

    # 1) Prices (daily)
    if include_prices:
        typer.echo("[1/5] Pre-caching daily bars ...")
        for i, s in enumerate(sym_list):
            result, error = _safe_api_call(
                hub.get_bars, s, start, end, 
                multiplier=1, timespan="day", adjusted=True, cfg=config,
                api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
            )
            if result is not None:
                row_count = len(result) if hasattr(result, 'empty') and not result.empty else 0
                typer.echo(f"  - {s}: {row_count} rows")
            else:
                typer.echo(f"  ! {s}: bars failed - {error}")
            
            # Progress display
            if (i + 1) % 5 == 0:
                typer.echo(f"    Progress: {i + 1}/{len(sym_list)} stocks completed")

    # Build business-day date index (engine uses trading-day check; we filter by is_trading_day)
    dates = pd.date_range(start=start, end=end, freq="B")
    dates = [d for d in dates if hub.is_trading_day(d)]

    # 2) News (per trading day, per symbol) - most likely to trigger API limits
    if include_news:
        typer.echo("[2/5] Pre-caching news (per trading day) ...")
        total_requests = len(dates) * len(sym_list)
        completed = 0
        
        for d_idx, d in enumerate(dates):
            # Strategy queries news_end = D-1, start = D-lookback
            end_d = (d - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            start_d = (d - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
            
            for s_idx, s in enumerate(sym_list):
                result, error = _safe_api_call(
                    hub.get_news, s, start_d, end_d, 
                    limit=page_limit, cfg=config,
                    api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
                )
                completed += 1
                
                if result is not None:
                    news_count = len(result[0]) if isinstance(result, tuple) and len(result) > 0 else 0
                    if news_count > 0:
                        typer.echo(f"  ✓ {s}@{d.date()}: {news_count} news")
                else:
                    typer.echo(f"  ! {s}@{d.date()}: news failed - {error}")
                
                # Progress display (show every 10 requests)
                if completed % 10 == 0:
                    progress = (completed / total_requests) * 100
                    typer.echo(f"    📈 News cache progress: {completed}/{total_requests} ({progress:.1f}%)")
            
            # Show progress after completing each day
            if (d_idx + 1) % 5 == 0:
                typer.echo(f"    📅 Date progress: {d_idx + 1}/{len(dates)} days completed")

    # 3) Financials (cache to storage/cache/financials)
    if include_financials:
        typer.echo("[3/5] Pre-caching financials ...")
        for i, s in enumerate(sym_list):
            success_count = 0
            for timeframe in [None, "annual", "quarterly"]:
                result, error = _safe_api_call(
                    hub.get_financials, s, 
                    timeframe=timeframe, limit=50, use_cache=True, cfg=config,
                    api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
                )
                if result is not None:
                    success_count += 1
                else:
                    tf_name = timeframe or "all"
                    typer.echo(f"  ! {s} ({tf_name}): financials failed - {error}")
            
            if success_count > 0:
                typer.echo(f"  - {s}: {success_count}/3 timeframes cached")
            
            # Progress display
            if (i + 1) % 5 == 0:
                typer.echo(f"    Progress: {i + 1}/{len(sym_list)} stocks completed")

    # 4) Corporate actions (dividends & splits)
    if include_corp_actions:
        typer.echo("[4/5] Pre-caching corporate actions (dividends & splits) ...")
        for i, s in enumerate(sym_list):
            div_result, div_error = _safe_api_call(
                hub.get_dividends, s,
                api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
            )
            split_result, split_error = _safe_api_call(
                hub.get_splits, s,
                api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
            )
            
            success_parts = []
            if div_result is not None:
                div_count = len(div_result) if hasattr(div_result, '__len__') else 0
                success_parts.append(f"div:{div_count}")
            else:
                typer.echo(f"  ! {s}: dividends failed - {div_error}")
                
            if split_result is not None:
                split_count = len(split_result) if hasattr(split_result, '__len__') else 0
                success_parts.append(f"split:{split_count}")
            else:
                typer.echo(f"  ! {s}: splits failed - {split_error}")
            
            if success_parts:
                typer.echo(f"  - {s}: {', '.join(success_parts)}")
            
            # Progress display
            if (i + 1) % 5 == 0:
                typer.echo(f"    Progress: {i + 1}/{len(sym_list)} stocks completed")

    # 5) Stock indicators (per trading day, per symbol)
    if include_indicators:
        typer.echo("[5/5] Pre-caching stock indicators (per trading day) ...")
        total_requests = len(dates) * len(sym_list)
        completed = 0
        
        for d_idx, d in enumerate(dates):
            d_str = d.strftime("%Y-%m-%d")
            for s_idx, s in enumerate(sym_list):
                result, error = _safe_api_call(
                    hub.get_stock_indicators, s, 
                    date=d_str, use_cache=True, cfg=config,
                    api_delay=api_delay, batch_delay=batch_delay, max_retries=max_retries
                )
                completed += 1
                
                if result is not None:
                    indicator_count = len([k for k, v in result.items() if v != 0.0]) if isinstance(result, dict) else 0
                    if indicator_count > 0:
                        typer.echo(f"  ✓ {s}@{d_str}: {indicator_count} indicators")
                else:
                    typer.echo(f"  ! {s}@{d_str}: indicators failed - {error}")
                
                # Progress display (show every 20 requests)
                if completed % 20 == 0:
                    progress = (completed / total_requests) * 100
                    typer.echo(f"    📊 Indicators cache progress: {completed}/{total_requests} ({progress:.1f}%)")
            
            # Show progress after completing each day
            if (d_idx + 1) % 10 == 0:
                typer.echo(f"    📅 Date progress: {d_idx + 1}/{len(dates)} days completed")

    typer.echo("[PRE-CACHE] Done! 🎉")
    typer.echo(f"📈 Total processed: {len(sym_list)} stocks × {len(dates)} trading days")
    typer.echo("Now you can run backtests offline!")


if __name__ == "__main__":  # pragma: no cover
    app()



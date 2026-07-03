from __future__ import annotations

import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from stockbench.adapters.polygon_client import PolygonClient
from stockbench.adapters.finnhub_client import FinnhubClient
from stockbench.utils.io import (
    ensure_dir,
    write_parquet_idempotent,
    atomic_append_jsonl,
)
from stockbench.llm.llm_client import LLMClient, LLMConfig
# Remove missing module imports
import numpy as np



# Calculate project root directory (absolute path)
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_BACKTEST_DIR = os.path.join(_PROJECT_ROOT, "backtest_data")
_STORAGE_BASE = os.path.join(_PROJECT_ROOT, "storage")
_PARQUET_BASE = os.path.join(_STORAGE_BASE, "parquet")
_CACHE_BASE = os.path.join(_STORAGE_BASE, "cache")
_REPORT_BASE = os.path.join(_STORAGE_BASE, "reports")
_CORP_ACTIONS_DIR = os.path.join(_CACHE_BASE, "corporate_actions")
_NEWS_BY_DAY_BASE = os.path.join(_CACHE_BASE, "news_by_day")

_polygon_client = PolygonClient(os.getenv("POLYGON_API_KEY", ""))
_finnhub_client = FinnhubClient(os.getenv("FINNHUB_API_KEY", ""))


# Global data mode control: auto | offline_only
# - auto: current behavior (use local cache first, then call APIs if needed)
# - offline_only (aliases: offline/cache_only/cache): strictly use local data; never call external APIs
_DATA_MODE: str = str(os.getenv("TA_DATA_MODE", "auto")).lower()

def _normalize_data_mode(mode: str | None) -> str:
    m = str(mode or "auto").strip().lower()
    if m in {"offline_only", "offline", "cache_only", "cache"}:
        return "offline_only"
    return "auto"

def set_data_mode(mode: str) -> None:
    """Set global data mode. Useful for CLI override."""
    global _DATA_MODE
    _DATA_MODE = _normalize_data_mode(mode)

def _effective_data_mode(cfg: Optional[Dict] = None) -> str:
    try:
        if isinstance(cfg, dict):
            v = (((cfg.get("data", {}) or {}).get("mode", None)))
            if v is not None:
                return _normalize_data_mode(str(v))
    except Exception:
        pass
    return _normalize_data_mode(_DATA_MODE)

def _is_offline_only(cfg: Optional[Dict] = None) -> bool:
    return _effective_data_mode(cfg) == "offline_only"

# Global variable to ensure statistics are shown only once
_cache_stats_shown = False


def _show_news_cache_stats():
    """Show news cache statistics (display only once)"""
    global _cache_stats_shown
    if _cache_stats_shown:
        return
    
    try:
        cache_dir = os.path.join(_CACHE_BASE, "news")
        if not os.path.exists(cache_dir):
            logger.info("[CACHE_STATS] News cache directory does not exist")
            _cache_stats_shown = True
            return
            
        cache_files = [f for f in os.listdir(cache_dir) if f.endswith('.json')]
        total_news = 0
        
        for filename in cache_files:
            try:
                filepath = os.path.join(cache_dir, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                if isinstance(cached_data, list):
                    total_news += len(cached_data)
                elif isinstance(cached_data, dict) and "items" in cached_data:
                    total_news += len(cached_data.get("items", []))
            except Exception:
                continue
                
        logger.info(f"[CACHE_STATS] Found {len(cache_files)} news cache files, total cached {total_news} news items")
        _cache_stats_shown = True
        
    except Exception as e:
        logger.debug(f"Failed to get cache statistics: {e}")
        _cache_stats_shown = True


def _generate_news_cache_key(ticker: str, gte: str, lte: str, top_k_event_count: int, lookback_days: int) -> str:
    """
    Generate deterministic cache key for news queries
    
    Args:
        ticker: Stock symbol
        gte: Start date
        lte: End date  
        top_k_event_count: Number of news items
        lookback_days: Lookback days
        
    Returns:
        Cache key string
    """
    # Standardize date format to ensure consistency
    gte_normalized = pd.to_datetime(gte).strftime('%Y-%m-%d') if gte else "None"
    lte_normalized = pd.to_datetime(lte).strftime('%Y-%m-%d') if lte else "None"
    
    # Generate original string for cache key
    cache_string = f"{ticker}|{gte_normalized}|{lte_normalized}|{top_k_event_count}|{lookback_days}"
    
    # Use MD5 hash to generate fixed-length cache key
    cache_hash = hashlib.md5(cache_string.encode('utf-8')).hexdigest()
    
    return f"news_{cache_hash}"


def _news_unique_key(item: Dict) -> str:
    """Generate a stable unique key for a news item for deduplication.
    Priority: explicit id -> url -> (title|published_utc).
    """
    if not isinstance(item, dict):
        return ""
    _id = str(item.get("id") or "").strip()
    if _id:
        return f"id:{_id}"
    url = str(item.get("url") or "").strip()
    if url:
        return f"url:{url}"
    title = str(item.get("title") or "").strip()
    ts = str(item.get("published_utc") or "").strip()
    return f"tt:{title}|{ts}"


def _save_news_items_to_day_cache(ticker: str, news_items: List[Dict]) -> None:
    """Persist news items into per-day normalized cache under news_by_day/{ticker}/{YYYY-MM-DD}.json.
    This function is idempotent and deduplicates by _news_unique_key.
    """
    try:
        if not news_items:
            return
        base_dir = os.path.join(_NEWS_BY_DAY_BASE, ticker)
        ensure_dir(base_dir)
        # Group items by publish date (UTC date)
        buckets: Dict[str, List[Dict]] = {}
        for it in news_items:
            ts = str(it.get("published_utc") or "").strip()
            if not ts:
                continue
            try:
                d = pd.to_datetime(ts).tz_localize(None).date()
            except Exception:
                try:
                    d = pd.to_datetime(ts, errors="coerce").tz_localize(None).date()
                except Exception:
                    continue
            d_str = str(d)
            buckets.setdefault(d_str, []).append(it)

        for d_str, items in buckets.items():
            path = os.path.join(base_dir, f"{d_str}.json")
            existing: List[Dict] = []
            existing_keys: set[str] = set()
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and "items" in data:
                            existing = list(data.get("items", []))
                        elif isinstance(data, list):  # legacy
                            existing = data
                for it in existing:
                    existing_keys.add(_news_unique_key(it))
            except Exception:
                existing = []
                existing_keys = set()

            merged: List[Dict] = list(existing)
            new_count = 0
            for it in items:
                key = _news_unique_key(it)
                if key and key not in existing_keys:
                    merged.append(it)
                    existing_keys.add(key)
                    new_count += 1

            # Sort by published_utc desc for better readability
            try:
                merged.sort(key=lambda x: str(x.get("published_utc", "")), reverse=True)
            except Exception:
                pass

            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump({"items": merged}, f, ensure_ascii=False, indent=2)
                logger.debug(f"[NEWS_DAY_CACHE] {ticker}@{d_str}: +{new_count}, total {len(merged)}")
            except Exception as e:
                logger.warning(f"Failed to write day news cache {ticker}@{d_str}: {e}")
    except Exception as e:
        logger.warning(f"save_news_items_to_day_cache failed: {ticker}: {e}")


def _read_news_from_day_cache_range(ticker: str, start_str: str, end_str: str, max_items: int) -> List[Dict]:
    """Aggregate day cache files within [start_str, end_str] and return up to max_items items sorted by time desc.
    """
    try:
        base_dir = os.path.join(_NEWS_BY_DAY_BASE, ticker)
        if not os.path.isdir(base_dir):
            return []
        s = pd.to_datetime(start_str).date()
        e = pd.to_datetime(end_str).date()
        days = pd.date_range(start=s, end=e, freq="D")
        collected: List[Dict] = []
        seen: set[str] = set()
        for d in reversed(days):  # newest first
            path = os.path.join(base_dir, f"{str(d.date())}.json")
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                items = data.get("items", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
                # keep only items with basic quality
                valid_items: List[Dict] = []
                for it in items:
                    title = str(it.get("title", "")).strip()
                    desc = str(it.get("description", "")).strip()
                    if title or desc:
                        key = _news_unique_key(it)
                        if key and key not in seen:
                            seen.add(key)
                            valid_items.append(it)
                # sort by time desc
                try:
                    valid_items.sort(key=lambda x: str(x.get("published_utc", "")), reverse=True)
                except Exception:
                    pass
                collected.extend(valid_items)
                if len(collected) >= max_items:
                    break
            except Exception as e:
                logger.debug(f"read day news cache failed {ticker}@{d}: {e}")
                continue
        return collected[:max_items]
    except Exception as e:
        logger.debug(f"read_news_from_day_cache_range failed: {ticker}: {e}")
        return []


def _get_news_from_cache(cache_key: str, cfg: Optional[Dict] = None) -> Optional[List[Dict]]:
    """
    Get news data from cache
    
    Args:
        cache_key: Cache key
        
    Returns:
        Cached news list, or None if not exists
    """
    # Global cache switch
    cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower() if isinstance(cfg, dict) else "full"
    if cache_mode == "off":
        return None

    try:
        cache_dir = os.path.join(_CACHE_BASE, "news")
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")
        
        if not os.path.exists(cache_file):
            return None
            
        with open(cache_file, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
            
        # Support both new and old cache formats
        if isinstance(cached_data, list):
            return cached_data
        elif isinstance(cached_data, dict) and "items" in cached_data:
            return cached_data["items"]
        else:
            logger.warning(f"Invalid cache format: {cache_key}")
            return None
            
    except Exception as e:
        logger.warning(f"Failed to read news cache {cache_key}: {e}")
        return None


def _save_news_to_cache(cache_key: str, news_items: List[Dict], cfg: Optional[Dict] = None) -> None:
    """
    Save news data to cache
    
    Args:
        cache_key: Cache key
        news_items: News list
    """
    cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower() if isinstance(cfg, dict) else "full"
    if cache_mode == "off":
        return

    try:
        cache_dir = os.path.join(_CACHE_BASE, "news")
        ensure_dir(cache_dir)
        
        cache_file = os.path.join(cache_dir, f"{cache_key}.json")
        
        # Save new format cache data (including metadata)
        cache_data = {
            "items": news_items,
            "metadata": {
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "cache_key": cache_key,
                "count": len(news_items)
            }
        }
        
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
            
        logger.debug(f"News cache saved: {cache_key}, count: {len(news_items)}")
        
    except Exception as e:
        logger.warning(f"Failed to save news cache {cache_key}: {e}")


def _empty_bars_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "date", "open", "high", "low", "close", "volume", "vwap"
    ])


def _normalize_day_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.tz_localize(None).dt.date
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _log_quality_issue(kind: str, symbol: str, payload: Dict[str, object]) -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = os.path.join(_REPORT_BASE, "quality", date_str)
    ensure_dir(out_dir)
    rec = {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": kind,
        "symbol": symbol,
        **payload,
    }
    atomic_append_jsonl(os.path.join(out_dir, f"{symbol}.jsonl"), rec)


def _detect_duplicates(df: pd.DataFrame, key: str) -> int:
    if df.empty or key not in df.columns:
        return 0
    dup = int(df.duplicated(subset=[key]).sum())
    return dup


def is_trading_day(date: pd.Timestamp) -> bool:
    """
    Determine if a given date is a trading day
    
    Simplified rules:
    - Exclude weekends (Saturday, Sunday)
    - Exclude major US holidays (New Year's Day, Independence Day, Christmas, etc.)
    
    Args:
        date: Date to check
        
    Returns:
        True if it's a trading day, False if not
    """
    # Check if it's a weekend
    if date.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    
    # Check major US holidays
    year = date.year
    month = date.month
    day = date.day
    
    # New Year's Day (January 1)
    if month == 1 and day == 1:
        return False
    
    # Independence Day (July 4)
    if month == 7 and day == 4:
        return False
    
    # Christmas Day (December 25)
    if month == 12 and day == 25:
        return False
    
    # Martin Luther King Jr. Day (third Monday in January)
    if month == 1:
        third_monday = 15 + (7 - pd.Timestamp(year, 1, 15).weekday()) % 7
        if day == third_monday:
            return False
    
    # Presidents Day (third Monday in February)
    if month == 2:
        third_monday = 15 + (7 - pd.Timestamp(year, 2, 15).weekday()) % 7
        if day == third_monday:
            return False
    
    # Memorial Day (last Monday in May)
    if month == 5:
        last_monday = 31 - (pd.Timestamp(year, 5, 31).weekday() + 1) % 7
        if day == last_monday:
            return False
    
    # Labor Day (first Monday in September)
    if month == 9:
        first_monday = 1 + (7 - pd.Timestamp(year, 9, 1).weekday()) % 7
        if day == first_monday:
            return False
    
    # Columbus Day (second Monday in October)
    if month == 10:
        second_monday = 8 + (7 - pd.Timestamp(year, 10, 8).weekday()) % 7
        if day == second_monday:
            return False
    
    # Thanksgiving (fourth Thursday in November)
    if month == 11:
        fourth_thursday = 22 + (3 - pd.Timestamp(year, 11, 22).weekday()) % 7
        if day == fourth_thursday:
            return False
    
    return True


def get_next_trading_day(date: pd.Timestamp, max_search_days: int = 10) -> pd.Timestamp:
    """
    Get the next trading day after the specified date
    
    Args:
        date: Starting date
        max_search_days: Maximum search days to prevent infinite loops
        
    Returns:
        Date of the next trading day
    """
    current_date = date + pd.Timedelta(days=1)
    search_count = 0
    
    while search_count < max_search_days:
        if is_trading_day(current_date):
            return current_date
        current_date += pd.Timedelta(days=1)
        search_count += 1
    
    # If no trading day found, return original date plus one day (fallback handling)
    logger.warning(f"No trading day found within {max_search_days} days starting from {date}")
    return date + pd.Timedelta(days=1)


def _get_trading_days_between(start_date: str, end_date: str) -> int:
    """
    Calculate the number of trading days between two dates (rough estimate)
    Excludes weekends but not holidays (simplified handling)
    
    Args:
        start_date: Start date, format YYYY-MM-DD
        end_date: End date, format YYYY-MM-DD
        
    Returns:
        Estimated number of trading days
    """
    try:
        start_dt = pd.to_datetime(start_date).date()
        end_dt = pd.to_datetime(end_date).date()
        
        if start_dt > end_dt:
            return 0
            
        # Generate date range
        date_range = pd.date_range(start=start_dt, end=end_dt, freq='D')
        
        # Exclude weekends (Saturday=5, Sunday=6)
        trading_days = [d for d in date_range if d.weekday() < 5]
        
        return len(trading_days)
    except Exception as e:
        logger.warning(f"Failed to calculate trading days: {e}")
        # Fallback to simple estimate: total days * 5/7
        try:
            start_dt = pd.to_datetime(start_date).date()
            end_dt = pd.to_datetime(end_date).date()
            total_days = (end_dt - start_dt).days + 1
            return max(1, int(total_days * 5 / 7))
        except Exception:
            return 1


def _check_data_completeness(df: pd.DataFrame, start_date: str, end_date: str, min_completeness: float = 0.7) -> bool:
    """
    Check data completeness
    
    Args:
        df: Data DataFrame
        start_date: Expected start date
        end_date: Expected end date 
        min_completeness: Minimum completeness threshold (0.7 means at least 70% of data)
        
    Returns:
        True indicates data is sufficiently complete, False indicates API supplementation needed
    """
    try:
        if df.empty:
            return False
            
        expected_days = _get_trading_days_between(start_date, end_date)
        actual_days = len(df)
        
        if expected_days <= 0:
            return True  # Invalid date range, consider complete
            
        completeness = actual_days / expected_days
        
        logger.debug(f"Data completeness check: expected {expected_days} days, actual {actual_days} days, completeness {completeness:.2%}")
        
        return completeness >= min_completeness
    except Exception as e:
        logger.warning(f"Data completeness check failed: {e}")
        # Return False conservatively on error to trigger API call
        return False


def _normalize_timestamp_for_comparison(timestamp: pd.Timestamp) -> pd.Timestamp:
    """Unified timezone handling: convert timezone-aware timestamps to naive timestamps to avoid timezone comparison errors"""
    if timestamp is None or pd.isna(timestamp):
        return timestamp
    if timestamp.tz is not None:
        return timestamp.tz_localize(None)
    return timestamp


def _read_local_day_csv(symbol: str) -> pd.DataFrame:
    processed = os.path.join(_BACKTEST_DIR, f"{symbol}_prices_processed.csv")
    raw = os.path.join(_BACKTEST_DIR, f"{symbol}_prices.csv")
    path = processed if os.path.exists(processed) else raw
    if not os.path.exists(path):
        return _empty_bars_df()

    df = pd.read_csv(path)
    df = df.rename(columns={
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    })
    # Handle date timezone: remove timezone, convert to date column
    df["date"] = pd.to_datetime(df["date"], utc=True, errors="coerce").dt.tz_localize(None)
    df["date"] = df["date"].dt.date
    if "vwap" not in df.columns:
        df["vwap"] = df["close"]
    df = df[["date", "open", "high", "low", "close", "volume", "vwap"]]
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _write_partitioned_parquet(df: pd.DataFrame, symbol: str, granularity: str) -> None:
    if df.empty:
        return
    base_dir = os.path.join(_PARQUET_BASE, symbol, granularity)
    ensure_dir(base_dir)
    # Both daily and minute data are written by "date" partitions (minute partition filenames don't include time to avoid massive small files and illegal characters)
    key_col = "date"
    if key_col not in df.columns:
        return
    # Normalization and quality check (daily data only)
    df = _normalize_day_df(df)
    for key, g in df.groupby(key_col):
        fname = os.path.join(base_dir, f"{str(key)}.parquet")
        # Quality detection: duplicates/sparsity
        dups = _detect_duplicates(g, key_col)
        if dups > 0:
            _log_quality_issue("duplicate_rows", symbol, {"granularity": granularity, "key": str(key), "duplicates": dups})
        # Daily data only, minute sparsity check removed
        try:
            # Idempotent write by content hash (atomic replace)
            write_parquet_idempotent(g, fname, sort_by=[c for c in g.columns if c != key_col])
        except Exception as exc:  # pragma: no cover
            logger.warning(f"write parquet failed: {fname}: {exc}")


def _filter_day_by_date(df: pd.DataFrame, start: str, end: str, symbol: str = "UNKNOWN") -> pd.DataFrame:
    if df.empty:
        return df
    s = pd.to_datetime(start).date() if start else df["date"].min()
    e = pd.to_datetime(end).date() if end else df["date"].max()
    m = (df["date"] >= s) & (df["date"] <= e)
    out = df.loc[m].reset_index(drop=True)
    # Rough gap detection: record when adjacent date span > 5 days (doesn't precisely exclude weekends/holidays, just notification)
    if len(out) >= 2:
        diffs = pd.Series(out["date"]).sort_values().diff().dt.days.fillna(0)
        if (diffs > 5).any():
            _log_quality_issue("large_day_gap", symbol, {"max_gap_days": int(diffs.max())})
    return out


def _read_parquet_range(symbol: str, granularity: str, start: str, end: str) -> pd.DataFrame:
    try:
        base_dir = os.path.join(_PARQUET_BASE, symbol, granularity)
        if not os.path.isdir(base_dir):
            return pd.DataFrame([])
        files = [f for f in os.listdir(base_dir) if f.endswith('.parquet')]
        if not files:
            return pd.DataFrame([])
        s = pd.to_datetime(start).date() if start else None
        e = pd.to_datetime(end).date() if end else None
        selected: List[str] = []
        for f in files:
            try:
                d = pd.to_datetime(f.replace('.parquet', '')).date()
            except Exception:
                continue
            if (s is None or d >= s) and (e is None or d <= e):
                selected.append(os.path.join(base_dir, f))
        if not selected:
            return pd.DataFrame([])
        dfs = [pd.read_parquet(p) for p in sorted(selected)]
        df = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame([])
        # Daily data only
        df = _normalize_day_df(df)
        return df
    except Exception as exc:
        logger.warning(f"read parquet range failed: {symbol} {granularity} {start}-{end}: {exc}")
        return pd.DataFrame([])




# Get daily or minute data
def get_bars(ticker: str, start: str, end: str, multiplier: int, timespan: str, adjusted: bool, cfg: Optional[Dict] = None, **_kwargs) -> pd.DataFrame:
    try:
        if timespan == "day":
            # 1) Prioritize reading local Parquet partitions
            local = _read_parquet_range(ticker, "day", start, end)
            if not local.empty:
                # New: Check data completeness
                filtered_local = _filter_day_by_date(local, start, end, ticker)
                if _check_data_completeness(filtered_local, start, end):
                    logger.debug(f"✅ Local Parquet data complete, return directly: {ticker}, {len(filtered_local)} records")
                    return filtered_local
                else:
                    logger.info(f"⚠️ Local Parquet data incomplete, will try other sources: {ticker}, current {len(filtered_local)} records")
            
            # 2) Local CSV → write Parquet → filter
            df = _read_local_day_csv(ticker)
            df = _normalize_day_df(df)
            _write_partitioned_parquet(df, ticker, granularity="day")
            
            if not df.empty:
                filtered_csv = _filter_day_by_date(df, start, end, ticker)
                if _check_data_completeness(filtered_csv, start, end):
                    logger.debug(f"✅ Local CSV data complete, return directly: {ticker}, {len(filtered_csv)} records")
                    return filtered_csv
                else:
                    logger.info(f"⚠️ Local CSV data incomplete, will try other sources: {ticker}, current {len(filtered_csv)} records")
            
            # Check API availability before deciding strategy
            api_available = not _is_offline_only(cfg) and _polygon_client.api_key
            
            if not api_available:
                # API not available: return best available cached data (even if incomplete)
                logger.info(f"🔄 [API-UNAVAILABLE] API not available, returning best cached data")
                if not local.empty:
                    fallback_local = _filter_day_by_date(local, start, end, ticker)
                    logger.info(f"📊 [CACHE-FALLBACK] Returning incomplete Parquet data: {ticker}, {len(fallback_local)} records (API unavailable)")
                    return fallback_local
                elif not df.empty:
                    fallback_csv = _filter_day_by_date(df, start, end, ticker)
                    logger.info(f"📊 [CACHE-FALLBACK] Returning incomplete CSV data: {ticker}, {len(fallback_csv)} records (API unavailable)")
                    return fallback_csv
                else:
                    logger.info(f"[OFFLINE_ONLY] No cached data available for {ticker} {start}-{end}")
                    return _empty_bars_df()

            # 3) API is available: try to fetch complete data
            logger.info(f"🌐 [API-PRIORITY] API available, attempting to fetch complete data: {ticker}, {start} to {end}")
            try:
                aggs = _polygon_client.list_aggs(ticker, start, end, multiplier=1, timespan="day", adjusted=adjusted)
                if not aggs:
                    logger.warning(f"API returned no data: {ticker}, falling back to cached data")
                    # API returned no data: try cached data as fallback
                    if not local.empty:
                        fallback_local = _filter_day_by_date(local, start, end, ticker)
                        logger.info(f"📊 [API-FALLBACK] Returning cached Parquet data: {ticker}, {len(fallback_local)} records (API empty)")
                        return fallback_local
                    elif not df.empty:
                        fallback_csv = _filter_day_by_date(df, start, end, ticker)
                        logger.info(f"📊 [API-FALLBACK] Returning cached CSV data: {ticker}, {len(fallback_csv)} records (API empty)")
                        return fallback_csv
                    else:
                        return _empty_bars_df()
                
                df = pd.DataFrame([{
                    "date": pd.to_datetime(x.get("t"), unit="ms", utc=True).tz_localize(None).date(),
                    "open": x.get("o"),
                    "high": x.get("h"),
                    "low": x.get("l"),
                    "close": x.get("c"),
                    "volume": x.get("v"),
                    "vwap": x.get("vw", x.get("c")),
                } for x in aggs])
                df = _normalize_day_df(df)
                _write_partitioned_parquet(df, ticker, granularity="day")
                out = _filter_day_by_date(df, start, end, ticker)
                logger.info(f"✅ API fetch successful: {ticker}, {len(out)} records")
                return out
            except Exception as api_exc:
                logger.error(f"❌ API call failed for {ticker}: {api_exc}")
                logger.info(f"🔄 [API-FAILED] Falling back to cached data after API failure")
                # API call failed: try cached data as fallback
                if not local.empty:
                    fallback_local = _filter_day_by_date(local, start, end, ticker)
                    logger.info(f"📊 [API-FALLBACK] Returning cached Parquet data: {ticker}, {len(fallback_local)} records (API failed)")
                    return fallback_local
                elif not df.empty:
                    fallback_csv = _filter_day_by_date(df, start, end, ticker)
                    logger.info(f"📊 [API-FALLBACK] Returning cached CSV data: {ticker}, {len(fallback_csv)} records (API failed)")
                    return fallback_csv
                else:
                    logger.warning(f"❌ No cached data available for {ticker}")
                    return _empty_bars_df()
        else:
            return _empty_bars_df()
    except Exception as exc:  # pragma: no cover
        logger.exception(f"❌ get_bars failed: {ticker}: {exc}")
        # Try to return any available cached data even on error
        try:
            logger.info(f"🔄 [ERROR-RECOVERY] Attempting to return cached data after error")
            local_recovery = _read_parquet_range(ticker, "day", start, end)
            if not local_recovery.empty:
                filtered_recovery = _filter_day_by_date(local_recovery, start, end, ticker)
                logger.info(f"📊 [ERROR-RECOVERY] Returning {len(filtered_recovery)} cached records despite error")
                return filtered_recovery
        except Exception:
            pass
        return _empty_bars_df()

# Get daily data for entire market on specified date at once, experimental interface for future market scanning/stock selection
def get_grouped_daily(date: str, adjusted: bool, cfg: Optional[Dict] = None, **_kwargs) -> pd.DataFrame:
    try:
        if _is_offline_only(cfg):
            logger.info(f"[OFFLINE_ONLY] Skip API for get_grouped_daily: {date}")
            return pd.DataFrame([])
        res = _polygon_client.get_grouped_daily_aggs(date, adjusted)
        if not res:
            return pd.DataFrame([])
        df = pd.DataFrame(res)
        return df
    except Exception as exc:  # pragma: no cover
        logger.exception(f"get_grouped_daily failed: {date}: {exc}")
        return pd.DataFrame([])

# Get snapshot data for specified stock
def get_universal_snapshots(symbols: List[str], cfg: Optional[Dict] = None, **_kwargs) -> Dict[str, Dict]:
    try:
        if _is_offline_only(cfg):
            logger.info("[OFFLINE_ONLY] Skip API for get_universal_snapshots")
            return {s: {} for s in symbols}
        return _polygon_client.get_universal_snapshots(symbols)
    except Exception as exc:  # pragma: no cover
        logger.exception(f"get_universal_snapshots failed: {exc}")
        return {s: {} for s in symbols}

# Get gainers/losers list for specified stock
def get_gainers_losers(top_n: int, cfg: Optional[Dict] = None, **_kwargs) -> Dict[str, List[str]]:
    try:
        if _is_offline_only(cfg):
            logger.info("[OFFLINE_ONLY] Skip API for get_gainers_losers")
            return {"gainers": [], "losers": []}
        return _polygon_client.get_gainers_losers(top_n)
    except Exception as exc:  # pragma: no cover
        logger.exception(f"get_gainers_losers failed: {exc}")
        return {"gainers": [], "losers": []}

# Get news data
def get_news(ticker: str, gte: str, lte: str, limit: int = 100, page_token: Optional[str] = None, cfg: Optional[Dict] = None, **_kwargs) -> Tuple[List[Dict], Optional[str]]:
    """
    Get news data
    
    New strategy:
    1. Prioritize using Finnhub to get news
    2. If Finnhub news count is insufficient, supplement with Polygon
    3. Time range: N days before backtest date to 1 day before (N configured by config.news.lookback_days, avoid lookahead bias)
    4. Target: Get 5 news items (title + description)
    5. If still insufficient, leave empty
    
    Note: To avoid lookahead bias, automatically advance end date by one day
    """
    try:
        # Record function call parameters
        logger.info(f"📋 Start getting news - Stock: {ticker}, GTE: {gte}, LTE: {lte}, limit: {limit}, page_token: {page_token}")
        
        # Show cache statistics (only on first call)
        _show_news_cache_stats()
        
        # Get parameters from configuration, default to 5 news items
        target_news_count = cfg.get("news", {}).get("top_k_event_count", 5) if cfg else 5
        # Get lookback days from configuration, default to 3 days
        lookback_days = cfg.get("news", {}).get("lookback_days", 3) if cfg else 3
        logger.debug(f"📋 Configuration parameters - Target news count: {target_news_count}, Lookback days: {lookback_days}")
        
        # Calculate time range: avoid lookahead bias by using news from previous day(s)
        # Principle: If making decision on day T, only use news up to day T-1
        if lte and gte:
            # Both start and end dates specified
            raw_start_date = pd.to_datetime(gte)
            raw_end_date = pd.to_datetime(lte)
            
            if raw_start_date == raw_end_date:
                # Single day query for decision date T -> fetch news from day T-1
                # This maintains lookahead bias prevention for single day queries
                decision_date = raw_start_date
                end_date = decision_date - timedelta(days=1)
                start_date = end_date - timedelta(days=lookback_days - 1)
                logger.debug(f"📅 Single day decision query: decision_date={decision_date.strftime('%Y-%m-%d')}, news_range={start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
            else:
                # Multi-day range - apply lookahead bias prevention to end date only
                start_date = raw_start_date
                end_date = raw_end_date - timedelta(days=1)
                # Ensure valid range after adjustment
                if start_date > end_date:
                    # If adjustment creates invalid range, use single day before start_date
                    end_date = start_date - timedelta(days=1)
                    logger.warning(f"📅 Date range adjusted to prevent lookahead bias: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        elif lte:
            # Only end date specified (decision date)
            decision_date = pd.to_datetime(lte)
            end_date = decision_date - timedelta(days=1)
            start_date = end_date - timedelta(days=lookback_days - 1)
        elif gte:
            # Only start date specified - treat as decision date
            decision_date = pd.to_datetime(gte)
            end_date = decision_date - timedelta(days=1)
            start_date = end_date - timedelta(days=lookback_days - 1)
        else:
            # Neither exists, use current time as decision date
            decision_date = pd.Timestamp.now()
            end_date = decision_date - timedelta(days=1)
            start_date = end_date - timedelta(days=lookback_days - 1)
        
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')

        # Initialize cache variables for error recovery
        day_cached = None
        cached_result = None
        
        # 0) Try normalized per-day cache aggregation first
        day_cached = _read_news_from_day_cache_range(ticker, start_str, end_str, max_items=target_news_count)
        if day_cached and len(day_cached) >= target_news_count:
            logger.info(f"✅ [NEWS_DAY_CACHE] Hit day cache with sufficient data: {ticker}, {len(day_cached)}/{target_news_count}")
            return day_cached, None
        elif day_cached:
            logger.info(f"⚠️ [NEWS_DAY_CACHE] Found partial cache data: {ticker}, {len(day_cached)}/{target_news_count}, will try API")

        # 0.5) Deterministic hash cache (legacy) then write-through to day cache before return
        cache_key = _generate_news_cache_key(ticker, gte, lte, target_news_count, lookback_days)
        logger.debug(f"🔍 Check news cache - key: {cache_key[:16]}...")
        cached_result = _get_news_from_cache(cache_key, cfg)
        if cached_result is not None and len(cached_result) >= target_news_count:
            logger.info(f"✅ Get news from hash cache with sufficient data: {ticker}, key: {cache_key[:16]}..., count: {len(cached_result)}")
            try:
                _save_news_items_to_day_cache(ticker, cached_result)
            except Exception as _:
                pass
            return cached_result, None
        elif cached_result is not None:
            logger.info(f"⚠️ Hash cache has partial data: {ticker}, {len(cached_result)}/{target_news_count}, will try API")
        else:
            logger.debug(f"⚠️ Cache miss, need to fetch via API")

        # Check API availability before deciding strategy
        api_available = not _is_offline_only(cfg) and (_finnhub_client.is_available() or _polygon_client.api_key)
        
        if not api_available:
            # API not available: return best available cached data
            logger.info(f"🔄 [API-UNAVAILABLE] API not available for news, returning best cached data")
            
            # First try previously found partial cache data
            if day_cached:
                logger.info(f"📊 [CACHE-FALLBACK] Returning day cache data: {ticker}, {len(day_cached)} items (API unavailable)")
                return day_cached, None
            elif cached_result is not None:
                logger.info(f"📊 [CACHE-FALLBACK] Returning hash cache data: {ticker}, {len(cached_result)} items (API unavailable)")
                return cached_result, None
            
            # Try extended range search as last resort
            extended_start = (pd.to_datetime(start_str) - timedelta(days=2)).strftime('%Y-%m-%d')
            extended_end = (pd.to_datetime(end_str) + timedelta(days=2)).strftime('%Y-%m-%d')
            fallback_cached = _read_news_from_day_cache_range(ticker, extended_start, extended_end, max_items=target_news_count * 2)
            
            if fallback_cached:
                logger.info(f"📊 [CACHE-FALLBACK] Returning extended cache data: {ticker}, {len(fallback_cached)} items (API unavailable)")
                return fallback_cached[:target_news_count], None
            else:
                logger.info(f"[OFFLINE_ONLY] No cached news available for {ticker} {start_str}-{end_str}")
                return [], None
        
        # Validate time range (for debugging)
        logger.info(f"📅 News time range: {ticker}, Original end date: {raw_end_date.strftime('%Y-%m-%d')}, "
                   f"Adjusted range: {start_str} to {end_str} (avoid lookahead bias)")
        
        all_news_items = []
        
        # 1. Try Finnhub API only if client is available
        finnhub_available = _finnhub_client.is_available()
        logger.info(f"🔧 [Finnhub] Client status: {'available' if finnhub_available else 'unavailable'}")
        
        if finnhub_available:
            logger.info(f"🔍 [Finnhub] Start getting news: {ticker}, time range: {start_str} to {end_str}, request count: {target_news_count * 2}")
            try:
                finnhub_news = _finnhub_client.get_company_news(ticker, start_str, end_str, limit=target_news_count * 2)
                
                if finnhub_news:
                    # Record raw data statistics
                    logger.debug(f"📊 [Finnhub] Raw response: {len(finnhub_news)} news items")
                    
                    # Ensure has title and description
                    valid_finnhub_news = []
                    invalid_count = 0
                    for item in finnhub_news:
                        title = item.get('title', '').strip()
                        description = item.get('description', '').strip()
                        if title or description:  # At least one is not empty
                            valid_finnhub_news.append(item)
                        else:
                            invalid_count += 1
                            logger.debug(f"🚫 [Finnhub] Invalid news item: title='{title}', desc='{description}'")
                    
                    all_news_items.extend(valid_finnhub_news)
                    logger.info(f"✅ [Finnhub] Successfully got {len(valid_finnhub_news)} valid news items (raw: {len(finnhub_news)}, invalid: {invalid_count})")
                    
                    # Record time distribution
                    if valid_finnhub_news:
                        times = [item.get('published_utc', '') for item in valid_finnhub_news]
                        valid_times = [t for t in times if t]
                        if valid_times:
                            logger.debug(f"📅 [Finnhub] News time distribution: {min(valid_times)} to {max(valid_times)}")
                else:
                    logger.warning(f"⚠️ [Finnhub] No news obtained: {ticker}")
            except Exception as finnhub_exc:
                logger.error(f"❌ [Finnhub] Exception occurred while getting news: {finnhub_exc}", exc_info=True)
        else:
            logger.warning(f"❌ [Finnhub] Client unavailable, skip: {ticker}")
        
        # 2. Try Polygon API only if client is available and news count is insufficient
        if len(all_news_items) < target_news_count and _polygon_client.api_key:
            needed_count = target_news_count - len(all_news_items)
            logger.info(f"📊 News count insufficient ({len(all_news_items)}/{target_news_count}), start Polygon supplement")
            logger.info(f"🔍 [Polygon] Start getting news: {ticker}, need {needed_count} items")
            
            try:
                polygon_news_items = []
                cursor = None
                total_fetched = 0
                api_call_count = 0
                
                # Use same time range, no expansion
                while len(polygon_news_items) < needed_count * 2:  # Get more, then filter
                    api_call_count += 1
                    logger.debug(f"🌐 [Polygon] API call #{api_call_count}, cursor: {cursor}")
                    
                    try:
                        items, cursor = _polygon_client.list_ticker_news(
                            ticker, start_str, end_str, limit=limit, page_token=cursor
                        )
                        
                        if not items:
                            logger.debug(f"🔚 [Polygon] Call #{api_call_count} returned empty result, end fetch")
                            break
                        
                        total_fetched += len(items)
                        logger.debug(f"📊 [Polygon] Call #{api_call_count} got {len(items)} raw news items")
                        
                        # Filter valid news (has title or description)
                        valid_items = []
                        invalid_items = 0
                        for item in items:
                            title = item.get('title', '').strip()
                            description = item.get('description', '').strip()
                            if title or description:
                                valid_items.append(item)
                            else:
                                invalid_items += 1
                        
                        polygon_news_items.extend(valid_items)
                        logger.debug(f"✅ [Polygon] Call #{api_call_count}: valid {len(valid_items)}, invalid {invalid_items}")
                        
                        if not cursor:
                            logger.debug(f"🔚 [Polygon] No more pages, end fetch")
                            break
                    except Exception as api_exc:
                        logger.error(f"❌ [Polygon] API call #{api_call_count} failed: {api_exc}")
                        break
                
                # Sort by publish time, take latest
                if polygon_news_items:
                    logger.debug(f"📊 [Polygon] News count before sorting: {len(polygon_news_items)}")
                    polygon_news_items.sort(key=lambda x: x.get("published_utc", ""), reverse=True)
                    
                    # Record time distribution
                    times = [item.get('published_utc', '') for item in polygon_news_items]
                    valid_times = [t for t in times if t]
                    if valid_times:
                        logger.debug(f"📅 [Polygon] News time distribution: {min(valid_times)} to {max(valid_times)}")
                    
                    # Only take needed count
                    original_count = len(polygon_news_items)
                    polygon_news_items = polygon_news_items[:needed_count]
                    logger.debug(f"🎯 [Polygon] Cut to needed count: {len(polygon_news_items)}/{original_count}")
                    
                    # Ensure no duplicates (based on title and publish time)
                    existing_titles = {item.get('title', '') for item in all_news_items}
                    unique_polygon_items = []
                    duplicates_count = 0
                    for item in polygon_news_items:
                        title = item.get('title', '')
                        if title not in existing_titles:
                            unique_polygon_items.append(item)
                        else:
                            duplicates_count += 1
                            logger.debug(f"🔄 [Polygon] Found duplicate news: {title[:50]}...")
                    
                    all_news_items.extend(unique_polygon_items)
                    logger.info(f"✅ [Polygon] Supplement complete - API calls: {api_call_count}, raw: {total_fetched}, valid: {len(polygon_news_items)}, dedup: {duplicates_count}, final added: {len(unique_polygon_items)}")
                else:
                    logger.warning(f"⚠️ [Polygon] Still no valid news after {api_call_count} calls: {ticker}")
                
            except Exception as exc:
                logger.error(f"❌ [Polygon] Exception occurred while getting news: {exc}", exc_info=True)
        elif len(all_news_items) >= target_news_count:
            logger.info(f"✅ [Finnhub] Requirements satisfied, skip Polygon: {len(all_news_items)}/{target_news_count} items")
        else:
            logger.warning(f"⚠️ [API-UNAVAILABLE] Both Finnhub and Polygon APIs unavailable, using existing news: {len(all_news_items)} items")
        
        # 4. Limit final count
        original_total = len(all_news_items)
        final_news_items = all_news_items[:target_news_count]
        logger.debug(f"🎯 Final count limit: {len(final_news_items)}/{original_total} (target: {target_news_count})")
        
        # Count data sources
        finnhub_count = sum(1 for item in final_news_items if item.get('api_source', '').startswith('finnhub'))
        polygon_count = sum(1 for item in final_news_items if item.get('api_source', '') == 'polygon')
        other_count = len(final_news_items) - finnhub_count - polygon_count
        
        # Record detailed data quality statistics
        if final_news_items:
            titles_count = sum(1 for item in final_news_items if item.get('title', '').strip())
            descriptions_count = sum(1 for item in final_news_items if item.get('description', '').strip())
            logger.debug(f"📊 Data quality statistics: has title {titles_count}/{len(final_news_items)}, has description {descriptions_count}/{len(final_news_items)}")
        
        # 5. Cache results if we got any data from APIs
        if final_news_items:
            _save_news_to_cache(cache_key, final_news_items, cfg)
            try:
                _save_news_items_to_day_cache(ticker, final_news_items)
            except Exception as _:
                pass
        
        # Enhanced completion log showing data source distribution
        source_info = []
        if finnhub_count > 0:
            source_info.append(f"Finnhub: {finnhub_count}")
        if polygon_count > 0:
            source_info.append(f"Polygon: {polygon_count}")
        if other_count > 0:
            source_info.append(f"Other: {other_count}")
        
        source_summary = ", ".join(source_info) if source_info else "No API data source"
        
        if len(final_news_items) < target_news_count:
            logger.warning(f"📰 News fetch incomplete: {ticker}, got {len(final_news_items)}/{target_news_count} | Data source: [{source_summary}]")
            # Analyze possible reasons for insufficient fetch
            reasons = []
            if not finnhub_available:
                reasons.append("Finnhub unavailable")
            if not _polygon_client.api_key:
                reasons.append("Polygon API key missing")
            if original_total < target_news_count:
                reasons.append(f"Total fetch insufficient({original_total})")
            if reasons:
                logger.debug(f"🔍 Possible reasons for insufficient fetch: {', '.join(reasons)}")
        else:
            logger.info(f"📰 News fetch complete: {ticker}, successfully got target count: {len(final_news_items)}/{target_news_count} | Data source distribution: [{source_summary}]")
        
        return final_news_items, None
        
    except Exception as exc:
        logger.error(f"❌ get_news function execution failed - Stock: {ticker}, Error: {exc}", exc_info=True)
        # Record detailed context on failure
        logger.debug(f"🔍 Failure context: gte={gte}, lte={lte}, limit={limit}, cfg={cfg}")
        
        # Try to return cached data as last resort
        logger.info(f"🔄 [ERROR-RECOVERY] Attempting to return cached news data after error")
        try:
            # First try previously found partial cache data
            if day_cached:
                logger.info(f"📊 [ERROR-RECOVERY] Returning day cache data: {ticker}, {len(day_cached)} items despite error")
                return day_cached, None
            elif cached_result is not None:
                logger.info(f"📊 [ERROR-RECOVERY] Returning hash cache data: {ticker}, {len(cached_result)} items despite error")
                return cached_result, None
        except:
            pass
        
        return [], None

# Get dividend data
def get_dividends(ticker: str, cfg: Optional[Dict] = None, **_kwargs) -> pd.DataFrame:
    """Get dividends with local persistent cache preferred.
    Cache path: storage/cache/corporate_actions/{ticker}.dividends.json
    Falls back to backtest_data/dividends.json when cache/API unavailable.
    """
    # 0) Local persistent cache first
    try:
        ensure_dir(_CORP_ACTIONS_DIR)
        cache_path = os.path.join(_CORP_ACTIONS_DIR, f"{ticker}.dividends.json")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) >= 0:
                return pd.DataFrame(data)
    except Exception:
        pass

    # Strict offline: skip API
    if _is_offline_only(cfg):
        try:
            path = os.path.join(_BACKTEST_DIR, "dividends.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rows = [it for it in data if (it.get("ticker") == ticker)]
                return pd.DataFrame(rows)
        except Exception as exc:
            logger.warning(f"local dividends read failed: {exc}")
        return pd.DataFrame([])

    # 1) API
    try:
        items = _polygon_client.list_dividends(ticker)
        if items is None:
            items = []
        # Write-through cache
        try:
            ensure_dir(_CORP_ACTIONS_DIR)
            cache_path = os.path.join(_CORP_ACTIONS_DIR, f"{ticker}.dividends.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
        except Exception:
            pass
        return pd.DataFrame(items)
    except Exception:
        pass

    # 2) Legacy local fallback
    try:
        path = os.path.join(_BACKTEST_DIR, "dividends.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = [it for it in data if (it.get("ticker") == ticker)]
            return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning(f"local dividends read failed: {exc}")
    return pd.DataFrame([])

# Get stock split data
def get_splits(ticker: str, cfg: Optional[Dict] = None, **_kwargs) -> pd.DataFrame:
    """Get stock splits with local persistent cache preferred.
    Cache path: storage/cache/corporate_actions/{ticker}.splits.json
    Falls back to backtest_data/splits.json when cache/API unavailable.
    """
    # 0) Local persistent cache first
    try:
        ensure_dir(_CORP_ACTIONS_DIR)
        cache_path = os.path.join(_CORP_ACTIONS_DIR, f"{ticker}.splits.json")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list) and len(data) >= 0:
                return pd.DataFrame(data)
    except Exception:
        pass

    # Strict offline: skip API
    if _is_offline_only(cfg):
        try:
            path = os.path.join(_BACKTEST_DIR, "splits.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                rows = [it for it in data if (it.get("ticker") == ticker)]
                return pd.DataFrame(rows)
        except Exception as exc:
            logger.warning(f"local splits read failed: {exc}")
        return pd.DataFrame([])

    # 1) API
    try:
        items = _polygon_client.list_splits(ticker)
        if items is None:
            items = []
        # Write-through cache
        try:
            ensure_dir(_CORP_ACTIONS_DIR)
            cache_path = os.path.join(_CORP_ACTIONS_DIR, f"{ticker}.splits.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
        except Exception:
            pass
        return pd.DataFrame(items)
    except Exception:
        pass

    # 2) Legacy local fallback
    try:
        path = os.path.join(_BACKTEST_DIR, "splits.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = [it for it in data if (it.get("ticker") == ticker)]
            return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning(f"local splits read failed: {exc}")
    return pd.DataFrame([])

# Get detailed data for specified stock
def get_ticker_details(ticker: str, date: Optional[str] = None, cfg: Optional[Dict] = None, **_kwargs) -> Dict:
    try:
        if _is_offline_only(cfg):
            logger.info("[OFFLINE_ONLY] Skip API for get_ticker_details")
            return {}
        return _polygon_client.get_ticker_details(ticker, date)
    except Exception:
        return {}

# Get market status
def get_market_status(cfg: Optional[Dict] = None, **_kwargs) -> Dict:
    try:
        if _is_offline_only(cfg):
            logger.info("[OFFLINE_ONLY] Skip API for get_market_status")
            return {"market": "unknown"}
        return _polygon_client.get_market_status()
    except Exception:
        return {"market": "unknown"}

# Align local CSV (legacy) with current Parquet (daily) format, output difference report and save to reports/alignments.
def compare_with_legacy_day(symbol: str, start: str, end: str, tolerance: float = 1e-6) -> Dict[str, object]:
    """Align local CSV (legacy) with current Parquet (daily) format, output difference report and save to reports/alignments.
    Returns brief statistics dictionary.
    """
    try:
        legacy = _read_local_day_csv(symbol)
        legacy = _normalize_day_df(legacy)
        # Read local saved daily Parquet partitions
        base_dir = os.path.join(_PARQUET_BASE, symbol, "day")
        rows: List[pd.DataFrame] = []
        if os.path.isdir(base_dir):
            for fname in os.listdir(base_dir):
                if not fname.endswith(".parquet"):
                    continue
                d = fname.replace(".parquet", "")
                rows.append(pd.read_parquet(os.path.join(base_dir, fname)))
        current = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=legacy.columns)
        current = _normalize_day_df(current)
        # Filter interval
        s = pd.to_datetime(start).date() if start else None
        e = pd.to_datetime(end).date() if end else None
        if s:
            legacy = legacy[legacy["date"] >= s]
            current = current[current["date"] >= s]
        if e:
            legacy = legacy[legacy["date"] <= e]
            current = current[current["date"] <= e]
        # Merge and compare close price differences
        merged = legacy.merge(current, on="date", how="inner", suffixes=("_legacy", "_cur"))
        merged["close_diff"] = (merged["close_legacy"] - merged["close_cur"]).abs()
        mismatch = int((merged["close_diff"] > tolerance).sum())
        report = {
            "symbol": symbol,
            "start": start,
            "end": end,
            "rows_compared": int(len(merged)),
            "mismatch_close_gt_tol": mismatch,
            "tolerance": tolerance,
        }
        out_dir = os.path.join(_REPORT_BASE, "alignments")
        ensure_dir(out_dir)
        atomic_append_jsonl(os.path.join(out_dir, f"{symbol}.jsonl"), report)
        return report
    except Exception as exc:  # pragma: no cover
        logger.warning(f"compare_with_legacy_day failed for {symbol}: {exc}")
        return {"symbol": symbol, "error": str(exc)}

# Get financial statements (annual/quarterly). Permanent file caching enabled by default.
def get_financials(ticker: str, timeframe: Optional[str] = None, limit: int = 50, use_cache: bool = True, cfg: Optional[Dict] = None, **_kwargs) -> List[Dict]:
    """Get financial statements (annual/quarterly). Permanent file caching enabled by default.
    Cache key: storage/cache/financials/{ticker}.{timeframe or all}.json
    """
    try:
        cache_dir = os.path.join(_CACHE_BASE, "financials")
        ensure_dir(cache_dir)
        cache_name = f"{ticker}.{timeframe or 'all'}.json"
        cache_path = os.path.join(cache_dir, cache_name)
        if use_cache and os.path.exists(cache_path):
            # Remove TTL control - cache is permanently valid
            # st = os.stat(cache_path)
            # if (datetime.now().timestamp() - st.st_mtime) <= 24 * 3600:
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data[:limit]
            except Exception:
                # Corrupted: delete and treat as cache miss
                try:
                    os.remove(cache_path)
                except Exception:
                    pass
                    
        # Strict offline: skip API
        if _is_offline_only(cfg):
            logger.info(f"[OFFLINE_ONLY] Skip API for get_financials: {ticker}")
            return []

        items = _polygon_client.list_financials(ticker, timeframe=timeframe, limit=limit)
        # Simple deduplication (by reporting period + file type or id)
        seen: set[str] = set()
        deduped: List[Dict] = []
        for it in items or []:
            key = str(it.get("id") or (it.get("fiscal_period"), it.get("fiscal_year"), it.get("start_date"), it.get("end_date")))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(it)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(deduped, f, ensure_ascii=False)
        except Exception:
            pass
        return deduped[:limit]
    except Exception as exc:  # pragma: no cover
        logger.warning(f"get_financials failed: {ticker}: {exc}")
        return []


def get_stock_indicators(ticker: str, date: Optional[str] = None, use_cache: bool = True, cfg: Optional[Dict] = None, **_kwargs) -> Dict[str, float]:
    """Get stock key metrics including market cap, P/E ratio, dividend yield, 52-week high/low, etc.
    
    Args:
        ticker: Stock symbol
        date: Query date (YYYY-MM-DD), uses latest data if empty
        use_cache: Whether to use cache
        
    Returns:
        Dict containing: market_cap, pe_ratio, dividend_yield, week_52_high, week_52_low, quarterly_dividend
    """
    import json
    import os
    from datetime import datetime, timedelta
    
    logger.info(f"📊 [FUNDAMENTAL_DATA] get_stock_indicators called:")
    logger.info(f"  - ticker: {ticker}")
    logger.info(f"  - date: {date}")
    logger.info(f"  - use_cache: {use_cache}")
    
    try:
        # Set cache
        cache_dir = os.path.join(_CACHE_BASE, "stock_indicators")
        ensure_dir(cache_dir)
        
        # Cache key contains date information
        cache_date = date or datetime.now().strftime("%Y-%m-%d")
        cache_name = f"{ticker}_{cache_date}.json"
        cache_path = os.path.join(cache_dir, cache_name)
        
        # Check cache
        if use_cache and os.path.exists(cache_path):
            try:
                # For historical dates, always trust cache
                is_today = (cache_date == datetime.now().strftime("%Y-%m-%d"))
                if not is_today:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                        if isinstance(cached_data, dict):
                            logger.debug(f"Using cached stock metrics data (historical): {ticker}@{cache_date}")
                            return cached_data
                else:
                    # Same-day cache: respect TTL 12h
                    st = os.stat(cache_path)
                    if (datetime.now().timestamp() - st.st_mtime) <= 12 * 3600:
                        with open(cache_path, "r", encoding="utf-8") as f:
                            cached_data = json.load(f)
                            if isinstance(cached_data, dict):
                                logger.debug(f"Using cached stock metrics data (today, within TTL): {ticker}")
                                return cached_data
                    else:
                        # Expired: let it refresh below
                        pass
            except Exception:
                # Cache corrupted, delete
                try:
                    os.remove(cache_path)
                except Exception:
                    pass
        
        # Strict offline mode: read precomputed indicator cache only; do not derive
        if _is_offline_only(cfg):
            try:
                if os.path.exists(cache_path):
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    if isinstance(cached_data, dict):
                        return cached_data
            except Exception as _off_exc:
                logger.warning(f"[OFFLINE_ONLY] Failed to read precomputed indicators: {ticker}@{cache_date}: {_off_exc}")
            return {
                "market_cap": 0.0,
                "pe_ratio": 0.0,
                "dividend_yield": 0.0,
                "week_52_high": 0.0,
                "week_52_low": 0.0,
                "quarterly_dividend": 0.0
            }

        # 🔧 CACHE-FIRST STRATEGY: Try APIs only if available, otherwise use cached data
        logger.info(f"🚀 [FUNDAMENTAL_DATA] Attempting to get stock indicators for {ticker}")
        indicators = None
        data_source = "unknown"
        
        # Try Finnhub API only if client is available
        if _finnhub_client.is_available():
            try:
                logger.info(f"📡 [FUNDAMENTAL_DATA] Calling Finnhub client with ticker={ticker}, date={date}")
                indicators = _finnhub_client.get_stock_indicators(ticker, date)
                if indicators and any(indicators.get(key, 0) > 0 for key in ["market_cap", "pe_ratio", "week_52_high", "week_52_low"]):
                    data_source = "finnhub"
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Finnhub successfully returned data for {ticker}")
                else:
                    logger.warning(f"⚠️ [FUNDAMENTAL_DATA] Finnhub returned empty/invalid data for {ticker}")
                    indicators = None
            except Exception as e:
                logger.warning(f"❌ [FUNDAMENTAL_DATA] Finnhub failed for {ticker}: {e}")
                indicators = None
        else:
            logger.info(f"🔍 [FUNDAMENTAL_DATA] Finnhub client unavailable")
        
        # Try Polygon API only if Finnhub failed and Polygon client has API key
        if indicators is None and _polygon_client.api_key:
            logger.info(f"🔄 [FUNDAMENTAL_DATA] Trying Polygon API for {ticker}")
            try:
                logger.info(f"📡 [FUNDAMENTAL_DATA] Calling Polygon client with ticker={ticker}, date={date}")
                indicators = _polygon_client.get_stock_indicators(ticker, date)
                if indicators and any(indicators.get(key, 0) > 0 for key in ["market_cap", "pe_ratio", "week_52_high", "week_52_low"]):
                    data_source = "polygon"
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Polygon successfully returned data for {ticker}")
                else:
                    logger.warning(f"⚠️ [FUNDAMENTAL_DATA] Polygon returned empty/invalid data for {ticker}")
                    indicators = None
            except Exception as e:
                logger.warning(f"❌ [FUNDAMENTAL_DATA] Polygon failed for {ticker}: {e}")
                indicators = None
        elif indicators is None:
            logger.warning(f"⚠️ [FUNDAMENTAL_DATA] Polygon API key not available")
        
        # If both APIs failed or unavailable, try cached data first, then use default values
        if indicators is None:
            logger.info(f"🔄 [API-FAILED] Both APIs failed/unavailable for {ticker}, trying cached data")
            # Try to load cached data as fallback
            try:
                if os.path.exists(cache_path):
                    with open(cache_path, "r", encoding="utf-8") as f:
                        cached_data = json.load(f)
                    if isinstance(cached_data, dict) and any(cached_data.get(key, 0) > 0 for key in ["market_cap", "pe_ratio", "week_52_high", "week_52_low"]):
                        indicators = cached_data
                        data_source = "cache_fallback" 
                        logger.info(f"📊 [CACHE-FALLBACK] Using cached indicators for {ticker}")
            except Exception as cache_exc:
                logger.warning(f"Failed to load cached indicators: {cache_exc}")
            
            # Final fallback to default values
            if indicators is None:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] No cached data available for {ticker}, using default values")
                indicators = {
                    "market_cap": 0.0,
                    "pe_ratio": 0.0,
                    "dividend_yield": 0.0,
                    "week_52_high": 0.0,
                    "week_52_low": 0.0,
                    "quarterly_dividend": 0.0
                }
                data_source = "default_fallback"
        
        # Add data source identifier
        if indicators:
            indicators["data_source"] = data_source
            logger.info(f"📊 [FUNDAMENTAL_DATA] Final indicators for {ticker} from {data_source}: market_cap={indicators.get('market_cap', 0):.0f}")
        
        # Cache results
        try:
            if indicators:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(indicators, f, ensure_ascii=False)
        except Exception as cache_exc:
            logger.warning(f"⚠️ Failed to cache stock metrics data: {ticker}, {cache_exc}")
        
        logger.info(f"✅ [FUNDAMENTAL_DATA] Final result for {ticker} (source: {data_source}):")
        logger.info(f"  - Market Cap: ${indicators.get('market_cap', 0):,.0f}")
        logger.info(f"  - P/E: {indicators.get('pe_ratio', 0):.2f}")
        logger.info(f"  - Dividend Yield: {indicators.get('dividend_yield', 0):.2f}%")
        logger.info(f"  - 52-Week High: ${indicators.get('week_52_high', 0):.2f}")
        logger.info(f"  - 52-Week Low: ${indicators.get('week_52_low', 0):.2f}")
        logger.info(f"  - Quarterly Dividend: ${indicators.get('quarterly_dividend', 0):.4f}")
        
        return indicators
        
    except Exception as exc:
        logger.error(f"❌ [FUNDAMENTAL_DATA] Failed to get stock metrics: {ticker}, {exc}")
        logger.exception("Detailed error:")
        return {
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "dividend_yield": 0.0,
            "week_52_high": 0.0,
            "week_52_low": 0.0,
            "quarterly_dividend": 0.0,
            "data_source": "error_fallback"
        }


def clear_old_news_cache() -> None:
    """Clean corrupted news cache files (time-based cleaning removed)"""
    try:
        cache_dir = os.path.join(_CACHE_BASE, "news")
        if not os.path.exists(cache_dir):
            return
            
        # Remove time-based cleaning logic
        # cutoff_time = datetime.now().timestamp() - 24 * 3600  # 24 hours ago
        
        for filename in os.listdir(cache_dir):
            if not filename.endswith('.json'):
                continue
                
            filepath = os.path.join(cache_dir, filename)
            try:
                # Only check if file is corrupted, no longer clean based on time
                with open(filepath, "r", encoding="utf-8") as f:
                    json.load(f)  # Try to load JSON, check if corrupted
                # st = os.stat(filepath)
                # if st.st_mtime < cutoff_time:
                #     os.remove(filepath)
                #     logger.info(f"Clean expired cache: {filename}")
            except json.JSONDecodeError:
                # File corrupted, delete
                try:
                    os.remove(filepath)
                    logger.info(f"Clean corrupted cache: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to clean cache file {filename}: {e}")
            except Exception as e:
                logger.warning(f"Failed to check cache file {filename}: {e}")
                
    except Exception as e:
        logger.warning(f"Cache cleanup failed: {e}")


def get_cache_info() -> Dict[str, object]:
    """Get cache information for debugging and monitoring"""
    try:
        cache_dir = os.path.join(_CACHE_BASE, "news")
        if not os.path.exists(cache_dir):
            return {"status": "no_cache_dir"}
            
        cache_files = []
        total_size = 0
        
        for filename in os.listdir(cache_dir):
            if not filename.endswith('.json'):
                continue
                
            filepath = os.path.join(cache_dir, filename)
            try:
                st = os.stat(filepath)
                with open(filepath, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                
                cache_info = {
                    "filename": filename,
                    "size_bytes": st.st_size,
                    "format": "unknown"
                }
                
                if isinstance(cached_data, list):
                    cache_info["format"] = "legacy_list"
                    cache_info["item_count"] = len(cached_data)
                elif isinstance(cached_data, dict) and "items" in cached_data and "metadata" in cached_data:
                    cache_info["format"] = "new_with_metadata"
                    cache_info["item_count"] = len(cached_data.get("items", []))
                else:
                    cache_info["format"] = "unknown_structure"
                
                cache_files.append(cache_info)
                total_size += st.st_size
                
            except Exception as e:
                cache_files.append({
                    "filename": filename,
                    "error": str(e),
                    "format": "corrupted"
                })
                
        return {
            "status": "ok",
            "cache_dir": cache_dir,
            "total_files": len(cache_files),
            "total_size_bytes": total_size,
            "files": cache_files
        }
        
    except Exception as e:
        return {"status": "error", "error": str(e)}
from __future__ import annotations

from typing import Dict, List, Set, Optional
from collections import Counter
import logging

import math
import numpy as np
import pandas as pd
try:
    from stockbench.core.schemas import FeatureInput, TechSnapshot, NewsSnapshot, PositionState
except ImportError:
    # If import fails, use simple type annotations
    from typing import Any
    FeatureInput = Any
    TechSnapshot = Any
    NewsSnapshot = Any
    PositionState = Any

# Set up logger
logger = logging.getLogger(__name__)

def _compute_stock_indicators(ticker: str, date: str, current_price: float) -> Dict[str, float]:
    """Calculate key stock indicator features
    
    Args:
        ticker: Stock symbol
        date: Current date (YYYY-MM-DD format)
        current_price: Current stock price
        
    Returns:
        Dict containing normalized stock indicator features
    """
    from . import data_hub
    import logging
    import math
    
    logger = logging.getLogger(__name__)
    
    out: Dict[str, float] = {
        "market_cap": 0.0,               # Market cap (USD)
        "pe_ratio": 0.0,                 # P/E ratio (raw value)
        "dividend_yield": 0.0,           # Dividend yield (raw value, percentage)
        "week_52_high": 0.0,             # 52-week high price (USD)
        "week_52_low": 0.0,              # 52-week low price (USD)
        "quarterly_dividend": 0.0,       # Quarterly dividend amount (USD/share)
    }
    
    try:
        # Get stock indicator data
        logger.info(f"📊 [FUNDAMENTAL_DATA] Getting stock indicators for {ticker} on {date}")
        indicators = data_hub.get_stock_indicators(ticker, date)
        logger.info(f"📊 [FUNDAMENTAL_DATA] Retrieved {len(indicators) if indicators else 0} indicators: {list(indicators.keys()) if indicators else 'None'}")
        
        # 1. Use raw market cap directly
        market_cap = indicators.get("market_cap", 0)
        if market_cap > 0:
            out["market_cap"] = float(market_cap)
        
        # 2. Use raw P/E ratio directly
        pe_ratio = indicators.get("pe_ratio", 0)
        if pe_ratio > 0:
            out["pe_ratio"] = float(pe_ratio)
        
        # 3. Use raw dividend yield directly
        dividend_yield = indicators.get("dividend_yield", 0)
        if dividend_yield > 0:
            out["dividend_yield"] = float(dividend_yield)
        
        # 4. Use 52-week high and low prices directly
        week_52_high = indicators.get("week_52_high", 0)
        week_52_low = indicators.get("week_52_low", 0)
        
        if week_52_high > 0:
            out["week_52_high"] = float(week_52_high)
        if week_52_low > 0:
            out["week_52_low"] = float(week_52_low)
        
        # 5. Use quarterly dividend amount directly
        quarterly_dividend = indicators.get("quarterly_dividend", 0)
        if quarterly_dividend > 0:
            out["quarterly_dividend"] = float(quarterly_dividend)
        
        logger.info(f"✅ [FUNDAMENTAL_DATA] Stock indicators computed for {ticker}: market_cap={out['market_cap']}, pe_ratio={out['pe_ratio']:.2f}, quarterly_dividend={out.get('quarterly_dividend', 0)}")
        
    except Exception as e:
        logger.error(f"❌ [FUNDAMENTAL_DATA] Failed to compute stock indicators for {ticker}: {e}")
    
    final_result = out
    logger.info(f"🔄 [FUNDAMENTAL_DATA] Returning fundamental data for {ticker}: {final_result}")
    return final_result


def build_features_for_prompt(
    bars_day: pd.DataFrame,
    snapshot: Dict,
    news_items: List[Dict],
    position_state: Dict,
    details: Dict,
    config: Dict,
    include_price: bool = True,
    exclude_fundamental: bool = False,  # Whether to exclude fundamental data
) -> Dict:
    """
    Build feature data for new prompt format, compatible with single_agent_v1.txt prompt format
    
    Input:
        bars_day: Daily bar data
        snapshot: Market snapshot
        news_items: News data
        position_state: Position state containing the following fields:
            - current_position_value: Current position market value (cash amount)
            - holding_days: Number of holding days
            - shares: Current shares held, used for position size and trading decisions
        details: Asset details
        config: Configuration dictionary
        include_price: Whether to include current price field (needed for live trading, can be set to False for backtesting)
        exclude_fundamental: Whether to exclude fundamental data (used for conditional data loading in dual-agent architecture)
    Output:
        Feature data that conforms to single_agent_v1.txt prompt format, including:
        - market_data: Market data (ticker, open, close_7d)
        - fundamental_data: Fundamental data (only included when exclude_fundamental=False)
        - news_events: News events (top_k_events)
        - position_state: Position state (current_position_value, holding_days, shares)
        
    Note:
        - Only supports daily data, does not use minute-level data
        - During backtesting, strictly excludes data from the decision day to prevent lookahead bias
        - During live trading, can include current day data
        - Output format strictly follows the schema defined in single_agent_v1.txt
        - When exclude_fundamental=True, fundamental_data section will not be calculated or included, used for dual-agent architecture optimization
    """
    # Check if debug output is enabled
    enable_debug = config.get("backtest", {}).get("enable_detailed_logging", False) if config else False
    
    # Add logger
    import logging
    logger = logging.getLogger(__name__)
    
    # Start logging
    logger.info(f"🌟 [FUNDAMENTAL_DATA] build_features_for_prompt called")
    
    if enable_debug:
        logger.debug(f"\n=== build_features_for_prompt function called ===")
        logger.debug(f"Asset details: {details} (type: {type(details)})")
        logger.debug(f"Snapshot data: {snapshot} (type: {type(snapshot)})")
        logger.debug(f"Position state: {position_state} (type: {type(position_state)})")
        logger.debug(f"News count: {len(news_items) if news_items else 0}")
        logger.debug(f"Daily data length: {len(bars_day) if not bars_day.empty else 0}")
        logger.debug(f"Include price: {include_price}")
    
    # Log detailed key input parameters
    logger.info(f"🔍 [FUNDAMENTAL_DATA] Input parameters analysis:")
    logger.info(f"  - details: {details} (type: {type(details)})")
    logger.info(f"  - snapshot: {snapshot} (type: {type(snapshot)})")
    logger.info(f"  - position_state: {position_state} (type: {type(position_state)})")
    logger.info(f"  - bars_day empty: {bars_day.empty if hasattr(bars_day, 'empty') else 'N/A'}")
    logger.info(f"  - include_price: {include_price}")
    
    # Parameter validation
    if position_state is None:
        if enable_debug:
            logger.debug("Warning: position_state is None, will use default values")
        position_state = {}
    elif not isinstance(position_state, dict):
        if enable_debug:
            logger.debug(f"Warning: position_state is not dict type, but {type(position_state)}, will use default values")
        position_state = {}
    
    if details is None:
        if enable_debug:
            logger.debug("Warning: details is None, will use default values")
        details = {}
    elif not isinstance(details, dict):
        if enable_debug:
            logger.debug(f"Warning: details is not dict type, but {type(details)}, will use default values")
        details = {}
    
    if snapshot is None:
        if enable_debug:
            logger.debug("Warning: snapshot is None, will use default values")
        snapshot = {}
    elif not isinstance(snapshot, dict):
        if enable_debug:
            logger.debug(f"Warning: snapshot is not dict type, but {type(snapshot)}, will use default values")
        snapshot = {}
    
    try:
        # Get configuration parameters
        features_cfg = config.get("features", {})
        history_cfg = features_cfg.get("history", {})
        news_cfg = features_cfg.get("news", {})
        position_cfg = features_cfg.get("position", {})
        
        # Historical data window configuration
        price_series_days = int(history_cfg.get("price_series_days", 7))
          
        # Get top_k_event_count from news configuration
        news_top_k = config.get("news", {}).get("top_k_event_count", 5)
        
        # Only support daily mode
        if enable_debug:
            logger.debug("Only support daily data processing")
        
        # Unify and sort data
        day_df = bars_day.copy() if isinstance(bars_day, pd.DataFrame) else pd.DataFrame([])
        
        if not day_df.empty and "date" in day_df.columns:
            day_df = day_df.sort_values("date").reset_index(drop=True)
        
        # Determine basic information
        symbol = (details.get("ticker") if isinstance(details, dict) else None) or \
                 (snapshot.get("symbol") if isinstance(snapshot, dict) else None) or "UNKNOWN"
        
        logger.info(f"🎯 [FUNDAMENTAL_DATA] Symbol determination:")
        logger.info(f"  - details.get('ticker'): {details.get('ticker') if isinstance(details, dict) else 'N/A'}")
        logger.info(f"  - snapshot.get('symbol'): {snapshot.get('symbol') if isinstance(snapshot, dict) else 'N/A'}")
        logger.info(f"  - final symbol: {symbol}")
        
        # Get current price - used for internal calculations (such as fundamental data), not affected by include_price
        current_price = None
        logger.info(f"💰 [FUNDAMENTAL_DATA] Current price determination for internal calculations:")
        # Note: No longer limited by include_price here, because fundamental data calculation requires price
        try:
            # 1. Prioritize using price from snapshot
            if isinstance(snapshot, dict) and snapshot.get("price"):
                price_val = snapshot["price"]
                logger.info(f"  - Found price in snapshot: {price_val} (type: {type(price_val)})")
                logger.debug(f"Get price from snapshot: {price_val} (type: {type(price_val)})")
                current_price = float(price_val) if price_val is not None else None
                
            # 2. If no snapshot price, use current day's opening price (backtesting scenario)
            elif not day_df.empty and "open" in day_df.columns:
                price_val = day_df["open"].iloc[-1]  # Opening price of the latest day
                logger.info(f"  - Using day_df open price: {price_val} (type: {type(price_val)})")
                logger.debug(f"Get current price from daily opening price: {price_val} (type: {type(price_val)})")
                current_price = float(price_val) if price_val is not None else None
                
            # 3. Last fallback: use previous day's closing price
            elif not day_df.empty and "close" in day_df.columns:
                price_val = day_df["close"].iloc[-1]
                logger.info(f"  - Using day_df close price: {price_val} (type: {type(price_val)})")
                logger.debug(f"Get price from daily closing price: {price_val} (type: {type(price_val)})")
                current_price = float(price_val) if price_val is not None else None
            else:
                logger.warning(f"  - No price source available!")
                
            logger.info(f"  - Final current_price for internal use: {current_price} (type: {type(current_price)})")
            logger.debug(f"Final current price (for internal calculations): {current_price} (type: {type(current_price)})")
        except (ValueError, TypeError) as e:
            logger.error(f"  - Error getting current price: {e}")
            current_price = None
        
        # Build price series - fix duplicate data issue
        close_series = []
        try:
            if not day_df.empty and "close" in day_df.columns:
                # Ensure data is sorted by date and deduplicated
                if "date" in day_df.columns:
                    day_df_clean = day_df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
                    if enable_debug:
                        logger.debug(f"Data rows before deduplication: {len(day_df)}, after: {len(day_df_clean)}")
                else:
                    day_df_clean = day_df.drop_duplicates(keep="last")
                    if enable_debug:
                        logger.debug(f"Data rows before deduplication: {len(day_df)}, after: {len(day_df_clean)}")
                
                # Get closing prices from day 1 to day 7 ago (excluding current day)
                if len(day_df_clean) > 1:
                    # Fix: Exclude the last row (current day), then take the previous N days
                    # Ensure we don't go beyond available data
                    available_historical_data = len(day_df_clean) - 1  # Exclude current day
                    if available_historical_data > 0:
                        # Take available historical data, up to price_series_days
                        start_idx = max(0, available_historical_data - price_series_days)
                        end_idx = available_historical_data  # Exclude the last (current) day
                        close_data = day_df_clean["close"].iloc[start_idx:end_idx]
                        if enable_debug:
                            logger.debug(f"Fixed closing price data (last {len(close_data)} days): {close_data.tolist()}")
                            logger.debug(f"Data range: iloc[{start_idx}:{end_idx}] from {len(day_df_clean)} total rows")
                    else:
                        close_data = pd.Series([], dtype=float)
                        if enable_debug:
                            logger.debug(f"No historical data available after excluding current day")
                else:
                    close_data = pd.Series([], dtype=float)
                    if enable_debug:
                        logger.debug(f"Insufficient data, cannot get closing prices for the past {price_series_days} days")
                
                # Convert to float and handle None values
                close_series = []
                for val in close_data:
                    if val is not None and not pd.isna(val):
                        close_series.append(float(val))
                    else:
                        close_series.append(0.0)
                
                # If data is insufficient, pad with 0s at the front
                if len(close_series) < price_series_days:
                    close_series = [0.0] * (price_series_days - len(close_series)) + close_series
                
                if enable_debug:
                    logger.debug(f"Processed price series: {close_series}")
                
                # Validate the reasonableness of the price series
                unique_prices = len(set([p for p in close_series if p != 0.0]))  # Exclude padding zeros
                non_zero_prices = len([p for p in close_series if p != 0.0])
                if non_zero_prices > 0 and unique_prices < non_zero_prices * 0.3:  # If duplication rate exceeds 70%
                    logger.warning(f"⚠️ [PRICE_DATA_ISSUE] High duplication rate in price series for {symbol}: unique={unique_prices}, total_non_zero={non_zero_prices}, series={close_series}")
                    if enable_debug:
                        logger.debug(f"Warning: High duplication rate in price series, unique price count: {unique_prices}/{non_zero_prices} (excluding zeros)")
                
            else:
                if enable_debug:
                    logger.debug("Daily data is empty or missing close column, using default price series")
                close_series = [0.0] * price_series_days
        except Exception as e:
            if enable_debug:
                logger.debug(f"Error building price series: {e}")
            close_series = [0.0] * price_series_days
        
        
        
        # Process news data
        top_events = []
        try:
            if news_items:
                logger.info(f"📰 [NEWS_PROCESSING] Processing {len(news_items)} news items for {symbol}, target: {news_top_k}")
                if enable_debug:
                    logger.debug(f"Original news data count: {len(news_items)}")
                    logger.debug(f"Using top_k_event_count: {news_top_k}")
                
                # Prioritize title+description combination mode (Mode B), fall back to title-only mode (Mode A) when description is missing
                for i, item in enumerate(news_items[:news_top_k]):
                    title = item.get("title", "")
                    description = item.get("description", "")
                    
                    if title and description and description.strip():
                        # Mode B: Title + description combination
                        combined_content = f"{title.strip()} - {description.strip()}"
                        top_events.append(combined_content)
                        if enable_debug:
                            logger.debug(f"Using mode B (title+description) [{i+1}]: {title[:50]}...")
                    elif title:
                        # Mode A: Title-only fallback
                        top_events.append(title.strip())
                        if enable_debug:
                            logger.debug(f"Using mode A (title only) [{i+1}]: {title[:50]}...")
                    else:
                        logger.warning(f"⚠️ [NEWS_PROCESSING] Empty news item [{i+1}] for {symbol}: {item}")
                    
                logger.info(f"✅ [NEWS_PROCESSING] Processed {len(top_events)} news events for {symbol} (target: {news_top_k})")
                if enable_debug:
                    logger.debug(f"Number of processed news events: {len(top_events)}")
                    for i, event in enumerate(top_events):
                        logger.debug(f"Event {i+1}: {event[:100]}...")
                        
                # Warn if we didn't get enough news
                if len(top_events) < news_top_k:
                    logger.warning(f"⚠️ [NEWS_DATA_INSUFFICIENT] Only got {len(top_events)} news events for {symbol}, expected {news_top_k}")
            else:
                logger.warning(f"⚠️ [NEWS_PROCESSING] No news data available for {symbol}")
                if enable_debug:
                    logger.debug("No news data")
        except Exception as e:
            logger.error(f"❌ [NEWS_PROCESSING] Error processing news data for {symbol}: {e}")
            if enable_debug:
                logger.debug(f"Error processing news data: {e}")
            top_events = []
        
        # Check all possible None values before building feature structure
        if enable_debug:
            logger.debug(f"\n=== Data check before building features ===")
            logger.debug(f"day_df is empty: {day_df.empty}")
            logger.debug(f"day_df columns: {list(day_df.columns) if not day_df.empty else 'N/A'}")
            if not day_df.empty and "open" in day_df.columns:
                open_value = day_df["open"].iloc[-1]
                logger.debug(f"open value: {open_value}, type: {type(open_value)}")
            
            logger.debug(f"position_state content: {position_state}")
            logger.debug(f"position_state type: {type(position_state)}")
            
            # Check values in position_state one by one
            for key in ["current_position_value", "holding_days", "shares"]:
                value = position_state.get(key) if isinstance(position_state, dict) else None
                logger.debug(f"{key}: {value}, type: {type(value)}")
        
        # Build feature structure - use deduplicated data to get opening price
        try:
            if 'day_df_clean' in locals() and not day_df_clean.empty and "open" in day_df_clean.columns:
                open_price = float(day_df_clean["open"].iloc[-1]) if day_df_clean["open"].iloc[-1] is not None else 0.0
            elif not day_df.empty and "open" in day_df.columns:
                open_price = float(day_df["open"].iloc[-1]) if day_df["open"].iloc[-1] is not None else 0.0
            else:
                open_price = 0.0
            
            # Only output opening price log when rebalance_at_open is True
            rebalance_at_open = config.get("backtest", {}).get("rebalance_at_open", True) if config else True
            if rebalance_at_open:
                logger.debug(f"Using opening price: {open_price}")
        except Exception as e:
            logger.debug(f"Error getting opening price: {e}")
            open_price = 0.0
        
        # Get trading date for stock indicator calculation
        trading_date = None
        logger.info(f"📅 [FUNDAMENTAL_DATA] Trading date determination:")
        if isinstance(snapshot, dict) and snapshot.get("ts_utc"):
            try:
                ts_str = snapshot["ts_utc"]
                logger.info(f"  - Found ts_utc in snapshot: {ts_str}")
                if ts_str and not ts_str.startswith("1970-"):
                    dt = pd.to_datetime(ts_str)
                    trading_date = dt.strftime("%Y-%m-%d")
                    logger.info(f"  - Parsed trading_date from snapshot: {trading_date}")
                    if enable_debug:
                        logger.debug(f"Extract trading date from snapshot: {trading_date}")
                else:
                    logger.warning(f"  - Invalid ts_utc: {ts_str}")
            except Exception as e:
                logger.error(f"  - Failed to parse ts_utc from snapshot: {e}")
                if enable_debug:
                    logger.debug(f"Failed to extract date from snapshot: {e}")
        else:
            logger.warning(f"  - No ts_utc in snapshot")
        
        # If no date in snapshot, try to extract from daily data
        if not trading_date and not day_df.empty and "date" in day_df.columns:
            logger.info(f"  - Attempting to extract date from day_df")
            try:
                last_date = day_df["date"].iloc[-1]
                logger.info(f"    - last_date from day_df: {last_date} (type: {type(last_date)})")
                if last_date is not None:
                    dt = pd.to_datetime(last_date)
                    trading_date = dt.strftime("%Y-%m-%d")
                    logger.info(f"    - parsed trading_date from day_df: {trading_date}")
                    if enable_debug:
                        logger.debug(f"Extract trading date from daily data: {trading_date}")
                else:
                    logger.warning(f"    - last_date is None")
            except Exception as e:
                logger.error(f"    - Failed to extract date from day_df: {e}")
                if enable_debug:
                    logger.debug(f"Failed to extract date from daily data: {e}")
        else:
            logger.info(f"  - day_df is empty or missing date column")
        
        # If still no date, use current date as fallback
        if not trading_date:
            from datetime import datetime
            trading_date = datetime.now().strftime("%Y-%m-%d")
            logger.warning(f"  - Using current date as fallback: {trading_date}")
            if enable_debug:
                logger.debug(f"Using current date as fallback: {trading_date}")
        
        logger.info(f"  - Final trading_date: {trading_date}")
        
        # Build market data section
        market_data = {
            "ticker": symbol,
            "open": open_price,
            "close_7d": close_series,
            "date": trading_date
        }
        
        # Only add price field when include_price=True
        if include_price and current_price is not None:
            market_data["price"] = current_price
        
        # Calculate stock fundamental indicators (conditional calculation)
        fundamental_data = {}
        if not exclude_fundamental:
            logger.info(f"🚀 [FUNDAMENTAL_DATA] Starting fundamental data computation for {symbol}")
            logger.info(f"🔍 [FUNDAMENTAL_DATA] Parameter validation:")
            logger.info(f"  - symbol: '{symbol}' (valid: {bool(symbol and symbol != 'UNKNOWN')})")
            logger.info(f"  - trading_date: '{trading_date}' (valid: {bool(trading_date)})")
            logger.info(f"  - current_price: {current_price} (valid: {bool(current_price and current_price > 0)})")
            
            try:
                if symbol and symbol != "UNKNOWN" and trading_date and current_price and current_price > 0:
                    logger.info(f"✅ [FUNDAMENTAL_DATA] All parameters valid, calling _compute_stock_indicators({symbol}, {trading_date}, {current_price})")
                    stock_indicators = _compute_stock_indicators(symbol, trading_date, current_price)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Got stock_indicators from _compute_stock_indicators: {stock_indicators}")
                    fundamental_data.update(stock_indicators)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Updated fundamental_data: {fundamental_data}")
                    if enable_debug:
                        logger.debug(f"Stock indicator features added: {list(stock_indicators.keys())}")
                else:
                    missing_params = []
                    if not symbol or symbol == "UNKNOWN":
                        missing_params.append(f"symbol({symbol})")
                    if not trading_date:
                        missing_params.append(f"trading_date({trading_date})")
                    if not current_price or current_price <= 0:
                        missing_params.append(f"current_price({current_price})")
                    logger.warning(f"❌ [FUNDAMENTAL_DATA] Missing or invalid required params: {', '.join(missing_params)}")
            except Exception as e:
                logger.error(f"❌ [FUNDAMENTAL_DATA] Failed to compute fundamental data: {e}")
                logger.exception("Detailed error:")
                if enable_debug:
                    logger.debug(f"Stock indicator feature calculation failed: {e}")
                # Set default values
                fundamental_data = {
                    "market_cap_tier": 0.0,
                    "pe_ratio_norm": 0.0,
                    "dividend_yield_norm": 0.0,
                    "week_52_position": 0.0,
                    "week_52_momentum": 0.0,
                    "dividend_strength": 0.0
                }
                logger.info(f"⚠️ [FUNDAMENTAL_DATA] Using default fundamental_data: {fundamental_data}")
            
            logger.info(f"📦 [FUNDAMENTAL_DATA] Final fundamental_data for {symbol}: {fundamental_data}")
        else:
            logger.info(f"⏭️ [FUNDAMENTAL_DATA] Skipping fundamental data computation for {symbol} (exclude_fundamental=True)")
        
        # Build feature data, conditionally include fundamental_data
        feature_sections = {
            "market_data": market_data,
            "news_events": {
                "top_k_events": top_events if top_events else ["No news available"]
            },
            "position_state": {
                "current_position_value": float(position_state.get("current_position_value", 0.0)) if isinstance(position_state, dict) else 0.0,
                "holding_days": int(position_state.get("holding_days", 0)) if isinstance(position_state, dict) else 0,
                "shares": float(position_state.get("shares", 0.0)) if isinstance(position_state, dict) else 0.0
            }
        }
        
        # Only include fundamental_data section when not excluding fundamental data
        if not exclude_fundamental:
            feature_sections["fundamental_data"] = fundamental_data
        
        features = {
            "symbol": symbol,
            "features": feature_sections
        }
        
        if enable_debug:
            logger.debug(f"\n=== Built feature data ===")
            logger.debug(f"Symbol: {symbol}")
            logger.debug(f"Current price: {current_price}")
            logger.debug(f"Price series: {close_series}")
            logger.debug(f"News events: {top_events}")
            logger.debug(f"Position state: {features['features']['position_state']}")
            logger.debug(f"Fundamental data: {'included' if not exclude_fundamental else 'excluded'}")
            if not exclude_fundamental:
                logger.debug(f"Fundamental indicators: {list(fundamental_data.keys())}")
            logger.debug(f"=== Feature building completed ===\n")
        
        return features
        
    except Exception as e:
        # Error handling: return default format
        enable_debug = config.get("backtest", {}).get("enable_detailed_logging", False) if config else False
        if enable_debug:
            logger.debug(f"=== Error occurred during feature building ===")
            logger.debug(f"Error message: {e}")
            logger.debug(f"Returning default format")
        logger.warning(f"Error occurred during feature building: {e}, returning default format")
        
        # Build default market_data, decide whether to include price field based on include_price
        market_data = {
            "ticker": "UNKNOWN",
            "open": 0.0,
            "close_7d": [0.0] * 7,
            "date": "1970-01-01"
        }
        
        # Only add price field when include_price=True
        if include_price:
            market_data["price"] = 0.0
        
        # Build default feature data, conditionally include fundamental_data
        default_feature_sections = {
            "market_data": market_data,
            "news_events": {
                "top_k_events": ["No news available"]
            },
            "position_state": {
                "current_position_value": 0.0,
                "holding_days": 0,
                "shares": 0.0
            }
        }
        
        # Only include fundamental_data section when not excluding fundamental data
        if not exclude_fundamental:
            default_fundamental_data = {
                "market_cap_tier": 0.0,
                "pe_ratio_norm": 0.0,
                "dividend_yield_norm": 0.0,
                "week_52_position": 0.0,
                "week_52_momentum": 0.0,
                "dividend_strength": 0.0
            }
            default_feature_sections["fundamental_data"] = default_fundamental_data
        
        return {
            "symbol": "UNKNOWN",
            "features": default_feature_sections
        }


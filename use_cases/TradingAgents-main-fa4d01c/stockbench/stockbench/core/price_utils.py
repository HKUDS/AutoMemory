"""
Unified price retrieval and position value calculation utilities

Solves problems of inconsistent price data field names and confusing calculation logic
"""

from typing import Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)


def get_unified_price(symbol: str, ctx: Dict, portfolio: Dict = None, 
                     price_type: str = "open", fallback_price: float = None) -> Optional[float]:
    """
    Unified price retrieval function, solves field name inconsistency issues
    
    Args:
        symbol: Stock symbol
        ctx: Strategy context, may contain open_map, open_price_map, etc.
        portfolio: Portfolio data, may contain open_prices, open_price_map, open_map, etc.
        price_type: Price type ("open", "close", "mark")
        fallback_price: Final fallback price
    
    Returns:
        Price value or None
    """
    
    # Determine lookup fields based on price type
    if price_type == "open":
        field_names = ["open_map", "open_price_map", "open_prices"]
    elif price_type == "close":
        field_names = ["close_map", "close_price_map", "close_prices", "mark_map"]
    elif price_type == "mark":
        field_names = ["mark_map", "mark_price_map", "mark_prices", "close_map"]
    else:
        logger.warning(f"Unsupported price type: {price_type}")
        return fallback_price
    
    # 1. Priority retrieval from ctx
    if ctx:
        logger.debug(f"[PRICE_UTIL] {symbol}: Starting to search {price_type} price from ctx, available fields: {list(ctx.keys())}")
        for field_name in field_names:
            price_map = ctx.get(field_name, {})
            logger.debug(f"[PRICE_UTIL] {symbol}: Checking ctx.{field_name}, type={type(price_map)}, length={len(price_map) if isinstance(price_map, dict) else 'N/A'}")
            if isinstance(price_map, dict) and symbol in price_map:
                price = price_map[symbol]
                if price is not None and price > 0:
                    logger.debug(f"[PRICE_UTIL] {symbol}: Got {price_type} price from ctx.{field_name} = {price:.4f}")
                    return float(price)
                else:
                    logger.debug(f"[PRICE_UTIL] {symbol}: ctx.{field_name}[{symbol}] price invalid: {price}")
            elif isinstance(price_map, dict):
                logger.debug(f"[PRICE_UTIL] {symbol}: {symbol} not found in ctx.{field_name}, has: {list(price_map.keys())[:5]}")
    else:
        logger.debug(f"[PRICE_UTIL] {symbol}: ctx is empty")
    
    # 2. Fallback to portfolio retrieval
    if portfolio:
        for field_name in field_names:
            price_map = portfolio.get(field_name, {})
            if isinstance(price_map, dict) and symbol in price_map:
                price = price_map[symbol]
                if price is not None and price > 0:
                    logger.debug(f"[PRICE_UTIL] {symbol}: Got {price_type} price from portfolio.{field_name} = {price:.4f}")
                    return float(price)
    
    # 3. Final fallback
    if fallback_price is not None and fallback_price > 0:
        logger.debug(f"[PRICE_UTIL] {symbol}: Using fallback price = {fallback_price:.4f}")
        return float(fallback_price)
    
    logger.warning(f"[PRICE_UTIL] {symbol}: Unable to get {price_type} price")
    return None


def calculate_position_value(symbol: str, shares: float, ctx: Dict, portfolio: Dict = None, 
                           position_avg_price: float = None) -> float:
    """
    Unified position value calculation function
    
    Args:
        symbol: Stock symbol
        shares: Number of shares held
        ctx: Strategy context
        portfolio: Portfolio data
        position_avg_price: Position average cost price
    
    Returns:
        Position value (amount)
    """
    
    if shares == 0:
        return 0.0
    
    # Price retrieval priority:
    # 1. Opening price (for intraday trading decisions)
    # 2. Market price/closing price (for valuation)
    # 3. Average cost price (final fallback)
    
    # Try to get opening price
    open_price = get_unified_price(symbol, ctx, portfolio, "open")
    if open_price is not None:
        value = shares * open_price
        logger.debug(f"[POSITION_VALUE] {symbol}: {shares:.2f} shares × {open_price:.4f} (open) = {value:.2f}")
        return value
    
    # Fallback to market price/closing price
    mark_price = get_unified_price(symbol, ctx, portfolio, "mark")
    if mark_price is not None:
        value = shares * mark_price
        logger.debug(f"[POSITION_VALUE] {symbol}: {shares:.2f} shares × {mark_price:.4f} (mark) = {value:.2f}")
        return value
    
    # Final fallback to average cost price
    if position_avg_price is not None and position_avg_price > 0:
        value = shares * position_avg_price
        logger.debug(f"[POSITION_VALUE] {symbol}: {shares:.2f} shares × {position_avg_price:.4f} (avg_price) = {value:.2f}")
        return value
    
    logger.warning(f"[POSITION_VALUE] {symbol}: Unable to calculate position value, shares={shares}")
    return 0.0


def add_price_fallback_mechanism(price_map: Dict[str, float], 
                                historical_prices: Dict[str, float] = None,
                                default_price: float = 100.0,
                                is_holiday: bool = False) -> Dict[str, float]:
    """
    Add fallback mechanism for price data, handle API data missing issues
    
    Args:
        price_map: Current price mapping
        historical_prices: Historical prices as fallback
        default_price: Final default price
        is_holiday: Whether it's a market holiday (affects log output)
    
    Returns:
        Enhanced price mapping
    """
    
    enhanced_map = price_map.copy()
    symbols_used_historical = []
    
    # If historical prices available, use to supplement missing prices
    if historical_prices:
        for symbol, hist_price in historical_prices.items():
            if symbol not in enhanced_map or enhanced_map[symbol] is None or enhanced_map[symbol] <= 0:
                if hist_price is not None and hist_price > 0:
                    enhanced_map[symbol] = hist_price
                    symbols_used_historical.append(symbol)
                    if is_holiday:
                        logger.info(f"[HOLIDAY_FALLBACK] {symbol}: Holiday using previous day price {hist_price:.4f}")
                    else:
                        logger.info(f"[PRICE_FALLBACK] {symbol}: Using historical price {hist_price:.4f}")
    
    # Final fallback: set default values for still missing prices
    symbols_with_missing_prices = []
    for symbol, price in enhanced_map.items():
        if price is None or price <= 0:
            enhanced_map[symbol] = default_price
            symbols_with_missing_prices.append(symbol)
    
    if symbols_with_missing_prices:
        if is_holiday:
            logger.warning(f"[HOLIDAY_FALLBACK] Holiday following stocks have no historical data, using default price {default_price}: {symbols_with_missing_prices}")
        else:
            logger.warning(f"[PRICE_FALLBACK] Following stocks using default price {default_price}: {symbols_with_missing_prices}")
    
    # Summary log
    if is_holiday and symbols_used_historical:
        logger.info(f"[HOLIDAY_FALLBACK] Holiday successfully used previous day data for assets: {symbols_used_historical}")
    
    return enhanced_map


def validate_price_data_consistency(ctx: Dict, portfolio: Dict = None) -> Dict[str, Any]:
    """
    Validate price data consistency, used for debugging
    
    Returns:
        Validation result report
    """
    
    report = {
        "ctx_fields": [],
        "portfolio_fields": [],
        "inconsistencies": [],
        "missing_data": []
    }
    
    # Check price fields in ctx
    price_fields = ["open_map", "open_price_map", "close_map", "mark_map"]
    for field in price_fields:
        if field in ctx:
            report["ctx_fields"].append({
                "field": field,
                "type": type(ctx[field]).__name__,
                "count": len(ctx[field]) if isinstance(ctx[field], dict) else 0
            })
    
    # Check price fields in portfolio
    if portfolio:
        portfolio_price_fields = ["open_prices", "open_price_map", "open_map", "close_prices", "mark_prices"]
        for field in portfolio_price_fields:
            if field in portfolio:
                report["portfolio_fields"].append({
                    "field": field,
                    "type": type(portfolio[field]).__name__,
                    "count": len(portfolio[field]) if isinstance(portfolio[field], dict) else 0
                })

    return report

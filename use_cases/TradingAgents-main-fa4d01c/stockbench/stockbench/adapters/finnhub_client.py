import os
import finnhub
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger


class FinnhubError(Exception):
    """Finnhub API specific exception class"""
    
    def __init__(self, message: str = "", error_code: Optional[str] = None, payload: Optional[dict] = None) -> None:
        super().__init__(f"FinnhubError: {message}")
        self.error_code = error_code  # Error code
        self.payload = payload or {}


class FinnhubClient:
    """Finnhub API client"""
    
    def __init__(self, api_key: Optional[str] = None, timeout: int = 30):
        """
        Initialize Finnhub client
        
        Args:
            api_key: Finnhub API key, if None then get from environment variable
            timeout: Request timeout in seconds (default: 30)
        """
        self.api_key = api_key or os.getenv("FINNHUB_API_KEY", "")
        self.timeout = timeout
        if not self.api_key:
            logger.warning("Finnhub API key not set")
            self.client = None
        else:
            try:
                self.client = finnhub.Client(api_key=self.api_key)
                # Set default timeout
                self.client.DEFAULT_TIMEOUT = timeout
                logger.info(f"Finnhub client initialized successfully with timeout: {timeout}s")
            except Exception as e:
                logger.error(f"Finnhub client initialization failed: {e}")
                self.client = None
    
    def is_available(self) -> bool:
        """Check if Finnhub is available"""
        return self.client is not None
    
    def get_company_news(self, symbol: str, start_date: str, end_date: str, limit: int = 100, timeout: Optional[int] = None) -> List[Dict]:
        """
        Get company news
        
        Args:
            symbol: Stock symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            limit: Maximum number of results to return
            timeout: Request timeout in seconds (if None, uses client default)
            
        Returns:
            Standardized news list
        """
        logger.debug(f"🔍 [Finnhub] API request parameters: symbol={symbol}, start_date={start_date}, end_date={end_date}, limit={limit}")
        
        if not self.is_available():
            logger.error("❌ [Finnhub] Client unavailable - check API key or network connection")
            return []
        
        try:
            # Record API call start
            import time
            start_time = time.time()
            
            # Set timeout for this specific API call
            news_timeout = timeout if timeout is not None else self.timeout
            logger.debug(f"🌐 [Finnhub] Starting API call: company_news({symbol}, {start_date}, {end_date}) with timeout: {news_timeout}s")
            
            # Temporarily set timeout for this API call
            original_timeout = self.client.DEFAULT_TIMEOUT
            self.client.DEFAULT_TIMEOUT = news_timeout
            
            try:
                # Use company_news API with specific timeout
                news_data = self.client.company_news(symbol, _from=start_date, to=end_date)
            finally:
                # Restore original timeout
                self.client.DEFAULT_TIMEOUT = original_timeout
            
            # Record API call duration
            elapsed_time = time.time() - start_time
            logger.debug(f"⏱️ [Finnhub] API call completed, time taken: {elapsed_time:.2f}s")
            
            if not news_data:
                logger.warning(f"⚠️ [Finnhub] API returned empty result - symbol: {symbol}, time range: {start_date} to {end_date}")
                return []
            
            # Record raw data statistics
            logger.debug(f"📊 [Finnhub] API raw response: {len(news_data)} news items")
            
            # Validate data quality
            if news_data:
                # Check timestamp distribution
                timestamps = [item.get('datetime', 0) for item in news_data if item.get('datetime', 0) > 0]
                if timestamps:
                    from datetime import datetime
                    times_str = [datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') for ts in timestamps[:3]]
                    logger.debug(f"📅 [Finnhub] News timestamp examples: {', '.join(times_str)}...")
                else:
                    logger.warning(f"⚠️ [Finnhub] Found news with invalid timestamps")
                
                # Check data integrity
                headlines_count = sum(1 for item in news_data if item.get('headline', '').strip())
                summaries_count = sum(1 for item in news_data if item.get('summary', '').strip())
                logger.debug(f"📝 [Finnhub] Data integrity: headlines {headlines_count}/{len(news_data)}, summaries {summaries_count}/{len(news_data)}")
            
            # Sort by time (newest first)
            original_count = len(news_data)
            news_data = sorted(news_data, key=lambda x: x.get('datetime', 0), reverse=True)
            logger.debug(f"🔄 [Finnhub] Time sorting completed")
            
            # Apply quantity limit
            if len(news_data) > limit:
                news_data = news_data[:limit]
                logger.debug(f"✂️ [Finnhub] Applied quantity limit: {len(news_data)}/{original_count} (limit: {limit})")
            
            # Standardize data format
            standardized_news = []
            conversion_errors = 0
            
            for idx, item in enumerate(news_data):
                try:
                    # Extract and validate timestamp
                    datetime_ts = item.get('datetime', 0)
                    if datetime_ts and datetime_ts > 0:
                        published_utc = datetime.fromtimestamp(datetime_ts).isoformat()
                    else:
                        published_utc = ''
                        if idx < 3:  # Only log warnings for the first few to avoid excessive logging
                            logger.debug(f"⚠️ [Finnhub] News item {idx+1} has no valid timestamp")
                    
                    standardized_item = {
                        'title': item.get('headline', '').strip(),
                        'description': item.get('summary', '').strip(),
                        'published_utc': published_utc,
                        'source': item.get('source', ''),
                        'url': item.get('url', ''),
                        'category': item.get('category', ''),
                        'image': item.get('image', ''),
                        'related_symbols': [symbol],  # Finnhub company news default associated with queried stock
                        'api_source': 'finnhub_company',
                        'id': item.get('id', ''),
                        'datetime': datetime_ts
                    }
                    standardized_news.append(standardized_item)
                    
                except Exception as convert_exc:
                    conversion_errors += 1
                    if conversion_errors <= 3:  # Only log the first few errors
                        logger.warning(f"⚠️ [Finnhub] News item {idx+1} format conversion failed: {convert_exc}")
            
            if conversion_errors > 0:
                logger.warning(f"⚠️ [Finnhub] Total {conversion_errors} news items failed format conversion")
            
            # Final quality validation
            if standardized_news:
                valid_items = sum(1 for item in standardized_news if item.get('title') or item.get('description'))
                logger.info(f"✅ [Finnhub] Successfully retrieved {symbol} news: {len(standardized_news)} items (valid content: {valid_items})")
                
                # Log time range validation
                valid_times = [item['published_utc'] for item in standardized_news if item['published_utc']]
                if valid_times:
                    time_range = f"{min(valid_times)} to {max(valid_times)}"
                    logger.debug(f"📅 [Finnhub] Actual news time range: {time_range}")
            else:
                logger.warning(f"⚠️ [Finnhub] No valid news after formatting")
                
            return standardized_news
            
        except Exception as e:
            error_str = str(e).lower()
            logger.error(f"❌ [Finnhub] Failed to retrieve {symbol} news: {e}", exc_info=True)
            
            # For API-related explicit errors, throw FinnhubError for caller to handle
            if any(keyword in error_str for keyword in ["api", "key", "unauthorized", "permission", "forbidden", "rate limit"]):
                # Detailed error diagnosis
                if "api" in error_str or "key" in error_str:
                    logger.debug(f"🔍 [Finnhub] API key related issue: check API key validity")
                    raise FinnhubError(f"API key error: {e}", error_code="API_KEY_ERROR")
                elif "unauthorized" in error_str or "permission" in error_str or "forbidden" in error_str:
                    logger.debug(f"🔍 [Finnhub] Permission issue: check API key validity and account permissions")
                    raise FinnhubError(f"Permission denied: {e}", error_code="PERMISSION_ERROR")
                elif "rate limit" in error_str:
                    logger.debug(f"🔍 [Finnhub] Rate limit: suggest reducing request frequency")
                    raise FinnhubError(f"Request rate too high: {e}", error_code="RATE_LIMIT_ERROR")
                else:
                    raise FinnhubError(f"API error: {e}", error_code="API_ERROR")
            
            # For temporary errors like network timeout, log but return empty list
            elif "timeout" in error_str:
                logger.debug(f"🔍 [Finnhub] Timeout issue: check network connection stability")
                return []
            else:
                # Return empty list for other unknown errors to avoid program interruption
                logger.debug(f"🔍 [Finnhub] Unknown error, returning empty result")
                return []
    
    def get_company_profile(self, symbol: str) -> Dict:
        """
        Get basic company information, including market capitalization
        
        Args:
            symbol: Stock symbol
            
        Returns:
            A dictionary containing basic company information
        """
        if not self.is_available():
            logger.warning("Finnhub client unavailable")
            return {}
        
        try:
            logger.debug(f"🏢 [Finnhub] Getting company profile for {symbol}")
            profile = self.client.company_profile2(symbol=symbol)
            
            if profile:
                logger.debug(f"✅ [Finnhub] Successfully retrieved profile for {symbol}")
                return profile
            else:
                logger.warning(f"⚠️ [Finnhub] No profile data for {symbol}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ [Finnhub] Failed to get company profile for {symbol}: {e}")
            return {}
    
    def get_basic_financials(self, symbol: str, metric: str = "all") -> Dict:
        """
        Get basic financial metrics
        
        Args:
            symbol: Stock symbol
            metric: Metric type ("all", "valuation", "growth", etc.)
            
        Returns:
            A dictionary containing financial metrics
        """
        if not self.is_available():
            logger.warning("Finnhub client unavailable")
            return {}
        
        try:
            logger.debug(f"📊 [Finnhub] Getting basic financials for {symbol}, metric={metric}")
            financials = self.client.company_basic_financials(symbol=symbol, metric=metric)
            
            if financials:
                logger.debug(f"✅ [Finnhub] Successfully retrieved financials for {symbol}")
                return financials
            else:
                logger.warning(f"⚠️ [Finnhub] No financial data for {symbol}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ [Finnhub] Failed to get basic financials for {symbol}: {e}")
            return {}
    
    def get_stock_indicators(self, symbol: str, date: Optional[str] = None) -> Dict[str, float]:
        """
        Get key stock indicators, combining multiple API calls
        
        Args:
            symbol: Stock symbol
            date: Query date (YYYY-MM-DD); if None, use the latest data
            
        Returns:
            Dict containing: market_cap, pe_ratio, dividend_yield, week_52_high, week_52_low, quarterly_dividend
        """
        import logging
        from datetime import datetime, timedelta
        
        logger = logging.getLogger(__name__)
        
        logger.info(f"🚀 [FUNDAMENTAL_DATA] FinnhubClient.get_stock_indicators called:")
        logger.info(f"  - symbol: {symbol}")
        logger.info(f"  - date: {date}")
        
        result = {
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "dividend_yield": 0.0,
            "week_52_high": 0.0,
            "week_52_low": 0.0,
            "quarterly_dividend": 0.0
        }
        
        if not self.is_available():
            logger.warning(f"❌ [FUNDAMENTAL_DATA] Finnhub client unavailable for {symbol}")
            return result
        
        try:
            # 1. Get basic company information (market cap)
            logger.info(f"🏢 [FUNDAMENTAL_DATA] Step 1: Getting company profile for {symbol}")
            profile = self.get_company_profile(symbol)
            if profile and "marketCapitalization" in profile:
                # Finnhub returns market cap in millions of USD; convert to USD
                market_cap_millions = profile.get("marketCapitalization", 0)
                if market_cap_millions:
                    result["market_cap"] = float(market_cap_millions) * 1_000_000
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Market cap: ${result['market_cap']:,.0f}")
            else:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] No market cap data for {symbol}")
            
            # 2. Get basic financial metrics
            logger.info(f"📊 [FUNDAMENTAL_DATA] Step 2: Getting basic financials for {symbol}")
            financials = self.get_basic_financials(symbol, "all")
            if financials and "metric" in financials:
                metrics = financials["metric"]
                
                # P/E ratio
                pe_ttm = metrics.get("peTTM")
                if pe_ttm and pe_ttm > 0:
                    result["pe_ratio"] = float(pe_ttm)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] P/E ratio: {result['pe_ratio']:.2f}")
                
                # Dividend yield (annualized)
                dividend_yield = metrics.get("dividendYieldIndicatedAnnual")
                if dividend_yield and dividend_yield > 0:
                    result["dividend_yield"] = float(dividend_yield) * 100  # convert to percentage
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Dividend yield: {result['dividend_yield']:.2f}%")
                
                # 52-week high and low
                week_52_high = metrics.get("52WeekHigh")
                week_52_low = metrics.get("52WeekLow")
                if week_52_high and week_52_high > 0:
                    result["week_52_high"] = float(week_52_high)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] 52-week high: ${result['week_52_high']:.2f}")
                if week_52_low and week_52_low > 0:
                    result["week_52_low"] = float(week_52_low)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] 52-week low: ${result['week_52_low']:.2f}")
            else:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] No financial metrics for {symbol}")
            
            # 3. Set default dividend data (Finnhub API limitation)
            logger.info(f"💰 [FUNDAMENTAL_DATA] Step 3: Setting default dividend data for {symbol}")
            # Due to Finnhub free API limitations, use default dividend data
            if "quarterly_dividend" not in result:
                result["quarterly_dividend"] = 0.0
                logger.info(f"ℹ️ [FUNDAMENTAL_DATA] Using default quarterly dividend: $0.0000")
            
            logger.info(f"🎯 [FUNDAMENTAL_DATA] Finnhub indicators summary for {symbol}:")
            logger.info(f"  - Market Cap: ${result['market_cap']:,.0f}")
            logger.info(f"  - P/E Ratio: {result['pe_ratio']:.2f}")
            logger.info(f"  - Dividend Yield: {result['dividend_yield']:.2f}%")
            logger.info(f"  - 52W High: ${result['week_52_high']:.2f}")
            logger.info(f"  - 52W Low: ${result['week_52_low']:.2f}")
            logger.info(f"  - Quarterly Div: ${result['quarterly_dividend']:.4f}")
            
        except Exception as e:
            logger.error(f"❌ [FUNDAMENTAL_DATA] Failed to get stock indicators for {symbol}: {e}")
            logger.exception("Detailed error information:")
        
        return result
    
    def get_general_news(self, category: str = 'general', limit: int = 50) -> List[Dict]:
        """
        Get general news
        
        Args:
            category: News category
            limit: Maximum number of results to return
            
        Returns:
            Standardized news list
        """
        if not self.is_available():
            logger.warning("Finnhub client unavailable")
            return []
        
        try:
            # Use general_news API
            news_data = self.client.general_news(category, min_id=0)
            
            if not news_data:
                logger.debug(f"Finnhub returned no general news data")
                return []
            
            # Limit quantity
            if len(news_data) > limit:
                news_data = news_data[:limit]
            
            # Standardize data format
            standardized_news = []
            for item in news_data:
                standardized_item = {
                    'title': item.get('headline', ''),
                    'description': item.get('summary', ''),
                    'published_utc': datetime.fromtimestamp(item.get('datetime', 0)).isoformat() if item.get('datetime') else '',
                    'source': item.get('source', ''),
                    'url': item.get('url', ''),
                    'category': item.get('category', ''),
                    'image': item.get('image', ''),
                    'related_symbols': item.get('related', []),  # Finnhub general news may contain related stocks
                    'api_source': 'finnhub_general',
                    'id': item.get('id', ''),
                    'datetime': item.get('datetime', 0)
                }
                standardized_news.append(standardized_item)
            
            logger.info(f"Finnhub successfully retrieved general news: {len(standardized_news)} items")
            return standardized_news
            
        except Exception as e:
            error_str = str(e).lower()
            logger.error(f"❌ [Finnhub] Failed to retrieve general news: {e}", exc_info=True)
            
            # For API-related explicit errors, throw FinnhubError for caller to handle
            if any(keyword in error_str for keyword in ["api", "key", "unauthorized", "permission", "forbidden", "rate limit"]):
                # Detailed error diagnosis
                if "api" in error_str or "key" in error_str:
                    logger.debug(f"🔍 [Finnhub] API key related issue: check API key validity")
                    raise FinnhubError(f"API key error: {e}", error_code="API_KEY_ERROR")
                elif "unauthorized" in error_str or "permission" in error_str or "forbidden" in error_str:
                    logger.debug(f"🔍 [Finnhub] Permission issue: check API key validity and account permissions")
                    raise FinnhubError(f"Permission denied: {e}", error_code="PERMISSION_ERROR")
                elif "rate limit" in error_str:
                    logger.debug(f"🔍 [Finnhub] Rate limit: suggest reducing request frequency")
                    raise FinnhubError(f"Request rate too high: {e}", error_code="RATE_LIMIT_ERROR")
                else:
                    raise FinnhubError(f"API error: {e}", error_code="API_ERROR")
            
            # For temporary errors like network timeout, log but return empty list
            elif "timeout" in error_str:
                logger.debug(f"🔍 [Finnhub] Timeout issue: check network connection stability")
                return []
            else:
                # Return empty list for other unknown errors to avoid program interruption
                logger.debug(f"🔍 [Finnhub] Unknown error, returning empty result")
                return []
    
    def get_company_profile(self, symbol: str) -> Dict:
        """
        Get basic company information, including market capitalization
        
        Args:
            symbol: Stock symbol
            
        Returns:
            A dictionary containing basic company information
        """
        if not self.is_available():
            logger.warning("Finnhub client unavailable")
            return {}
        
        try:
            logger.debug(f"🏢 [Finnhub] Getting company profile for {symbol}")
            profile = self.client.company_profile2(symbol=symbol)
            
            if profile:
                logger.debug(f"✅ [Finnhub] Successfully retrieved profile for {symbol}")
                return profile
            else:
                logger.warning(f"⚠️ [Finnhub] No profile data for {symbol}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ [Finnhub] Failed to get company profile for {symbol}: {e}")
            return {}
    
    def get_basic_financials(self, symbol: str, metric: str = "all") -> Dict:
        """
        Get basic financial metrics
        
        Args:
            symbol: Stock symbol
            metric: Metric type ("all", "valuation", "growth", etc.)
            
        Returns:
            A dictionary containing financial metrics
        """
        if not self.is_available():
            logger.warning("Finnhub client unavailable")
            return {}
        
        try:
            logger.debug(f"📊 [Finnhub] Getting basic financials for {symbol}, metric={metric}")
            financials = self.client.company_basic_financials(symbol=symbol, metric=metric)
            
            if financials:
                logger.debug(f"✅ [Finnhub] Successfully retrieved financials for {symbol}")
                return financials
            else:
                logger.warning(f"⚠️ [Finnhub] No financial data for {symbol}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ [Finnhub] Failed to get basic financials for {symbol}: {e}")
            return {}
    
    def get_stock_indicators(self, symbol: str, date: Optional[str] = None) -> Dict[str, float]:
        """
        Get key stock indicators, combining multiple API calls
        
        Args:
            symbol: Stock symbol
            date: Query date (YYYY-MM-DD); if None, use the latest data
            
        Returns:
            Dict containing: market_cap, pe_ratio, dividend_yield, week_52_high, week_52_low, quarterly_dividend
        """
        import logging
        from datetime import datetime, timedelta
        
        logger = logging.getLogger(__name__)
        
        logger.info(f"🚀 [FUNDAMENTAL_DATA] FinnhubClient.get_stock_indicators called:")
        logger.info(f"  - symbol: {symbol}")
        logger.info(f"  - date: {date}")
        
        result = {
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "dividend_yield": 0.0,
            "week_52_high": 0.0,
            "week_52_low": 0.0,
            "quarterly_dividend": 0.0
        }
        
        if not self.is_available():
            logger.warning(f"❌ [FUNDAMENTAL_DATA] Finnhub client unavailable for {symbol}")
            return result
        
        try:
            # 1. Get basic company information (market cap)
            logger.info(f"🏢 [FUNDAMENTAL_DATA] Step 1: Getting company profile for {symbol}")
            profile = self.get_company_profile(symbol)
            if profile and "marketCapitalization" in profile:
                # Finnhub returns market cap in millions of USD; convert to USD
                market_cap_millions = profile.get("marketCapitalization", 0)
                if market_cap_millions:
                    result["market_cap"] = float(market_cap_millions) * 1_000_000
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Market cap: ${result['market_cap']:,.0f}")
            else:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] No market cap data for {symbol}")
            
            # 2. Get basic financial metrics
            logger.info(f"📊 [FUNDAMENTAL_DATA] Step 2: Getting basic financials for {symbol}")
            financials = self.get_basic_financials(symbol, "all")
            if financials and "metric" in financials:
                metrics = financials["metric"]
                
                # P/E ratio
                pe_ttm = metrics.get("peTTM")
                if pe_ttm and pe_ttm > 0:
                    result["pe_ratio"] = float(pe_ttm)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] P/E ratio: {result['pe_ratio']:.2f}")
                
                # Dividend yield (annualized)
                dividend_yield = metrics.get("dividendYieldIndicatedAnnual")
                if dividend_yield and dividend_yield > 0:
                    result["dividend_yield"] = float(dividend_yield) * 100  # convert to percentage
                    logger.info(f"✅ [FUNDAMENTAL_DATA] Dividend yield: {result['dividend_yield']:.2f}%")
                
                # 52-week high and low
                week_52_high = metrics.get("52WeekHigh")
                week_52_low = metrics.get("52WeekLow")
                if week_52_high and week_52_high > 0:
                    result["week_52_high"] = float(week_52_high)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] 52-week high: ${result['week_52_high']:.2f}")
                if week_52_low and week_52_low > 0:
                    result["week_52_low"] = float(week_52_low)
                    logger.info(f"✅ [FUNDAMENTAL_DATA] 52-week low: ${result['week_52_low']:.2f}")
            else:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] No financial metrics for {symbol}")
            
            # 3. Get dividend information (use Polygon API as backup)
            logger.info(f"💰 [FUNDAMENTAL_DATA] Step 3: Getting dividend data for {symbol} from Polygon API")
            try:
                from ..core import data_hub
                dividends_df = data_hub.get_dividends(symbol)
                if not dividends_df.empty:
                    # Sort by ex-dividend date to get the most recent dividend
                    if 'ex_dividend_date' in dividends_df.columns:
                        dividends_df = dividends_df.sort_values('ex_dividend_date', ascending=False)
                    
                    # Find the most recent quarterly dividend
                    latest_dividend = None
                    for _, row in dividends_df.iterrows():
                        cash_amount = row.get('cash_amount', 0)
                        frequency = row.get('frequency', 0)
                        if cash_amount > 0:
                            # If frequency is available, prefer quarterly dividends (frequency=4)
                            if frequency == 4:
                                latest_dividend = float(cash_amount)
                                break
                            elif latest_dividend is None:
                                latest_dividend = float(cash_amount)
                    
                    if latest_dividend and latest_dividend > 0:
                        result["quarterly_dividend"] = latest_dividend
                        logger.info(f"✅ [FUNDAMENTAL_DATA] Latest quarterly dividend from Polygon: ${result['quarterly_dividend']:.4f}")
                    else:
                        logger.info(f"ℹ️ [FUNDAMENTAL_DATA] No valid dividend amounts for {symbol}")
                else:
                    logger.info(f"ℹ️ [FUNDAMENTAL_DATA] No dividend data from Polygon for {symbol}")
            except Exception as e:
                logger.warning(f"⚠️ [FUNDAMENTAL_DATA] Failed to get dividend data from Polygon for {symbol}: {e}")
                logger.info(f"ℹ️ [FUNDAMENTAL_DATA] Using default dividend value for {symbol}")
            
            logger.info(f"🎯 [FUNDAMENTAL_DATA] Finnhub indicators summary for {symbol}:")
            logger.info(f"  - Market Cap: ${result['market_cap']:,.0f}")
            logger.info(f"  - P/E Ratio: {result['pe_ratio']:.2f}")
            logger.info(f"  - Dividend Yield: {result['dividend_yield']:.2f}%")
            logger.info(f"  - 52W High: ${result['week_52_high']:.2f}")
            logger.info(f"  - 52W Low: ${result['week_52_low']:.2f}")
            logger.info(f"  - Quarterly Div: ${result['quarterly_dividend']:.4f}")
            
        except Exception as e:
            logger.error(f"❌ [FUNDAMENTAL_DATA] Failed to get stock indicators for {symbol}: {e}")
            logger.exception("Detailed error information:")
        
        return result

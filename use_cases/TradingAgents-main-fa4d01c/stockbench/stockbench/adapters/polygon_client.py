from __future__ import annotations

import os
import time
import random
from typing import Dict, List, Optional, Tuple

import httpx


class PolygonError(Exception):
    def __init__(self, status_code: int, message: str = "", payload: Optional[dict] = None) -> None:
        super().__init__(f"PolygonError {status_code}: {message}")
        self.status_code = status_code
        self.payload = payload or {}


class PolygonClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://api.polygon.io") -> None:
        self.api_key = api_key or os.getenv("POLYGON_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self._client: Optional[httpx.Client] = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            # Enable proxy support - now properly configured with socks5:// format
            self._client = httpx.Client(base_url=self.base_url, timeout=30.0)
        return self._client

    def _request(self, method: str, path: str, params: Optional[Dict] = None) -> Dict:
        import logging
        logger = logging.getLogger(__name__)
        
        params = dict(params or {})
        if "apiKey" not in params:
            params["apiKey"] = self.api_key
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        client = self._get_client()
        
        # Record HTTP request details
        logger.debug(f"🌐 [Polygon] HTTP request: {method} {url}")
        if logger.isEnabledFor(logging.DEBUG):
            # Only show parameters at debug level, hide API key
            safe_params = {k: v if k != "apiKey" else "***" for k, v in params.items()}
            logger.debug(f"📋 [Polygon] Request parameters: {safe_params}")

        # Exponential backoff + jitter
        backoff = 0.5
        last_error = None
        
        for attempt in range(6):
            try:
                logger.debug(f"🔄 [Polygon] Attempt {attempt + 1} request")
                resp = client.request(method, url, params=params)
                
                # Record response status
                logger.debug(f"📊 [Polygon] Response status: {resp.status_code}")
                
                if resp.status_code == 429 or resp.status_code >= 500:
                    # Retryable
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        sleep_s = float(retry_after) if retry_after else backoff * (2 ** attempt)
                    except Exception:
                        sleep_s = backoff * (2 ** attempt)
                    sleep_s = min(sleep_s, 30.0)
                    # Jitter ±20%
                    sleep_s *= (0.8 + random.random() * 0.4)
                    
                    # Record retry information
                    if resp.status_code == 429:
                        logger.warning(f"⚠️ [Polygon] Rate limited (429), retrying attempt {attempt + 2} after {sleep_s:.1f}s")
                    else:
                        logger.warning(f"⚠️ [Polygon] Server error ({resp.status_code}), retrying attempt {attempt + 2} after {sleep_s:.1f}s")
                        
                    time.sleep(sleep_s)
                    continue
                if 400 <= resp.status_code < 500:
                    # Non-429 client errors throw unified exception directly
                    error_text = resp.text[:500]  # Limit error message length
                    logger.error(f"❌ [Polygon] Client error {resp.status_code}: {error_text}")
                    raise PolygonError(resp.status_code, message=resp.text)
                    
                resp.raise_for_status()
                
                # Parse JSON response
                try:
                    json_data = resp.json()
                    logger.debug(f"✅ [Polygon] Request successful, response size: ~{len(str(json_data))} characters")
                    return json_data
                except Exception as json_exc:
                    logger.error(f"❌ [Polygon] JSON parsing failed: {json_exc}, response content: {resp.text[:200]}...")
                    return {}
                    
            except httpx.RequestError as req_exc:
                last_error = req_exc
                sleep_s = min(backoff * (2 ** attempt), 30.0)
                sleep_s *= (0.8 + random.random() * 0.4)
                
                logger.warning(f"⚠️ [Polygon] Network request exception: {req_exc}, retrying attempt {attempt + 2} after {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
                
            except httpx.HTTPStatusError as e:
                # Compatible with some cases where raise_for_status throws error
                sc = e.response.status_code if e.response is not None else 0
                if sc != 429 and 400 <= sc < 500:
                    logger.error(f"❌ [Polygon] HTTP status error {sc}: {str(e)}")
                    raise PolygonError(sc, message=str(e))
                # Other cases (like 5xx) go to retry
                last_error = e
                logger.warning(f"⚠️ [Polygon] HTTP status error {sc}, will retry")
                
        # Exceeded retry limit
        logger.error(f"❌ [Polygon] Still failed after 6 retries, last error: {last_error}")
        logger.debug(f"🔍 [Polygon] Failure analysis: Check network connection, API key validity, service status")
        return {}

    # Aggregates (minute/daily bars)
    def list_aggs(self, ticker: str, start: str, end: str, multiplier: int, timespan: str, adjusted: bool) -> List[Dict]:
        path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{start}/{end}"
        params = {"adjusted": str(adjusted).lower(), "sort": "asc", "limit": 50000}
        results: List[Dict] = []
        url = path
        cursor = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", url, params)
            if not data:
                break
            items = data.get("results") or []
            results.extend(items)
            cursor = data.get("next_url") or data.get("next_page_token") or data.get("nextCursor") or data.get("next_cursor") or data.get("next") or data.get("cursor")
            if not cursor:
                break
            # Some return complete next_url URL
            if str(cursor).startswith("http"):
                url = cursor
                params = {"apiKey": self.api_key}
            else:
                url = path
        return results

    # Grouped daily (market-wide daily bars)
    def get_grouped_daily_aggs(self, date: str, adjusted: bool) -> List[Dict]:
        path = f"/v2/aggs/grouped/locale/us/market/stocks/{date}"
        params = {"adjusted": str(adjusted).lower()}
        data = self._request("GET", path, params)
        return data.get("results") or []

    # Technical indicators (if using API)
    def list_indicators(self, ticker: str, timespan: str, windows: List[int]) -> Dict[str, List[Dict]]:
        # Recommend local calculation, keeping placeholder here
        return {}

    # Snapshots (universal multiple tickers)
    def get_universal_snapshots(self, symbols: List[str]) -> Dict[str, Dict]:
        out: Dict[str, Dict] = {}
        # Polygon may limit batch size for multi-ticker snapshot API, request in batches
        CHUNK = 50
        for i in range(0, len(symbols), CHUNK):
            batch = symbols[i:i+CHUNK]
            tickers = ",".join(batch)
            path = "/v2/snapshot/locale/us/markets/stocks/tickers"
            params = {"tickers": tickers}
            data = self._request("GET", path, params)
            for item in data.get("tickers", []) or []:
                sym = item.get("ticker")
                if sym:
                    out[sym] = item
        # Fill missing with empty values
        for s in symbols:
            out.setdefault(s, {})
        return out

    def get_gainers_losers(self, top_n: int) -> Dict[str, List[str]]:
        top_n = max(0, int(top_n))
        gainers = self._request("GET", "/v2/snapshot/locale/us/markets/stocks/gainers", {})
        losers = self._request("GET", "/v2/snapshot/locale/us/markets/stocks/losers", {})
        g = [x.get("ticker") for x in (gainers.get("tickers") or [])][:top_n]
        l = [x.get("ticker") for x in (losers.get("tickers") or [])][:top_n]
        return {"gainers": [t for t in g if t], "losers": [t for t in l if t]}

    # News v2 (requires loop calls, use next_url to determine completion)
    def list_ticker_news(self, ticker: str, gte: str, lte: str, limit: int = 100, page_token: Optional[str] = None) -> Tuple[List[Dict], Optional[str]]:
        import logging
        logger = logging.getLogger(__name__)
        
        path = "/v2/reference/news"
        params: Dict[str, object] = {
            "ticker": ticker,
            "limit": min(limit, 1000),
            "order": "asc",
        }
        if gte:
            params["published_utc.gte"] = gte
        if lte:
            params["published_utc.lte"] = lte
        if page_token:
            params["cursor"] = page_token
            
        # Record detailed API request information
        logger.debug(f"🌐 [Polygon] Starting news API request")
        logger.debug(f"📋 [Polygon] Request parameters: path={path}")
        logger.debug(f"📋 [Polygon] Query parameters: ticker={ticker}, gte={gte}, lte={lte}, limit={params['limit']}, cursor={page_token}")
        
        # Check API key status
        if not self.api_key:
            logger.error("❌ [Polygon] API key not set")
            return [], None
        
        try:
            # Record request start time
            start_time = time.time()
            data = self._request("GET", path, params)
            elapsed_time = time.time() - start_time
            
            if not data:
                logger.warning(f"⚠️ [Polygon] API returned empty response - ticker: {ticker}, time range: {gte} to {lte}")
                return [], None
                
            logger.debug(f"⏱️ [Polygon] API request completed, elapsed time: {elapsed_time:.2f}s")
            
            items: List[Dict] = data.get("results") or []
            next_url = data.get("next_url")
            
            # Record response statistics
            logger.debug(f"📊 [Polygon] Response statistics: results={len(items)}, has_next_url={bool(next_url)}")
            
        except PolygonError as poly_exc:
            logger.error(f"❌ [Polygon] API error - status code: {poly_exc.status_code}, message: {str(poly_exc)}")
            # Provide specific suggestions based on error code
            if poly_exc.status_code == 401:
                logger.debug(f"🔍 [Polygon] Authentication failed, check API key validity")
            elif poly_exc.status_code == 403:
                logger.debug(f"🔍 [Polygon] Insufficient permissions, check API subscription level")
            elif poly_exc.status_code == 429:
                logger.debug(f"🔍 [Polygon] Request rate too high, suggest adding delay or upgrading plan")
            elif poly_exc.status_code >= 500:
                logger.debug(f"🔍 [Polygon] Server error, suggest retrying later")
            return [], None
            
        except Exception as exc:
            logger.error(f"❌ [Polygon] Request exception: {exc}", exc_info=True)
            return [], None
        
        # Extract cursor parameter from next_url
        next_cursor = None
        if next_url:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(next_url)
                query_params = parse_qs(parsed_url.query)
                next_cursor = query_params.get('cursor', [None])[0]
                logger.debug(f"🔗 [Polygon] Successfully extracted next page cursor: {next_cursor}")
            except Exception as e:
                logger.warning(f"⚠️ [Polygon] Failed to parse next_url: {e}, URL: {next_url[:100]}...")
        else:
            logger.debug(f"🔗 [Polygon] No next page link, this is the last page")
        
        # Record detailed response statistics
        logger.debug(f"📊 [Polygon] API response details: items={len(items)}, next_cursor={next_cursor}")
        
        # Data quality validation and statistics
        if items:
            # Validate returned news time range
            valid_times = [item.get("published_utc", "") for item in items if item.get("published_utc")]
            if valid_times:
                first_time = min(valid_times)
                last_time = max(valid_times)
                logger.debug(f"📅 [Polygon] News time range: {first_time} to {last_time}")
                
                # Validate if time range meets expectations
                time_warnings = []
                if gte and first_time < gte:
                    time_warnings.append(f"Earliest news {first_time} earlier than requested start time {gte}")
                if lte and last_time > lte:
                    time_warnings.append(f"Latest news {last_time} later than requested end time {lte}")
                
                if time_warnings:
                    logger.warning(f"⚠️ [Polygon] Time range anomaly: {'; '.join(time_warnings)}")
            else:
                logger.warning(f"⚠️ [Polygon] Returned news items missing valid timestamps")
            
            # Data completeness statistics
            titles_count = sum(1 for item in items if item.get('title', '').strip())
            descriptions_count = sum(1 for item in items if item.get('description', '').strip())
            urls_count = sum(1 for item in items if item.get('article_url', '').strip())
            
            logger.debug(f"📝 [Polygon] Data completeness: titles {titles_count}/{len(items)}, descriptions {descriptions_count}/{len(items)}, URLs {urls_count}/{len(items)}")
            
            # Warn if data quality is low
            if titles_count < len(items) * 0.8:
                logger.warning(f"⚠️ [Polygon] High title missing rate: {len(items) - titles_count}/{len(items)}")
            if descriptions_count < len(items) * 0.5:
                logger.warning(f"⚠️ [Polygon] High description missing rate: {len(items) - descriptions_count}/{len(items)}")
        else:
            logger.debug(f"📊 [Polygon] Returned empty result set")
        
        return items, next_cursor

    # Ticker events (calendar/events: can filter earnings, etc.)
    def list_ticker_events(self, ticker: str, types: Optional[str] = None, limit: int = 1000) -> List[Dict]:
        path = f"/vX/reference/tickers/{ticker}/events"
        params: Dict[str, object] = {"limit": max(1, min(int(limit), 1000))}
        if types:
            params["types"] = types
        results: List[Dict] = []
        url = path
        cursor: Optional[str] = None
        while True:
            if cursor:
                params["cursor"] = cursor
            data = self._request("GET", url, params)
            if not data:
                break
            items = data.get("results") or []
            results.extend(items)
            cursor = data.get("next_url") or data.get("next_page_token") or data.get("next_cursor")
            if not cursor:
                break
            if str(cursor).startswith("http"):
                url = cursor
                params = {"apiKey": self.api_key}
            else:
                url = path
        return results

    # Corporate actions (dividends)
    def list_dividends(self, ticker: str) -> List[Dict]:
        path = "/v3/reference/dividends"
        params = {"ticker": ticker, "limit": 1000}
        data = self._request("GET", path, params)
        return data.get("results") or []

    # Corporate actions (stock splits, get stock split records)
    def list_splits(self, ticker: str) -> List[Dict]:
        path = "/v3/reference/splits"
        params = {"ticker": ticker, "limit": 1000}
        data = self._request("GET", path, params)
        return data.get("results") or []

    # Corporate actions (stock details, get stock detail information)
    def get_ticker_details(self, ticker: str, date: Optional[str] = None) -> Dict:
        path = f"/v3/reference/tickers/{ticker}"
        params: Dict[str, object] = {}
        if date:
            params["date"] = date
        data = self._request("GET", path, params)
        return data.get("results") or {}

    # Market status (get market status)
    def get_market_status(self) -> Dict:
        data = self._request("GET", "/v1/marketstatus/now", {})
        return data or {"market": "unknown"}

    # Financials (financial statements, vX)
    def list_financials(self, ticker: str, timeframe: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """Get list of financial statements. timeframe can be 'annual' or 'quarterly' (supported by Polygon API).
        Returns raw results list, caller handles trimming/caching.
        """
        path = "/vX/reference/financials"
        params: Dict[str, object] = {"ticker": ticker, "limit": max(1, min(int(limit), 1000))}
        if timeframe:
            params["timeframe"] = timeframe
        data = self._request("GET", path, params)
        return data.get("results") or []

    # Stock indicators (market cap, 52-week high/low, dividend yield, etc.)
    def get_stock_indicators(self, ticker: str, date: Optional[str] = None) -> Dict[str, float]:
        """Get key stock indicators, including market cap, 52-week high/low, dividend yield, etc.
        
        Args:
            ticker: Stock symbol
            date: Query date (YYYY-MM-DD); if None, use the latest data
            
        Returns:
            Dict containing: market_cap, pe_ratio, dividend_yield, week_52_high, week_52_low, quarterly_dividend
        """
        import logging
        from datetime import datetime, timedelta
        import pandas as pd
        
        logger = logging.getLogger(__name__)
        
        logger.info(f"🏗️ [FUNDAMENTAL_DATA] PolygonClient.get_stock_indicators called:")
        logger.info(f"  - ticker: {ticker}")
        logger.info(f"  - date: {date}")
        
        result = {
            "market_cap": 0.0,
            "pe_ratio": 0.0,
            "dividend_yield": 0.0,
            "week_52_high": 0.0,
            "week_52_low": 0.0,
            "quarterly_dividend": 0.0
        }
        
        logger.info(f"🏗️ [FUNDAMENTAL_DATA] Initialized default result: {result}")
        
        try:
            # 1. Get market cap - via ticker details
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Step 1: Getting ticker details")
            ticker_details = self.get_ticker_details(ticker, date)
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Ticker details result: {ticker_details}")
            if ticker_details and "market_cap" in ticker_details:
                result["market_cap"] = float(ticker_details.get("market_cap", 0))
                logger.info(f"🏗️ [FUNDAMENTAL_DATA] Updated market_cap: {result['market_cap']}")
            else:
                logger.warning(f"🏗️ [FUNDAMENTAL_DATA] No market_cap found in ticker_details")
            
            # 2. Compute 52-week high and low
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Step 2: Getting 52-week price data")
            if date:
                end_date = datetime.strptime(date, "%Y-%m-%d")
            else:
                end_date = datetime.now()
            start_date = end_date - timedelta(weeks=52)
            
            start_str = start_date.strftime("%Y-%m-%d")
            end_str = end_date.strftime("%Y-%m-%d")
            
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Date range: {start_str} to {end_str}")
            
            # Get 52-week price data
            price_data = self.list_aggs(ticker, start_str, end_str, 1, "day", True)
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Price data length: {len(price_data) if price_data else 0}")
            if price_data:
                highs = [float(x.get("h", 0)) for x in price_data if x.get("h")]
                lows = [float(x.get("l", 0)) for x in price_data if x.get("l")]
                logger.info(f"🏗️ [FUNDAMENTAL_DATA] Found {len(highs)} highs and {len(lows)} lows")
                if highs:
                    result["week_52_high"] = max(highs)
                    logger.info(f"🏗️ [FUNDAMENTAL_DATA] 52-week high: {result['week_52_high']}")
                if lows:
                    result["week_52_low"] = min(lows)
                    logger.info(f"🏗️ [FUNDAMENTAL_DATA] 52-week low: {result['week_52_low']}")
            else:
                logger.warning(f"🏗️ [FUNDAMENTAL_DATA] No price data available")
            
            # Get latest price (for dividend yield and P/E calculations)
            current_price = 0.0
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Step 3: Getting current price for calculations")
            if price_data:
                # Use the most recent closing price
                recent_prices = [float(x.get("c", 0)) for x in price_data[-5:] if x.get("c")]
                if recent_prices:
                    current_price = recent_prices[-1]
                    logger.info(f"🏗️ [FUNDAMENTAL_DATA] Current price: {current_price}")
                else:
                    logger.warning(f"🏗️ [FUNDAMENTAL_DATA] No recent prices found")
            else:
                logger.warning(f"🏗️ [FUNDAMENTAL_DATA] No price data for current price calculation")
            
            # 3. Get dividend data to compute dividend yield and quarterly dividend
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Step 4: Getting dividend data")
            dividends = self.list_dividends(ticker)
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Dividend records: {len(dividends) if dividends else 0}")
            if dividends:
                
                # Calculate total dividends over the past year
                one_year_ago = end_date - timedelta(days=365)
                annual_dividends = 0.0
                quarterly_dividend = 0.0
                
                for div in dividends:
                    try:
                        ex_date_str = div.get("ex_dividend_date", "")
                        if ex_date_str:
                            ex_date = datetime.strptime(ex_date_str, "%Y-%m-%d")
                            if ex_date >= one_year_ago:
                                cash_amount = float(div.get("cash_amount", 0))
                                annual_dividends += cash_amount
                                
                                # Check if this is a quarterly dividend
                                frequency = div.get("frequency", 0)
                                if frequency == 4 and cash_amount > quarterly_dividend:
                                    quarterly_dividend = cash_amount
                    except (ValueError, TypeError):
                        continue
                
                result["quarterly_dividend"] = quarterly_dividend
                
                # Calculate dividend yield
                if current_price > 0 and annual_dividends > 0:
                    result["dividend_yield"] = annual_dividends / current_price
            
            # 4. Calculate P/E ratio (requires EPS data)
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Step 5: Getting P/E ratio")
            financials = self.list_financials(ticker, limit=4)  # Get the latest 4 quarters
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Financial records: {len(financials) if financials else 0}")
            if financials:
                try:
                    # Find the most recent EPS value
                    latest_eps = 0.0
                    for financial in financials:
                        fin_data = financial.get("financials", {})
                        income_statement = fin_data.get("income_statement", {})
                        
                        # Try different EPS field names
                        eps_fields = ["basic_earnings_per_share", "earnings_per_share", "diluted_earnings_per_share"]
                        for field in eps_fields:
                            eps_data = income_statement.get(field, {})
                            if isinstance(eps_data, dict) and "value" in eps_data:
                                eps_value = float(eps_data["value"])
                                if eps_value != 0:
                                    latest_eps = eps_value
                                    break
                        if latest_eps != 0:
                            break
                    
                    # Calculate P/E ratio
                    if latest_eps > 0 and current_price > 0:
                        result["pe_ratio"] = current_price / latest_eps
                        
                except (KeyError, ValueError, TypeError):
                    pass
            
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Final result for {ticker}: {result}")
            return result
            
        except Exception as e:
            logger.error(f"❌ [FUNDAMENTAL_DATA] Error getting stock indicators for {ticker}: {e}")
            logger.exception("Detailed error:")
            logger.info(f"🏗️ [FUNDAMENTAL_DATA] Returning default result: {result}")
            return result 
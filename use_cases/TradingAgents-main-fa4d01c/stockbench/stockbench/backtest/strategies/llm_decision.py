"""
LLM Decision Strategy (for backtesting phase)

This module provides an LLM-based trading strategy `Strategy` that is called by the backtesting engine 
on a daily basis during backtesting:
Build features from factors/news/financials, call LLM for analysis and decision-making, generate buy/sell orders

Design objectives:
- Interact with the backtesting engine through a unified `on_bar(ctx)` interface, returning daily order list
- Control through configuration the news lookback window, feature window, etc.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from typing import Dict, List
import pandas as pd

from stockbench.core.executor import decide_batch as unified_decide_batch
from stockbench.core import data_hub


class Strategy:
    """LLM-based backtesting strategy.

    Usage: Called by the backtesting engine on each backtesting day via `on_bar(ctx)` to get order list.

    Attribute descriptions:
    - cfg: Configuration dictionary related to strategy and backtesting
    - news_lookback_days: News lookback window days for feature construction
    - page_limit: News item retrieval limit
    - warmup_days: Historical lookback days needed for feature construction (e.g., moving averages, financials)
    - agent_mode: Agent mode, "multi" (multi-agent) or "single" (single-agent)
    - previous_decisions: Previous decision results for backward compatibility
    - decision_history: Long-term historical decision records, storing all historical decisions by date and symbol
    """
    def __init__(self, cfg: Dict) -> None:
        """Initialize strategy.

        Parameters:
        - cfg: Configuration dictionary containing risk/news/backtest/llm sub-configurations
        """
        self.cfg = cfg
        self.news_lookback_days = int((cfg or {}).get("news", {}).get("lookback_days", 7))
        self.page_limit = int((cfg or {}).get("news", {}).get("page_limit", 50))
        self.warmup_days = int((cfg or {}).get("backtest", {}).get("warmup_days", 60))
        # Agent mode: "dual" (dual-agent) or "single" (single-agent), default single-agent
        agents_mode = (cfg or {}).get("agents", {}).get("mode")
        self.agent_mode = str(agents_mode or "single").lower()
        
        # Debug: Detailed configuration parsing process
        logger.debug(f"[DEBUG] Agent mode configuration parsing:")
        logger.debug(f"  - agents.mode: {agents_mode}")
        logger.debug(f"  - Final agent_mode: {self.agent_mode}")
        
        # Store previous decision results for backward compatibility
        self.previous_decisions: Dict | None = None
        
        # Long-term historical decision record system
        # Structure: {symbol: [{"date": "YYYY-MM-DD", "decision": {...}, "meta": {...}}, ...]}
        self.decision_history: Dict[str, List[Dict]] = {}
        
        # Get historical record parameters from configuration
        history_cfg = (cfg or {}).get("backtest", {}).get("history", {})
        self.max_records_per_symbol = int(history_cfg.get("max_records_per_symbol", 10))
        self.max_history_days = int(history_cfg.get("max_history_days", 30))
        
        # Temporary storage for pending decisions (to be recorded after execution)
        self.pending_decisions: Dict[str, Dict] = {}
        self.pending_meta: Dict = {}
        
        logger.debug(f"[DEBUG] Strategy initialization: Long-term historical record system enabled")
        logger.debug(f"[DEBUG] Strategy initialization: Maximum {self.max_records_per_symbol} historical records per symbol")
        logger.debug(f"[DEBUG] Strategy initialization: Maximum {self.max_history_days} days of historical records")
    
    def _add_decision_to_history(self, date: str, decisions: Dict[str, Dict], meta: Dict = None, clear_date_first: bool = False):
        """Add decision results to long-term historical records
        
        Args:
            date: Decision date in YYYY-MM-DD format
            decisions: Decision results dictionary
            meta: Meta information
            clear_date_first: Whether to clear existing records for this date first (override mechanism)
        """
        logger.info(f"=== Long-term Historical Record Save Started ===")
        logger.info(f"[HISTORY_SAVE] Starting to save decision records for date {date}")
        logger.debug(f"[HISTORY_SAVE] Input decisions type: {type(decisions)}")
        logger.debug(f"[HISTORY_SAVE] Input decisions keys: {list(decisions.keys()) if decisions else 'None'}")
        logger.debug(f"[HISTORY_SAVE] Input meta: {meta}")
        logger.debug(f"[HISTORY_SAVE] Clear date first: {clear_date_first}")
        
        if not decisions:
            logger.warning(f"[HISTORY_SAVE] Warning: No decision data to save")
            return
        
        # Override mechanism: Clear existing records for this date first
        if clear_date_first:
            logger.info(f"[HISTORY_SAVE] Override mode enabled, clearing existing records for date {date}")
            self._clear_decisions_for_date(date)
            
        # Extract decision records (excluding meta information)
        decision_records = {k: v for k, v in decisions.items() if k != "__meta__"}
        logger.info(f"[HISTORY_SAVE] Extracted decision records for {len(decision_records)} symbols")
        logger.debug(f"[HISTORY_SAVE] Decision record symbols: {list(decision_records.keys())}")
        
        # Historical record state before saving
        logger.debug(f"[HISTORY_SAVE] Historical record state before saving:")
        for symbol, records in self.decision_history.items():
            logger.debug(f"  - {symbol}: {len(records)} records")
            if records:
                latest = records[0]
                logger.debug(f"    Latest record: date={latest.get('date', 'N/A')}, action={latest.get('decision', {}).get('action', 'N/A')}")
        
        saved_count = 0
        for symbol, decision in decision_records.items():
            logger.debug(f"[HISTORY_SAVE] Processing symbol {symbol}:")
            logger.debug(f"  - Decision content: {decision}")
            
            if not isinstance(decision, dict):
                logger.warning(f"  - Skipping: Decision is not in dictionary format")
                continue
                
            # Ensure the historical record list for this symbol exists
            if symbol not in self.decision_history:
                self.decision_history[symbol] = []
                logger.debug(f"  - Created new historical record list")
            else:
                logger.debug(f"  - Existing historical record count: {len(self.decision_history[symbol])}")
            
            # Build historical record entry
            history_entry = {
                "date": date,
                "decision": decision.copy(),  # Copy decision content
                "meta": meta.copy() if meta else {}
            }
            logger.debug(f"  - Built historical record entry: {history_entry}")
            
            # Add to the beginning of historical record list (newest first)
            self.decision_history[symbol].insert(0, history_entry)
            logger.debug(f"  - Added to beginning of historical record list")
            
            # Limit the number of historical records per symbol
            if len(self.decision_history[symbol]) > self.max_records_per_symbol:
                removed_count = len(self.decision_history[symbol]) - self.max_records_per_symbol
                self.decision_history[symbol] = self.decision_history[symbol][:self.max_records_per_symbol]
                logger.info(f"[HISTORY_LIMIT] {symbol}: Cleaned {removed_count} old records, keeping latest {self.max_records_per_symbol} records")
            
            logger.debug(f"  - Historical record count after saving: {len(self.decision_history[symbol])}")
            saved_count += 1
        
        logger.info(f"=== Long-term Historical Record Save Completed ===")
        logger.info(f"[HISTORY_SAVE] Successfully saved decision records for {saved_count} symbols")
        logger.info(f"[HISTORY_SAVE] Current historical record statistics: Total {len(self.decision_history)} symbols have historical records")
        
        # Detailed post-save state
        for symbol, records in self.decision_history.items():
            logger.debug(f"[HISTORY_SAVE] {symbol}: {len(records)} historical records")
            if records:
                logger.debug(f"  - Latest record: date={records[0].get('date', 'N/A')}")
                latest_decision = records[0].get('decision', {})
                logger.debug(f"    action={latest_decision.get('action', 'N/A')}")
                logger.debug(f"    target_cash_amount={latest_decision.get('target_cash_amount', 'N/A')}")
                logger.debug(f"    confidence={latest_decision.get('confidence', 'N/A')}")
                
                if len(records) > 1:
                    logger.debug(f"  - Historical record timeline:")
                    for i, record in enumerate(records[:5]):  # Only show first 5 records
                        logger.debug(f"    {i+1}. {record.get('date', 'N/A')} - {record.get('decision', {}).get('action', 'N/A')}")
                    if len(records) > 5:
                        logger.debug(f"    ... {len(records) - 5} more records")
        
        logger.info(f"=== Long-term Historical Record Save Ended ===\n")
    
    def _get_decision_history_for_prompt(self, symbols: List[str] = None) -> Dict[str, List[Dict]]:
        """Get historical decision records for building prompts"""
        logger.info(f"\n=== Historical Record Building Started ===")
        logger.info(f"[HISTORY_BUILD] Starting to build historical decision records for prompt")
        logger.debug(f"[HISTORY_BUILD] Requested symbol count: {len(symbols) if symbols else 'all'}")
        logger.debug(f"[HISTORY_BUILD] Requested symbols: {symbols if symbols else 'all available symbols'}")
        
        history_for_prompt = {}
        
        # If no symbols specified, return all historical records
        target_symbols = symbols if symbols else list(self.decision_history.keys())
        logger.debug(f"[HISTORY_BUILD] Target symbols: {target_symbols}")
        logger.debug(f"[HISTORY_BUILD] Symbols in current long-term historical records: {list(self.decision_history.keys())}")
        
        found_count = 0
        for symbol in target_symbols:
            logger.debug(f"\n[HISTORY_BUILD] Processing symbol {symbol}:")
            
            if symbol in self.decision_history:
                # Convert historical record format to prompt-required format
                symbol_history = []
                original_records = self.decision_history[symbol]
                logger.debug(f"  - Found {len(original_records)} original historical records")
                
                for i, entry in enumerate(original_records):
                    decision = entry["decision"]
                    logger.debug(f"  - Processing record {i+1}:")
                    logger.debug(f"    Original date: {entry.get('date', 'N/A')}")
                    logger.debug(f"    Original decision: {decision}")
                    
                    history_record = {
                        "date": entry["date"],
                        "action": decision.get("action", "hold"),
                        "cash_change": decision.get("cash_change", 0.0),
                        "target_cash_amount": decision.get("target_cash_amount", 0.0),
                        "shares": decision.get("shares", 0.0),
                        "confidence": decision.get("confidence", 0.5)
                    }
                    logger.debug(f"    Converted record: {history_record}")
                    symbol_history.append(history_record)
                
                history_for_prompt[symbol] = symbol_history
                found_count += 1
                logger.debug(f"  - Successfully built {len(symbol_history)} historical records for prompt")
            else:
                logger.warning(f"  - Warning: Symbol {symbol} does not exist in long-term historical records")
                logger.debug(f"  - Will return empty historical records")
                history_for_prompt[symbol] = []
        
        logger.info(f"\n=== Historical Record Building Completed ===")
        logger.info(f"[HISTORY_BUILD] Successfully built historical records for {found_count} symbols")
        logger.info(f"[HISTORY_BUILD] Returned historical record statistics:")
        for symbol, records in history_for_prompt.items():
            logger.debug(f"  - {symbol}: {len(records)} records")
            if records:
                logger.debug(f"    Latest record: {records[0].get('date', 'N/A')} - {records[0].get('action', 'N/A')}")
                logger.debug(f"    Record time range: {records[-1].get('date', 'N/A')} to {records[0].get('date', 'N/A')}")
        
        logger.info(f"=== Historical Record Building Ended ===\n")
        return history_for_prompt
    
    def _cleanup_old_history(self, current_date: str):
        """Clean up expired historical records"""
        logger.info(f"\n=== Historical Record Cleanup Started ===")
        logger.info(f"[HISTORY_CLEANUP] Starting to clean up expired historical records")
        logger.debug(f"[HISTORY_CLEANUP] Current date: {current_date}")
        logger.debug(f"[HISTORY_CLEANUP] Maximum retention days: {self.max_history_days}")
        
        try:
            from datetime import datetime, timedelta
            current_dt = datetime.strptime(current_date, "%Y-%m-%d")
            cutoff_date = current_dt - timedelta(days=self.max_history_days)
            cutoff_date_str = cutoff_date.strftime("%Y-%m-%d")
            logger.debug(f"[HISTORY_CLEANUP] Cutoff date: {cutoff_date_str}")
            
            # State before cleanup
            logger.debug(f"[HISTORY_CLEANUP] Historical record state before cleanup:")
            total_records_before = 0
            for symbol, records in self.decision_history.items():
                logger.debug(f"  - {symbol}: {len(records)} records")
                total_records_before += len(records)
                if records:
                    oldest_date = records[-1].get('date', 'N/A')
                    newest_date = records[0].get('date', 'N/A')
                    logger.debug(f"    Time range: {oldest_date} to {newest_date}")
            logger.debug(f"  - Total: {total_records_before} records")
            
            cleaned_count = 0
            symbols_to_remove = []
            
            for symbol in list(self.decision_history.keys()):
                logger.debug(f"\n[HISTORY_CLEANUP] Processing symbol {symbol}:")
                original_count = len(self.decision_history[symbol])
                logger.debug(f"  - Original record count: {original_count}")
                
                # Filter out expired records
                original_records = self.decision_history[symbol]
                valid_records = [
                    entry for entry in original_records
                    if entry["date"] >= cutoff_date_str
                ]
                expired_records = [
                    entry for entry in original_records
                    if entry["date"] < cutoff_date_str
                ]
                
                logger.debug(f"  - Valid record count: {len(valid_records)}")
                logger.debug(f"  - Expired record count: {len(expired_records)}")
                
                if expired_records:
                    logger.debug(f"  - Expired record details:")
                    for i, record in enumerate(expired_records[:3]):  # Only show first 3 records
                        logger.debug(f"    {i+1}. {record.get('date', 'N/A')} - {record.get('decision', {}).get('action', 'N/A')}")
                    if len(expired_records) > 3:
                        logger.debug(f"    ... {len(expired_records) - 3} more expired records")
                
                # Update historical records
                self.decision_history[symbol] = valid_records
                expired_count = original_count - len(valid_records)
                cleaned_count += expired_count
                
                # Log cleanup results
                if expired_count > 0:
                    logger.info(f"[HISTORY_CLEANUP] {symbol}: Removed {expired_count} expired records (older than {cutoff_date_str}), {len(valid_records)} records remaining")
                
                # If a symbol has no historical records left, mark for deletion
                if not valid_records:
                    logger.info(f"[HISTORY_CLEANUP] {symbol}: No valid records left, removing symbol from history")
                    symbols_to_remove.append(symbol)
                    logger.debug(f"  - Mark for deletion: no valid records")
                else:
                    logger.debug(f"  - Retained record count: {len(valid_records)}")
                    oldest_valid = valid_records[-1].get('date', 'N/A')
                    newest_valid = valid_records[0].get('date', 'N/A')
                    logger.debug(f"  - Valid record time range: {oldest_valid} to {newest_valid}")
            
            # Delete symbols with no historical records
            for symbol in symbols_to_remove:
                del self.decision_history[symbol]
                logger.debug(f"[HISTORY_CLEANUP] Delete symbol {symbol}: no valid historical records")
            
            # State after cleanup
            logger.debug(f"\n[HISTORY_CLEANUP] Historical record state after cleanup:")
            total_records_after = 0
            for symbol, records in self.decision_history.items():
                logger.debug(f"  - {symbol}: {len(records)} records")
                total_records_after += len(records)
            logger.debug(f"  - Total: {total_records_after} records")
            
            if cleaned_count > 0:
                logger.debug(f"\n[HISTORY_CLEANUP] Cleanup completed: cleaned {cleaned_count} expired records")
                logger.debug(f"[HISTORY_CLEANUP] Record reduction: {total_records_before} -> {total_records_after}")
            else:
                logger.debug(f"\n[HISTORY_CLEANUP] Cleanup completed: no expired records to clean")
                
        except Exception as e:
            logger.error(f"[HISTORY_CLEANUP] Error: Historical record cleanup failed: {e}")
            import traceback
            logger.error(f"[HISTORY_CLEANUP] Error details: {traceback.format_exc()}")
        
        logger.info(f"=== Historical Record Cleanup Ended ===\n")
    
    def _clear_decisions_for_date(self, target_date: str):
        """Clear decision records for a specific date (used for retry mechanism)
        
        Args:
            target_date: Target date in YYYY-MM-DD format
        """
        logger.info(f"\n=== Clear Target Date Decision Records Started ===")
        logger.info(f"[DATE_CLEAR] Starting to clear decision records for date: {target_date}")
        
        cleared_symbols = []
        total_cleared_records = 0
        
        # Iterate through all symbols and clear records for the target date
        for symbol in list(self.decision_history.keys()):
            original_count = len(self.decision_history[symbol])
            logger.debug(f"[DATE_CLEAR] Processing symbol {symbol}: original record count = {original_count}")
            
            # Filter out records from the target date
            filtered_records = [
                record for record in self.decision_history[symbol]
                if record.get("date") != target_date
            ]
            
            cleared_count = original_count - len(filtered_records)
            if cleared_count > 0:
                self.decision_history[symbol] = filtered_records
                cleared_symbols.append(symbol)
                total_cleared_records += cleared_count
                logger.debug(f"[DATE_CLEAR] {symbol}: cleared {cleared_count} records for date {target_date}")
                
                # If no records remain for this symbol, delete the symbol
                if not filtered_records:
                    del self.decision_history[symbol]
                    logger.debug(f"[DATE_CLEAR] {symbol}: symbol deleted (no remaining records)")
            else:
                logger.debug(f"[DATE_CLEAR] {symbol}: no records found for date {target_date}")
        
        logger.info(f"\n=== Clear Target Date Decision Records Completed ===")
        logger.info(f"[DATE_CLEAR] Summary:")
        logger.info(f"  - Target date: {target_date}")
        logger.info(f"  - Symbols affected: {len(cleared_symbols)}")
        logger.info(f"  - Total records cleared: {total_cleared_records}")
        if cleared_symbols:
            logger.info(f"  - Affected symbols: {cleared_symbols}")
        logger.info(f"  - Remaining symbols with records: {len(self.decision_history)}")
        logger.info(f"=== Clear Target Date Decision Records Ended ===\n")
    
    def _build_previous_decisions_for_compatibility(self, current_date: str) -> Dict:
        """For backward compatibility, build previous_decisions format"""
        logger.info(f"\n=== Backward Compatibility Build Started ===")
        logger.info(f"[COMPATIBILITY_BUILD] Starting to build backward-compatible previous_decisions format")
        logger.debug(f"[COMPATIBILITY_BUILD] Current date: {current_date}")
        logger.debug(f"[COMPATIBILITY_BUILD] Long-term historical record status: {len(self.decision_history)} symbols")
        
        if not self.decision_history:
            logger.warning(f"[COMPATIBILITY_BUILD] Warning: no long-term historical records, returning None")
            logger.info(f"=== Backward Compatibility Build Ended ===\n")
            return None
            
        # Find the most recent decision date
        latest_date = None
        latest_decisions = {}
        
        logger.debug(f"[COMPATIBILITY_BUILD] Analyzing latest decision dates for all symbols:")
        for symbol, records in self.decision_history.items():
            if records:
                record_date = records[0]["date"]  # Latest record
                logger.debug(f"  - {symbol}: latest record date {record_date}")
                if latest_date is None or record_date > latest_date:
                    latest_date = record_date
                    logger.debug(f"    -> Updated to latest date: {latest_date}")
            else:
                logger.debug(f"  - {symbol}: no historical record")
        
        if latest_date:
            logger.debug(f"\n[COMPATIBILITY_BUILD] Determined latest decision date: {latest_date}")
            logger.debug(f"[COMPATIBILITY_BUILD] Building decision records for that date:")
            
            # Build previous_decisions format
            for symbol, records in self.decision_history.items():
                if records and records[0]["date"] == latest_date:
                    decision = records[0]["decision"]
                    latest_decisions[symbol] = decision
                    logger.debug(f"  - {symbol}: add decision record")
                    logger.debug(f"    action: {decision.get('action', 'N/A')}")
                    logger.debug(f"    target_cash_amount: {decision.get('target_cash_amount', 'N/A')}")
                    logger.debug(f"    confidence: {decision.get('confidence', 'N/A')}")
                else:
                    if records:
                        logger.debug(f"  - {symbol}: skip, latest record date {records[0]['date']} != {latest_date}")
                    else:
                        logger.debug(f"  - {symbol}: skip, no historical record")
            
            # Add meta information
            meta_info = {
                "date": latest_date,
                "calls": 1  # This might need to be obtained from actual calls
            }
            latest_decisions["__meta__"] = meta_info
            logger.debug(f"\n[COMPATIBILITY_BUILD] add meta information: {meta_info}")
            
            logger.debug(f"\n[COMPATIBILITY_BUILD] build completed: {len(latest_decisions)-1} symbols' decision records")
            logger.debug(f"[COMPATIBILITY_BUILD] returned previous_decisions structure:")
            for key, value in latest_decisions.items():
                if key != "__meta__":
                    logger.debug(f"  - {key}: {type(value)} - {value}")
                else:
                    logger.debug(f"  - {key}: {type(value)} - {value}")
        else:
            logger.warning(f"[COMPATIBILITY_BUILD] warning: no valid decision date found")
            latest_decisions = None
        
        logger.info(f"=== Backward Compatibility Build Ended ===\n")
        return latest_decisions if latest_decisions else None


    def _build_features_for_day(self, ctx) -> List[Dict]:
        """
        Build daily features: Get historical data, news, financials, etc., and build feature list.
        Optimization: Directly build new format features to avoid subsequent repeated conversions
        """
        features_list = []
        open_map = ctx["open_map"]
        datasets = ctx["datasets"]
        portfolio = ctx["portfolio"]
        
        # Get configuration parameters
        news_lookback_days = int(self.cfg.get("news", {}).get("lookback_days", 7))
        page_limit = int(self.cfg.get("news", {}).get("page_limit", 100))
        warmup_days = int(self.cfg.get("backtest", {}).get("warmup_days", 7))
        
        for symbol in open_map.keys():
            # Get historical data (for feature construction)
            start_date = ctx["date"] - pd.Timedelta(days=warmup_days + 5)  # Get 5 extra days as buffer
            end_date = ctx["date"]
            
            bars_day = datasets.get_day_bars(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
            
            # Get news data
            news_items = []
            try:
                # News fetching logic: let data_hub.py handle lookahead bias prevention
                # If making decisions on May 1st with lookback_days=3, should fetch news from April 28-30
                news_end_date = end_date  # Pass decision date directly, let get_news() handle bias prevention
                news_start_date = end_date - pd.Timedelta(days=self.news_lookback_days)  # Go back lookback days
                
                logger.debug(f"[DEBUG] News fetching parameter correction:")
                logger.debug(f"[DEBUG]   Decision date: {end_date.strftime('%Y-%m-%d')}")
                logger.debug(f"[DEBUG]   News fetching range: {news_start_date.strftime('%Y-%m-%d')} to {news_end_date.strftime('%Y-%m-%d')}")
                
                news_result = data_hub.get_news(
                    symbol, 
                    news_start_date.strftime("%Y-%m-%d"), 
                    news_end_date.strftime("%Y-%m-%d"),
                    limit=page_limit
                )
                if news_result is not None:
                    news_raw, _ = news_result
                else:
                    news_raw = []
                
                # Handle different news data formats
                if isinstance(news_raw, dict):
                    if "results" in news_raw and isinstance(news_raw["results"], list):
                        news_items = news_raw["results"]
                    elif "data" in news_raw and isinstance(news_raw["data"], list):
                        news_items = news_raw["data"]
                    else:
                        news_items = news_raw
                elif isinstance(news_raw, list):
                    news_items = news_raw
                else:
                    news_items = []
                
                # 🚨 Time filtering logic (consistent with fetching logic)
                if news_items:
                    valid_news = []
                    
                    logger.debug(f"[DEBUG] Start time filtering - news count: {len(news_items)}")
                    logger.debug(f"[DEBUG] Using time range consistent with fetching: {news_start_date.strftime('%Y-%m-%d')} to {news_end_date.strftime('%Y-%m-%d')}")
                    
                    for i, news in enumerate(news_items):
                        if not isinstance(news, dict):
                            logger.debug(f"[DEBUG] News #{i}: skip - not dictionary type")
                            continue
                            
                        news_time_str = news.get("published_utc") or news.get("published_date")
                        if not news_time_str:
                            logger.debug(f"[DEBUG] News #{i}: skip - no time field")
                            continue
                            
                        try:
                            news_time = pd.to_datetime(news_time_str, utc=True, errors="coerce")
                            if pd.isna(news_time):
                                logger.debug(f"[DEBUG] News #{i}: skip - time parsing failed: {news_time_str}")
                                continue
                                
                            from stockbench.core.data_hub import _normalize_timestamp_for_comparison
                            news_time_naive = _normalize_timestamp_for_comparison(news_time)
                            filter_start_naive = _normalize_timestamp_for_comparison(news_start_date)
                            # 🚨 Fix: Let news_end_date include the entire day, not just midnight
                            # Set end date to 23:59:59 of that day
                            news_end_date_eod = news_end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                            filter_end_naive = _normalize_timestamp_for_comparison(news_end_date_eod)
                            
                            logger.debug(f"[DEBUG] News #{i}: time comparison - news:{news_time_naive.strftime('%Y-%m-%d %H:%M')}, range:{filter_start_naive.strftime('%Y-%m-%d')} to {news_end_date.strftime('%Y-%m-%d')} 23:59")
                            
                            if filter_start_naive <= news_time_naive <= filter_end_naive:
                                valid_news.append(news)
                                logger.debug(f"[DEBUG] News #{i}: ✅ Passed time filtering")
                            else:
                                logger.debug(f"[DEBUG] News #{i}: ❌ Time out of range")
                        except Exception as e:
                            logger.debug(f"[DEBUG] News #{i}: Time processing exception: {e}")
                            continue
                    
                    news_items = valid_news
                    logger.debug(f"[DEBUG] Time filtering completed - remaining news count: {len(news_items)}")
                        
            except Exception as e:
                # Failed to get news
                import traceback
                traceback.print_exc()
            
            # Get financial data
            financials = []
            try:
                financials = data_hub.get_financials(symbol)
            except Exception as e:
                # Failed to get financial data
                pass
            
            # Get dividend and split data
            dividends = pd.DataFrame()
            splits = pd.DataFrame()
            try:
                dividends = data_hub.get_dividends(symbol)
                splits = data_hub.get_splits(symbol)
            except Exception as e:
                # Failed to get dividend/split data
                pass
            
            # Build market snapshot
            ref_price = open_map.get(symbol, 0.0)
            snapshot = {
                "symbol": symbol,
                "price": ref_price,
                "ts_utc": ctx["date"].strftime("%Y-%m-%dT00:00:00Z")
            }
            
            # Build symbol details
            details = {"ticker": symbol}
            
            # Build position state
            position = portfolio.positions.get(symbol)
            # Use unified price tools to calculate position value
            current_position_value = 0.0
            if position and hasattr(position, "shares") and position.shares:
                from stockbench.core.price_utils import calculate_position_value
                
                # Prepare fallback price
                fallback_price = ref_price or (bars_day["close"].iloc[-1] if not bars_day.empty and "close" in bars_day.columns else 100.0)
                
                try:
                    # Print debug info: check price data in ctx
                    if ctx:
                        open_map_keys = list(ctx.get("open_map", {}).keys())
                        open_price_map_keys = list(ctx.get("open_price_map", {}).keys())
                        logger.debug(f"[POSITION_VALUE_DEBUG] {symbol}: ctx.open_map has {len(open_map_keys)} stocks: {open_map_keys[:5]}")
                        logger.debug(f"[POSITION_VALUE_DEBUG] {symbol}: ctx.open_price_map has {len(open_price_map_keys)} stocks: {open_price_map_keys[:5]}")
                        if symbol in ctx.get("open_map", {}):
                            logger.debug(f"[POSITION_VALUE_DEBUG] {symbol}: found price in open_map = {ctx['open_map'][symbol]}")
                        if symbol in ctx.get("open_price_map", {}):
                            logger.debug(f"[POSITION_VALUE_DEBUG] {symbol}: found price in open_price_map = {ctx['open_price_map'][symbol]}")
                    
                    current_position_value = calculate_position_value(
                        symbol=symbol,
                        shares=position.shares,
                        ctx=ctx,
                        portfolio=None,  # No portfolio object here
                        position_avg_price=getattr(position, 'avg_price', None)
                    )
                    
                    # If unified tool also fails, use original fallback logic
                    if current_position_value == 0.0 and fallback_price and fallback_price > 0:
                        current_position_value = float(position.shares * fallback_price)
                        logger.info(f"[POSITION_VALUE] {symbol}: {position.shares:.2f} shares × {fallback_price:.4f} (final_fallback) = {current_position_value:.2f}")
                        
                except Exception as e:
                    current_position_value = 0.0
                    logger.warning(f"[POSITION_VALUE] {symbol}: Failed to calculate position value: {e}")
                    # Print more detailed error information
                    logger.warning(f"[POSITION_VALUE_DEBUG] {symbol}: ctx keys = {list(ctx.keys()) if ctx else 'None'}")
                    import traceback
                    logger.warning(f"[POSITION_VALUE_DEBUG] {symbol}: detailed error = {traceback.format_exc()}")
            
            # If no position object, create a default position state
            if position is None:
                # Create default position state: 0 shares, 0 avg price, 0 holding days
                position = type('Position', (), {
                    'shares': 0,
                    'avg_price': 0.0,
                    'holding_days': 0
                })()
            

            holding_days = int(getattr(position, "holding_days", 0) or 0) if position else 0
            position_state = {
                "current_position_value": current_position_value,  # Use amount instead of percentage
                "holding_days": holding_days,
                "shares": round(float(getattr(position, "shares", 0) or 0), 2)
            }
            
            # Convert news_items to simple title+description format
            simple_news_list = []
            if news_items:
                for news_item in news_items:
                    if isinstance(news_item, dict):
                        title = news_item.get("title", "")
                        description = news_item.get("description", "")
                        if title:
                            # Format: "title - description" if both exist, otherwise just title
                            if description and description.strip():
                                news_text = f"{title} - {description}"
                            else:
                                news_text = title
                            simple_news_list.append(news_text)
                    elif isinstance(news_item, str) and news_item.strip():
                        simple_news_list.append(news_item)
            
            if not simple_news_list:
                simple_news_list = ["No news available"]
            
            # Build historical close_7d price series correctly
            close_7d = []
            try:
                if not bars_day.empty and "close" in bars_day.columns:
                    # Sort data by date and remove duplicates
                    if "date" in bars_day.columns:
                        bars_clean = bars_day.drop_duplicates(subset=["date"], keep="last").sort_values("date")
                    else:
                        bars_clean = bars_day.drop_duplicates(keep="last")
                    
                    # Get closing prices from the past 7 days (excluding current day)
                    if len(bars_clean) > 1:
                        # Exclude current day and take previous 7 days
                        available_historical_data = len(bars_clean) - 1  # Exclude current day
                        if available_historical_data > 0:
                            start_idx = max(0, available_historical_data - 7)
                            end_idx = available_historical_data  # Exclude current day
                            close_data = bars_clean["close"].iloc[start_idx:end_idx]
                            
                            # Convert to float list
                            for val in close_data:
                                if val is not None and not pd.isna(val):
                                    close_7d.append(float(val))
                                else:
                                    close_7d.append(0.0)
                    
                    # Pad with 0s if insufficient data
                    if len(close_7d) < 7:
                        close_7d = [0.0] * (7 - len(close_7d)) + close_7d
                        
                    # Ensure exactly 7 elements
                    close_7d = close_7d[-7:]  # Take last 7 elements
                        
                else:
                    # No historical data available
                    close_7d = [0.0] * 7
                    
            except Exception as e:
                logger.warning(f"Error building close_7d for {symbol}: {e}")
                close_7d = [0.0] * 7

            # Build minimal features structure
            fi = {
                "symbol": symbol,
                "features": {
                    "market_data": {"ticker": symbol, "open": ref_price, "close_7d": close_7d},
                    "news_events": {"top_k_events": simple_news_list},
                    "position_state": position_state
                },
                "market_ctx": {"daily_drawdown_pct": float(ctx.get("daily_drawdown_pct") or 0.0)}
            }
            
            features_list.append(fi)
        
        return features_list

    def on_bar(self, ctx) -> List[Dict]:
        """
        Generate daily orders: First construct features, then have LLM generate target positions, finally convert to buy/sell orders.
        ctx: {date, symbols, open_map/open_price_map, ref_price_map, portfolio, cfg, datasets, rejected_orders, ...}
        """
        # Call LLM to generate decisions
        open_map = ctx["open_map"]
        if not open_map:
            return []
        
        # 1) Feature construction
        features_list = self._build_features_for_day(ctx)
        
        # 2) Clean up expired historical records
        current_date = ctx["date"].strftime("%Y-%m-%d")
        self._cleanup_old_history(current_date)
        
        # 3) Build historical decision records for LLM call
        # Get current symbol list
        current_symbols = [fi["symbol"] for fi in features_list]
        
        # For backward compatibility, build previous_decisions format
        self.previous_decisions = self._build_previous_decisions_for_compatibility(current_date)
        
        # Get long-term historical records for prompt construction
        decision_history = self._get_decision_history_for_prompt(current_symbols)
        
        # Add detailed logs for historical records
        logger.debug(f"[DEBUG] LLM Strategy: current_date={current_date}")
        logger.debug(f"[DEBUG] LLM Strategy: long-term historical record statistics: total {len(self.decision_history)} symbols have historical records")
        for symbol, records in self.decision_history.items():
            if symbol in current_symbols:
                logger.debug(f"[DEBUG] LLM Strategy: {symbol} has {len(records)} historical records")
                if records:
                    latest_record = records[0]
                    logger.debug(f"[DEBUG] LLM Strategy: {symbol} latest record - date={latest_record['date']}, action={latest_record['decision'].get('action', 'unknown')}")
        
        # 4) Use unified executor for decision-making (automatically route to single or dual Agent mode based on configuration)
        logger.info(f"\n=== Unified Executor Decision Call Started ===")
        logger.info(f"[UNIFIED_EXECUTOR] Using unified executor for decision-making, Agent mode: {self.agent_mode}")
        logger.info(f"[UNIFIED_EXECUTOR] Agent mode in configuration: {(self.cfg or {}).get('agents', {}).get('mode', 'single')}")
        logger.info(f"[UNIFIED_EXECUTOR] Passed historical record parameters:")
        logger.debug(f"  - previous_decisions: {type(self.previous_decisions)} - {self.previous_decisions}")
        logger.debug(f"  - decision_history: {type(decision_history)} - {len(decision_history) if decision_history else 0} symbols")
        if decision_history:
            for symbol, records in decision_history.items():
                logger.debug(f"    {symbol}: {len(records)} records")
        
        # Build bars_data for feature conversion (complete version, including all data needed for dual-agent mode)
        bars_data = {}
        for fi in features_list:
            symbol = fi["symbol"]
            # Get historical data from ctx for feature conversion
            start_date = ctx["date"] - pd.Timedelta(days=self.warmup_days)  # Look back 60 days for feature construction
            end_date = ctx["date"]
            
            # Get market snapshot data
            ref_price = open_map.get(symbol, 0.0)
            snapshot = {
                "symbol": symbol,
                "price": ref_price,
                "ts_utc": ctx["date"].strftime("%Y-%m-%dT00:00:00Z")
            }
            
            # Get details data
            details = {"ticker": symbol}
            
            # Get news data (extract from original features to avoid duplicate API calls)
            news_items = []
            try:
                # Extract news data from already built features
                if "features" in fi and "news_events" in fi["features"]:
                    top_k_events = fi["features"]["news_events"].get("top_k_events", [])
                    # Convert news events to simple news_items format for decision agent
                    if isinstance(top_k_events, list) and top_k_events and top_k_events[0] != "No news data available":
                        for event in top_k_events:
                            if isinstance(event, str) and event.strip():
                                # Since top_k_events is already in title+description format from analysis,
                                # we can use it directly as title for the decision agent
                                news_items.append({
                                    "title": event,  # This already contains "title - description"
                                    "description": "",  # Keep empty since title already has full info
                                    "published_utc": ctx["date"].strftime("%Y-%m-%dT00:00:00Z")
                                })
            except Exception as e:
                logger.warning(f"Failed to extract news data from features {symbol}: {e}")
            
            # Get position state (extract from already built features)
            position_state = {}
            try:
                if "features" in fi and "position_state" in fi["features"]:
                    position_state = fi["features"]["position_state"].copy()
                else:
                    # Fallback plan: build default position state
                    position = ctx["portfolio"].positions.get(symbol)
                    current_position_value = 0.0
                    if position and hasattr(position, "shares") and position.shares:
                        from stockbench.core.price_utils import calculate_position_value
                        try:
                            current_position_value = calculate_position_value(
                                symbol=symbol,
                                shares=position.shares,
                                ctx=ctx,
                                portfolio=None,
                                position_avg_price=getattr(position, 'avg_price', None)
                            )
                        except Exception:
                            fallback_price = ref_price or 100.0
                            current_position_value = float(position.shares * fallback_price)
                    
                    holding_days = int(getattr(position, "holding_days", 0) or 0) if position else 0
                    position_state = {
                        "current_position_value": current_position_value,
                        "holding_days": holding_days,
                        "shares": round(float(getattr(position, "shares", 0) or 0), 2)
                    }
            except Exception as e:
                logger.warning(f"Failed to build position state {symbol}: {e}")
                position_state = {
                    "current_position_value": 0.0,
                    "holding_days": 0,
                    "shares": 0.0
                }
            
            bars_data[symbol] = {
                "bars_day": ctx["datasets"].get_day_bars(symbol, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")),
                "snapshot": snapshot,
                "details": details,
                "news_items": news_items,
                "position_state": position_state
            }
        # Get run_id from ctx for organizing LLM cache directory
        run_id = ctx.get("run_id")
        
        # Extract rejected_orders from ctx for retry mechanism
        rejected_orders = ctx.get("rejected_orders", None)
        
        logger.info(f"[UNIFIED_EXECUTOR] Calling unified executor decision, automatically routing to correct Agent mode")
        logger.info(f"[UNIFIED_EXECUTOR] Parameter details:")
        logger.debug(f"  - features_list length: {len(features_list)}")
        logger.debug(f"  - cfg: {type(self.cfg)}")
        logger.debug(f"  - enable_llm: {True}")
        logger.debug(f"  - run_id: {run_id}")
        logger.debug(f"  - previous_decisions: {'Yes' if self.previous_decisions else 'No'}")
        logger.debug(f"  - decision_history: {'Yes' if decision_history else 'No'}")
        logger.debug(f"  - rejected_orders: {len(rejected_orders) if rejected_orders else 0} orders")
        
        if rejected_orders:
            logger.info(f"[UNIFIED_EXECUTOR] Processing {len(rejected_orders)} rejected orders for retry")
        
        decisions_map = unified_decide_batch(features_list, cfg=self.cfg, enable_llm=True, bars_data=bars_data, run_id=run_id, previous_decisions=self.previous_decisions, decision_history=decision_history, rejected_orders=rejected_orders, ctx=ctx)
        
        logger.info(f"[UNIFIED_EXECUTOR] Unified executor decision completed, return result type: {type(decisions_map)}")
        logger.info(f"[UNIFIED_EXECUTOR] Return result keys: {list(decisions_map.keys()) if decisions_map else 'None'}")
        logger.info(f"=== Unified Executor Decision Call Ended ===\n")
        
        # Note: Order rejection retry logic has been removed, unified retry mechanism will handle automatically

        # 4) Generate orders
        orders: List[Dict] = []
        pf = ctx["portfolio"]
        ref_price_map = ctx.get("ref_price_map", {}) or {}
        equity_for_sizing = float(ctx.get("equity_for_sizing") or 0.0)
        
        # Add debug logs
        logger.debug(f"[DEBUG] LLM Strategy: equity_for_sizing={equity_for_sizing}, portfolio.equity={pf.equity}")
        logger.debug(f"[DEBUG] LLM Strategy: feature_count={len(features_list)}, decision_count={len(decisions_map)}")
        
        for fi in features_list:
            s = fi["symbol"]
            decision = decisions_map.get(s, {})
            action = decision.get("action", "hold")
            
            ref_px =  open_map.get(s) # ref_price_map.get(s)
            
            if ref_px is None or ref_px <= 0:
                logger.debug(f"[DEBUG] LLM Strategy: {s} - skip, invalid reference price")
                continue
                
            pos = pf.positions.get(s)
            current_value = (pos.shares * float(ref_px)) if pos else 0.0
            
            # Fix target_cash_amount handling logic for hold operations
            if action == "hold" and "target_cash_amount" not in decision:
                # For hold operations without target_cash_amount, use current position value
                target_cash = current_value
                logger.debug(f"[DEBUG] LLM Strategy: {s} - hold operation auto-set target_cash={target_cash} (current position value)")
            else:
                target_cash = float(decision.get("target_cash_amount", 0.0))
            
            # Add debug information for each symbol
            logger.debug(f"[DEBUG] LLM Strategy: {s} - action={action}, target_cash={target_cash}, ref_px={ref_px}")
            # Directly use LLM output target cash amount, no need to convert to percentage
            target_value = max(0.0, target_cash)  # Ensure target amount is non-negative
            delta_value = target_value - current_value
            
            logger.debug(f"[DEBUG] LLM Strategy: {s} - current_value={current_value}, target_value={target_value}, delta_value={delta_value}")
            
            # CRITICAL FIX: Detect and correct LLM decision logic errors
            # When target_cash_amount equals current_position_value, it should be a hold operation
            if action == "increase" and abs(delta_value) < 0.01:
                logger.warning(f"[DECISION_LOGIC_FIX] {s}: LLM marked as 'increase' but delta_value={delta_value:.4f} ≈ 0, treating as 'hold'")
                action = "hold"  # Override incorrect LLM decision
            elif action == "decrease" and abs(delta_value) < 0.01:
                logger.warning(f"[DECISION_LOGIC_FIX] {s}: LLM marked as 'decrease' but delta_value={delta_value:.4f} ≈ 0, treating as 'hold'")
                action = "hold"  # Override incorrect LLM decision
            
            # Only trigger trades under explicit actions
            if action == "increase" and delta_value > 0:
                qty = round(delta_value / float(ref_px), 2)
                if qty > 0:
                    orders.append({"symbol": s, "side": "buy", "qty": qty})
                    logger.debug(f"[DEBUG] LLM Strategy: {s} - generated buy order: qty={qty}")
                else:
                    logger.debug(f"[DEBUG] LLM Strategy: {s} - skip, calculated quantity is 0")
            elif action in ("decrease", "close") and delta_value < 0:
                qty = round(abs(delta_value) / float(ref_px), 2)
                if pos and pos.shares > 0:
                    qty = min(qty, pos.shares)
                if qty > 0:
                    orders.append({"symbol": s, "side": "sell", "qty": -qty})
                    logger.debug(f"[DEBUG] LLM Strategy: {s} - generated sell order: qty={qty}")
                else:
                    logger.debug(f"[DEBUG] LLM Strategy: {s} - skip, calculated quantity is 0")
            else:
                logger.debug(f"[DEBUG] LLM Strategy: {s} - skip, action={action}, delta_value={delta_value}")
        
        logger.debug(f"[DEBUG] LLM Strategy: final generated order count={len(orders)}")
        
        # 5) Store pending decisions (to be recorded after execution)
        logger.info(f"\n=== Store Pending Decisions Started ===")
        logger.info(f"[PENDING_SAVE] Preparing to store pending decisions")
        logger.debug(f"[PENDING_SAVE] Current date: {current_date}")
        logger.debug(f"[PENDING_SAVE] Decision result type: {type(decisions_map)}")
        logger.debug(f"[PENDING_SAVE] Decision result keys: {list(decisions_map.keys()) if decisions_map else 'None'}")
        
        # Store pending decisions for later recording
        self.pending_decisions = decisions_map.copy() if decisions_map else {}
        
        # Store meta information
        meta = {"date": current_date, "calls": 1}
        if decisions_map and "__meta__" in decisions_map:
            decisions_map["__meta__"]["date"] = current_date
            meta.update(decisions_map["__meta__"])
            logger.debug(f"[PENDING_SAVE] Update meta info - date={current_date}")
            logger.debug(f"[PENDING_SAVE] Complete meta info: {meta}")
        else:
            logger.debug(f"[PENDING_SAVE] Using basic meta info: {meta}")
        
        self.pending_meta = meta.copy()
        
        # Print current decision result summary
        if decisions_map:
            logger.debug(f"\n[PENDING_SAVE] Current decision result summary:")
            for symbol, decision in decisions_map.items():
                if symbol != "__meta__":
                    action = decision.get("action", "unknown")
                    target_cash = decision.get("target_cash_amount", 0.0)
                    cash_change = decision.get("cash_change", 0.0)
                    confidence = decision.get("confidence", 0.0)
                    logger.debug(f"[PENDING_SAVE]   {symbol} - action={action}, target_cash_amount={target_cash}, cash_change={cash_change}, confidence={confidence}")
        
        logger.info(f"[PENDING_SAVE] Pending decisions stored, waiting for recording after trade execution")
        logger.info(f"=== Store Pending Decisions Completed ===\n")
        
        return orders 

    def record_executed_decisions(self, executed_symbols: List[str], portfolio=None) -> None:
        """Record decisions using intelligent recording strategy
        
        Strategy:
        1. Hold decisions: Record all (hold never gets rejected)
        2. Buy/sell decisions: Only record successfully executed ones
        
        Args:
            executed_symbols: List of symbols that were successfully executed
            portfolio: Portfolio object to get current shares information
        """
        if not self.pending_decisions:
            logger.debug(f"[DELAYED_RECORD] No pending decisions, skipping recording")
            return
            
        logger.info(f"\n=== Intelligent Decision Recording Started ===")
        logger.info(f"[SMART_RECORD] Implementing intelligent recording strategy:")
        logger.info(f"[SMART_RECORD] - Hold decisions: Record ALL (never get rejected)")
        logger.info(f"[SMART_RECORD] - Buy/sell decisions: Only record successfully executed ones")
        logger.info(f"[SMART_RECORD] Successfully executed symbols: {executed_symbols}")
        logger.info(f"[SMART_RECORD] Pending decision symbols: {list(self.pending_decisions.keys())}")
        
        # Use override mechanism: clear existing records for this date first to avoid duplicates
        current_date = self.pending_meta.get("date", "unknown")
        logger.info(f"[SMART_RECORD] Using override mechanism for date: {current_date}")
        
        # Intelligent recording strategy
        final_decisions = {}
        hold_count = 0
        executed_count = 0
        skipped_count = 0
        
        for symbol, decision in self.pending_decisions.items():
            if symbol.startswith("__"):  # Skip meta fields
                continue
                
            action = decision.get("action", "hold").lower()
            
            # Add current shares information from portfolio (after execution)
            if portfolio and hasattr(portfolio, 'positions'):
                position = portfolio.positions.get(symbol)
                if position and hasattr(position, 'shares'):
                    decision["shares"] = round(float(position.shares), 2)
                    logger.debug(f"[SMART_RECORD] {symbol}: Adding shares info: {decision['shares']}")
                else:
                    decision["shares"] = 0.0
                    logger.debug(f"[SMART_RECORD] {symbol}: No position, shares set to 0")
            else:
                logger.warning(f"[SMART_RECORD] {symbol}: No portfolio provided, cannot record shares")
                decision["shares"] = 0.0
            
            if action == "hold":
                # Strategy 1: Record all hold decisions (hold never gets rejected)
                final_decisions[symbol] = decision
                hold_count += 1
                logger.debug(f"[SMART_RECORD] {symbol}: HOLD decision recorded (hold decisions always recorded)")
            elif symbol in executed_symbols:
                # Strategy 2: Only record successfully executed buy/sell decisions
                final_decisions[symbol] = decision
                executed_count += 1
                logger.debug(f"[SMART_RECORD] {symbol}: {action.upper()} decision recorded (successfully executed)")
            else:
                # Buy/sell decision that was not executed (probably rejected) - skip recording
                skipped_count += 1
                logger.debug(f"[SMART_RECORD] {symbol}: {action.upper()} decision NOT recorded (not executed, likely rejected)")
        
        # Copy meta information
        if "__meta__" in self.pending_decisions:
            final_decisions["__meta__"] = self.pending_decisions["__meta__"]
        
        # Record final decisions with override mechanism
        if final_decisions:
            logger.info(f"\n[SMART_RECORD] Recording summary:")
            logger.info(f"  - Hold decisions recorded: {hold_count}")
            logger.info(f"  - Executed buy/sell decisions recorded: {executed_count}")
            logger.info(f"  - Rejected buy/sell decisions skipped: {skipped_count}")
            logger.info(f"  - Total decisions to record: {len([k for k in final_decisions.keys() if not k.startswith('__')])}")
            
            # Use override mechanism to ensure clean recording
            self._add_decision_to_history(
                current_date, 
                final_decisions, 
                self.pending_meta,
                clear_date_first=True  # Override mechanism: clear existing records for this date first
            )
        else:
            logger.debug(f"[SMART_RECORD] No decisions meet the recording criteria")
        
        # Clear pending decisions
        self.pending_decisions = {}
        self.pending_meta = {}
        logger.info(f"=== Intelligent Decision Recording Completed ===\n")
from __future__ import annotations

import logging
from typing import Dict, List, Optional
import os
import json
from datetime import datetime

logger = logging.getLogger(__name__)

from stockbench.llm.llm_client import LLMClient, LLMConfig
from stockbench.utils.formatting import round_numbers_in_obj


def _prompt_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_prompt_dir(), name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "System: You are a fundamental filter agent responsible for determining which stocks need fundamental analysis. Output compliant filter results in JSON format."


def _prompt_version(name: str) -> str:
    base = os.path.splitext(name)[0]
    return base.replace("_", "/")


def _build_history_from_previous_decisions(previous_decisions: Optional[Dict] = None, current_features: Optional[Dict] = None) -> Dict[str, List[Dict]]:
    """Build historical records from previous decision results (similar to dual_agent)"""
    history = {}
    
    logger.info(f"[FUNDAMENTAL_FILTER] Building historical decision records: previous_decisions={'Yes' if previous_decisions else 'No'}")
    
    if not previous_decisions:
        logger.info(f"[FUNDAMENTAL_FILTER] No historical decision records, returning empty history")
        return history
    
    try:
        decisions = {k: v for k, v in previous_decisions.items() if k != "__meta__"}
        logger.info(f"[FUNDAMENTAL_FILTER] Extracted historical decisions for {len(decisions)} symbols")
        
        history_date = None
        if "__meta__" in previous_decisions:
            meta = previous_decisions["__meta__"]
            if isinstance(meta, dict) and "date" in meta:
                history_date = meta["date"]
                logger.info(f"[FUNDAMENTAL_FILTER] Historical decision date: {history_date}")
            else:
                logger.info(f"[FUNDAMENTAL_FILTER] No valid historical decision date found")
        else:
            logger.info(f"[FUNDAMENTAL_FILTER] No meta information found")
        
        for symbol, decision in decisions.items():
            if not isinstance(decision, dict):
                logger.info(f"[FUNDAMENTAL_FILTER] Skipping invalid decision record: {symbol}")
                continue
                
            # Fix historical record target_cash_amount logic
            action = decision.get("action", "hold")
            cash_change = decision.get("cash_change", 0.0)
            
            # For hold operations, if target_cash_amount is 0, try to get actual position value from current features
            target_cash_amount = decision.get("target_cash_amount", 0.0)
            if action == "hold" and target_cash_amount == 0.0 and cash_change == 0.0:
                # Try to get current position value from current_features
                if current_features and symbol in current_features:
                    current_pos = current_features[symbol].get("position_state", {}).get("current_position_value", 0.0)
                    if current_pos > 0:
                        target_cash_amount = current_pos
                        logger.debug(f"[FUNDAMENTAL_FILTER] Corrected Hold operation history record {symbol}: target_cash_amount corrected from 0.0 to {target_cash_amount}")
                    else:
                        logger.debug(f"[FUNDAMENTAL_FILTER] Hold operation history record {symbol}: current position is 0, keep target_cash_amount=0")
                else:
                    logger.debug(f"[FUNDAMENTAL_FILTER] Hold operation history record {symbol}: cannot get current position, keep target_cash_amount=0")
            
            history_record = {
                "date": history_date,
                "action": action,
                "cash_change": cash_change,
                "target_cash_amount": target_cash_amount,
                "reasons": decision.get("reasons", []),
                "confidence": decision.get("confidence", 0.5)
            }
            
            history[symbol] = [history_record]
            logger.debug(f"[FUNDAMENTAL_FILTER] Built historical record for {symbol}: action={history_record['action']}, cash_change={history_record['cash_change']}, target_cash_amount={history_record['target_cash_amount']}")
        
        logger.info(f"[FUNDAMENTAL_FILTER] Successfully built historical records for {len(history)} symbols")
            
    except Exception as e:
        logger.error(f"[FUNDAMENTAL_FILTER] Failed to build historical records: {e}")
    
    return history


def filter_stocks_needing_fundamental(features_list: List[Dict], cfg: Dict | None = None, 
                                     enable_llm: bool = True,
                                     run_id: Optional[str] = None, ctx: Dict = None,
                                     previous_decisions: Optional[Dict] = None,
                                     decision_history: Optional[Dict[str, List[Dict]]] = None) -> Dict:
    """
    Fundamental Filter Agent: Determine which stocks need fundamental analysis
    
    Args:
        features_list: Input features list for stocks
        cfg: Configuration dictionary containing llm sub-configuration
        enable_llm: Whether to enable LLM, if False then require fundamental for all stocks
        run_id: Backtest run ID for organizing LLM cache directory
        ctx: Context dictionary containing portfolio information
        previous_decisions: Previous decision results for backward compatibility
        decision_history: Long-term historical decision records
        
    Returns:
        Dictionary with:
        {
            "stocks_need_fundamental": ["AAPL", "MSFT"],  # List of stock symbols
            "reasoning": {
                "AAPL": "Reason why AAPL needs fundamental analysis",
                "MSFT": "Reason why MSFT needs fundamental analysis", 
                "GOOGL": "Reason why GOOGL doesn't need fundamental analysis"
            }
        }
    """
    
    # If LLM not enabled, require fundamental analysis for all stocks (conservative fallback)
    if not enable_llm:
        symbols = [item.get("symbol", "UNKNOWN") for item in features_list]
        return {
            "stocks_need_fundamental": symbols,
            "reasoning": {symbol: f"LLM not enabled, requiring fundamental analysis for {symbol} as conservative fallback" 
                         for symbol in symbols}
        }
    
    # Use the already selected llm config (processed by --llm-profile in run_backtest.py)
    llm_cfg_raw = (cfg or {}).get("llm", {})
    
    # If no llm config found, this is an error - don't fallback to defaults
    if not llm_cfg_raw:
        logger.error("❌ No LLM configuration found! Please specify --llm-profile parameter.")
        raise ValueError("No LLM configuration found. Use --llm-profile parameter to specify configuration.")
    
    # Get dual agent configuration
    dual_agent_cfg = (cfg or {}).get("agents", {}).get("dual_agent", {})
    filter_cfg = dual_agent_cfg.get("fundamental_filter", {})
    
    # Read global cache.mode configuration
    cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower()

    llm_cfg = LLMConfig(
        provider=str(llm_cfg_raw.get("provider", "openai-compatible")),
        base_url=str(llm_cfg_raw.get("base_url", "https://api.openai.com/v1")),
        # Use dedicated fundamental_filter model, fallback to llm_profile model, then analyzer_model
        model=str(filter_cfg.get("model") or llm_cfg_raw.get("fundamental_filter_model") or llm_cfg_raw.get("model") or llm_cfg_raw.get("analyzer_model", "gpt-4o-mini")),
        temperature=float(filter_cfg.get("temperature", 0.3)),  # Lower temperature for filtering
        max_tokens=int(filter_cfg.get("max_tokens", 4000)),  # Smaller tokens for filtering
        seed=llm_cfg_raw.get("seed"),
        timeout_sec=float(llm_cfg_raw.get("timeout_sec", 60)),
        max_retries=int(llm_cfg_raw.get("retry", {}).get("max_retries", 3)),
        backoff_factor=float(llm_cfg_raw.get("retry", {}).get("backoff_factor", 0.5)),
        cache_enabled=bool(llm_cfg_raw.get("cache", {}).get("enabled", True)),
        cache_ttl_hours=int(llm_cfg_raw.get("cache", {}).get("ttl_hours", 24)),
        budget_prompt_tokens=int(llm_cfg_raw.get("budget", {}).get("max_prompt_tokens", 200_000)),
        budget_completion_tokens=int(llm_cfg_raw.get("budget", {}).get("max_completion_tokens", 200_000)),
        auth_required=llm_cfg_raw.get("auth_required"),
    )

    # Refine LLM read/write switches based on cache.mode
    if cache_mode == "off":
        llm_cfg.cache_read_enabled = False
        llm_cfg.cache_write_enabled = False
    elif cache_mode == "llm_write_only":
        llm_cfg.cache_read_enabled = False
        llm_cfg.cache_write_enabled = True
    elif cache_mode == "full":
        llm_cfg.cache_read_enabled = True
        llm_cfg.cache_write_enabled = True
    else:
        llm_cfg.cache_read_enabled = None
        llm_cfg.cache_write_enabled = None
    
    # Read prompt from config
    prompt_name = (cfg or {}).get("agents", {}).get("dual_agent", {}).get("fundamental_filter", {}).get("prompt", "fundamental_filter_v1.txt")
    system_prompt = _load_prompt(prompt_name)
    
    # Build input format for filtering (without fundamental_data sections)
    symbols = {}
    total_current_position = 0.0
    
    for item in features_list:
        symbol = item.get("symbol", "UNKNOWN")
        features = item.get("features", {})
        
        # Exclude fundamental_data section for filtering input
        filtered_features = {}
        for section_key, section_data in features.items():
            if section_key != "fundamental_data":
                filtered_features[section_key] = section_data
        
        # Accumulate current total position value
        current_pos_value = filtered_features.get("position_state", {}).get("current_position_value", 0.0)
        total_current_position += current_pos_value
        
        # Build symbols format for filter agent
        symbols[symbol] = {
            "features": filtered_features
        }
    
    # Build portfolio information (similar to single agent)
    portfolio_cfg = cfg.get("portfolio", {}) if cfg else {}
    
    if ctx and "portfolio" in ctx:
        # Use real-time data: current cash + current position value
        current_cash = float(ctx["portfolio"].cash)
        total_assets = current_cash + total_current_position  # Real-time calculation of total assets
        available_cash = current_cash  # Available cash is current cash (inherited from previous day)
    else:
        # Fallback to fixed values in config file
        total_assets = portfolio_cfg.get("total_cash", 100000)  # Default 
        available_cash = total_assets - total_current_position
    
    # Build historical decision records for filter agent
    if decision_history:
        logger.info(f"[FUNDAMENTAL_FILTER] Using long-term historical records, containing history of {len(decision_history)} symbols")
        history = decision_history
    else:
        logger.info(f"[FUNDAMENTAL_FILTER] Building historical records from previous_decisions, previous_decisions={'available' if previous_decisions else 'none'}")
        # Build current_features for historical record correction
        current_features = {}
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            features = item.get("features", {})
            current_features[symbol] = features
        
        history = _build_history_from_previous_decisions(previous_decisions, current_features)
        logger.info(f"[FUNDAMENTAL_FILTER] Historical record construction completed, containing history of {len(history)} symbols")
    
    # Build complete input data for filter agent (now includes full history)
    filter_input = {
        "portfolio_info": {
            "total_assets": total_assets,
            "available_cash": available_cash,
            "position_value": total_current_position,
        },
        "symbols": symbols,
        "history": history  # Filter agent now receives complete historical decision records
    }
    
    # Try to extract trading date from features with enhanced fallback
    trade_date = None
    try:
        if features_list and len(features_list) > 0:
            # Try multiple sources for date extraction
            for item in features_list:
                features = item.get("features", {})
                market_data = features.get("market_data", {})
                
                # Method 1: Direct date field
                if "date" in market_data:
                    trade_date = market_data["date"]
                    break
                    
                # Method 2: Timestamp field
                elif "timestamp" in market_data:
                    timestamp = market_data["timestamp"]
                    if isinstance(timestamp, str):
                        try:
                            dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                            trade_date = dt.strftime("%Y-%m-%d")
                            break
                        except:
                            pass
            
            # Method 3: Try to extract from context if available
            if not trade_date and ctx:
                if "date" in ctx:
                    ctx_date = ctx["date"]
                    if hasattr(ctx_date, 'strftime'):
                        trade_date = ctx_date.strftime("%Y-%m-%d")
                    elif isinstance(ctx_date, str):
                        trade_date = ctx_date
                        
            # Method 4: Fallback to current date for consistency
            if not trade_date:
                from datetime import datetime
                trade_date = datetime.now().strftime("%Y-%m-%d")
                logger.warning(f"[FUNDAMENTAL_FILTER] No date found in features, using current date: {trade_date}")
                
    except Exception as e:
        # Final fallback to current date
        from datetime import datetime
        trade_date = datetime.now().strftime("%Y-%m-%d")
        logger.warning(f"[FUNDAMENTAL_FILTER] Error extracting date: {e}, using current date: {trade_date}")
    
    # Build user prompt
    user_prompt = json.dumps(round_numbers_in_obj(filter_input, 2), ensure_ascii=False)
    
    client = LLMClient()
    
    # Call LLM for filtering
    try:
        data, meta = client.generate_json("fundamental_filter", llm_cfg, system_prompt, user_prompt, 
                                         trade_date=trade_date, run_id=run_id, retry_attempt=0)
        
        logger.info(f"[FUNDAMENTAL_FILTER] LLM call completed: cached={meta.get('cached', False)}, "
                   f"latency={meta.get('latency_ms', 0)}ms")
        
        # Parse filtering results
        if not data or not isinstance(data, dict):
            logger.warning(f"[FUNDAMENTAL_FILTER] LLM returned invalid data format, fallback to require all stocks")
            # Fallback: require fundamental analysis for all stocks
            symbols_list = [item.get("symbol", "UNKNOWN") for item in features_list]
            return {
                "stocks_need_fundamental": symbols_list,
                "reasoning": {symbol: f"LLM returned invalid format, requiring fundamental analysis as fallback" 
                             for symbol in symbols_list}
            }
        
        # Extract filter results
        stocks_need_fundamental = data.get("stocks_need_fundamental", [])
        reasoning = data.get("reasoning", {})
        
        # Validate and clean results
        if not isinstance(stocks_need_fundamental, list):
            logger.warning(f"[FUNDAMENTAL_FILTER] stocks_need_fundamental is not a list, fallback to require all")
            symbols_list = [item.get("symbol", "UNKNOWN") for item in features_list]
            return {
                "stocks_need_fundamental": symbols_list,
                "reasoning": {symbol: f"Invalid filter result format, requiring fundamental analysis as fallback" 
                             for symbol in symbols_list}
            }
        
        if not isinstance(reasoning, dict):
            logger.warning(f"[FUNDAMENTAL_FILTER] reasoning is not a dict, using default reasoning")
            reasoning = {}
        
        # Ensure all input symbols have reasoning
        input_symbols = {item.get("symbol", "UNKNOWN") for item in features_list}
        for symbol in input_symbols:
            if symbol not in reasoning:
                if symbol in stocks_need_fundamental:
                    reasoning[symbol] = f"Requires fundamental analysis (no specific reasoning provided)"
                else:
                    reasoning[symbol] = f"Technical analysis sufficient (no specific reasoning provided)"
        
        # Filter out any hallucinated symbols
        stocks_need_fundamental_filtered = [symbol for symbol in stocks_need_fundamental if symbol in input_symbols]
        
        if len(stocks_need_fundamental_filtered) != len(stocks_need_fundamental):
            removed_symbols = set(stocks_need_fundamental) - input_symbols
            logger.warning(f"[FUNDAMENTAL_FILTER] Removed hallucinated symbols: {removed_symbols}")
        
        logger.info(f"[FUNDAMENTAL_FILTER] Filter completed: {len(stocks_need_fundamental_filtered)}/{len(input_symbols)} "
                   f"stocks need fundamental analysis: {stocks_need_fundamental_filtered}")
        logger.info(f"[FUNDAMENTAL_FILTER] Used historical records for {len(history)} symbols in filtering decision")
        
        return {
            "stocks_need_fundamental": stocks_need_fundamental_filtered,
            "reasoning": reasoning
        }
        
    except Exception as e:
        logger.error(f"[FUNDAMENTAL_FILTER] Error during filtering: {e}")
        # Fallback: require fundamental analysis for all stocks
        symbols_list = [item.get("symbol", "UNKNOWN") for item in features_list]
        return {
            "stocks_need_fundamental": symbols_list,
            "reasoning": {symbol: f"Error during filtering ({str(e)[:50]}), requiring fundamental analysis as fallback" 
                         for symbol in symbols_list}
        }

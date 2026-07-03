from __future__ import annotations

import logging
from typing import Dict, List, Optional
import os
import json
from datetime import datetime

logger = logging.getLogger(__name__)

from stockbench.llm.llm_client import LLMClient, LLMConfig
from stockbench.utils.formatting import round_numbers_in_obj
from stockbench.agents.fundamental_filter_agent import filter_stocks_needing_fundamental
from stockbench.core.features import build_features_for_prompt


def _prompt_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(name: str) -> str:
    path = os.path.join(_prompt_dir(), name)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "System: You are a decision agent responsible for making trading decisions based on filtered data. Output compliant decisions in JSON format."


def _prompt_version(name: str) -> str:
    base = os.path.splitext(name)[0]
    return base.replace("_", "/")


def _filter_hallucination_decisions(decisions_data: dict, valid_symbols: set) -> dict:
    """
    Filter out hallucinated decisions, keeping only actual input stock symbols
    
    Args:
        decisions_data: Decision data dictionary returned by LLM
        valid_symbols: Set of valid stock symbols actually input
        
    Returns:
        Filtered decision data dictionary
    """
    if not isinstance(decisions_data, dict):
        return decisions_data
    
    filtered_decisions = {}
    hallucinated_symbols = []
    
    for symbol, decision in decisions_data.items():
        if symbol in valid_symbols:
            filtered_decisions[symbol] = decision
        else:
            hallucinated_symbols.append(symbol)
    
    # Log filtered hallucinated decisions
    if hallucinated_symbols:
        logger.warning(f"[HALLUCINATION_FILTER] Filtered hallucinated decision symbols: {hallucinated_symbols}")
        logger.info(f"[FILTER_STATS] Valid decisions: {len(filtered_decisions)}, Filtered decisions: {len(hallucinated_symbols)}")
    
    return filtered_decisions


def _validate_decision_logic(action: str, target_cash_amount: float, current_position_value: float) -> bool:
    """
    Validate whether decision logic is reasonable
    
    Args:
        action: Decision action ("increase", "decrease", "hold", "close")
        target_cash_amount: Target cash amount
        current_position_value: Current position value
        
    Returns:
        bool: True if logic is reasonable, False if logic is unreasonable
    """
    try:
        action = str(action).lower().strip()
        target_cash_amount = float(target_cash_amount)
        current_position_value = float(current_position_value)
        
        # Increase operation: target amount should be greater than current position value
        if action == "increase":
            if target_cash_amount <= current_position_value:
                logger.warning(f"[VALIDATION_ERROR] Increase operation unreasonable: target_cash_amount({target_cash_amount:.2f}) <= current_position_value({current_position_value:.2f})")
                return False
        
        # Decrease operation: target amount should be less than current position value
        elif action == "decrease":
            if target_cash_amount >= current_position_value:
                logger.warning(f"[VALIDATION_ERROR] Decrease operation unreasonable: target_cash_amount({target_cash_amount:.2f}) >= current_position_value({current_position_value:.2f})")
                return False
        
        # Close operation: target amount should be 0 or close to 0
        elif action == "close":
            if target_cash_amount > 0.01:  # Allow small margin of error
                logger.warning(f"[VALIDATION_ERROR] Close operation unreasonable: target_cash_amount({target_cash_amount:.2f}) > 0")
                return False
        
        # Hold operation: target amount should equal current position value (allow small fluctuations)
        elif action == "hold":
            # For hold operation, allow certain tolerance range
            tolerance = max(current_position_value * 0.01, 100.0)  # 1% or 100 unit tolerance
            if abs(target_cash_amount - current_position_value) > tolerance:
                logger.warning(f"[VALIDATION_WARNING] Hold operation has significant deviation: target_cash_amount({target_cash_amount:.2f}) vs current_position_value({current_position_value:.2f}), difference: {abs(target_cash_amount - current_position_value):.2f}")
                # For hold operation, only warning, don't return False
        
        logger.info(f"[VALIDATION_OK] {action} operation validation passed: target_cash_amount({target_cash_amount:.2f}) vs current_position_value({current_position_value:.2f})")
        return True
        
    except Exception as e:
        logger.error(f"[VALIDATION_ERROR] Error occurred during validation: {e}")
        return False


def decide_batch_dual_agent(features_list: List[Dict], cfg: Dict | None = None, enable_llm: bool = True, 
                           bars_data: Dict[str, Dict] = None, 
                           run_id: Optional[str] = None, previous_decisions: Optional[Dict] = None, 
                           decision_history: Optional[Dict[str, List[Dict]]] = None, ctx: Dict = None, 
                           rejected_orders: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """
    Dual agent batch decision making. Input is features list, returns {symbol: decision_output_dict}.
    
    This function implements the dual-agent architecture:
    1. Step 1: Fundamental Filter Agent - determines which stocks need fundamental analysis
    2. Step 2: Enhanced Feature Construction - builds features with/without fundamental data based on filtering
    3. Step 3: Decision Agent - makes final trading decisions
    
    Args:
        features_list: Input features list
        cfg: Configuration dictionary containing llm sub-configuration
        enable_llm: Whether to enable LLM, if False then fallback to neutral decisions
        bars_data: Raw historical data dictionary {symbol: {"bars_day": df}} for feature construction
        run_id: Backtest run ID for organizing LLM cache directory
        previous_decisions: Previous decision results for backward compatibility
        decision_history: Long-term historical decision records
        ctx: Context dictionary containing portfolio information
        rejected_orders: List of rejected order information for retry logic
        
    Returns:
        Dictionary {symbol: decision_dict, "__meta__": meta_dict}
    """
    
    results: Dict[str, Dict] = {}
    meta_agg: Dict[str, object] = {"calls": 0, "cache_hits": 0, "parse_errors": 0, "latency_ms_sum": 0, 
                                  "tokens_prompt": 0, "tokens_completion": 0, "prompt_version": None}
    
    # If LLM not enabled, directly fallback to hold
    if not enable_llm:
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            current_position_value = float((item.get("features", {}).get("position_state") or {}).get("current_position_value", 0.0))
            hold_decision = {
                "action": "hold",
                "target_cash_amount": current_position_value,
                "cash_change": 0.0,
                "reasons": [f"LLM not enabled, {symbol} maintains current position"],
                "confidence": 0.5,
                "timestamp": datetime.now().isoformat(),
                "analysis_excerpt": "",
                "tech_score": 0.5,
                "sent_score": 0.0,
                "event_risk": "normal"
            }
            results[symbol] = round_numbers_in_obj(hold_decision, 2)
        results["__meta__"] = meta_agg
        return results
    
    logger.info(f"🚀 [DUAL_AGENT] Starting dual-agent decision process for {len(features_list)} stocks")
    
    try:
        # Step 1: Fundamental Filter Agent - determines which stocks need fundamental analysis
        logger.info(f"📊 [DUAL_AGENT] Step 1: Calling fundamental filter agent")
        filter_result = filter_stocks_needing_fundamental(
            features_list=features_list,
            cfg=cfg,
            enable_llm=enable_llm,
            run_id=run_id,
            ctx=ctx,
            previous_decisions=previous_decisions,
            decision_history=decision_history
        )
        
        stocks_need_fundamental = filter_result.get("stocks_need_fundamental", [])
        reasoning = filter_result.get("reasoning", {})
        
        logger.info(f"✅ [DUAL_AGENT] Filter completed: {len(stocks_need_fundamental)}/{len(features_list)} stocks need fundamental analysis")
        logger.info(f"📋 [DUAL_AGENT] Stocks needing fundamental: {stocks_need_fundamental}")
        
        # Step 2: Enhanced Feature Construction - build features with/without fundamental data
        logger.info(f"🔧 [DUAL_AGENT] Step 2: Building enhanced features based on filtering results")
        enhanced_features_list = []
        
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            features = item.get("features", {})
            
            # Conditionally rebuild features based on filter results
            enhanced_features = None
            rebuild_success = False
            
            # Check if bars_data is available for rebuilding
            if bars_data and symbol in bars_data:
                try:
                    original_data = bars_data.get(symbol, {})
                    
                    # Validate required data components
                    required_keys = ["bars_day", "snapshot", "position_state"]
                    missing_keys = [key for key in required_keys if key not in original_data]
                    
                    if missing_keys:
                        logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: Missing data components for rebuild: {missing_keys}")
                    
                    # Determine whether to include fundamental data based on filter results
                    exclude_fundamental = symbol not in stocks_need_fundamental
                    
                    # For stocks needing fundamental data, preserve existing news data from original features
                    # to avoid losing news information during feature rebuilding
                    original_news_events = features.get("news_events", {}).get("top_k_events", [])
                    preserved_news_items = []
                    
                    if original_news_events and original_news_events != ["No news data available"]:
                        # Convert existing news events back to news_items format for rebuild
                        for event in original_news_events:
                            if isinstance(event, dict):
                                preserved_news_items.append(event)
                            elif isinstance(event, str):
                                preserved_news_items.append({"title": event, "description": ""})
                    
                    # Use preserved news items if available, otherwise fall back to bars_data news
                    news_items_for_rebuild = preserved_news_items or original_data.get("news_items", [])
                    
                    # Check configuration for include_current_price setting
                    include_current_price = (cfg or {}).get("features", {}).get("include_current_price", False)
                    
                    # Attempt to rebuild features with appropriate fundamental data inclusion
                    rebuilt_features = build_features_for_prompt(
                        bars_day=original_data.get("bars_day"), 
                        snapshot=original_data.get("snapshot", {}),
                        news_items=news_items_for_rebuild,
                        position_state=original_data.get("position_state", {}),
                        details=original_data.get("details", {}),
                        config=cfg or {},
                        include_price=include_current_price,  # Use configuration setting
                        exclude_fundamental=exclude_fundamental
                    )
                    
                    # Extract only the features part to avoid double nesting
                    # rebuilt_features has structure {"symbol": "...", "features": {...}}
                    # We only need the "features" part here
                    enhanced_features = rebuilt_features.get("features", {})
                    
                    rebuild_success = True
                    
                    if symbol in stocks_need_fundamental:
                        logger.debug(f"📊 [DUAL_AGENT] {symbol}: Successfully rebuilt features WITH fundamental data")
                    else:
                        logger.debug(f"🎯 [DUAL_AGENT] {symbol}: Successfully rebuilt features WITHOUT fundamental data")
                        
                except Exception as e:
                    logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: Failed to rebuild features: {e}")
                    enhanced_features = None
            else:
                logger.warning(f"⚠️ [DUAL_AGENT] {symbol}: bars_data not available for feature rebuild")
            
            # Fallback: use original features if rebuild failed or data unavailable
            if not rebuild_success or enhanced_features is None:
                enhanced_features = features.copy()
                logger.info(f"🔄 [DUAL_AGENT] {symbol}: Using original features as fallback (may lack fundamental data)")
            
            # Add filter reasoning to the enhanced features
            enhanced_features["filter_reasoning"] = reasoning.get(symbol, "No reasoning provided")
            
            enhanced_item = {
                "symbol": symbol,
                "features": enhanced_features
            }
            enhanced_features_list.append(enhanced_item)
            
        # Calculate statistics for monitoring
        stocks_with_fundamental = len(stocks_need_fundamental)
        stocks_without_fundamental = len(enhanced_features_list) - stocks_with_fundamental
        
        logger.info(f"✅ [DUAL_AGENT] Enhanced features built for {len(enhanced_features_list)} stocks:")
        logger.info(f"   📊 {stocks_with_fundamental} stocks WITH fundamental data: {list(stocks_need_fundamental)}")
        logger.info(f"   🎯 {stocks_without_fundamental} stocks WITHOUT fundamental data")
        
        # Log feature optimization for monitoring
        if stocks_with_fundamental > 0:
            logger.info(f"🔧 [DUAL_AGENT] Feature enhancement: Added fundamental data for {stocks_with_fundamental} stocks requiring deeper analysis")
        if stocks_without_fundamental > 0:
            logger.info(f"🔧 [DUAL_AGENT] Feature optimization: Excluded fundamental data for {stocks_without_fundamental} stocks to reduce noise")
        
        # Step 3: Decision Agent - makes final trading decisions using enhanced features
        logger.info(f"🎯 [DUAL_AGENT] Step 3: Calling decision agent with enhanced features")
        
        # Use the decision agent prompt from config
        prompt_name = (cfg or {}).get("agents", {}).get("dual_agent", {}).get("decision_agent", {}).get("prompt", "decision_agent_v1.txt")
        system_prompt = _load_prompt(prompt_name)
        meta_agg["prompt_version"] = _prompt_version(prompt_name)
        
        # Get LLM configuration for decision agent
        # Use the already selected llm config (processed by --llm-profile in run_backtest.py)
        llm_cfg_raw = (cfg or {}).get("llm", {})
        
        # If no llm config found, this is an error - don't fallback to defaults
        if not llm_cfg_raw:
            logger.error("❌ No LLM configuration found! Please specify --llm-profile parameter.")
            raise ValueError("No LLM configuration found. Use --llm-profile parameter to specify configuration.")
        
        # Get dual agent decision configuration
        dual_agent_cfg = (cfg or {}).get("agents", {}).get("dual_agent", {})
        decision_cfg = dual_agent_cfg.get("decision_agent", {})
        
        # Read global cache.mode configuration
        cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower()

        llm_cfg = LLMConfig(
            provider=str(llm_cfg_raw.get("provider", "openai-compatible")),
            base_url=str(llm_cfg_raw.get("base_url", "https://api.openai.com/v1")),
            # Use dedicated decision_agent model, fallback to llm_profile model, then other fallbacks
            model=str(decision_cfg.get("model") or llm_cfg_raw.get("decision_agent_model") or llm_cfg_raw.get("model") or llm_cfg_raw.get("single_agent_model") or llm_cfg_raw.get("analyzer_model", "gpt-4o-mini")),
            temperature=float(decision_cfg.get("temperature", 0.7)),
            max_tokens=int(decision_cfg.get("max_tokens", 8000)),
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

        # Refine LLM cache read/write switches based on cache.mode
        if cache_mode == "off":
            llm_cfg.cache_read_enabled = False
            llm_cfg.cache_write_enabled = False
        elif cache_mode == "llm_write_only":
            llm_cfg.cache_read_enabled = False
            llm_cfg.cache_write_enabled = True
        elif cache_mode == "full":
            # If read cache is not available now, set read to False; keep True for future enablement
            llm_cfg.cache_read_enabled = True
            llm_cfg.cache_write_enabled = True
        else:
            # Unknown value: fall back to profile defaults
            llm_cfg.cache_read_enabled = None
            llm_cfg.cache_write_enabled = None
        
        client = LLMClient()
        
        return _decide_batch_portfolio_dual_agent(
            enhanced_features_list,
            llm_cfg,
            system_prompt,
            client,
            meta_agg,
            cfg,
            bars_data,
            run_id,
            previous_decisions,
            decision_history,
            ctx,
            rejected_orders,
        )
    
    except Exception as e:
        logger.error(f"❌ [DUAL_AGENT] Error during dual-agent processing: {e}")
        logger.exception("Detailed error:")
        
        # Fallback to hold decisions for all stocks
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            current_position_value = float((item.get("features", {}).get("position_state") or {}).get("current_position_value", 0.0))
            hold_decision = {
                "action": "hold",
                "target_cash_amount": current_position_value,
                "cash_change": 0.0,
                "reasons": [f"Dual-agent error ({str(e)[:50]}), {symbol} maintains current position"],
                "confidence": 0.5,
                "timestamp": datetime.now().isoformat(),
                "analysis_excerpt": "",
                "tech_score": 0.5,
                "sent_score": 0.0,
                "event_risk": "normal"
            }
            results[symbol] = round_numbers_in_obj(hold_decision, 2)
        results["__meta__"] = meta_agg
        return results


def _decide_batch_portfolio_dual_agent(features_list: List[Dict], llm_cfg: LLMConfig, system_prompt: str,
                                      client: LLMClient, meta_agg: Dict, cfg: Dict, bars_data: Dict, 
                                      run_id: Optional[str], previous_decisions: Optional[Dict] = None, 
                                      decision_history: Optional[Dict[str, List[Dict]]] = None, ctx: Dict = None, 
                                      rejected_orders: Optional[List[Dict]] = None) -> Dict[str, Dict]:
    """Dual-agent batch portfolio decision making with comprehensive retry mechanism"""
    results = {}
    
    # Build input format conforming to prompt template
    symbols = {}
    total_current_position = 0.0
    
    for item in features_list:
        symbol = item.get("symbol", "UNKNOWN")
        features = item.get("features", {})
        
        # Accumulate current total position value
        current_pos_value = features.get("position_state", {}).get("current_position_value", 0.0)
        total_current_position += current_pos_value
        
        # Build symbols format conforming to template (enhanced features include filter_reasoning)
        symbols[symbol] = {
            "features": features
        }
    
    # Build portfolio info (similar to single agent)
    portfolio_cfg = cfg.get("portfolio", {}) if cfg else {}
    
    if ctx and "portfolio" in ctx:
        current_cash = float(ctx["portfolio"].cash)
        total_assets = current_cash + total_current_position
        available_cash = current_cash
        available_cash_ratio = current_cash / total_assets if total_assets > 0 else 0.0
        remaining_cash_ratio = available_cash_ratio
    else:
        total_assets = portfolio_cfg.get("total_cash", 100000)  # Keep consistent with fundamental_filter_agent
        available_cash = total_assets - total_current_position
        remaining_cash_ratio = available_cash / total_assets if total_assets > 0 else 0.0
        available_cash_ratio = remaining_cash_ratio
    
    # Get minimum cash ratio requirement
    min_cash_ratio = portfolio_cfg.get("min_cash_ratio", 0.0)
    
    # Build historical decision records
    if decision_history:
        logger.info(f"[DEBUG] Dual agent decision: Using long-term historical records, containing history of {len(decision_history)} symbols")
        history = decision_history
    else:
        logger.info(f"[DEBUG] Dual agent decision: Building historical records from previous_decisions, previous_decisions={'available' if previous_decisions else 'none'}")
        # Build current_features for historical record correction
        current_features = {}
        for item in features_list:
            symbol = item.get("symbol", "UNKNOWN")
            features = item.get("features", {})
            current_features[symbol] = features
        
        history = _build_history_from_previous_decisions(previous_decisions, current_features)
        logger.info(f"[DEBUG] Dual agent decision: Historical record construction completed, containing history of {len(history)} symbols")
    
    # Build complete input data
    portfolio_input = {
        "portfolio_info": {
            "total_assets": total_assets,
            "available_cash": available_cash,
            "position_value": total_current_position,
        },
        "symbols": symbols,
        "history": history
    }
    
    # Build base user prompt
    base_user_prompt = json.dumps(round_numbers_in_obj(portfolio_input, 2), ensure_ascii=False)
    
    # Try to extract trading date with enhanced fallback
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
                trade_date = datetime.now().strftime("%Y-%m-%d")
                logger.warning(f"[DUAL_AGENT_DECISION] No date found in features, using current date: {trade_date}")
                
    except Exception as e:
        # Final fallback to current date
        trade_date = datetime.now().strftime("%Y-%m-%d")
        logger.warning(f"[DUAL_AGENT_DECISION] Error extracting date: {e}, using current date: {trade_date}")
    
    # Get unified retry configuration
    retry_cfg = cfg.get("agents", {}).get("retry", {}) if cfg else {}
    max_unified_retries = int(retry_cfg.get("max_attempts", 3))
    
    # Check if order rejection info is included and determine starting retry count
    order_rejection_info = []
    current_retry_attempt = 0
    engine_retry_count = 0  # Track engine-level retries separately
    
    if rejected_orders:
        # Create a lookup for rejected orders by symbol
        rejected_by_symbol = {order.get("symbol"): order for order in rejected_orders}
        
        for symbol in symbols.keys():
            rejection_info = rejected_by_symbol.get(symbol)
            
            if rejection_info:
                rejection_reason = rejection_info.get("reason", "Order rejected")
                rejection_context = rejection_info.get("context", {})
                
                # Track engine retry count from rejected orders
                engine_retry_count = max(engine_retry_count, rejection_info.get("retry_count", 0))
                
                # Check if this is a portfolio-wide rebalancing issue
                is_portfolio_rebalance = rejection_context.get("portfolio_rebalance_needed", False)
                
                if is_portfolio_rebalance:
                    # Portfolio-wide cash constraint issue
                    total_required = rejection_context.get("total_cash_required_all_orders", 0)
                    available_cash = rejection_context.get("available_cash", 0)
                    cash_shortfall = rejection_context.get("cash_shortfall", 0)
                    suggestion = rejection_context.get("suggestion", "")
                    
                    rejection_prompt = f"\\n\\n🚨 CRITICAL: PORTFOLIO-WIDE CASH CONSTRAINT VIOLATION\\n"
                    rejection_prompt += f"❌ Previous attempt failed due to insufficient total cash for all positions\\n"
                    rejection_prompt += f"📊 Financial Summary:\\n"
                    rejection_prompt += f"   • Available Cash: ${available_cash:,.2f}\\n"
                    rejection_prompt += f"   • Total Required: ${total_required:,.2f}\\n"
                    rejection_prompt += f"   • Cash Shortfall: ${cash_shortfall:,.2f}\\n"
                    rejection_prompt += f"\\n💡 ACTION REQUIRED: {suggestion}\\n"
                    rejection_prompt += f"\\n🔄 You MUST rebalance the ENTIRE portfolio to fit within the available cash budget.\\n"
                    rejection_prompt += f"Consider reducing all position sizes proportionally or selecting fewer stocks.\\n"
                    
                    # Add this global message only once (for the first rejected symbol)
                    if len(order_rejection_info) == 0:
                        order_rejection_info.append(rejection_prompt)
                else:
                    # Individual order rejection (legacy logic)
                    rejection_prompt = f"\\n\\n❌ IMPORTANT: Previous order for {symbol} was rejected.\\n"
                    rejection_prompt += f"Rejection reason: {rejection_reason}\\n"
                    
                    if rejection_context:
                        rejection_prompt += f"Additional context: {rejection_context}\\n"
                    
                    rejection_prompt += f"Please provide a corrected decision for {symbol} that addresses this rejection.\\n"
                    order_rejection_info.append(rejection_prompt)
                
                # Set current retry attempt to engine retry count for rejected orders
                current_retry_attempt = engine_retry_count
    
    # Unified retry loop - comprehensive validation and retry mechanism
    retry_count = current_retry_attempt
    data = None
    decisions_data = None
    
    # Check if this is an engine-level retry (rejected orders)
    is_engine_retry = rejected_orders is not None and len(rejected_orders) > 0
    if is_engine_retry:
        logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Engine-level retry detected ({len(rejected_orders)} rejected orders), engine_retry_count={engine_retry_count}")
    else:
        logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Starting unified retry loop: current_retry={retry_count}, max_total_retries={max_unified_retries}")
    
    # Global retry limit: total retries (engine + LLM) cannot exceed max_attempts
    while True:
        total_retry_attempt = engine_retry_count + retry_count
        
        if total_retry_attempt >= max_unified_retries:
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Global retry limit reached: engine_retries={engine_retry_count} + llm_retries={retry_count} = {total_retry_attempt} >= {max_unified_retries}")
            break
        # Build user prompt for this attempt
        if order_rejection_info:
            rejection_prompt = "\\n\\n" + "\\n".join(order_rejection_info)
            user_prompt = base_user_prompt + rejection_prompt
            logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Including order rejection prompts for {len(order_rejection_info)} stocks")
        else:
            user_prompt = base_user_prompt
        
        # Add any additional retry notes from previous validation failures
        if retry_count > current_retry_attempt and "retry_notes" in locals():
            user_prompt += "\\n\\n" + retry_notes
            logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Including retry notes from previous validation failures")
        
        # Calculate total retry attempt (engine retries + LLM retries)
        total_retry_attempt = engine_retry_count + retry_count
        
        # Log retry breakdown for debugging
        if total_retry_attempt > 0:
            logger.info(f"[RETRY_BREAKDOWN] Engine retries: {engine_retry_count}, LLM retries: {retry_count}, Total: {total_retry_attempt}")
        
        # Call LLM with complete retry attempt info
        data, meta = client.generate_json("decision_agent", llm_cfg, system_prompt, user_prompt,
                                         trade_date=trade_date, run_id=run_id, retry_attempt=total_retry_attempt)
        
        meta_agg["calls"] = int(meta_agg["calls"]) + 1
        meta_agg["cache_hits"] = int(meta_agg["cache_hits"]) + (1 if meta.get("cached") else 0)
        meta_agg["latency_ms_sum"] = int(meta_agg["latency_ms_sum"]) + int(meta.get("latency_ms", 0))
        usage = meta.get("usage", {})
        meta_agg["tokens_prompt"] = int(meta_agg["tokens_prompt"]) + int(usage.get("prompt_tokens", 0))
        meta_agg["tokens_completion"] = int(meta_agg["tokens_completion"]) + int(usage.get("completion_tokens", 0))
        
        # Parse batch decision results
        if not data or not isinstance(data, dict):
            meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
            
            # Check if this is a truncation issue (finish_reason: length)
            is_truncated = False
            if hasattr(meta, 'get') and meta.get('raw_response', {}).get('choices', []):
                finish_reason = meta['raw_response']['choices'][0].get('finish_reason')
                if finish_reason == 'length':
                    is_truncated = True
                    logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Response was truncated due to token limit")
            
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: LLM returned invalid data format (truncated: {is_truncated})")
            
            # Check if we can continue retrying (global limit)
            next_total_attempt = engine_retry_count + retry_count + 1
            if next_total_attempt < max_unified_retries:
                # For truncated responses, add instruction for more concise output
                if is_truncated:
                    logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Adding concise output instruction for truncation retry")
                    
                    # Add instruction for more concise output
                    if "user_prompt" in locals():
                        user_prompt += "\n\nIMPORTANT: Due to previous response truncation, please provide a more concise analysis while maintaining all required JSON decision fields."
                
                retry_count += 1
                continue
            else:
                # Final attempt failed, fallback to hold decisions
                logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to invalid data format (engine: {engine_retry_count}, llm: {retry_count})")
                for symbol in symbols.keys():
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    hold_decision = {
                        "action": "hold",
                        "target_cash_amount": current_position_value,
                        "cash_change": 0.0,
                        "reasons": [f"Dual-agent retry failed: invalid data format after {max_unified_retries} attempts"],
                        "confidence": 0.5,
                        "timestamp": datetime.now().isoformat()
                    }
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
                results["__meta__"] = meta_agg
                return results
        
        # Process decisions
        decisions_data = data.get("decisions", data)  # Handle both formats
        
        # Additional data validation and cleanup
        if not isinstance(decisions_data, dict):
            logger.warning(f"[DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Unable to extract valid decision data from LLM response")
            meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
            
            # Check if this is a truncation issue and try to extract partial decisions
            partial_decisions = None
            if hasattr(meta, 'get') and meta.get('raw_response', {}).get('choices', []):
                finish_reason = meta['raw_response']['choices'][0].get('finish_reason')
                if finish_reason == 'length':
                    logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Attempting to extract partial decisions from truncated response")
                    # Try to extract partial decisions using enhanced JSON parsing
                    try:
                        from stockbench.llm.llm_client import LLMClient
                        temp_client = LLMClient()
                        raw_content = meta.get('raw_response', {}).get('choices', [{}])[0].get('message', {}).get('content', '')
                        if raw_content:
                            partial_data = temp_client._extract_json_with_improved_logic(raw_content)
                            if partial_data and isinstance(partial_data, dict) and "decisions" in partial_data:
                                partial_decisions = partial_data.get("decisions")
                                logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Successfully extracted {len(partial_decisions)} partial decisions")
                    except Exception as e:
                        logger.debug(f"[DUAL_AGENT_UNIFIED_RETRY] Failed to extract partial decisions: {e}")
            
            if partial_decisions and isinstance(partial_decisions, dict) and len(partial_decisions) > 0:
                # Use partial decisions if we got some
                decisions_data = partial_decisions
                logger.info(f"[DUAL_AGENT_UNIFIED_RETRY] Using {len(partial_decisions)} partial decisions from truncated response")
            else:
                # Check if we can continue retrying (global limit)
                next_total_attempt = engine_retry_count + retry_count + 1
                if next_total_attempt < max_unified_retries:
                    retry_count += 1
                    continue
                else:
                    # Final attempt failed, fallback to hold decisions
                    logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to unparseable data (engine: {engine_retry_count}, llm: {retry_count})")
                for symbol in symbols.keys():
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    hold_decision = {
                        "action": "hold",
                        "target_cash_amount": current_position_value,
                        "cash_change": 0.0,
                        "reasons": [f"Dual-agent retry failed: unparseable data after {max_unified_retries} attempts"],
                        "confidence": 0.5,
                        "timestamp": datetime.now().isoformat()
                    }
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
                results["__meta__"] = meta_agg
                return results
        
        # Filter hallucinated decisions
        decisions_data = _filter_hallucination_decisions(decisions_data, set(symbols.keys()))
        
        # Comprehensive validation: logic validation and fund constraints
        logic_validation_failed = False
        cash_shortage_detected = False
        cash_ratio_violation = False
        invalid_decisions = []
        
        # Calculate predicted cash usage and validate constraints
        predicted_cash_usage = 0.0
        
        for symbol in symbols.keys():
            symbol_decision = decisions_data.get(symbol)
            
            if isinstance(symbol_decision, dict):
                try:
                    action = str(symbol_decision.get("action", "hold")).lower().strip()
                    
                    # For hold action, if no target_cash_amount, use current position value
                    if action == "hold" and "target_cash_amount" not in symbol_decision:
                        target_cash_amount = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    else:
                        target_cash_amount = float(symbol_decision.get("target_cash_amount", 0.0))
                    
                    # Get current position value
                    current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                    
                    # Validate decision logic using the same function as single agent
                    if not _validate_decision_logic(action, target_cash_amount, current_position_value):
                        logic_validation_failed = True
                        invalid_decisions.append({
                            "symbol": symbol,
                            "action": action,
                            "target_cash_amount": target_cash_amount,
                            "current_position_value": current_position_value
                        })
                        logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: {symbol} {action} operation logic unreasonable")
                        
                except (ValueError, TypeError) as e:
                    logic_validation_failed = True
                    invalid_decisions.append({
                        "symbol": symbol,
                        "error": str(e)
                    })
                    logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: {symbol} decision parsing failed: {e}")
                    
                # Calculate cash usage for this decision
                try:
                    cash_change = target_cash_amount - current_position_value
                    if cash_change > 0:  # Only count positive cash changes (purchases)
                        predicted_cash_usage += cash_change
                except:
                    pass
        
        # Check fund constraints
        available_cash_after = available_cash - predicted_cash_usage
        predicted_remaining_ratio = available_cash_after / total_assets if total_assets > 0 else 0.0
        
        if available_cash_after < 0:
            cash_shortage_detected = True
        
        if predicted_remaining_ratio < min_cash_ratio:
            cash_ratio_violation = True
        
        # If validation passed, process and return results
        if not logic_validation_failed and not cash_shortage_detected and not cash_ratio_violation:
            logger.info(f"✅ [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: All validations passed, processing results")
            
            # Process each decision
            for symbol, symbol_data in symbols.items():
                current_position_value = symbol_data["features"].get("position_state", {}).get("current_position_value", 0.0)
                
                # Get decision for this symbol
                symbol_decision = decisions_data.get(symbol)
                
                if isinstance(symbol_decision, dict):
                    try:
                        action = str(symbol_decision.get("action", "hold")).lower()
                        
                        # For hold action, if no target_cash_amount, use current position value
                        if action == "hold" and "target_cash_amount" not in symbol_decision:
                            target_cash_amount = current_position_value
                        else:
                            target_cash_amount = float(symbol_decision.get("target_cash_amount", current_position_value))
                        
                        reasons = symbol_decision.get("reasons", ["No specific reason"])
                        if not isinstance(reasons, list):
                            reasons = [str(reasons)]
                        
                        confidence = float(symbol_decision.get("confidence", 0.5))
                        target_cash_amount = max(0.0, target_cash_amount)
                        confidence = max(0.0, min(1.0, confidence))
                        
                        cash_change = target_cash_amount - current_position_value
                        
                        results[symbol] = round_numbers_in_obj({
                            "action": action,
                            "target_cash_amount": target_cash_amount,
                            "cash_change": cash_change,
                            "reasons": reasons,
                            "confidence": confidence,
                            "timestamp": datetime.now().isoformat()
                        }, 2)
                        
                    except Exception as e:
                        # Parsing failed, use hold decision
                        meta_agg["parse_errors"] = int(meta_agg["parse_errors"]) + 1
                        hold_decision = {
                            "action": "hold",
                            "target_cash_amount": current_position_value,
                            "cash_change": 0.0,
                            "reasons": [f"Dual-agent decision parsing error: {str(e)[:50]}"],
                            "confidence": 0.5,
                            "timestamp": datetime.now().isoformat()
                        }
                        results[symbol] = round_numbers_in_obj(hold_decision, 2)
                else:
                    # No decision for this symbol, use hold
                    hold_decision = {
                        "action": "hold",
                        "target_cash_amount": current_position_value,
                        "cash_change": 0.0,
                        "reasons": ["No decision provided by dual-agent"],
                        "confidence": 0.5,
                        "timestamp": datetime.now().isoformat()
                    }
                    results[symbol] = round_numbers_in_obj(hold_decision, 2)
            
            results["__meta__"] = meta_agg
            return results
        
        # Validation failed, prepare for retry
        if logic_validation_failed:
            logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Decision logic validation failed for {len(invalid_decisions)} decisions")
        if cash_shortage_detected:
            logger.warning(f"🚨 [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Insufficient available_cash: Expected remaining cash {available_cash_after:.2f} < 0")
        if cash_ratio_violation:
            logger.warning(f"⚠️ [DUAL_AGENT_UNIFIED_RETRY] Attempt {retry_count + 1}: Expected remaining cash ratio {predicted_remaining_ratio:.3f} below minimum requirement {min_cash_ratio:.3f}")
        
        # Check if we can continue retrying (global limit)  
        next_total_attempt = engine_retry_count + retry_count + 1
        if next_total_attempt < max_unified_retries:
            logger.info(f"🔄 [DUAL_AGENT_UNIFIED_RETRY] Preparing retry {next_total_attempt + 1}/{max_unified_retries} (engine: {engine_retry_count}, llm: {retry_count + 1})")
            
            # Generate different retry prompts based on violation type
            retry_notes_list = []
            
            if logic_validation_failed:
                logic_error_details = []
                for invalid in invalid_decisions:
                    if "error" not in invalid:
                        if invalid["action"] == "increase":
                            logic_error_details.append(f"{invalid['symbol']}: increase operation requires target_cash_amount > current_position_value, but you set {invalid['target_cash_amount']:.0f} <= {invalid['current_position_value']:.0f}")
                        elif invalid["action"] == "decrease":
                            logic_error_details.append(f"{invalid['symbol']}: decrease operation requires target_cash_amount < current_position_value, but you set {invalid['target_cash_amount']:.0f} >= {invalid['current_position_value']:.0f}")
                        elif invalid["action"] == "close":
                            logic_error_details.append(f"{invalid['symbol']}: close operation requires target_cash_amount = 0, but you set {invalid['target_cash_amount']:.0f}")
                    else:
                        logic_error_details.append(f"{invalid['symbol']}: parsing error - {invalid['error']}")
                
                retry_notes_list.append(f"❌ DECISION LOGIC ERRORS: The following decisions have unreasonable logic:\\n" + "\\n".join(logic_error_details) + "\\nPlease correct these logical inconsistencies.")
            
            if cash_shortage_detected:
                retry_notes_list.append(f"💰 INSUFFICIENT FUNDS: Total predicted cash usage {predicted_cash_usage:.2f} exceeds available cash {available_cash:.2f}. Please reduce purchase amounts or choose different stocks.")
            
            if cash_ratio_violation:
                retry_notes_list.append(f"⚖️ CASH RATIO VIOLATION: Predicted remaining cash ratio {predicted_remaining_ratio:.3f} is below minimum requirement {min_cash_ratio:.3f}. Please maintain higher cash reserves.")
            
            retry_notes = "\\n\\n" + "\\n\\n".join(retry_notes_list)
            retry_count += 1
            continue
        else:
            # Final attempt failed, fallback to hold decisions
            logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] All {max_unified_retries} attempts failed due to validation errors (engine: {engine_retry_count}, llm: {retry_count})")
            for symbol in symbols.keys():
                current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
                hold_decision = {
                    "action": "hold",
                    "target_cash_amount": current_position_value,
                    "cash_change": 0.0,
                    "reasons": [f"Dual-agent validation failed after {max_unified_retries} attempts"],
                    "confidence": 0.5,
                    "timestamp": datetime.now().isoformat()
                }
                results[symbol] = round_numbers_in_obj(hold_decision, 2)
            results["__meta__"] = meta_agg
            return results
    
    # Should not reach here, but fallback to hold decisions just in case
    logger.error(f"[DUAL_AGENT_UNIFIED_RETRY] Unexpected exit from retry loop, using hold decisions")
    for symbol in symbols.keys():
        current_position_value = symbols[symbol]["features"].get("position_state", {}).get("current_position_value", 0.0)
        hold_decision = {
            "action": "hold",
            "target_cash_amount": current_position_value,
            "cash_change": 0.0,
            "reasons": ["Dual-agent unexpected error, maintaining current position"],
            "confidence": 0.5,
            "timestamp": datetime.now().isoformat()
        }
        results[symbol] = round_numbers_in_obj(hold_decision, 2)
    results["__meta__"] = meta_agg
    return results


def _build_history_from_previous_decisions(previous_decisions: Optional[Dict] = None, current_features: Optional[Dict] = None) -> Dict[str, List[Dict]]:
    """Build historical records from previous decision results (same as single agent)"""
    history = {}
    
    logger.info(f"[DEBUG] Building historical decision records: previous_decisions={'Yes' if previous_decisions else 'No'}")
    
    if not previous_decisions:
        logger.info(f"[DEBUG] No historical decision records, returning empty history")
        return history
    
    try:
        decisions = {k: v for k, v in previous_decisions.items() if k != "__meta__"}
        logger.info(f"[DEBUG] Extracted historical decisions for {len(decisions)} symbols")
        
        history_date = None
        if "__meta__" in previous_decisions:
            meta = previous_decisions["__meta__"]
            if isinstance(meta, dict) and "date" in meta:
                history_date = meta["date"]
                logger.info(f"[DEBUG] Historical decision date: {history_date}")
            else:
                logger.info(f"[DEBUG] No valid historical decision date found")
        else:
            logger.info(f"[DEBUG] No meta information found")
        
        for symbol, decision in decisions.items():
            if not isinstance(decision, dict):
                logger.info(f"[DEBUG] Skipping invalid decision record: {symbol}")
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
                        logger.debug(f"[DUAL_AGENT] Corrected Hold operation history record {symbol}: target_cash_amount corrected from 0.0 to {target_cash_amount}")
                    else:
                        logger.debug(f"[DUAL_AGENT] Hold operation history record {symbol}: current position is 0, keep target_cash_amount=0")
                else:
                    logger.debug(f"[DUAL_AGENT] Hold operation history record {symbol}: cannot get current position, keep target_cash_amount=0")
            
            history_record = {
                "date": history_date,
                "action": action,
                "cash_change": cash_change,
                "target_cash_amount": target_cash_amount,
                "reasons": decision.get("reasons", []),
                "confidence": decision.get("confidence", 0.5)
            }
            
            history[symbol] = [history_record]
            logger.info(f"[DEBUG] Built historical record for {symbol}: action={history_record['action']}, cash_change={history_record['cash_change']}, target_cash_amount={history_record['target_cash_amount']}")
        
        logger.info(f"[DEBUG] Successfully built historical records for {len(history)} symbols")
            
    except Exception as e:
        logger.error(f"Failed to build historical records: {e}")
    
    return history

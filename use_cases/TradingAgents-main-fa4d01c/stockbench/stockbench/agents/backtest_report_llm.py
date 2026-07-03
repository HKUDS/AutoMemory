from __future__ import annotations

import json
import os
import logging
from typing import Dict, Any, Optional

from stockbench.llm.llm_client import LLMClient, LLMConfig
from stockbench.utils.logging_helper import get_llm_logger

# Get logger
llm_logger = get_llm_logger()


def _format_pct(x: Any) -> str:
	try:
		return f"{float(x):.2%}"
	except Exception:
		return str(x)


def _format_float(x: Any, nd: int = 2) -> str:
	try:
		return f"{float(x):.{nd}f}"
	except Exception:
		return str(x)


def _strip_code_fences(text: str) -> str:
	try:
		s = text.strip()
		if s.startswith("```") and s.endswith("```"):
			lines = s.splitlines()
			if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
				return "\n".join(lines[1:-1]).strip()
		return s
	except Exception:
		return text


def _load_prompt(prompt_name: str) -> str:
	"""Load prompt file content"""
	prompt_path = os.path.join(os.path.dirname(__file__), "prompts", prompt_name)
	try:
		with open(prompt_path, "r", encoding="utf-8") as f:
			return f.read().strip()
	except FileNotFoundError:
		llm_logger.warning(f"⚠️ Prompt file not found: {prompt_name}")
		return f"System Role: You are a quantitative backtest assistant. Please generate JSON format responses based on input data."


def generate_backtest_report(payload: Dict[str, Any], cfg: Dict | None = None, run_id: Optional[str] = None, profile_name: Optional[str] = None) -> str:
    # Use the already selected llm config (processed by --llm-profile in run_backtest.py)
    llm_cfg_raw = (cfg or {}).get("llm", {})
    
    # If no llm config found, this is an error - don't fallback to defaults
    if not llm_cfg_raw:
        llm_logger.error("❌ No LLM configuration found! Please specify --llm-profile parameter.")
        raise ValueError("No LLM configuration found. Use --llm-profile parameter to specify configuration.")
    
    llm_cfg = LLMConfig(
        provider=str(llm_cfg_raw.get("provider", "openai")),
        base_url=str(llm_cfg_raw.get("base_url", "https://api.openai.com/v1")),
        model=str(llm_cfg_raw.get("backtest_report_model", llm_cfg_raw.get("single_agent_model", "gpt-4o-mini"))),
        		temperature=0.2,  # Fixed for backtest reports
		max_tokens=700,   # Fixed for backtest reports
        seed=llm_cfg_raw.get("seed"),
        timeout_sec=float(llm_cfg_raw.get("timeout_sec", 60)),
        max_retries=int(llm_cfg_raw.get("retry", {}).get("max_retries", 3)),
        backoff_factor=float(llm_cfg_raw.get("retry", {}).get("backoff_factor", 0.5)),
        		cache_enabled=True,
        cache_ttl_hours=int(llm_cfg_raw.get("cache", {}).get("ttl_hours", 24)),
        budget_prompt_tokens=200_000,
        budget_completion_tokens=int(llm_cfg_raw.get("max_tokens", 16_000)),
        auth_required=llm_cfg_raw.get("auth_required"),
    )
    # Read global cache.mode and refine read/write switches
    cache_mode = str((cfg or {}).get("cache", {}).get("mode", "full")).lower()
    if cache_mode == "off":
        llm_cfg.cache_read_enabled = False
        llm_cfg.cache_write_enabled = False
    elif cache_mode == "llm_write_only":
        llm_cfg.cache_read_enabled = False
        llm_cfg.cache_write_enabled = True
    elif cache_mode == "full":
        llm_cfg.cache_read_enabled = True
        llm_cfg.cache_write_enabled = True
    client = LLMClient()
    # Read system prompt template
    prompt_path = os.path.join(os.path.dirname(__file__), "prompts", "backtest_report_v1.txt")
    try:
        with open(prompt_path, "r", encoding="utf-8") as f:
            system_prompt = f.read().strip()
    except FileNotFoundError:
        # Fallback: use built-in system prompt
        system_prompt = (
            "You are a quantitative backtest result interpretation assistant. Please use objective, concise English to summarize the key points of a backtest result.\n"
            "Requirements: Strictly output JSON: {\"report\": \"...\"}\n"
            "Report should cover period/frequency/symbol scale, returns and risk, trading scale, relative benchmark (if any) and improvement suggestions."
        )
    user_prompt = (
        "Please write a 200-400 word summary based on the following data:\n"
        + json.dumps(payload, ensure_ascii=False)
    )

    llm_logger.info(f"🤖 Backtest report generation - model: {llm_cfg.model}")
    data, meta = client.generate_json("backtest_report", llm_cfg, system_prompt, user_prompt, run_id=run_id, retry_attempt=0)
    
    
    # Record call results and raw return content
    raw_content = meta.get("raw", "")
    if data:
        llm_logger.info(f"✅ Backtest report generation successful - latency: {meta.get('latency_ms', 0)}ms, cache hit: {meta.get('cached', False)}")
        llm_logger.debug(f"📄 LLM raw return: {raw_content[:200]}...")
    else:
        reason = meta.get('reason', 'unknown')
        llm_logger.error(f"❌ Backtest report generation failed - reason: {reason}")
        if raw_content:
            llm_logger.debug(f"📄 LLM raw return: {raw_content[:200]}...")
    
    # Prioritize JSON parsing results (system prompt requires JSON format output)
    if isinstance(data, dict):
        if isinstance(data.get("report"), str):
            llm_logger.debug(f"📄 Using report field from JSON")
            return data["report"].strip()
        if isinstance(data.get("text"), str):
            llm_logger.debug(f"📄 Using text field from JSON")
            return data["text"].strip()
        # If JSON doesn't contain expected fields, try using raw content
        if "raw_content" in data and isinstance(data["raw_content"], str):
            llm_logger.debug(f"📄 Using raw_content field from JSON")
            return data["raw_content"].strip()
    
    # Fallback: try using raw return content
    try:
        raw = (meta or {}).get("raw")
        if isinstance(raw, str) and raw.strip():
            clean = _strip_code_fences(raw)
            if clean.strip():
                llm_logger.debug(f"📄 Fallback using raw text output")
                return clean
    except Exception:
        pass
    # Disabled model fallback - use exact configuration specified by user
    # If the configured model fails, we should fail rather than fallback
    llm_logger.debug("Model fallback disabled - using exact user configuration")
    	# Debug: output miss reason for troubleshooting (doesn't affect return content)
    try:
        reason = (meta or {}).get("reason")
        cached = (meta or {}).get("cached")
        if reason or cached:
                    print(f"[backtest_report_llm] LLM output not used, reason={reason}, cached={cached}")
    except Exception:
        pass
    	# Fallback: combine metrics + summary_text to generate more complete natural language
    m = payload.get("metrics", {}) or {}
    period = payload.get("period", {}) or {}
    timespan = payload.get("timespan", "N/A")
    run_id = payload.get("run_id", "N/A")
    symbols = payload.get("symbols", []) or []
    summary_text = (payload.get("summary_text") or "").strip()
    parts = []
    parts.append(f"Backtest Report ({run_id})\nPeriod: {period.get('start','?')} ~ {period.get('end','?')}, Frequency: {timespan}, Symbols: {','.join(symbols) if symbols else 'N/A'}")
    parts.append(
        "Core Metrics: " 
        f"Cumulative Return {_format_pct(m.get('cum_return', 0))}, Max Drawdown {_format_pct(m.get('max_drawdown', 0))}, "
        f"Annualized Volatility {_format_pct(m.get('volatility', 0))}, Sharpe {_format_float(m.get('sharpe', 0), 2)}, "
        f"Trade Count {int(m.get('trades_count', 0))}, Notional Volume {_format_float(m.get('trades_notional', 0), 2)}."
    )
    # Relative indicators (if exists)
    if any(k in m for k in ("information_ratio", "tracking_error", "alpha_simple", "beta", "corr", "up_capture", "down_capture")):
        parts.append(
            "Relative to Benchmark: "
            f"IR={_format_float(m.get('information_ratio', 0), 2)}, TE={_format_float(m.get('tracking_error', 0), 2)}, "
            f"Alpha={_format_float(m.get('alpha_simple', 0), 2)}, Beta={_format_float(m.get('beta', 0), 2)}, "
            f"Correlation={_format_float(m.get('corr', 0), 2)}, Up/Down Capture={_format_float(m.get('up_capture', 0), 2)}/{_format_float(m.get('down_capture', 0), 2)}."
        )
    # Directly include summary from summary.txt (if available)
    if summary_text:
        parts.append("Reference Summary:\n" + summary_text)
    # Recommendations
    advice = []
    if int(m.get("trades_count", 0) or 0) < 10:
        advice.append("Small sample size, recommend expanding date and symbol range to improve representativeness")
    if float(m.get("volatility", 0) or 0) < 0.08 and float(m.get("sharpe", 0) or 0) > 2:
        advice.append("Low volatility and high Sharpe ratio, recommend adding cost/slippage sensitivity analysis and robustness validation")
    if advice:
        parts.append("Recommendations: " + "; ".join(advice) + ".")
    return "\n\n".join(parts) 

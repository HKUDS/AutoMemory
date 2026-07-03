# TradingAgents/graph/trading_graph.py

import os
import re
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List, Optional

from langgraph.prebuilt import ToolNode

from tradingagents.llm_clients import create_llm_client

from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_transactions,
    get_global_news
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .self_reflection import KnowledgeCardWriter, MacroRefresher, SelfReflector
from .signal_processing import SignalProcessor
from tradingagents.agents.utils.memory_stores import DecisionsLog


def _terminal_state_from_stream_trace(trace: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pick final graph state from stream_mode='values' chunks without a second invoke.

    Normally trace[-1] matches graph.invoke(). Problems when it does not:
    - Interrupted run: last chunk is mid-pipeline, so final_trade_decision was never set.
    - Rare ordering: last yield may not be the snapshot you want in edge LangGraph cases.

    Use the last chunk that already has a non-empty final_trade_decision (Portfolio Manager
    output); otherwise fall back to trace[-1].
    """
    if not trace:
        return {}
    for chunk in reversed(trace):
        ftd = chunk.get("final_trade_decision")
        if isinstance(ftd, str) and ftd.strip():
            return chunk
    return trace[-1]


def _extract_rating(final_decision: str) -> str:
    """Extract a rating keyword from the final PM decision text."""
    if not final_decision:
        return "UNKNOWN"
    # Look for explicit Rating: label first
    m = re.search(
        r"\*{0,2}Rating\*{0,2}[:\s]+\*{0,2}(Buy|Overweight|Hold|Underweight|Sell)\*{0,2}",
        final_decision,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).upper()
    # Fallback: scan for first standalone rating word
    for word in ("Overweight", "Underweight", "Buy", "Sell", "Hold"):
        if re.search(rf"\b{word}\b", final_decision, re.IGNORECASE):
            return word.upper()
    return "UNKNOWN"


def _extract_one_line_thesis(final_decision: str, llm=None) -> str:
    """Extract or generate a one-line thesis from the PM decision."""
    # Try to grab the Executive Summary sentence
    m = re.search(
        r"Executive Summary[:\s*]+(.+?)(?:\n|\.)", final_decision, re.IGNORECASE | re.DOTALL
    )
    if m:
        thesis = m.group(1).strip().replace("\n", " ")
        if len(thesis) > 200:
            thesis = thesis[:197] + "..."
        return thesis
    # Fall back to first non-empty line after stripping markdown formatting and known labels
    for line in final_decision.splitlines():
        clean = line.strip().lstrip("#* ").strip()
        clean = re.sub(r"^(THESIS|SUMMARY|RECOMMENDATION|DECISION)\s*:\s*", "", clean, flags=re.IGNORECASE)
        if len(clean) > 20:
            return clean[:200]
    return final_decision[:200]


def _extract_key_risks(final_decision: str) -> List[str]:
    """Extract 2-3 key risk bullets from PM decision text."""
    risks = []
    # Look for risk section (stop at a blank line or next label)
    m = re.search(
        r"(?:key risks?|risk factors?)\s*[:\-]\s*(.+?)(?:\n\n|(?=\n[A-Z][A-Z\s]+:)|\Z)",
        final_decision,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        block = m.group(1)
        # Try bullet-formatted list first
        bullets = re.findall(r"[-*•\d]+[.)]\s*(.+)", block)
        if bullets:
            risks = [b.strip()[:150] for b in bullets[:3]]
        else:
            # Fall back: comma- or semicolon-separated inline list
            items = re.split(r"[,;]\s*", block.strip())
            risks = [it.strip()[:150] for it in items if it.strip()][:3]
    return risks


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
        callbacks: Optional[List] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
            callbacks: Optional list of callback handlers (e.g., for tracking LLM/tool stats)
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG
        self.callbacks = callbacks or []

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(self.config["data_cache_dir"], exist_ok=True)
        os.makedirs(self.config["results_dir"], exist_ok=True)

        # Initialize LLMs with provider-specific thinking configuration
        llm_kwargs = self._get_provider_kwargs()

        # Add callbacks to kwargs if provided (passed to LLM constructor)
        if self.callbacks:
            llm_kwargs["callbacks"] = self.callbacks

        deep_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["deep_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )
        quick_client = create_llm_client(
            provider=self.config["llm_provider"],
            model=self.config["quick_think_llm"],
            base_url=self.config.get("backend_url"),
            **llm_kwargs,
        )

        self.deep_thinking_llm = deep_client.get_llm()
        self.quick_thinking_llm = quick_client.get_llm()
        
        # Initialize memories (now backed by persistent JSONL)
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.portfolio_manager_memory = FinancialSituationMemory("portfolio_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config["max_debate_rounds"],
            max_risk_discuss_rounds=self.config["max_risk_discuss_rounds"],
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.portfolio_manager_memory,
            self.conditional_logic,
        )

        self.propagator = Propagator()
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # Memory writers (Type K, R, M)
        self.self_reflector = SelfReflector(self.quick_thinking_llm)
        self.kc_writer = KnowledgeCardWriter(self.quick_thinking_llm)
        self.macro_refresher = MacroRefresher(
            self.quick_thinking_llm,
            get_global_news_fn=self._call_global_news,
        )

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _call_global_news(self, curr_date: str, look_back_days: int = 7, limit: int = 10) -> str:
        """Thin wrapper so MacroRefresher can call get_global_news without importing the tool."""
        return get_global_news.invoke(
            {"curr_date": curr_date, "look_back_days": look_back_days, "limit": limit}
        )

    def _get_provider_kwargs(self) -> Dict[str, Any]:
        """Get provider-specific kwargs for LLM client creation."""
        kwargs = {}
        provider = self.config.get("llm_provider", "").lower()

        if provider == "google":
            thinking_level = self.config.get("google_thinking_level")
            if thinking_level:
                kwargs["thinking_level"] = thinking_level

        elif provider == "openai":
            reasoning_effort = self.config.get("openai_reasoning_effort")
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

        elif provider == "anthropic":
            effort = self.config.get("anthropic_effort")
            if effort:
                kwargs["effort"] = effort

        return kwargs

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_global_news,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                ]
            ),
        }

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date."""

        self.ticker = company_name
        trade_date_str = str(trade_date)

        # Type M: refresh macro notes if stale (once per process)
        try:
            self.macro_refresher.refresh_if_stale(trade_date_str)
        except Exception:
            pass

        # Initialize state
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        args = self.propagator.get_graph_args(callbacks=self.callbacks)

        if self.debug:
            # Debug mode with tracing. Stream emits full state after every node; many
            # nodes only update debate/risk fields and leave messages unchanged, so the
            # same last message (e.g. HumanMessage "Continue" or the Trader reply) would
            # otherwise be pretty_printed many times — dedupe by stable message identity.
            trace = []
            last_printed_key = None
            def _last_msg_key(msg) -> tuple:
                mid = getattr(msg, "id", None)
                if mid:
                    return ("id", mid)
                content = getattr(msg, "content", "")
                if not isinstance(content, str):
                    content = repr(content)
                return ("content", type(msg).__name__, content)
            for chunk in self.graph.stream(init_agent_state, **args):
                msgs = chunk.get("messages") or []
                if not msgs:
                    trace.append(chunk)
                    continue
                last = msgs[-1]
                key = _last_msg_key(last)
                if key != last_printed_key:
                    last_printed_key = key
                    last.pretty_print()
                trace.append(chunk)
            final_state = _terminal_state_from_stream_trace(trace)
        else:
            # Standard mode without tracing
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # -----------------------------------------------------------------
        # Post-propagate memory writes
        # -----------------------------------------------------------------

        final_decision = final_state.get("final_trade_decision") or ""

        # Type D: append decision to decisions log
        try:
            rating = _extract_rating(final_decision)
            thesis = _extract_one_line_thesis(final_decision)
            key_risks = _extract_key_risks(final_decision)
            DecisionsLog.append(
                company_name,
                {
                    "trade_date": trade_date_str,
                    "ticker": company_name,
                    "rating": rating,
                    "one_line_thesis": thesis,
                    "key_risks": key_risks,
                },
            )
        except Exception:
            pass

        # Type K: merge ticker knowledge card (one LLM call)
        try:
            self.kc_writer.merge(final_state, company_name, trade_date_str)
        except Exception:
            pass

        # Type R: self-reflection (one LLM call, appends to all 5 BM25 buckets)
        try:
            self.self_reflector.run(
                final_state,
                {
                    "bull_memory": self.bull_memory,
                    "bear_memory": self.bear_memory,
                    "trader_memory": self.trader_memory,
                    "invest_judge_memory": self.invest_judge_memory,
                    "portfolio_manager_memory": self.portfolio_manager_memory,
                },
            )
        except Exception:
            pass

        # Return decision and processed signal
        return final_state, self.process_signal(final_decision)

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "aggressive_history": final_state["risk_debate_state"]["aggressive_history"],
                "conservative_history": final_state["risk_debate_state"]["conservative_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file
        directory = Path(self.config["results_dir"]) / self.ticker / "TradingAgentsStrategy_logs"
        directory.mkdir(parents=True, exist_ok=True)

        log_path = directory / f"full_states_log_{trade_date}.json"
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(self.log_states_dict[str(trade_date)], f, indent=4)

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_portfolio_manager(
            self.curr_state, returns_losses, self.portfolio_manager_memory
        )

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)

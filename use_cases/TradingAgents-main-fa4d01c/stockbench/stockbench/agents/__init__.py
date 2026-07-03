"""
Agents package for Trading Agent v2

This package provides intelligent LLM-powered agents for various trading tasks,
including:

- Decision making agents for buy/sell/hold recommendations
- Fundamental analysis filtering agents
- Backtest reporting and analysis agents
- Multi-agent coordination and workflows

Key Components:
- DualAgentLLM: Main decision-making agent with dual-stage analysis
- FundamentalFilterAgent: Pre-filtering based on fundamental analysis
- BacktestReportLLM: Intelligent backtest report generation
- Prompt Templates: Versioned prompt templates for consistent agent behavior

These agents leverage Large Language Models to provide intelligent trading decisions
and analysis, combining market data with sophisticated reasoning capabilities.
"""

# Decision making agents
from .dual_agent_llm import (
    decide_batch_dual_agent
)

# Fundamental analysis agents
from .fundamental_filter_agent import (
    filter_stocks_needing_fundamental
)

# Reporting and analysis agents
from .backtest_report_llm import (
    generate_backtest_report
)

__all__ = [
    # Decision making
    'decide_batch_dual_agent',
    
    # Fundamental analysis
    'filter_stocks_needing_fundamental',
    
    # Reporting and analysis
    'generate_backtest_report'
] 
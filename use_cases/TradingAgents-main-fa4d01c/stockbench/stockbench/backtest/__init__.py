"""
Backtest package for Trading Agent v2

This package provides comprehensive backtesting capabilities for trading strategies,
including:

- Core backtesting engine with portfolio management
- Strategy implementations (LLM-based decision making)
- Performance metrics and evaluation
- Data handling and pipeline management  
- Visualization and reporting tools
- Slippage modeling

Key Components:
- BacktestEngine: Core backtesting simulation engine
- Strategy: Base and LLM-based trading strategies
- Datasets: Data loading and management
- Metrics: Performance evaluation and analysis
- Reports: Output generation and visualization
"""

# Core engine and data components
from .engine import (
    BacktestEngine,
    Portfolio, 
    Position,
    TradeRecord,
    PortfolioSnapshot,
    load_benchmark_components,
    build_per_symbol_bh_benchmark,
    price_to_returns,
    aggregate_with_rebalance,
    align_with_strategy_nav
)
from .datasets import Datasets
from .slippage import Slippage

# Strategy implementations
from .strategies import LlmDecision

# Pipeline and execution
from .pipeline import run_backtest

# Metrics and evaluation
from .metrics import (
    evaluate,
    compute_nav_to_metrics_series,
    compare_symbol_series,
    compute_per_symbol_metrics_from_nav,
    compute_simple_average_benchmark,
    compute_weighted_average_benchmark,
    extract_key_metrics_summary
)

# Reporting and visualization
from .reports import write_outputs
from .visualization import (
    plot_aggregated_cumreturn_analysis,
    plot_stock_price_trends,
    generate_individual_stocks_summary,
    plot_nav_comparison,
    plot_totalassets_comparison,
    plot_multi_period_performance_heatmap,
    plot_rolling_metrics_comparison,
    plot_performance_ranking_over_time
)

# Data processing utilities
from .summarize import main as summarize_backtest_results

__all__ = [
    # Core engine components
    'BacktestEngine',
    'Portfolio',
    'Position', 
    'TradeRecord',
    'PortfolioSnapshot',
    'Datasets',
    'Slippage',
    
    # Strategy implementations
    'LlmDecision',
    
    # Pipeline and execution
    'run_backtest',
    
    # Engine utilities
    'load_benchmark_components',
    'build_per_symbol_bh_benchmark',
    'price_to_returns',
    'aggregate_with_rebalance',
    'align_with_strategy_nav',
    
    # Metrics and evaluation
    'evaluate',
    'compute_nav_to_metrics_series',
    'compare_symbol_series',
    'compute_per_symbol_metrics_from_nav',
    'compute_simple_average_benchmark',
    'compute_weighted_average_benchmark',
    'extract_key_metrics_summary',
    
    # Reporting and visualization
    'write_outputs',
    'plot_aggregated_cumreturn_analysis',
    'plot_stock_price_trends', 
    'generate_individual_stocks_summary',
    'plot_nav_comparison',
    'plot_totalassets_comparison',
    'plot_multi_period_performance_heatmap',
    'plot_rolling_metrics_comparison',
    'plot_performance_ranking_over_time',
    
    # Data processing
    'summarize_backtest_results'
] 
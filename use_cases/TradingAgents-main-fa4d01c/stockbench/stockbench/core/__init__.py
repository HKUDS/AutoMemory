"""
Core package for Trading Agent v2

This package provides the fundamental building blocks for the trading agent system,
including:

- Data schemas and type definitions for all trading components
- Market data access and caching layer (data_hub)
- Feature engineering and technical indicator computation
- Order execution planning and portfolio management
- Price utilities and validation mechanisms

Key Components:
- Schemas: Pydantic models for type-safe data structures
- DataHub: Unified market data access layer with caching
- Features: Technical analysis and feature engineering
- Executor: Order planning and execution logic
- PriceUtils: Price data handling and validation

This core module serves as the foundation for all other trading agent components,
providing reliable data structures, market data access, and execution primitives.
"""

# Data schemas and type definitions
from .schemas import (
    TechSnapshot,
    NewsSnapshot, 
    PositionState,
    FeatureInput,
    AnalyzerOutput,
    DecisionOutput,
    Order,
)

# Market data access and caching
from .data_hub import (
    # Market data functions
    get_bars,
    get_grouped_daily,
    get_universal_snapshots,
    get_gainers_losers,
    get_news,
    get_dividends,
    get_splits,
    get_ticker_details,
    get_market_status,
    get_financials,
    get_stock_indicators,
    
    # Trading day utilities
    is_trading_day,
    get_next_trading_day,
    
    # Cache management
    clear_old_news_cache,
    get_cache_info,
    
    # Data quality and comparison
    compare_with_legacy_day
)

# Feature engineering and technical analysis
from .features import (
    build_features_for_prompt
)

# Order execution and portfolio management
from .executor import (
    plan_orders,
    decide_batch
)

# Price utilities and validation
from .price_utils import (
    get_unified_price,
    calculate_position_value,
    add_price_fallback_mechanism,
    validate_price_data_consistency
)


__all__ = [
    # Data schemas
    'TechSnapshot',
    'NewsSnapshot',
    'PositionState', 
    'FeatureInput',
    'AnalyzerOutput',
    'DecisionOutput',
    'Order',
    
    # Market data access
    'get_bars',
    'get_grouped_daily',
    'get_universal_snapshots',
    'get_gainers_losers',
    'get_news',
    'get_dividends',
    'get_splits',
    'get_ticker_details',
    'get_market_status',
    'get_financials',
    'get_stock_indicators',
    
    # Trading day utilities
    'is_trading_day',
    'get_next_trading_day',
    
    # Cache management
    'clear_old_news_cache',
    'get_cache_info',
    
    # Data quality tools
    'compare_with_legacy_day',
    
    # Feature engineering
    'build_features_for_prompt',
    
    # Order execution
    'plan_orders',
    'decide_batch',
    
    # Price utilities
    'get_unified_price',
    'calculate_position_value',
    'add_price_fallback_mechanism',
    'validate_price_data_consistency',
    
] 
"""
Trading Agent v2 - Applications Package

This package contains command-line applications for the Trading Agent v2 system.

Available Applications:
- run_backtest: Backtesting application for strategy evaluation

Each application is a self-contained CLI tool that can be run independently.
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Trading Agent Team"

# Import main functions from applications for programmatic access
try:
    from .run_backtest import main as run_backtest_main
    
    __all__ = [
        "run_backtest_main",
    ]
except ImportError:
    # Handle cases where dependencies might not be available
    __all__ = []

# Application metadata
APPLICATIONS = {
    "run_backtest": {
        "name": "Backtest Runner",
        "description": "Run backtests for trading strategies with historical data",
        "module": "stockbench.apps.run_backtest",
        "main_function": "main"
    }
}


def get_application_info(app_name: str) -> dict | None:
    """
    Get information about a specific application.
    
    Args:
        app_name: Name of the application
        
    Returns:
        Application metadata dictionary or None if not found
    """
    return APPLICATIONS.get(app_name)


def list_applications() -> list[str]:
    """
    List all available applications.
    
    Returns:
        List of application names
    """
    return list(APPLICATIONS.keys())
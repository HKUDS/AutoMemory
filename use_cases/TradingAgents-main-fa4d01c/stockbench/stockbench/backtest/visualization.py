"""
Enhanced visualization module for trading agent backtesting.

This module provides advanced plotting functions for aggregated analysis,
including cumulative return analysis and price trend comparisons.
"""
from __future__ import annotations

import os
from typing import Dict, Optional

import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use('Agg')

from stockbench.backtest.metrics import (
    compute_simple_average_benchmark,
    compute_weighted_average_benchmark, 
    extract_key_metrics_summary,
    _compute_drawdown_series
)


def plot_aggregated_cumreturn_analysis(per_symbol_nav_df: pd.DataFrame, 
                                     output_path: str, 
                                     cfg: Optional[Dict] = None) -> None:
    """Plot aggregated cumulative return analysis for multiple stocks
    
    Args:
        per_symbol_nav_df: DataFrame with each column as a stock's NAV time series
        output_path: Output image path
        cfg: Visualization configuration
    """
    if per_symbol_nav_df.empty:
        print("[WARNING] Empty nav data, skipping cumreturn analysis")
        return
        
    # Get configuration
    viz_cfg = (cfg or {}).get("visualization", {})
    alpha = viz_cfg.get("individual_alpha", 0.3)
    linewidth = viz_cfg.get("average_linewidth", 3)
    figsize = viz_cfg.get("figure_size", [12, 8])
    dpi = viz_cfg.get("dpi", 300)
    
    # Calculate cumulative return in real-time based on nav data
    per_symbol_cumret = {}
    for symbol in per_symbol_nav_df.columns:
        try:
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) > 0:
                base_nav = nav_series.iloc[0] if nav_series.iloc[0] != 0 else 1.0
                cumret = nav_series / base_nav - 1.0
                per_symbol_cumret[symbol] = cumret
        except Exception as e:
            print(f"[WARNING] Failed to compute cumret for {symbol}: {e}")
            continue
    
    if not per_symbol_cumret:
        print("[WARNING] No valid cumret data, skipping plot")
        return
        
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Color palette for better distinction (colorblind-friendly)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
              '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
    
    # Individual stock lines with transparency
    for i, (symbol, cumret_series) in enumerate(per_symbol_cumret.items()):
        color = colors[i % len(colors)]
        ax.plot(cumret_series.index, cumret_series.values, 
               alpha=alpha, linewidth=1.2, color=color,
               label=symbol if len(per_symbol_cumret) <= 8 else "")
    
    # Calculate and plot benchmark lines
    try:
        # Simple average benchmark (equal weight)
        simple_avg_nav = compute_simple_average_benchmark(per_symbol_nav_df)
        if len(simple_avg_nav) > 0:
            base_nav = simple_avg_nav.iloc[0] if simple_avg_nav.iloc[0] != 0 else 1.0
            simple_avg_cumret = simple_avg_nav / base_nav - 1.0
            ax.plot(simple_avg_cumret.index, simple_avg_cumret.values, 
                   color='#FF4444', linewidth=linewidth, 
                   label='Equal Weight Average', linestyle='-')
        
        # Weighted average benchmark (using equal weight as initial configuration if needed)
        weighted_avg_nav = compute_weighted_average_benchmark(per_symbol_nav_df)
        if len(weighted_avg_nav) > 0:
            base_nav = weighted_avg_nav.iloc[0] if weighted_avg_nav.iloc[0] != 0 else 1.0
            weighted_avg_cumret = weighted_avg_nav / base_nav - 1.0
            ax.plot(weighted_avg_cumret.index, weighted_avg_cumret.values, 
                   color='#4444FF', linewidth=linewidth, 
                   label='Weighted Average', linestyle='--')
            
    except Exception as e:
        print(f"[WARNING] Failed to compute benchmark lines: {e}")
    
    # Chart styling
    ax.set_xlabel('Date', fontsize=12)
    ax.set_ylabel('Cumulative Return', fontsize=12)
    ax.set_title('Multi-Stock Cumulative Return Analysis', fontsize=14, fontweight='bold')
    ax.grid(True, alpha=0.3)
    ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    
    # Add key metrics annotations
    try:
        key_metrics_cfg = (cfg or {}).get("key_metrics", {})
        if key_metrics_cfg.get("annotation_box", True):
            # Get the list of metrics to highlight
            highlight_metrics = list(key_metrics_cfg.get("highlight", ["cum_return", "max_drawdown", "sortino"]))
            
            # Calculate key metrics for simple average benchmark
            simple_avg_nav = compute_simple_average_benchmark(per_symbol_nav_df)
            metrics_summary = extract_key_metrics_summary(simple_avg_nav, highlight_metrics)
            
            # Build dynamic annotation text
            annotation_lines = ["Equal Weight Benchmark:"]
            
            # Metric name mapping (for more friendly display names)
            metric_display_names = {
                "cum_return": "Cum Return",
                "max_drawdown": "Max Drawdown", 
                "sortino": "Sortino Ratio",
                "sharpe": "Sharpe Ratio",
                "volatility": "Volatility"
            }
            
            # Metric formatting function
            def format_metric_value(metric_name: str, value: float) -> str:
                if metric_name in ["cum_return", "max_drawdown", "volatility"]:
                    return f"{value:.2%}"
                elif metric_name in ["sortino", "sharpe"]:
                    return f"{value:.3f}"
                else:
                    return f"{value:.4f}"
            
            # Dynamically generate annotations based on configured highlight list
            for metric in highlight_metrics:
                if metric in metrics_summary:
                    display_name = metric_display_names.get(metric, metric.title())
                    formatted_value = format_metric_value(metric, metrics_summary[metric])
                    annotation_lines.append(f"{display_name}: {formatted_value}")
            
            annotation_text = "\n".join(annotation_lines)
            
            ax.text(0.02, 0.98, annotation_text, transform=ax.transAxes,
                   verticalalignment='top', bbox=dict(boxstyle='round', 
                   facecolor='white', alpha=0.8), fontsize=10)
    except Exception as e:
        print(f"[WARNING] Failed to add annotation box: {e}")
    
    # Save image
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        print(f"✅ Saved aggregated cumreturn analysis to {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save plot to {output_path}: {e}")
        plt.close(fig)


def plot_stock_price_trends(per_symbol_nav_df: pd.DataFrame, 
                           output_path: str, 
                           cfg: Optional[Dict] = None) -> None:
    """Plot normalized price trend comparison chart
    
    Args:
        per_symbol_nav_df: DataFrame with each column as a stock's NAV time series  
        output_path: Output image path
        cfg: Visualization configuration
    """
    if per_symbol_nav_df.empty:
        print("[WARNING] Empty nav data, skipping price trends")
        return
        
    # Get configuration
    viz_cfg = (cfg or {}).get("visualization", {}) 
    alpha = viz_cfg.get("individual_alpha", 0.3) 
    linewidth = viz_cfg.get("average_linewidth", 3)
    figsize = viz_cfg.get("figure_size", [12, 8])
    dpi = viz_cfg.get("dpi", 300)
    
    fig, ax1 = plt.subplots(1, 1, figsize=figsize, dpi=dpi)
    
    # Color palette for better distinction (colorblind-friendly)
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
              '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
    
    # Normalized price trends (starting from 1.0)
    normalized_prices = {}
    for i, symbol in enumerate(per_symbol_nav_df.columns):
        try:
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) > 0:
                base_nav = nav_series.iloc[0] if nav_series.iloc[0] != 0 else 1.0
                normalized_nav = nav_series / base_nav
                normalized_prices[symbol] = normalized_nav
                color = colors[i % len(colors)]
                ax1.plot(normalized_nav.index, normalized_nav.values, 
                        alpha=0.7, linewidth=1.5, color=color,
                        label=symbol if len(per_symbol_nav_df.columns) <= 8 else "")
        except Exception as e:
            print(f"[WARNING] Failed to normalize {symbol}: {e}")
            continue
    
    if not normalized_prices:
        print("[WARNING] No valid price data for trends analysis")
        plt.close(fig)
        return
    
    # Add benchmark lines to the chart
    try:
        # Simple average benchmark
        simple_avg_nav = compute_simple_average_benchmark(per_symbol_nav_df)
        if len(simple_avg_nav) > 0:
            base_nav = simple_avg_nav.iloc[0] if simple_avg_nav.iloc[0] != 0 else 1.0
            simple_avg_normalized = simple_avg_nav / base_nav
            ax1.plot(simple_avg_normalized.index, simple_avg_normalized.values,
                    color='#FF4444', linewidth=linewidth, 
                    label='Equal Weight Average', linestyle='-')
    except Exception as e:
        print(f"[WARNING] Failed to plot benchmark in trends: {e}")
    
    # Chart styling
    ax1.set_ylabel('Normalized Price (Start=1.0)', fontsize=12)
    ax1.set_xlabel('Date', fontsize=12)
    ax1.set_title('Stock Price Trend Comparison (Normalized)', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    # Simplified legend for readability
    if len(per_symbol_nav_df.columns) <= 8:
        ax1.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
    else:
        # Only show benchmark legend when too many stocks
        handles, labels = ax1.get_legend_handles_labels()
        benchmark_handles = [h for h, l in zip(handles, labels) if 'Average' in l]
        benchmark_labels = [l for l in labels if 'Average' in l]
        if benchmark_handles:
            ax1.legend(benchmark_handles, benchmark_labels, loc='upper left')
    
    # Add statistical information and key metrics
    try:
        key_metrics_cfg = (cfg or {}).get("key_metrics", {})
        if key_metrics_cfg.get("annotation_box", True):
            # Calculate statistical information
            num_stocks = len(per_symbol_nav_df.columns)
            period_start = str(per_symbol_nav_df.index[0].date())
            period_end = str(per_symbol_nav_df.index[-1].date())
            trading_days = len(per_symbol_nav_df)
            
            # Build statistical information
            annotation_lines = [
                "Statistics:",
                f"Stocks: {num_stocks}",
                f"Trading Days: {trading_days}",
                f"Period: {period_start} to {period_end}",
                ""  # Empty line separator
            ]
            
            # Add key metrics (using equal weight average benchmark)
            try:
                # Get the list of metrics to highlight
                highlight_metrics = list(key_metrics_cfg.get("highlight", ["cum_return", "max_drawdown", "sortino"]))
                
                # Calculate key metrics for equal weight average benchmark
                simple_avg_nav = compute_simple_average_benchmark(per_symbol_nav_df)
                metrics_summary = extract_key_metrics_summary(simple_avg_nav, highlight_metrics)
                
                # Metric name mapping (for more friendly display names)
                metric_display_names = {
                    "cum_return": "Avg Cum Return",
                    "max_drawdown": "Avg Max Drawdown", 
                    "sortino": "Avg Sortino Ratio",
                    "sharpe": "Avg Sharpe Ratio",
                    "volatility": "Avg Volatility"
                }
                
                # Metric formatting function
                def format_metric_value(metric_name: str, value: float) -> str:
                    if metric_name in ["cum_return", "max_drawdown", "volatility"]:
                        return f"{value:.2%}"
                    elif metric_name in ["sortino", "sharpe"]:
                        return f"{value:.3f}"
                    else:
                        return f"{value:.4f}"
                
                annotation_lines.append("Equal Weight Benchmark:")
                
                # Dynamically generate metrics based on configured highlight list
                for metric in highlight_metrics:
                    if metric in metrics_summary:
                        display_name = metric_display_names.get(metric, f"Avg {metric.title()}")
                        formatted_value = format_metric_value(metric, metrics_summary[metric])
                        annotation_lines.append(f"{display_name}: {formatted_value}")
                        
            except Exception as e:
                print(f"[WARNING] Failed to compute key metrics for price trends: {e}")
            
            stats_text = "\n".join(annotation_lines)
            
            ax1.text(0.02, 0.02, stats_text, transform=ax1.transAxes,
                    verticalalignment='bottom', bbox=dict(boxstyle='round',
                    facecolor='white', alpha=0.8), fontsize=10)
    except Exception as e:
        print(f"[WARNING] Failed to add stats annotation: {e}")
    
    # Save image
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight') 
        plt.close(fig)
        print(f"✅ Saved stock price trends to {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save trends plot to {output_path}: {e}")
        plt.close(fig)


def generate_individual_stocks_summary(per_symbol_nav_df: pd.DataFrame,
                                      output_dir: str,
                                      cfg: Optional[Dict] = None) -> None:
    """Generate summary file for individual stocks directory
    
    Args:
        per_symbol_nav_df: DataFrame with each column as a stock's nav time series
        output_dir: individual_stocks directory path
        cfg: Configuration information
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        # Create summary file
        summary_lines = []
        summary_lines.append("# Individual Stocks Analysis Summary")
        summary_lines.append("")
        summary_lines.append(f"Number of stocks analyzed: {len(per_symbol_nav_df.columns)}")
        summary_lines.append(f"Analysis period: {per_symbol_nav_df.index[0].date()} to {per_symbol_nav_df.index[-1].date()}")
        summary_lines.append(f"Trading days: {len(per_symbol_nav_df)}")
        summary_lines.append("")
        summary_lines.append("## Stock Performance Overview")
        summary_lines.append("")
        
        # Calculate key metrics for each stock
        for symbol in per_symbol_nav_df.columns:
            try:
                nav_series = per_symbol_nav_df[symbol].dropna()
                if len(nav_series) > 0:
                    metrics = extract_key_metrics_summary(nav_series)
                    summary_lines.append(
                        f"**{symbol}**: Cumulative Return {metrics.get('cum_return', 0):.2%}, "
                        f"Max Drawdown {metrics.get('max_drawdown', 0):.2%}, "
                        f"Sortino Ratio {metrics.get('sortino', 0):.3f}"
                    )
            except Exception as e:
                summary_lines.append(f"**{symbol}**: Calculation failed - {e}")
        
        summary_lines.append("")
        summary_lines.append("## Notes")
        summary_lines.append("- This directory contains detailed charts for each stock (if image format saving is enabled)")
        summary_lines.append("- Detailed metrics files for each stock (if text format saving is enabled)")
        summary_lines.append("- All data is based on buy&hold strategy, considering configured transaction costs")
        
        # Save summary file
        summary_path = os.path.join(output_dir, "README.md")
        with open(summary_path, "w", encoding="utf-8") as f:
            f.write("\n".join(summary_lines))
        
        print(f"✅ Generated individual stocks summary at {summary_path}")
        
    except Exception as e:
        print(f"[WARNING] Failed to generate individual stocks summary: {e}")


def plot_nav_comparison(strategy_nav: pd.Series, 
                       benchmark_nav: pd.Series,
                       strategy_label: str,
                       benchmark_label: str,
                       output_path: str,
                       cfg: Optional[Dict] = None) -> None:
    """Plot strategy vs benchmark NAV comparison (NAV perspective)"""
    
    viz_cfg = (cfg or {}).get("visualization", {})
    figsize = viz_cfg.get("figure_size", [12, 8])
    dpi = viz_cfg.get("dpi", 300)
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=figsize, dpi=dpi, sharex=True)
    
    # Align data
    df = pd.concat([
        strategy_nav.rename("strategy"),
        benchmark_nav.rename("benchmark")
    ], axis=1).dropna()
    
    if df.empty:
        print(f"[WARNING] No aligned data for comparison, skipping {output_path}")
        plt.close(fig)
        return
    
    # Upper chart: NAV comparison
    ax1.plot(df.index, df["strategy"], linewidth=2, label=strategy_label, color='blue')
    ax1.plot(df.index, df["benchmark"], linewidth=2, label=benchmark_label, color='orange')
    ax1.set_ylabel('NAV', fontsize=12)
    ax1.set_title(f'{strategy_label} vs {benchmark_label} - NAV Comparison', fontsize=14, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Lower chart: Drawdown comparison
    strategy_dd = _compute_drawdown_series(df["strategy"])
    benchmark_dd = _compute_drawdown_series(df["benchmark"])
    
    ax2.fill_between(df.index, strategy_dd, 0, alpha=0.3, color='blue', label=f'{strategy_label} Drawdown')
    ax2.fill_between(df.index, benchmark_dd, 0, alpha=0.3, color='orange', label=f'{benchmark_label} Drawdown')
    ax2.set_ylabel('Drawdown', fontsize=12)
    ax2.set_xlabel('Date', fontsize=12)
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    # Key metrics annotations
    try:
        strategy_metrics = extract_key_metrics_summary(df["strategy"])
        benchmark_metrics = extract_key_metrics_summary(df["benchmark"])
        
        annotation_text = (
            f'{strategy_label}: Cum Return {strategy_metrics["cum_return"]:.2%}, Max DD {strategy_metrics["max_drawdown"]:.2%}\n'
            f'{benchmark_label}: Cum Return {benchmark_metrics["cum_return"]:.2%}, Max DD {benchmark_metrics["max_drawdown"]:.2%}'
        )
        
        ax1.text(0.02, 0.95, annotation_text, transform=ax1.transAxes,
                verticalalignment='top', bbox=dict(boxstyle='round', 
                facecolor='white', alpha=0.8), fontsize=10)
    except Exception as e:
        print(f"[WARNING] Failed to add comparison metrics: {e}")
    
    # Save image
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        print(f"✅ Saved nav comparison to {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save comparison plot to {output_path}: {e}")
        plt.close(fig)


def plot_totalassets_comparison(strategy_nav: pd.Series, 
                               benchmark_nav: pd.Series,
                               strategy_label: str,
                               benchmark_label: str,
                               initial_cash: float,
                               output_path: str,
                               cfg: Optional[Dict] = None) -> None:
    """Plot strategy vs benchmark total assets comparison (asset perspective)"""
    
    viz_cfg = (cfg or {}).get("visualization", {})
    figsize = viz_cfg.get("figure_size", [12, 8])
    dpi = viz_cfg.get("dpi", 300)
    
    # Convert NAV to total assets
    strategy_assets = strategy_nav * initial_cash
    benchmark_assets = benchmark_nav * initial_cash
    
    fig, ax = plt.subplots(figsize=figsize, dpi=dpi)
    
    # Align data
    df = pd.concat([
        strategy_assets.rename("strategy"),
        benchmark_assets.rename("benchmark")
    ], axis=1).dropna()
    
    if df.empty:
        print(f"[WARNING] No aligned data for totalassets comparison, skipping {output_path}")
        plt.close(fig)
        return
    
    ax.plot(df.index, df["strategy"], linewidth=2, label=f'{strategy_label} Assets', color='blue')
    ax.plot(df.index, df["benchmark"], linewidth=2, label=f'{benchmark_label} Assets', color='orange')
    
    ax.set_ylabel('Total Assets (USD)', fontsize=12)
    ax.set_xlabel('Date', fontsize=12)
    ax.set_title(f'{strategy_label} vs {benchmark_label} - Total Assets Comparison', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(True, alpha=0.3)
    
    # Format Y-axis as currency
    import matplotlib.ticker as ticker
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f'${x/1000000:.1f}M'))
    
    # Key data annotations
    final_strategy = df["strategy"].iloc[-1]
    final_benchmark = df["benchmark"].iloc[-1]
    
    ax.text(0.02, 0.95, f'{strategy_label}: ${final_strategy:,.0f}',
            transform=ax.transAxes, fontsize=12, bbox=dict(boxstyle="round", facecolor='lightblue', alpha=0.8))
    ax.text(0.02, 0.88, f'{benchmark_label}: ${final_benchmark:,.0f}',
            transform=ax.transAxes, fontsize=12, bbox=dict(boxstyle="round", facecolor='wheat', alpha=0.8))
    ax.text(0.02, 0.81, f'Difference: ${final_strategy - final_benchmark:,.0f}',
            transform=ax.transAxes, fontsize=12, bbox=dict(boxstyle="round", facecolor='lightgreen', alpha=0.8))
    
    # Save image
    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        print(f"✅ Saved total assets comparison to {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to save assets comparison plot to {output_path}: {e}")
        plt.close(fig)


# ===== Phase 3: Multi-period Performance Comparison Analysis =====

def plot_multi_period_performance_heatmap(per_symbol_nav_df, output_path, dpi=300, cfg=None):
    """
    Create multi-period performance heatmap
    
    Args:
        per_symbol_nav_df: DataFrame, each column is a stock's net value series
        output_path: str, output file path
        dpi: int, image resolution
        cfg: Dict, configuration dictionary, may contain windows.heatmap_periods
    """
    try:
        import numpy as np
        if per_symbol_nav_df.empty:
            print("[WARNING] Empty nav dataframe for multi-period analysis")
            return
            
        # Read time windows from configuration, use default values if not configured
        default_windows = [5, 10, 21, 42, 63, 126, 252]  # week, 2-week, month, 2-month, quarter, half-year, year
        
        try:
            windows_cfg = (cfg or {}).get("windows", {})
            windows = list(windows_cfg.get("heatmap_periods", default_windows))
            # Ensure windows are integers and greater than 0
            windows = [int(w) for w in windows if int(w) > 0]
            if not windows:  # If configuration is empty or invalid, use default values
                windows = default_windows
        except Exception as e:
            print(f"[WARNING] Failed to parse heatmap_periods config, using defaults: {e}")
            windows = default_windows
        
        # Generate window labels
        window_labels = [f'{w}D' for w in windows]
        
        symbols = per_symbol_nav_df.columns.tolist()
        
        # Calculate rolling returns for each time window
        performance_data = []
        
        for symbol in symbols:
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) < 5:  # Need at least 5 data points
                continue
                
            symbol_performance = []
            for window in windows:
                if len(nav_series) >= window:
                    # Calculate rolling returns (total return of the most recent window days)
                    rolling_returns = nav_series.pct_change().rolling(window).sum()
                    latest_return = rolling_returns.iloc[-1] if not rolling_returns.empty else 0
                    symbol_performance.append(latest_return * 100)  # Convert to percentage
                else:
                    symbol_performance.append(np.nan)
            
            performance_data.append(symbol_performance)
        
        if not performance_data:
            print("[WARNING] No valid data for multi-period heatmap")
            return
            
        # Create heatmap data matrix
        performance_matrix = np.array(performance_data)
        
        # Create figure
        fig, ax = plt.subplots(figsize=(10, max(6, len(symbols) * 0.4)))
        
        # Create heatmap
        im = ax.imshow(performance_matrix, cmap='RdYlGn', aspect='auto')
        
        # Set coordinate axes
        ax.set_xticks(range(len(window_labels)))
        ax.set_xticklabels(window_labels)
        ax.set_yticks(range(len(symbols)))
        ax.set_yticklabels(symbols)
        
        # Set titles and labels
        ax.set_title('Multi-Period Return Heatmap (%)', fontsize=14, fontweight='bold', pad=20)
        ax.set_xlabel('Time Window', fontsize=12)
        ax.set_ylabel('Stock Symbol', fontsize=12)
        
        # Add numerical annotations
        for i in range(len(symbols)):
            for j in range(len(window_labels)):
                if not np.isnan(performance_matrix[i, j]):
                    text = f'{performance_matrix[i, j]:.1f}%'
                    color = 'white' if abs(performance_matrix[i, j]) > np.nanmax(np.abs(performance_matrix)) * 0.6 else 'black'
                    ax.text(j, i, text, ha='center', va='center', color=color, fontsize=8)
        
        # Add colorbar
        cbar = plt.colorbar(im, ax=ax, shrink=0.8)
        cbar.set_label('Return (%)', rotation=270, labelpad=20)
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        
        print(f"✅ Saved multi-period performance heatmap to {output_path}")
        
    except Exception as e:
        print(f"[ERROR] Failed to create multi-period heatmap: {e}")
        if 'fig' in locals():
            plt.close(fig)


def plot_rolling_metrics_comparison(per_symbol_nav_df, output_path, metric='sortino', window=63, dpi=300):
    """
    Create rolling metrics comparison chart
    
    Args:
        per_symbol_nav_df: DataFrame, each column is a stock's net value series
        output_path: str, output file path
        metric: str, metric type ('sortino', 'sharpe', 'drawdown')
        window: int, rolling window size
        dpi: int, image resolution
    """
    try:
        if per_symbol_nav_df.empty:
            print(f"[WARNING] Empty nav dataframe for rolling {metric} analysis")
            return
            
        from .metrics import compute_nav_to_metrics_series
        
        symbols = per_symbol_nav_df.columns.tolist()
        
        # Create figure
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Colorblind-friendly palette
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
                  '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5']
        
        metric_data = {}
        for i, symbol in enumerate(symbols):
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) < window:
                continue
                
            # Calculate rolling metrics
            if metric == 'sortino':
                metrics_df = compute_nav_to_metrics_series(nav_series, sortino_mode="rolling", window=window)
                metric_series = metrics_df['sortino'].dropna()
                metric_label = f'Rolling Sortino Ratio ({window}-day)'
                y_label = 'Sortino Ratio'
            elif metric == 'sharpe':
                returns = nav_series.pct_change().dropna()
                rolling_mean = returns.rolling(window).mean()
                rolling_std = returns.rolling(window).std()
                metric_series = (rolling_mean / rolling_std).dropna()
                metric_label = f'Rolling Sharpe Ratio ({window}-day)'
                y_label = 'Sharpe Ratio'
            elif metric == 'drawdown':
                metrics_df = compute_nav_to_metrics_series(nav_series, window=window)
                metric_series = metrics_df['max_drawdown_to_date'].dropna()
                metric_label = f'Cumulative Max Drawdown ({window}-day)'
                y_label = 'Max Drawdown'
            else:
                print(f"[WARNING] Unknown metric: {metric}")
                continue
                
            if not metric_series.empty:
                color = colors[i % len(colors)]
                ax.plot(metric_series.index, metric_series.values, 
                       label=symbol, alpha=0.8, linewidth=1.8, color=color)
                metric_data[symbol] = metric_series
        
        if not metric_data:
            print(f"[WARNING] No valid data for rolling {metric} comparison")
            plt.close(fig)
            return
            
        # Calculate and plot average line
        if len(metric_data) > 1:
            # Align all series to common date index
            aligned_df = pd.DataFrame(metric_data)
            avg_series = aligned_df.mean(axis=1, skipna=True)
            
            ax.plot(avg_series.index, avg_series.values, 
                   label='Average', linewidth=3, color='#FF4444', alpha=0.9)
        
        # Set chart properties
        ax.set_title(metric_label, fontsize=14, fontweight='bold', pad=20)
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel(y_label, fontsize=12)
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax.grid(True, alpha=0.3)
        
        # Format x-axis dates
        fig.autofmt_xdate()
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        
        print(f"✅ Saved rolling {metric} comparison to {output_path}")
        
    except Exception as e:
        print(f"[ERROR] Failed to create rolling {metric} comparison: {e}")
        if 'fig' in locals():
            plt.close(fig)


def plot_performance_ranking_over_time(per_symbol_nav_df, output_path, window=21, dpi=300):
    """
    Create performance ranking change chart
    
    Args:
        per_symbol_nav_df: DataFrame, each column is a stock's net value series
        output_path: str, output file path  
        window: int, rolling window size
        dpi: int, image resolution
    """
    try:
        if per_symbol_nav_df.empty:
            print("[WARNING] Empty nav dataframe for ranking analysis")
            return
            
        symbols = per_symbol_nav_df.columns.tolist()
        
        # Calculate rolling returns
        rolling_returns = {}
        for symbol in symbols:
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) >= window:
                returns = nav_series.pct_change().rolling(window).sum()
                rolling_returns[symbol] = returns
        
        if not rolling_returns:
            print("[WARNING] No valid data for ranking analysis")
            return
            
        # Align all data to common dates
        returns_df = pd.DataFrame(rolling_returns).dropna()
        
        if returns_df.empty:
            print("[WARNING] No aligned data for ranking analysis")
            return
            
        # Calculate daily rankings (1=best)
        rankings = returns_df.rank(axis=1, method='min', ascending=False)
        
        # Create figure
        fig, ax = plt.subplots(figsize=(14, 8))
        
        # Colorblind-friendly palette for ranking chart
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd', 
                  '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
                  '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5']
        
        for i, symbol in enumerate(symbols):
            if symbol in rankings.columns:
                rank_series = rankings[symbol].dropna()
                color = colors[i % len(colors)]
                ax.plot(rank_series.index, rank_series.values, 
                       label=symbol, alpha=0.8, linewidth=2, 
                       marker='o', markersize=3, color=color)
        
        # Set chart properties
        ax.set_title(f'Stock Return Ranking Over Time ({window}-day rolling)', fontsize=14, fontweight='bold', pad=20)
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Ranking', fontsize=12)
        ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
        ax.grid(True, alpha=0.3)
        
        # Invert y-axis so rank 1 is at top
        ax.invert_yaxis()
        ax.set_yticks(range(1, len(symbols) + 1))
        
        # Format x-axis dates
        fig.autofmt_xdate()
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=dpi, bbox_inches='tight')
        plt.close(fig)
        
        print(f"✅ Saved performance ranking chart to {output_path}")
        
    except Exception as e:
        print(f"[ERROR] Failed to create ranking chart: {e}")
        if 'fig' in locals():
            plt.close(fig)

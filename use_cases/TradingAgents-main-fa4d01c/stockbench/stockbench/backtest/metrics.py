from __future__ import annotations

from typing import Dict, Optional, List

import numpy as np
import pandas as pd


def _max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    roll_max = nav.cummax()
    dd = (nav / roll_max) - 1.0
    return float(dd.min())


def _annualization_factor(index: pd.DatetimeIndex) -> float:
    if index is None or len(index) < 2:
        return 252.0
    # Estimate frequency (mainly daily), if calendar frequency then use 252 directly
    return 252.0


def _rolling_ratio(excess: pd.Series, window: int) -> float:
    if excess is None or len(excess) < window:
        return float("nan")
    r = excess.rolling(window=window)
    mean = r.mean()
    std = r.std()
    out = (mean / std).iloc[-1] if std.iloc[-1] and std.iloc[-1] > 0 else np.nan
    return float(out) if pd.notna(out) else float("nan")


def evaluate(nav_series: pd.Series, trades: pd.DataFrame, benchmark_nav: Optional[pd.Series] = None) -> Dict:
    if nav_series is None or len(nav_series) == 0:
        base = {"cum_return": 0.0, "max_drawdown": 0.0, "volatility_daily": 0.0, "sortino": 0.0}
        # Trading statistics
        trades_count = int(0 if trades is None else len(trades))
        trades_notional = float(0.0)
        return {**base, "trades_count": trades_count, "trades_notional": trades_notional}
    nav_series = nav_series.astype(float)
    ret = nav_series.pct_change().fillna(0.0)
    
    # Basic return metrics (suitable for short-term)
    # Cumulative Return - calculated relative to initial investment (1.0) instead of first day NAV
    cum_return = float(nav_series.iloc[-1] - 1.0)
    mdd = _max_drawdown(nav_series)
    
    # Risk metrics (daily and annualized versions)
    vol_daily = float(ret.std())  # Daily volatility, more suitable for short-term
    vol_annual = float(ret.std() * (252 ** 0.5))  # Annualized volatility
    
    # Sortino ratio (only considers downside risk, more robust for short-term)
    downside_ret = ret[ret < 0]
    downside_vol = float(downside_ret.std()) if len(downside_ret) > 0 else 0.0
    sortino = float(ret.mean() / downside_vol) if downside_vol > 0 else 0.0
    
    # Annualized metrics (suitable for long-term, less reference value for short-term)
    sharpe_annual = float((ret.mean() * 252) / vol_annual) if vol_annual > 0 else 0.0
    sortino_annual = float((ret.mean() * 252) / (downside_vol * (252 ** 0.5))) if downside_vol > 0 else 0.0
    
    # Trading statistics
    try:
        trades_count = int(0 if trades is None else len(trades))
        if trades is None or trades_count == 0:
            trades_notional = 0.0
        else:
            # Total transaction amount
            trades_notional = float((trades["exec_price"].astype(float) * trades["qty"].astype(float)).sum())
    except Exception:
        trades_count = int(0 if trades is None else len(trades))
        trades_notional = 0.0

    # Short-term applicable metrics first, annualized metrics last
    out: Dict[str, float] = {
        # Basic metrics (suitable for short-term)
        "cum_return": cum_return, 
        "max_drawdown": mdd, 
        "volatility_daily": vol_daily,
        "sortino": sortino,
        "trades_count": trades_count, 
        "trades_notional": trades_notional,
        # Annualized metrics (long-term reference)
        "volatility": vol_annual,
        "sharpe": sharpe_annual,
        "sortino_annual": sortino_annual,
    }

    # Relative metrics (optional)
    if isinstance(benchmark_nav, pd.Series) and len(benchmark_nav) > 0:
        # Align to common dates
        df = pd.concat([
            nav_series.rename("nav"),
            benchmark_nav.rename("bench")
        ], axis=1).dropna()
        if len(df) >= 2:
            r_s = df["nav"].pct_change().fillna(0.0)
            r_b = df["bench"].pct_change().fillna(0.0)
            r_e = (r_s - r_b)
            af = _annualization_factor(df.index)
            te = float(r_e.std() * (af ** 0.5))
            ex_ret_ann = float(r_e.mean() * af)
            ir = float(ex_ret_ann / te) if te > 0 else 0.0
            # beta & corr (safe, avoid numpy warnings when variance==0)
            try:
                x = np.asarray(r_s, dtype=float)
                y = np.asarray(r_b, dtype=float)
                m = np.isfinite(x) & np.isfinite(y)
                x = x[m]
                y = y[m]
                if x.size >= 2 and y.size >= 2:
                    x_center = x - x.mean()
                    y_center = y - y.mean()
                    var_s = float(np.mean(x_center * x_center))
                    var_b = float(np.mean(y_center * y_center))
                    cov = float(np.mean(x_center * y_center)) if (var_s > 0 or var_b > 0) else 0.0
                    beta = float(cov / var_b) if var_b > 0 else 0.0
                    corr = float(cov / ((var_s * var_b) ** 0.5)) if (var_s > 0 and var_b > 0) else 0.0
                else:
                    beta = 0.0
                    corr = 0.0
            except Exception:
                beta = 0.0
                corr = 0.0
            # capture
            try:
                up_mask = r_b > 0
                down_mask = r_b < 0
                up_capture = float(r_s[up_mask].mean() / r_b[up_mask].mean()) if up_mask.any() and r_b[up_mask].mean() != 0 else 0.0
                down_capture = float(r_s[down_mask].mean() / r_b[down_mask].mean()) if down_mask.any() and r_b[down_mask].mean() != 0 else 0.0
            except Exception:
                up_capture = 0.0
                down_capture = 0.0
			# sortino_excess (using downside volatility of r_e as denominator)
            try:
                downside = r_e[r_e < 0]
                downside_dev = float(downside.std() * (af ** 0.5))
                sortino_excess = float(ex_ret_ann / downside_dev) if downside_dev > 0 else 0.0
            except Exception:
                sortino_excess = 0.0
			# Hit ratio (active)
            try:
                hit_ratio_active = float((r_e > 0).mean())
            except Exception:
                hit_ratio_active = 0.0
			# Rolling IR/TE
            roll_ir_63 = _rolling_ratio(r_e, 63)
            roll_ir_126 = _rolling_ratio(r_e, 126)
            roll_ir_252 = _rolling_ratio(r_e, 252)
            roll_te_63 = float(r_e.rolling(63).std().iloc[-1] * (af ** 0.5)) if len(r_e) >= 63 else float("nan")
            roll_te_126 = float(r_e.rolling(126).std().iloc[-1] * (af ** 0.5)) if len(r_e) >= 126 else float("nan")
            roll_te_252 = float(r_e.rolling(252).std().iloc[-1] * (af ** 0.5)) if len(r_e) >= 252 else float("nan")

            # Calculate non-annualized relative metrics (more suitable for short-term)
            excess_return_total = float(r_e.sum())  # Total excess return
            te_daily = float(r_e.std())  # Daily tracking error
            ir_daily = float(r_e.mean() / te_daily) if te_daily > 0 else 0.0  # Daily information ratio
            
            out.update({
                # Short-term applicable relative metrics
                "excess_return_total": excess_return_total,
                "tracking_error_daily": te_daily,
                "information_ratio_daily": ir_daily,
                "beta": beta,
                "corr": corr,
                "up_capture": up_capture,
                "down_capture": down_capture,
                "hit_ratio_active": hit_ratio_active,
                "sortino_excess": sortino_excess,
                # Rolling metrics (short-term window priority)
                "rolling_ir_63": roll_ir_63,
                "rolling_te_63": roll_te_63,
                "rolling_ir_126": roll_ir_126,
                "rolling_te_126": roll_te_126,
                # Annualized metrics (long-term reference)
                "excess_return_annual": ex_ret_ann,
                "tracking_error": te,
                "information_ratio": ir,
                "alpha_simple": ex_ret_ann,
                "rolling_ir_252": roll_ir_252,
                "rolling_te_252": roll_te_252,
                # Metadata
                "n": int(len(df)),
                "freq": "day",
            })

    return out 


# ===== Per-symbol daily metrics series =====

def compute_nav_to_metrics_series(nav: pd.Series, sortino_mode: str = "rolling", window: int = 63, metrics: Optional[List[str]] = None) -> pd.DataFrame:
    """
    Convert a single symbol's benchmark net asset value series to daily metrics series:
    - nav: Original net asset value (not forced to start at 1, allows NAV_0<=1 with fees)
    - cum_return: nav/first_day_nav - 1
    - max_drawdown_to_date: Daily high watermark drawdown
    - sortino: 
        rolling mode: Windowed calculation of negative standard deviation of daily returns, returns sortino for current window
        to_date mode: sortino from start date to current date
    Returns DataFrame, index aligned with nav, columns are [nav, cum_return, max_drawdown_to_date, sortino]
    """
    if nav is None or len(nav) == 0:
        return pd.DataFrame(columns=["nav", "cum_return", "max_drawdown_to_date", "sortino"])  # empty
    s = nav.astype(float).copy()
    out = pd.DataFrame(index=s.index)
    out["nav"] = s
    try:
        base = float(s.iloc[0]) if len(s) > 0 else 1.0
        base = base if base != 0 else 1.0
        out["cum_return"] = s / base - 1.0
    except Exception:
        out["cum_return"] = 0.0
    # Drawdown to current date
    try:
        roll_max = s.cummax()
        out["max_drawdown_to_date"] = s / roll_max - 1.0
    except Exception:
        out["max_drawdown_to_date"] = 0.0
    # Sortino calculation
    try:
        r = s.pct_change().fillna(0.0)
        if (sortino_mode or "rolling").lower() == "to_date":
            # Cumulative to current date: mean and downside volatility from start date to current date
            mean_to_date = r.expanding().mean()
            downside = r.where(r < 0, 0.0)
            downside_std_to_date = downside.expanding().std().fillna(0.0)
            out["sortino"] = mean_to_date / downside_std_to_date.replace(0.0, pd.NA)
        else:
            w = int(window or 63)
            if w <= 1:
                w = 2
            mean_w = r.rolling(w).mean()
            downside_w = r.where(r < 0, 0.0).rolling(w).std()
            out["sortino"] = mean_w / downside_w.replace(0.0, pd.NA)
        # Clean infinity and NaN
        out["sortino"] = out["sortino"].replace([np.inf, -np.inf], np.nan)
    except Exception:
        out["sortino"] = np.nan
    out = out.astype(float)
    # If metrics whitelist is provided, only keep specified columns (supports simple mapping from config names to column names)
    try:
        if metrics is not None and isinstance(metrics, (list, tuple)) and len(metrics) > 0:
            name_map = {
                "cum_return": "cum_return",
                "max_drawdown": "max_drawdown_to_date",
                "sortino": "sortino",
                "nav": "nav",
            }
            cols = [name_map.get(str(m), str(m)) for m in metrics]
            keep = [c for c in cols if c in out.columns]
            if keep:
                out = out[keep]
    except Exception:
        pass
    return out


def compare_symbol_series(strategy_series: pd.Series, benchmark_series: pd.Series) -> pd.Series:
    """
    Difference series based on cum_return:
    - Input is strategy and benchmark NAV or cumulative return series;
    - If input is NAV, normalize to cum_return first;
    Returns difference series aligned to common dates (strategy - benchmark).
    """
    if strategy_series is None or benchmark_series is None:
        return pd.Series(dtype=float)
    df = pd.concat([
        strategy_series.rename("s"),
        benchmark_series.rename("b")
    ], axis=1).dropna()
    if df.empty:
        return pd.Series(dtype=float)
    s = df["s"].astype(float)
    b = df["b"].astype(float)
    try:
        # If more like NAV (first day close to 1), convert to cum_return
        def to_cum(x: pd.Series) -> pd.Series:
            if len(x) == 0:
                return x
            first = float(x.iloc[0])
            if first == 0:
                return (x - first)
            # Normalize by first day value
            return x / first - 1.0
        s_cum = to_cum(s)
        b_cum = to_cum(b)
        return (s_cum - b_cum).astype(float)
    except Exception:
        return (s - b).astype(float)


# ===== Phase 1: Aggregated analysis metrics functions =====

def compute_per_symbol_metrics_from_nav(per_symbol_nav_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Real-time metrics calculation based on per_symbol_benchmark_nav.parquet
    
    Args:
        per_symbol_nav_df: Each column is a nav time series for one stock symbol
        
    Returns:
        Dict[symbol, metrics_df]: Daily metrics DataFrame for each stock
    """
    per_symbol_metrics = {}
    
    for symbol in per_symbol_nav_df.columns:
        try:
            nav_series = per_symbol_nav_df[symbol].dropna()
            if len(nav_series) > 0:
                # Reuse existing compute_nav_to_metrics_series function
                metrics_df = compute_nav_to_metrics_series(
                    nav_series, 
                    sortino_mode="rolling", 
                    window=63
                )
                per_symbol_metrics[symbol] = metrics_df
        except Exception as e:
            print(f"[WARNING] Failed to compute metrics for {symbol}: {e}")
            continue
            
    return per_symbol_metrics


def compute_simple_average_benchmark(per_symbol_nav_df: pd.DataFrame) -> pd.Series:
    """Calculate simple average benchmark (equal-weighted average of all stock nav)
    
    Args:
        per_symbol_nav_df: Each column is a nav time series for one stock symbol
        
    Returns:
        Simple average nav time series
    """
    if per_symbol_nav_df.empty:
        return pd.Series(dtype=float)
    
    # Equal-weighted average: ignore NaN values in calculation
    avg_nav = per_symbol_nav_df.mean(axis=1, skipna=True)
    return avg_nav.dropna()


def compute_weighted_average_benchmark(per_symbol_nav_df: pd.DataFrame, 
                                     initial_allocation_weights: Optional[pd.Series] = None) -> pd.Series:
    """Calculate weighted average benchmark (based on initial cash allocation weights)
    
    Args:
        per_symbol_nav_df: Each column is a nav time series for one stock symbol
        initial_allocation_weights: Initial weight Series, index is symbol, value is weight ratio
        
    Returns:
        Weighted average nav time series
    """
    if per_symbol_nav_df.empty:
        return pd.Series(dtype=float)
        
    if initial_allocation_weights is None:
        # Default equal weight (equal cash allocation)
        n_stocks = len(per_symbol_nav_df.columns)
        weights = pd.Series([1.0 / n_stocks] * n_stocks, index=per_symbol_nav_df.columns)
    else:
        # Use passed weights, ensure normalization
        weights = initial_allocation_weights / initial_allocation_weights.sum()
        
    # Calculate weighted average nav (ignore NaN values)
    weighted_nav = (per_symbol_nav_df * weights).sum(axis=1, skipna=True)
    return weighted_nav.dropna()


def extract_key_metrics_summary(nav_series: pd.Series, highlight_metrics: List[str] = None) -> Dict[str, float]:
    """Extract key metrics summary, supports dynamic metric selection
    
    Args:
        nav_series: nav time series
        highlight_metrics: List of metrics to extract, default is ["cum_return", "max_drawdown", "sortino"]
        
    Returns:
        Dictionary containing specified metrics
    """
	# Default metrics list
    if highlight_metrics is None:
        highlight_metrics = ["cum_return", "max_drawdown", "sortino"]
    
    if nav_series.empty:
        return {metric: 0.0 for metric in highlight_metrics}
    
    result = {}
    
    try:
		# Calculate all possibly needed metrics
        base_nav = nav_series.iloc[0] if nav_series.iloc[0] != 0 else 1.0
        
		# Cumulative return
        if "cum_return" in highlight_metrics:
            cum_return = float(nav_series.iloc[-1] / base_nav - 1.0)
            result["cum_return"] = cum_return
        
		# Maximum drawdown
        if "max_drawdown" in highlight_metrics:
            max_drawdown = float(_max_drawdown(nav_series))
            result["max_drawdown"] = max_drawdown
        
		# Sortino ratio
        if "sortino" in highlight_metrics:
            returns = nav_series.pct_change().fillna(0.0)
            downside_ret = returns[returns < 0]
            downside_vol = float(downside_ret.std()) if len(downside_ret) > 0 else 0.0
            sortino = float(returns.mean() / downside_vol) if downside_vol > 0 else 0.0
            result["sortino"] = sortino
        
		# Sharpe ratio (if needed)
        if "sharpe" in highlight_metrics:
            returns = nav_series.pct_change().fillna(0.0)
            sharpe = float(returns.mean() / returns.std()) if returns.std() > 0 else 0.0
            result["sharpe"] = sharpe
        
        # Annualized volatility (if needed)
        if "volatility" in highlight_metrics:
            returns = nav_series.pct_change().fillna(0.0)
            volatility = float(returns.std() * np.sqrt(252))  # Annualized
            result["volatility"] = volatility
        
        return result
        
    except Exception as e:
        print(f"[WARNING] Failed to extract key metrics: {e}")
        return {metric: 0.0 for metric in highlight_metrics}


def _compute_drawdown_series(nav_series: pd.Series) -> pd.Series:
    """Calculate drawdown time series (helper function)
    
    Args:
        nav_series: nav time series
        
    Returns:
        Drawdown time series
    """
    if nav_series.empty:
        return pd.Series(dtype=float)
    
    try:
        roll_max = nav_series.cummax()
        drawdown = (nav_series / roll_max) - 1.0
        return drawdown
    except Exception as e:
        print(f"[WARNING] Failed to compute drawdown series: {e}")
        return pd.Series([0.0] * len(nav_series), index=nav_series.index)
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, Any
import sys

import pandas as pd

from stockbench.utils.io import ensure_dir


def _default_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _unique_run_dir(root: str, run_id: str) -> tuple[str, str]:
    """Return unique output directory and final run_id.
    If `root/run_id` already exists, append timestamp suffix `_%Y%m%d_%H%M%S_%f` to avoid overwriting.
    """
    base = os.path.join(root, run_id)
    if not os.path.exists(base):
        return base, run_id
    # Directory already exists: append high-precision timestamp to ensure this run is independent
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    rid = f"{run_id}_{ts}"
    return os.path.join(root, rid), rid


# New: recursively clean NaN/Inf in objects to None, ensuring valid JSON
def _json_safe(obj: Any) -> Any:
    try:
        import math
        import numpy as _np  # type: ignore
    except Exception:
        math = None  # type: ignore
        _np = None  # type: ignore

    if obj is None:
        return None
    # Basic scalars
    if isinstance(obj, (str, int, bool)):
        return obj
    # Float handling (including numpy scalars)
    try:
        # Convert numpy float scalars to Python float
        if hasattr(obj, "item") and not isinstance(obj, (list, dict)):
            try:
                obj = obj.item()
            except Exception:
                pass
        if isinstance(obj, float):
            if ((math is not None and (math.isnan(obj) or math.isinf(obj)))):
                return None
            return obj
    except Exception:
        pass
    # Mappings/sequences
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    # Try to convert other types to serializable
    try:
        return float(obj) if isinstance(obj, (pd.Float64Dtype,)) else obj  # type: ignore
    except Exception:
        return str(obj)


# New: unify Series or single-column DataFrame to Series (prefer specified column names)
def _as_series(obj: Any, preferred_names: tuple[str, ...] = ("nav",)) -> pd.Series | None:
    if isinstance(obj, pd.Series):
        return obj
    if isinstance(obj, pd.DataFrame):
        cols = list(obj.columns)
        for name in preferred_names:
            if name in cols:
                return obj[name]
        if len(cols) >= 1:
            return obj.iloc[:, 0]
    return None


def _generate_summary_metrics_explanation() -> str:
    """Generate explanation of metrics mentioned in summary"""
    explanation = """
## Metrics Concept Explanation

### Core Metrics
- **cum_return**: Cumulative return, the rise/fall of final NAV relative to initial NAV
- **max_drawdown**: Maximum drawdown, the maximum decline of NAV relative to historical peak
- **volatility**: Annualized volatility, daily volatility annualized (×√252)
- **sharpe**: Sharpe ratio, annualized return divided by annualized volatility
- **sortino**: Sortino ratio, average return divided by downside volatility

### Trading Statistics
- **trades_count**: Number of trades, total trading count during backtest period
- **trades_notional**: Total trading amount, sum of all trade notional amounts

### Relative Benchmark Metrics (shown when benchmark configured)
- **information_ratio**: Annualized information ratio, annualized excess return divided by annualized tracking error
- **tracking_error**: Annualized tracking error, annualized standard deviation of excess returns
- **alpha_simple**: Simple alpha, equivalent to annualized excess return
"""
    return explanation.strip()


def _generate_complete_metrics_explanation() -> str:
    """Generate complete metrics explanation - supplement explanations for other metrics in metrics.json"""
    explanation = """
### Complete Metrics Description
Detailed explanations for other metrics included in metrics.json:

#### Extended Risk Metrics
- **volatility_daily**: Daily volatility, standard deviation of daily returns
- **sortino_annual**: Annualized Sortino ratio, annualized return divided by annualized downside volatility
- **sortino_excess**: Excess Sortino ratio, Sortino calculation based on excess returns

#### Extended Relative Benchmark Metrics
- **beta**: Beta coefficient, systematic risk exposure of strategy relative to benchmark
- **corr**: Correlation coefficient, degree of linear correlation between strategy and benchmark returns
- **up_capture**: Upside capture ratio, relative performance of strategy when benchmark rises
- **down_capture**: Downside capture ratio, relative performance of strategy when benchmark falls
- **excess_return_total**: Total excess return, cumulative excess return of strategy relative to benchmark
- **excess_return_annual**: Annualized excess return, annualized representation of total excess return
- **hit_ratio_active**: Active hit ratio, proportion of trading days when strategy outperforms benchmark

#### Rolling Window Metrics
- **rolling_ir_63/126/252**: 63/126/252-day rolling information ratio
- **tracking_error_daily**: Daily tracking error, daily standard deviation of excess returns
- **information_ratio_daily**: Daily information ratio, daily excess return divided by daily tracking error
- **rolling_te_63/126/252**: 63/126/252-day rolling tracking error

#### Other Statistical Metrics
- **n**: Sample size, number of effective trading days during backtest period
- **freq**: Data frequency identifier (only supports day)
"""
    return explanation.strip()


def write_outputs(result: Dict, run_id: str | None = None, cfg: Dict[str, Any] | None = None) -> str:
    # Improve run_id generation logic to make it clearer
    if run_id is None:
        # Extract strategy information from configuration
        strategy_name = "unknown"
        if cfg and isinstance(cfg, dict):
            strategy_name = cfg.get("strategy", {}).get("name", "unknown")
            if not strategy_name:
                # Try to infer strategy from other configurations
                if cfg.get("backtest", {}).get("enable_detailed_logging"):
                    strategy_name = "detailed"
                else:
                    strategy_name = "standard"
        
        # Generate timestamp with strategy name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_id = f"{strategy_name}_{timestamp}"
    
    root = os.path.join(os.getcwd(), "storage", "reports", "backtest")
    base, run_id = _unique_run_dir(root, run_id)
    ensure_dir(base)

    # trades
    trades: pd.DataFrame = result.get("trades") if isinstance(result.get("trades"), pd.DataFrame) else pd.DataFrame()
    trades_path = os.path.join(base, "trades.parquet")
    if not trades.empty:
        trades.to_parquet(trades_path, index=False)
    else:
        pd.DataFrame([]).to_parquet(trades_path)

    # daily nav
    nav = result.get("nav")
    nav_path = os.path.join(base, "daily_nav.parquet")
    if isinstance(nav, pd.Series):
        nav.to_frame(name="nav").to_parquet(nav_path)
    elif isinstance(nav, pd.DataFrame):
        nav.to_parquet(nav_path)
    else:
        pd.DataFrame([]).to_parquet(nav_path)


    # benchmark nav (M1)
    bench_nav = result.get("benchmark_nav")
    if isinstance(bench_nav, pd.Series) and len(bench_nav) > 0:
        bench_path = os.path.join(base, "benchmark_nav.parquet")
        bench_nav.to_frame(name="benchmark_nav").to_parquet(bench_path)


    # metrics (clean NaN/Inf before writing to disk)
    metrics = result.get("metrics") or {}
    metrics_safe = _json_safe(metrics)
    with open(os.path.join(base, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(metrics_safe, f, ensure_ascii=False, indent=2)

    # New: metrics_summary.csv (single row)
    try:
        nav_series = nav
        if isinstance(nav_series, pd.DataFrame):
            nav_series = nav_series["nav"] if "nav" in nav_series.columns else None
        cagr = 0.0
        n = int(len(nav_series)) if isinstance(nav_series, pd.Series) else 0
        if isinstance(nav_series, pd.Series) and n >= 2:
            nav_start = float(nav_series.iloc[0])
            nav_end = float(nav_series.iloc[-1])
            if nav_start > 0:
                cagr = (nav_end / nav_start) ** (252.0 / max(1, n)) - 1.0
        summary_row = {
            "run_id": run_id,
            "cagr": float(cagr),
            # Short-term applicable metrics prioritized
            "cum_return": float(metrics.get("cum_return", 0.0)),
            "max_drawdown": float(metrics.get("max_drawdown", 0.0)),
            "volatility_daily": float(metrics.get("volatility_daily", 0.0)),
            "sortino": float(metrics.get("sortino", 0.0)),
            "trades_count": int(metrics.get("trades_count", 0)),
            "trades_notional": float(metrics.get("trades_notional", 0.0)),
            # Relative metrics
            "information_ratio_daily": float(metrics.get("information_ratio_daily", 0.0)),
            "excess_return_total": float(metrics.get("excess_return_total", 0.0)),
            # Annualized metrics (reference)
            "volatility": float(metrics.get("volatility", 0.0)),
            "sharpe": float(metrics.get("sharpe", 0.0)),
            "information_ratio": float(metrics.get("information_ratio", 0.0)),
        }
        pd.DataFrame([summary_row]).to_csv(os.path.join(base, "metrics_summary.csv"), index=False)
    except Exception:
        pass

    # New: two charts (optional, if matplotlib is installed and benchmark exists)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
        # Unified Series view (compatible with single-column DataFrame)
        nav_s = _as_series(nav, ("nav",))
        bench_s = _as_series(bench_nav, ("benchmark_nav", "nav"))
        # Equity vs Benchmark + Drawdown
        if nav_s is not None and bench_s is not None and len(nav_s) > 0 and len(bench_s) > 0:
            df_plot = pd.concat([nav_s.rename("strategy"), bench_s.rename("benchmark")], axis=1).dropna()
            if len(df_plot) > 0:
                fig, ax1 = plt.subplots(figsize=(10, 6))
                ax1.plot(df_plot.index, df_plot["strategy"], label="strategy")
                ax1.plot(df_plot.index, df_plot["benchmark"], label="benchmark")
                ax1.set_ylabel("NAV")
                ax1.legend(loc="upper left")
                ax2 = ax1.twinx()
                dd = (df_plot["strategy"] / df_plot["strategy"].cummax() - 1.0)
                ax2.fill_between(df_plot.index, dd, 0, color="red", alpha=0.1)
                ax2.set_ylabel("Drawdown")
                fig.tight_layout()
                fig.savefig(os.path.join(base, "equity_vs_spy.png"))
                plt.close(fig)
        # Excess Return vs Benchmark
        if nav_s is not None and bench_s is not None and len(nav_s) > 0 and len(bench_s) > 0:
            r_s = nav_s.pct_change().fillna(0)
            r_b = bench_s.pct_change().fillna(0)
            r_e = (r_s - r_b).cumsum()
            if len(r_e) > 0:
                fig2, ax = plt.subplots(figsize=(10, 4))
                ax.plot(r_e.index, r_e.values, label="excess cumret")
                ax.set_ylabel("Excess CumRet")
                ax.legend()
                fig2.tight_layout()
                fig2.savefig(os.path.join(base, "excess_return_vs_spy.png"))
                plt.close(fig2)
    except Exception:
        pass

    # Environment and versions
    meta: Dict[str, Any] = {"python": sys.version}
    try:
        import pandas as _pd  # type: ignore
        meta["pandas"] = getattr(_pd, "__version__", "unknown")
    except Exception:
        meta["pandas"] = "unknown"
    try:
        import pyarrow as _pa  # type: ignore
        meta["pyarrow"] = getattr(_pa, "__version__", "unknown")
    except Exception:
        meta["pyarrow"] = "unknown"
    try:
        import httpx as _httpx  # type: ignore
        meta["httpx"] = getattr(_httpx, "__version__", "unknown")
    except Exception:
        meta["httpx"] = "unknown"
    with open(os.path.join(base, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # summary
    summary_lines = []
    summary_lines.append(f"run_id: {run_id}")
    # Changed to prioritize NAV-based period (can display even without trades), otherwise fallback to trades
    try:
        if isinstance(nav, pd.Series) and len(nav) > 0:
            start = str(nav.index[0])
            end = str(nav.index[-1])
        else:
            start = str(trades["ts"].min()) if not trades.empty else "NA"
            end = str(trades["ts"].max()) if not trades.empty else "NA"
    except Exception:
        start = end = "NA"
    summary_lines.append(f"period: {start} ~ {end}")
    # params snapshot (critical)
    try:
        bt = (cfg or {}).get("backtest", {}) if isinstance(cfg, dict) else {}
        news_cfg = (cfg or {}).get("news", {}) if isinstance(cfg, dict) else {}
        strat = (cfg or {}).get("strategy", "") if isinstance(cfg, dict) else ""
        timespan = (cfg or {}).get("backtest", {}).get("timespan") if isinstance(cfg, dict) else None
        agent_mode = (cfg or {}).get("agents", {}).get("mode") if isinstance(cfg, dict) else None
        summary_lines.append(
            "params: "
            f"commission_bps={bt.get('commission_bps')}, slippage_bps={bt.get('slippage_bps')}, fill_ratio={bt.get('fill_ratio')}, "
            f"news_agg={news_cfg.get('agg')}, trim_alpha={news_cfg.get('trim_alpha')}, strategy={strat or 'N/A'}, timespan={timespan or 'N/A'}, agent_mode={agent_mode or 'N/A'}"
        )
    except Exception:
        pass
    # Benchmark summary (if configured)
    try:
        bench_cfg = (cfg or {}).get("backtest", {}).get("benchmark", {}) if isinstance(cfg, dict) else {}
        if bench_cfg:
            if bench_cfg.get("basket"):
                comp = ",".join([it.get("symbol") if isinstance(it, dict) else str(it) for it in bench_cfg.get("basket", [])])
                summary_lines.append(
                    f"benchmark: basket=[{comp}], rebalance={bench_cfg.get('rebalance','daily')}, field={bench_cfg.get('field','adjusted_close')}, timespan={bench_cfg.get('timespan','day')}"
                )
            elif bench_cfg.get("symbol"):
                summary_lines.append(
                    f"benchmark: symbol={bench_cfg.get('symbol')}, field={bench_cfg.get('field','adjusted_close')}, timespan={bench_cfg.get('timespan','day')}"
                )
    except Exception:
        pass
    # Core metrics summary
    summary_lines.append(
        "metrics: "
        f"cum_return={metrics.get('cum_return', 0):.4f}, "
        f"max_drawdown={metrics.get('max_drawdown', 0):.4f}, "
        f"volatility={metrics.get('volatility', 0):.4f}, "
        f"sharpe={metrics.get('sharpe', 0):.4f}, "
        f"sortino={metrics.get('sortino', 0):.4f}"
    )
    
    # Trading statistics summary
    if metrics.get('trades_count', 0) > 0:
        summary_lines.append(
            "trading: "
            f"count={metrics.get('trades_count', 0)}, "
            f"notional={metrics.get('trades_notional', 0):.2f}"
        )
    # Append relative metrics summary (optional)
    try:
        if "information_ratio" in metrics or "tracking_error" in metrics or "alpha_simple" in metrics:
            summary_lines.append(
                "relative: "
                f"IR={metrics.get('information_ratio', 0):.4f}, "
                f"TE={metrics.get('tracking_error', 0):.4f}, "
                f"alpha={metrics.get('alpha_simple', 0):.4f}"
            )
    except Exception:
        pass
    summary_lines.append(f"trades: {0 if trades is None else len(trades)} rows")
    
    # Add metrics explanation section
    summary_lines.append("\n" + "="*80)
    summary_lines.append(_generate_summary_metrics_explanation())
    
    # Add complete metrics description section
    summary_lines.append("\n" + _generate_complete_metrics_explanation())
    
    with open(os.path.join(base, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    # New: detailed conclusion (conclusion.md) - simplified version
    try:
        if isinstance(nav, pd.Series) and len(nav) >= 2:
            period_start = str(nav.index[0].date())
            period_end = str(nav.index[-1].date())
        else:
            period_start = "NA"
            period_end = "NA"
        
        lines = [
            "# Backtest Conclusion Report",
            "",
            f"**Backtest Period**: {period_start} ~ {period_end}",
            "",
            "## Metric Values",
            "",
        ]
        
        # Display all metrics in the order they appear in metrics.json
        for key, value in metrics.items():
            if value is None:
                value_str = "null"
            elif isinstance(value, (int, float)):
                if key in ['trades_count', 'n']:
                    value_str = f"{int(value)}"
                else:
                    value_str = f"{value:.6f}"
            else:
                value_str = str(value)
            
            lines.append(f"- **{key}**: {value_str}")
        
        lines.extend([
            "",
            "## Metrics Concept Explanation",
            "",
            "- **cum_return**: Cumulative return, the rise/fall of final NAV relative to initial NAV",
            "- **max_drawdown**: Maximum drawdown, the maximum decline of NAV relative to historical peak",
            "- **volatility_daily**: Daily volatility, standard deviation of daily returns",
            "- **sortino**: Sortino ratio, average return divided by downside volatility",
            "- **trades_count**: Number of trades, total trading count during backtest period",
            "- **trades_notional**: Total trading amount, sum of all trade notional amounts",
            "- **volatility**: Annualized volatility, daily volatility annualized (×√252)",
            "- **sharpe**: Sharpe ratio, annualized return divided by annualized volatility",
            "- **sortino_annual**: Annualized Sortino ratio, annualized return divided by annualized downside volatility",
            "- **excess_return_total**: Total excess return, cumulative excess return of strategy relative to benchmark",
            "- **tracking_error_daily**: Daily tracking error, daily standard deviation of excess returns",
            "- **information_ratio_daily**: Daily information ratio, average excess return divided by daily tracking error",
            "- **beta**: Beta coefficient, systematic risk coefficient of strategy relative to benchmark",
            "- **corr**: Correlation coefficient, degree of linear correlation between strategy and benchmark returns",
            "- **up_capture**: Upside capture, average capture ratio of strategy when benchmark rises",
            "- **down_capture**: Downside capture, average capture ratio of strategy when benchmark falls",
            "- **hit_ratio_active**: Active hit ratio, proportion of trading days with positive excess returns",
            "- **sortino_excess**: Sortino ratio-excess, excess return divided by downside volatility",
            "- **rolling_ir_63**: Rolling information ratio (63 days), information ratio of short-term rolling window",
            "- **rolling_te_63**: Rolling tracking error (63 days), tracking error of short-term rolling window",
            "- **rolling_ir_126**: Rolling information ratio (126 days), information ratio of medium-term rolling window",
            "- **rolling_te_126**: Rolling tracking error (126 days), tracking error of medium-term rolling window",
            "- **excess_return_annual**: Annualized excess return, annualized excess return rate of strategy relative to benchmark",
            "- **tracking_error**: Annualized tracking error, annualized standard deviation of excess returns",
            "- **information_ratio**: Annualized information ratio, annualized excess return divided by annualized tracking error",
            "- **alpha_simple**: Simple alpha, equivalent to annualized excess return",
            "- **rolling_ir_252**: Rolling information ratio (252 days), information ratio of long-term rolling window",
            "- **rolling_te_252**: Rolling tracking error (252 days), tracking error of long-term rolling window",
            "- **n**: Sample size, number of effective data points",
            "- **freq**: Frequency, data frequency identifier",
        ])
        
        with open(os.path.join(base, "conclusion.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass

    # Solidify baseline (optional): write to baselines directory when cfg.backtest.baseline_name exists
    try:
        baseline_name = None
        if isinstance(cfg, dict):
            baseline_name = (cfg.get("backtest", {}) or {}).get("baseline_name")
        if baseline_name:
            bdir = os.path.join(os.getcwd(), "storage", "reports", "backtest", "baselines")
            ensure_dir(bdir)
            payload = {
                "run_id": run_id,
                "metrics": metrics,
                "period": {"start": start, "end": end},
                "params": {
                    "commission_bps": (cfg or {}).get("backtest", {}).get("commission_bps") if isinstance(cfg, dict) else None,
                    "slippage_bps": (cfg or {}).get("backtest", {}).get("slippage_bps") if isinstance(cfg, dict) else None,
                    "fill_ratio": (cfg or {}).get("backtest", {}).get("fill_ratio") if isinstance(cfg, dict) else None,
                    "news_agg": (cfg or {}).get("news", {}).get("agg") if isinstance(cfg, dict) else None,
                    "trim_alpha": (cfg or {}).get("news", {}).get("trim_alpha") if isinstance(cfg, dict) else None,
                },
            }
            with open(os.path.join(bdir, f"{baseline_name}.json"), "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # ===== New: per-symbol buy&hold benchmark outputs =====
    # Write per-symbol benchmark (if exists)
    try:
        per_symbol_nav = result.get("per_symbol_benchmark_nav")
        per_symbol_metrics = result.get("per_symbol_benchmark_metrics")
        if per_symbol_nav is not None or per_symbol_metrics is not None:
            ps_dir = os.path.join(base, "per_symbol_benchmark")
            ensure_dir(ps_dir)
            # nav matrix
            if hasattr(per_symbol_nav, "to_parquet"):
                per_nav_path = os.path.join(ps_dir, "per_symbol_benchmark_nav.parquet")
                try:
                    per_symbol_nav.to_parquet(per_nav_path)
                except Exception:
                    # Fallback to CSV
                    per_symbol_nav.to_csv(per_nav_path.replace(".parquet", ".csv"))
            # metrics: save as single parquet file (MultiIndex: date x symbol) or JSONL
            if isinstance(per_symbol_metrics, dict) and per_symbol_metrics:
                try:
                    import pandas as _pd  # type: ignore
                    frames = []
                    for sym, df in per_symbol_metrics.items():
                        if df is None:
                            continue
                        dfx = df.copy()
                        dfx["symbol"] = sym
                        dfx["date"] = dfx.index
                        frames.append(dfx.reset_index(drop=True))
                    if frames:
                        big = _pd.concat(frames, axis=0, ignore_index=True)
                        met_path = os.path.join(ps_dir, "per_symbol_benchmark_metrics.parquet")
                        try:
                            big.to_parquet(met_path, index=False)
                        except Exception:
                            big.to_csv(met_path.replace(".parquet", ".csv"), index=False)
                        # Also save as jsonl (grouped by symbol for easy reading)
                        jsonl_path = os.path.join(ps_dir, "per_symbol_benchmark_metrics.jsonl")
                        with open(jsonl_path, "w", encoding="utf-8") as jf:
                            for sym, df in per_symbol_metrics.items():
                                payload = {"symbol": sym, "metrics": _json_safe(df.to_dict(orient="index"))}
                                jf.write(json.dumps(payload, ensure_ascii=False) + "\n")
                except Exception:
                    pass
            # Text and images (optional)
            try:
                bench_cfg = (cfg or {}).get("backtest", {}).get("benchmark", {}) if isinstance(cfg, dict) else {}
                dm = (bench_cfg.get("daily_metrics", {}) or {})
                save_formats = list(dm.get("save_format", [])) if dm else []
                want_text = ("text" in save_formats)
                want_image = ("image" in save_formats)
                # Generate text summary
                if want_text and isinstance(per_symbol_metrics, dict):
                    for sym, df in per_symbol_metrics.items():
                        try:
                            if df is None or len(df) == 0:
                                continue
                            txt = []
                            txt.append(f"symbol: {sym}")
                            txt.append(f"rows: {len(df)}")
                            last = df.iloc[-1]
                            def _fmt(v):
                                try:
                                    return f"{float(v):.6f}"
                                except Exception:
                                    return str(v)
                            # Output driven by configured metrics list
                            metrics_cfg = (bench_cfg.get("daily_metrics", {}) or {}).get("metrics", ["cum_return", "max_drawdown", "sortino"]) if isinstance(bench_cfg, dict) else ["cum_return", "max_drawdown", "sortino"]
                            # Name mapping: config name -> DataFrame column name
                            name_map = {
                                "cum_return": "cum_return",
                                "max_drawdown": "max_drawdown_to_date",
                                "sortino": "sortino",
                            }
                            for m in metrics_cfg:
                                col = name_map.get(str(m), str(m))
                                if col in last:
                                    txt.append(f"{m}: {_fmt(last.get(col))}")
                            with open(os.path.join(ps_dir, f"{sym}_metrics.txt"), "w", encoding="utf-8") as ftxt:
                                ftxt.write("\n".join(txt))
                        except Exception:
                            continue
                # Generate images
                if want_image and hasattr(per_symbol_nav, "columns"):
                    try:
                        import matplotlib
                        matplotlib.use("Agg")
                        import matplotlib.pyplot as plt  # type: ignore
                        for sym in list(per_symbol_nav.columns):
                            try:
                                s = per_symbol_nav[sym].dropna()
                                if len(s) == 0:
                                    continue
                                base0 = float(s.iloc[0]) if len(s) > 0 else 1.0
                                base0 = base0 if base0 != 0 else 1.0
                                cum = s / base0 - 1.0
                                # Decide which curves to plot based on metrics configuration
                                dm_cfg = (bench_cfg.get("daily_metrics", {}) or {})
                                metrics_cfg = list(dm_cfg.get("metrics", []) or [])
                                draw_nav = (not metrics_cfg) or ("nav" in metrics_cfg)
                                draw_cum = (not metrics_cfg) or ("cum_return" in metrics_cfg)

                                if draw_nav and draw_cum:
                                    fig, ax1 = plt.subplots(figsize=(8, 5))
                                    ax1.plot(s.index, s.values, label="nav")
                                    ax1.set_ylabel("NAV")
                                    ax1.legend(loc="upper left")
                                    ax2 = ax1.twinx()
                                    ax2.plot(cum.index, cum.values, color="orange", label="cum_return")
                                    ax2.set_ylabel("CumRet")
                                    ax2.legend(loc="upper right")
                                elif draw_cum and not draw_nav:
                                    fig, ax1 = plt.subplots(figsize=(8, 5))
                                    ax1.plot(cum.index, cum.values, color="orange", label="cum_return")
                                    ax1.set_ylabel("CumRet")
                                    ax1.legend(loc="upper left")
                                else:
                                    # Default to at least plot NAV
                                    fig, ax1 = plt.subplots(figsize=(8, 5))
                                    ax1.plot(s.index, s.values, label="nav")
                                    ax1.set_ylabel("NAV")
                                    ax1.legend(loc="upper left")
                                title = f"{sym} Buy&Hold Benchmark"
                                try:
                                    dm_cfg = (bench_cfg.get("daily_metrics", {}) or {})
                                    sortino_cfg = (dm_cfg.get("sortino", {}) or {})
                                    fee_note = []
                                    bt_cfg = (cfg or {}).get("backtest", {}) if isinstance(cfg, dict) else {}
                                    cbps = float(bt_cfg.get("commission_bps", 0.0) or 0.0)
                                    sbps = float(bt_cfg.get("slippage_bps", 0.0) or 0.0)
                                    if cbps > 0:
                                        fee_note.append("commission")
                                    if sbps > 0:
                                        fee_note.append("slippage")
                                    fee_str = ",".join(fee_note) if fee_note else "no-fee"
                                    title += f" | fees: {fee_str} | sortino: {sortino_cfg.get('mode','rolling')}/{sortino_cfg.get('window',63)}"
                                except Exception:
                                    pass
                                ax1.set_title(title)
                                fig.tight_layout()
                                fig.savefig(os.path.join(ps_dir, f"{sym}_metrics.png"))
                                plt.close(fig)
                            except Exception:
                                continue
                    except Exception:
                        pass
            except Exception:
                pass
            
            # New: per_symbol aggregated analysis images
            try:
                from stockbench.backtest.visualization import (
                            plot_aggregated_cumreturn_analysis,
                            plot_stock_price_trends,
                            generate_individual_stocks_summary
                        )
                
                # Check aggregated analysis configuration
                aggregated_cfg = (cfg or {}).get("backtest", {}).get("benchmark", {}).get("aggregated_analysis", {})
                if aggregated_cfg.get("enabled", True) and hasattr(per_symbol_nav, "columns"):
                    plots_cfg = aggregated_cfg.get("plots", {})
                    
                    # 1. Cumulative return aggregated analysis chart
                    if plots_cfg.get("cumreturn_analysis", True):
                        cumret_path = os.path.join(ps_dir, "aggregated_cumreturn_analysis.png")
                        plot_aggregated_cumreturn_analysis(per_symbol_nav, cumret_path, aggregated_cfg)
                    
                    # 2. Standardized price trend comparison chart
                    if plots_cfg.get("price_trends", True):
                        trends_path = os.path.join(ps_dir, "stock_price_trends.png")
                        plot_stock_price_trends(per_symbol_nav, trends_path, aggregated_cfg)
                    
                    # 3. Organize individual_stocks directory
                    individual_dir = os.path.join(ps_dir, "individual_stocks")
                    os.makedirs(individual_dir, exist_ok=True)
                    
                    # Move existing single stock images to subdirectory (if exists)
                    import glob
                    existing_stock_images = glob.glob(os.path.join(ps_dir, "*_metrics.png"))
                    for img_path in existing_stock_images:
                        import shutil
                        filename = os.path.basename(img_path)
                        # Avoid moving aggregated images we just generated
                        if filename not in ["aggregated_cumreturn_analysis.png", "stock_price_trends.png"]:
                            new_path = os.path.join(individual_dir, filename)
                            try:
                                shutil.move(img_path, new_path)
                            except Exception:
                                pass  # If move fails, ignore
                    
                    # Generate individual stocks summary
                    generate_individual_stocks_summary(per_symbol_nav, individual_dir, cfg)
                
                # Phase 3: Multi-period performance comparison analysis
                multi_period_cfg = (cfg or {}).get("backtest", {}).get("benchmark", {}).get("multi_period_analysis", {})
                if multi_period_cfg.get("enabled", True) and hasattr(per_symbol_nav, "columns"):
                    try:
                        from stockbench.backtest.visualization import (
                            plot_multi_period_performance_heatmap,
                            plot_rolling_metrics_comparison,
                            plot_performance_ranking_over_time
                        )
                        
                        plots_cfg = multi_period_cfg.get("plots", {})
                        windows_cfg = multi_period_cfg.get("windows", {})
                        viz_cfg = multi_period_cfg.get("visualization", {})
                        
                        dpi = viz_cfg.get("dpi", 300)
                        
                        # 1. Multi-period return heatmap
                        if plots_cfg.get("performance_heatmap", True):
                            heatmap_path = os.path.join(ps_dir, "multi_period_performance_heatmap.png")
                            plot_multi_period_performance_heatmap(per_symbol_nav, heatmap_path, dpi=dpi, cfg=multi_period_cfg)
                        
                        # 2. Rolling Sortino ratio comparison
                        if plots_cfg.get("rolling_sortino", True):
                            window = windows_cfg.get("rolling_window", 63)
                            sortino_path = os.path.join(ps_dir, "rolling_sortino_comparison.png")
                            plot_rolling_metrics_comparison(per_symbol_nav, sortino_path, 
                                                        metric='sortino', window=window, dpi=dpi)
                        
                        # 3. Rolling Sharpe ratio comparison
                        if plots_cfg.get("rolling_sharpe", True):
                            window = windows_cfg.get("rolling_window", 63)
                            sharpe_path = os.path.join(ps_dir, "rolling_sharpe_comparison.png")
                            plot_rolling_metrics_comparison(per_symbol_nav, sharpe_path, 
                                                        metric='sharpe', window=window, dpi=dpi)
                        
                        # 4. Rolling drawdown comparison
                        if plots_cfg.get("rolling_drawdown", True):
                            window = windows_cfg.get("rolling_window", 63)
                            drawdown_path = os.path.join(ps_dir, "rolling_drawdown_comparison.png")
                            plot_rolling_metrics_comparison(per_symbol_nav, drawdown_path, 
                                                        metric='drawdown', window=window, dpi=dpi)
                        
                        # 5. Performance ranking change chart
                        if plots_cfg.get("ranking_over_time", True):
                            ranking_window = windows_cfg.get("ranking_window", 21)
                            ranking_path = os.path.join(ps_dir, "performance_ranking_over_time.png")
                            plot_performance_ranking_over_time(per_symbol_nav, ranking_path, 
                                                            window=ranking_window, dpi=dpi)
                        
                    except ImportError as e:
                        print(f"[WARNING] Failed to import multi-period visualization module: {e}")
                    except Exception as e:
                        print(f"[WARNING] Failed to generate multi-period analysis: {e}")
                
                # ===== New: Benchmark comparison image generation =====
                try:
                    # Check if benchmark comparison is enabled
                    benchmark_comparison_cfg = aggregated_cfg.get("benchmark_comparison", {})
                    if benchmark_comparison_cfg.get("enabled", True) and hasattr(per_symbol_nav, "columns"):
                        from stockbench.backtest.visualization import (
                            plot_nav_comparison,
                            plot_totalassets_comparison
                        )
                        from stockbench.backtest.metrics import (
                            compute_simple_average_benchmark,
                            compute_weighted_average_benchmark
                        )
                        
                        # Create benchmark comparison directory
                        comparison_dir = os.path.join(ps_dir, "benchmark_comparisons")
                        os.makedirs(comparison_dir, exist_ok=True)
                        
                        # Get strategy NAV data
                        strategy_nav = result.get("nav")
                        initial_cash = result.get("initial_cash", 1000000)  # Default 1 million
                        
                        if strategy_nav is not None and not per_symbol_nav.empty:
                            # Calculate benchmarks
                            simple_avg_nav = compute_simple_average_benchmark(per_symbol_nav)
                            weighted_avg_nav = compute_weighted_average_benchmark(per_symbol_nav)
                            
                            # Strategy vs simple average benchmark
                            if not simple_avg_nav.empty:
                                simple_dir = os.path.join(comparison_dir, "strategy_vs_simple_avg")
                                os.makedirs(simple_dir, exist_ok=True)
                                
                                nav_path = os.path.join(simple_dir, "nav_comparison.png")
                                plot_nav_comparison(strategy_nav, simple_avg_nav, "Strategy", "Simple Average", 
                                                nav_path, aggregated_cfg)
                                
                                assets_path = os.path.join(simple_dir, "totalassets_comparison.png")
                                plot_totalassets_comparison(strategy_nav, simple_avg_nav, "Strategy", "Simple Average", 
                                                        initial_cash, assets_path, aggregated_cfg)
                            
                            # Strategy vs weighted average benchmark
                            if not weighted_avg_nav.empty:
                                weighted_dir = os.path.join(comparison_dir, "strategy_vs_weighted_avg")
                                os.makedirs(weighted_dir, exist_ok=True)
                                
                                nav_path = os.path.join(weighted_dir, "nav_comparison.png")
                                plot_nav_comparison(strategy_nav, weighted_avg_nav, "Strategy", "Weighted Average", 
                                                nav_path, aggregated_cfg)
                                
                                assets_path = os.path.join(weighted_dir, "totalassets_comparison.png")
                                plot_totalassets_comparison(strategy_nav, weighted_avg_nav, "Strategy", "Weighted Average", 
                                                        initial_cash, assets_path, aggregated_cfg)
                            
                            # Strategy vs SPY benchmark (if available)
                            spy_nav = result.get("benchmark_nav")
                            if spy_nav is not None and not spy_nav.empty:
                                spy_dir = os.path.join(comparison_dir, "strategy_vs_spy")
                                os.makedirs(spy_dir, exist_ok=True)
                                
                                nav_path = os.path.join(spy_dir, "nav_comparison.png")
                                plot_nav_comparison(strategy_nav, spy_nav, "Strategy", "SPY", 
                                                nav_path, aggregated_cfg)
                                
                                assets_path = os.path.join(spy_dir, "totalassets_comparison.png")
                                plot_totalassets_comparison(strategy_nav, spy_nav, "Strategy", "SPY", 
                                                        initial_cash, assets_path, aggregated_cfg)
                        
                        print(f"✅ Generated benchmark comparison plots in {comparison_dir}")
                
                except ImportError as e:
                    print(f"[WARNING] Failed to import benchmark comparison module: {e}")
                except Exception as e:
                    print(f"[WARNING] Failed to generate benchmark comparison: {e}")
            
            except ImportError as e:
                print(f"[WARNING] Failed to import visualization module: {e}")
            except Exception as e:
                print(f"[WARNING] Failed to generate aggregated analysis: {e}")
            except Exception:
                pass
    except Exception:
        pass

    return base 
from __future__ import annotations

from pathlib import Path
import json
import os

import pandas as pd
import typer
import yaml

from stockbench.agents.backtest_report_llm import generate_backtest_report

app = typer.Typer(add_completion=False)


def _load_metrics(run_dir: Path) -> dict:
    path = run_dir / "metrics.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _load_config(run_dir: Path) -> dict:
    path = run_dir / "config.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _collect_stats(run_dir: Path) -> dict:
    stats = {}
    # trades count
    trades_path = run_dir / "trades.parquet"
    try:
        if trades_path.exists():
            df = pd.read_parquet(trades_path)
            stats["trades_count"] = int(len(df))
            if "symbol" in df.columns:
                stats["symbols"] = sorted(list(map(str, set(df["symbol"].dropna().unique().tolist()))))
            if "ts" in df.columns and len(df) > 0:
                stats["period"] = {
                    "start": str(df["ts"].min()),
                    "end": str(df["ts"].max()),
                }
    except Exception:
        pass
    # params snapshot
    cfg = _load_config(run_dir)
    bt = (cfg.get("backtest") or {}) if isinstance(cfg, dict) else {}
    news = (cfg.get("news") or {}) if isinstance(cfg, dict) else {}
    stats["timespan"] = bt.get("timespan")
    stats["costs"] = {
        "commission_bps": bt.get("commission_bps"),
        "slippage_bps": bt.get("slippage_bps"),
        "fill_ratio": bt.get("fill_ratio"),
    }
    stats["news_agg"] = news.get("agg")
    stats["trim_alpha"] = news.get("trim_alpha")
    return stats


@app.command()
def main(
    run_dir: Path = typer.Option(..., exists=True, file_okay=False, readable=True, help="Backtest output directory, e.g. storage/reports/backtest/llm_single_day"),
    cfg: Path = typer.Option(None, exists=True, readable=True, help="Optional: load YAML config to pass LLM client settings"),
    llm: bool = typer.Option(False, help="Whether to call real LLM to generate summary (default no)"),
    out: Path = typer.Option(None, help="Output text path (default write to run_dir/nl_summary.txt)"),
):
    # Configuration
    cfg_dict = {}
    if cfg is not None:
        with cfg.open("r", encoding="utf-8") as f:
            cfg_dict = yaml.safe_load(f)

    metrics = _load_metrics(run_dir)
    stats = _collect_stats(run_dir)
    payload = {"metrics": metrics, "stats": stats}

    text = generate_backtest_report(payload, cfg=cfg_dict)

    out_path = out or (run_dir / "nl_summary.txt")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    typer.echo(f"Summary generated: {out_path}")


if __name__ == "__main__":  # pragma: no cover
    app() 
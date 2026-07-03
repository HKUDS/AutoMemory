from __future__ import annotations


class Slippage:
    def __init__(self, buy_bps: float = 0.0, sell_bps: float | None = None, bps: float | None = None) -> None:
        if bps is not None:
            buy_bps = float(bps)
            sell_bps = float(bps if sell_bps is None else sell_bps)
        self.buy_bps = float(buy_bps)
        self.sell_bps = float(sell_bps if sell_bps is not None else buy_bps)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Slippage":
        bcfg = cfg.get("backtest", {}) or {}
        bps = float(bcfg.get("slippage_bps", 0.0))
        buy_bps = float(bcfg.get("slippage_buy_bps", bps))
        sell_bps = float(bcfg.get("slippage_sell_bps", bps))
        return cls(buy_bps=buy_bps, sell_bps=sell_bps)

    def apply(self, price: float) -> float:
        return self.apply_buy(price)

    def apply_buy(self, price: float) -> float:
        return float(price) * (1.0 + self.buy_bps / 10_000.0)

    def apply_sell(self, price: float) -> float:
        return float(price) * (1.0 - self.sell_bps / 10_000.0) 
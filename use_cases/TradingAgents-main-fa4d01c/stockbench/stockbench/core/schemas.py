from __future__ import annotations

from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, conlist, confloat
from pydantic import ConfigDict


class _ConfigBase(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")


class TechSnapshot(_ConfigBase):
    ret: Dict[str, confloat(ge=-1, le=1)] = Field(default_factory=dict)
    atr_pct: confloat(ge=0, le=1) = 0
    trend_score: confloat(ge=-1, le=1) = 0.0
    trend: Literal["up", "down", "sideways"] = "sideways"
    mom: confloat(ge=0, le=1) = 0.5
    vwap_dev: float = 0.0


class NewsSnapshot(_ConfigBase):
    sentiment: confloat(ge=-1, le=1) = 0.0
    top_k_events: List[str] = Field(default_factory=list, max_length=10)
    src_count: int = 0
    freshness_min: int = 0
    sentiment_reason: Optional[str] = None


class PositionState(_ConfigBase):
    current_position_value: float = 0.0  # Current position market value (cash amount)
    holding_days: int = 0  # Number of holding days
    shares: float = 0.0  # Current number of shares held (rounded to two decimal places)


class FeatureInput(_ConfigBase):
    schema_version: str = "v1"
    symbol: str
    ts_utc: str
    tech: TechSnapshot
    news: NewsSnapshot
    fund: Dict[str, float] = Field(default_factory=dict)
    market_ctx: Dict[str, Optional[float]] = Field(default_factory=dict)
    position_state: PositionState


class AnalyzerOutput(_ConfigBase):
    schema_version: str = "v1"
    tech_score: confloat(ge=0, le=1)
    sent_score: confloat(ge=-1, le=1)
    event_risk: Literal["low", "normal", "elevated", "high"]
    summary: List[str] = Field(default_factory=list)
    confidence: confloat(ge=0, le=1)



class DecisionOutput(_ConfigBase):
    schema_version: str = "v2"
    action: Literal["increase", "hold", "decrease", "close"]
    target_cash_amount: float = 0.0  # Target position cash amount
    cash_change: float = 0.0  # Cash change amount (positive for buy amount, negative for sell amount)
    reasons: List[str] = Field(default_factory=list)
    confidence: confloat(ge=0, le=1) = 0.5


class Order(_ConfigBase):
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    limit: Optional[float] = None
    slice: int = 1
    twap_slices: int = 1


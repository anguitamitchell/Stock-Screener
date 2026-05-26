from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Holding:
    ticker: str
    name: str
    weight: Optional[float] = None
    shares_held: Optional[float] = None
    sector: Optional[str] = None


@dataclass(frozen=True)
class ScreenResult:
    score: Optional[float]
    label: str
    reasons: List[str] = field(default_factory=list)
    confidence: float = 0.0
    research_status: str = "unreviewed"
    source: str = ""
    exposures: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PriceBar:
    timestamp: int
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]


@dataclass(frozen=True)
class PriceSnapshot:
    ticker: str
    last_close: Optional[float] = None
    previous_close: Optional[float] = None
    change_percent: Optional[float] = None
    average_volume_20d: Optional[float] = None
    sma_20: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    close_vs_sma_20_percent: Optional[float] = None
    close_vs_sma_50_percent: Optional[float] = None
    close_vs_sma_200_percent: Optional[float] = None
    return_5d_percent: Optional[float] = None
    return_20d_percent: Optional[float] = None
    return_60d_percent: Optional[float] = None
    return_120d_percent: Optional[float] = None
    low_20d: Optional[float] = None
    high_20d: Optional[float] = None
    low_60d: Optional[float] = None
    high_60d: Optional[float] = None
    atr_14: Optional[float] = None
    trading_days: int = 0
    source: str = ""
    error: Optional[str] = None


@dataclass(frozen=True)
class CompanyResearch:
    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    summary: str = ""
    source: str = ""
    error: Optional[str] = None


@dataclass(frozen=True)
class TradePlan:
    entry_low: Optional[float] = None
    entry_high: Optional[float] = None
    breakout_trigger: Optional[float] = None
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    reward_risk: Optional[float] = None
    label: str = "unavailable"
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class RatedHolding:
    holding: Holding
    price: Optional[PriceSnapshot]
    semantic: ScreenResult
    technical: ScreenResult
    trade_plan: Optional[TradePlan]
    final_score: Optional[float]
    rating: str
    rating_reasons: List[str]


@dataclass(frozen=True)
class EtfScreenReport:
    etf: str
    generated_at: datetime
    source: str
    holdings_count: int
    rated_holdings: List[RatedHolding]
    market_condition: Optional["MarketConditionReport"] = None
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeMarkSignal:
    ticker: str
    score: float
    regime: str
    setup_side: str
    setup_count: int
    countdown_side: str
    countdown_count: int
    exhaustion: str
    reasons: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class MarketSignal:
    name: str
    score: float
    regime: str
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)


@dataclass(frozen=True)
class MarketConditionReport:
    generated_at: datetime
    ticker: str
    overall_regime: str
    overall_score: float
    summary: str
    demark: DeMarkSignal
    structural_weakness: MarketSignal
    technology_support: MarketSignal
    defensive_rotation: MarketSignal
    credit_risk: MarketSignal
    dollar_yield_shock: MarketSignal
    fed_liquidity_notes: List[str] = field(default_factory=list)

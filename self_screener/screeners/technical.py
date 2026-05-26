from typing import Optional

from self_screener.models import Holding, PriceSnapshot, ScreenResult


def screen_holding(
    holding: Holding,
    price: Optional[PriceSnapshot] = None,
    benchmark: Optional[PriceSnapshot] = None,
) -> ScreenResult:
    if price is None:
        return ScreenResult(
            score=None,
            label="missing price data",
            reasons=["Technical score needs price data. Run the screener with --prices."],
        )

    if price.error:
        return ScreenResult(
            score=None,
            label="price unavailable",
            reasons=[f"Price snapshot unavailable: {price.error}"],
        )

    score = 0.0
    reasons = []

    close = price.last_close
    sma_20 = price.sma_20
    sma_50 = price.sma_50
    sma_200 = price.sma_200

    close_vs_20 = _ratio_percent(close, sma_20)
    close_vs_50 = _ratio_percent(close, sma_50)
    close_vs_200 = _ratio_percent(close, sma_200)
    sma_20_vs_200 = _ratio_percent(sma_20, sma_200)
    sma_50_vs_200 = _ratio_percent(sma_50, sma_200)

    score += _scaled_component(close_vs_20, full_at=5, floor_at=-5, weight=15)
    score += _scaled_component(close_vs_200, full_at=10, floor_at=-10, weight=20)
    score += _scaled_component(sma_20_vs_200, full_at=5, floor_at=-5, weight=15)
    score += _above_both_component(close, sma_20, sma_200)
    score += _momentum_component(price, benchmark)
    score += _scaled_component(close_vs_50, full_at=7, floor_at=-7, weight=10)
    score += _scaled_component(sma_50_vs_200, full_at=5, floor_at=-5, weight=5)

    rounded_score = round(score, 1)
    label = _label_for_score(rounded_score)

    reasons.append(
        f"Price/20D: {_format_ratio(close_vs_20)}; Price/200D: {_format_ratio(close_vs_200)}; "
        f"20D/200D: {_format_ratio(sma_20_vs_200)}."
    )
    reasons.append(
        f"Price is {_above_text(close, sma_20, '20D')} and {_above_text(close, sma_200, '200D')}; "
        f"momentum: 1M {_format_ratio(price.return_20d_percent)}, 3M {_format_ratio(price.return_60d_percent)}."
    )
    reasons.append(
        f"Additional checks: Price/50D {_format_ratio(close_vs_50)}, 50D/200D {_format_ratio(sma_50_vs_200)}, "
        f"3M vs benchmark {_format_ratio(_relative_return(price.return_60d_percent, benchmark.return_60d_percent if benchmark else None))}."
    )

    return ScreenResult(
        score=rounded_score,
        label=label,
        reasons=reasons,
    )


def _scaled_component(value: Optional[float], full_at: float, floor_at: float, weight: float) -> float:
    if value is None:
        return 0.0
    if value >= full_at:
        return weight
    if value <= floor_at:
        return 0.0
    position = (value - floor_at) / (full_at - floor_at)
    return max(0.0, min(weight, position * weight))


def _above_both_component(close: Optional[float], sma_20: Optional[float], sma_200: Optional[float]) -> float:
    if close is None:
        return 0.0
    above_20 = sma_20 is not None and close > sma_20
    above_200 = sma_200 is not None and close > sma_200
    if above_20 and above_200:
        return 20.0
    if above_20 or above_200:
        return 8.0
    return 0.0


def _momentum_component(price: PriceSnapshot, benchmark: Optional[PriceSnapshot]) -> float:
    score = 0.0
    score += _scaled_component(price.return_20d_percent, full_at=8, floor_at=-8, weight=5)
    score += _scaled_component(price.return_60d_percent, full_at=15, floor_at=-15, weight=5)
    score += _scaled_component(
        _relative_return(
            price.return_60d_percent,
            benchmark.return_60d_percent if benchmark else None,
        ),
        full_at=8,
        floor_at=-8,
        weight=5,
    )
    return min(15.0, score)


def _relative_return(value: Optional[float], benchmark_value: Optional[float]) -> Optional[float]:
    if value is None or benchmark_value is None:
        return None
    return value - benchmark_value


def _ratio_percent(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return ((numerator / denominator) - 1) * 100


def _label_for_score(score: float) -> str:
    if score >= 75:
        return "bullish"
    if score >= 60:
        return "constructive"
    if score >= 45:
        return "neutral"
    return "weak"


def _format_ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"


def _above_text(close: Optional[float], average: Optional[float], label: str) -> str:
    if close is None or average is None:
        return f"missing {label}"
    return f"above {label}" if close > average else f"below {label}"

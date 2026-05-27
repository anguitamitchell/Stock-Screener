from typing import Optional

from self_screener.models import DeMarkSignal, Holding, PriceSnapshot, ScreenResult


def screen_holding(
    holding: Holding,
    price: Optional[PriceSnapshot] = None,
    benchmark: Optional[PriceSnapshot] = None,
    market_demark: Optional[DeMarkSignal] = None,
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

    close = price.last_close
    sma_20 = price.sma_20
    sma_50 = price.sma_50
    sma_200 = price.sma_200

    price_above_200d = _is_above(close, sma_200)
    sma_20_above_50d = _is_above(sma_20, sma_50)
    sma_50_above_200d = _is_above(sma_50, sma_200)
    price_above_20d = _is_above(close, sma_20)
    sma_20_above_200d = _is_above(sma_20, sma_200)

    demark_source_score = market_demark.score if market_demark else 50.0
    demark_points = _weighted_points(demark_source_score, 10.0)

    momentum_checks = [price_above_200d, sma_20_above_50d, sma_50_above_200d]
    momentum_true_count = sum(1 for item in momentum_checks if item is True)
    momentum_points = round((momentum_true_count / 3) * 70.0, 1)
    momentum_label = _momentum_label(momentum_true_count)

    price_points = 10.0 if price_above_20d is True else 0.0
    moving_average_points = 10.0 if sma_20_above_200d is True else 0.0

    rounded_score = round(demark_points + momentum_points + price_points + moving_average_points, 1)
    label = _label_for_score(rounded_score)

    reasons = [
        f"DeMark bucket: {demark_points:.1f}/10 from selected ETF DeMark score {demark_source_score:.1f} ({market_demark.regime if market_demark else 'neutral fallback'}).",
        (
            f"Momentum bucket: {momentum_points:.1f}/70, {momentum_label}; "
            f"price > 200D is {_format_bool(price_above_200d)}, "
            f"20D > 50D is {_format_bool(sma_20_above_50d)}, "
            f"50D > 200D is {_format_bool(sma_50_above_200d)}."
        ),
        f"Price bucket: {price_points:.1f}/10; current price > 20D is {_format_bool(price_above_20d)}.",
        f"Moving Average bucket: {moving_average_points:.1f}/10; 20D > 200D is {_format_bool(sma_20_above_200d)}.",
    ]

    return ScreenResult(
        score=rounded_score,
        label=label,
        reasons=reasons,
        exposures={
            "demark_points": f"{demark_points:.1f}",
            "demark_source_score": f"{demark_source_score:.1f}",
            "momentum_points": f"{momentum_points:.1f}",
            "momentum_true_count": str(momentum_true_count),
            "momentum_label": momentum_label,
            "price_points": f"{price_points:.1f}",
            "moving_average_points": f"{moving_average_points:.1f}",
            "price_above_200d": _format_bool(price_above_200d),
            "sma_20_above_50d": _format_bool(sma_20_above_50d),
            "sma_50_above_200d": _format_bool(sma_50_above_200d),
            "price_above_20d": _format_bool(price_above_20d),
            "sma_20_above_200d": _format_bool(sma_20_above_200d),
        },
    )


def _is_above(value: Optional[float], comparison: Optional[float]) -> Optional[bool]:
    if value is None or comparison is None:
        return None
    return value > comparison


def _weighted_points(score: float, weight: float) -> float:
    return round(max(0.0, min(100.0, score)) * (weight / 100.0), 1)


def _momentum_label(true_count: int) -> str:
    if true_count == 3:
        return "bullish"
    if true_count == 2:
        return "neutral"
    return "bearish"


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


def _format_bool(value: Optional[bool]) -> str:
    if value is None:
        return "missing"
    return "true" if value else "false"

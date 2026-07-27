import os
from dataclasses import asdict
from datetime import datetime
from typing import List, Optional, Tuple

from self_screener.models import DeMarkSignal, MarketConditionReport, MarketSignal, PriceBar
from self_screener.providers.barchart_breadth import BreadthObservation, fetch_russell_3000_above_20d
from self_screener.providers.fred import FredObservation, fetch_fred_observation
from self_screener.providers.yahoo_prices import fetch_price_history, prefetch_price_histories
from self_screener.storage import store
HIGH_YIELD_OAS_SERIES_ID = "BAMLH0A0HYM2"
HIGH_YIELD_OAS_THRESHOLD = 3.5
DXY_THRESHOLD = 100.0
TEN_YEAR_YIELD_THRESHOLD = 4.5
# A finished report is reused within this window instead of being re-derived
# from its six input series on every screen. refresh=True always bypasses it.
MARKET_CONDITION_TTL_SECONDS = int(os.environ.get("MARKET_CONDITION_TTL_SECONDS", "3600"))


def build_market_condition(ticker: str = "SPY", refresh: bool = False) -> MarketConditionReport:
    symbol = ticker.upper()
    if not refresh:
        cached = _cached_market_condition(symbol)
        if cached is not None:
            return cached

    histories = prefetch_price_histories(
        [symbol, "SPY", "QQQ", "XLU", "XLP", "DX-Y.NYB", "^TNX"], refresh=refresh
    )
    bars = histories[symbol]
    spy_bars = histories["SPY"]
    tech_bars = histories["QQQ"]
    utilities_bars = histories["XLU"]
    staples_bars = histories["XLP"]
    dollar_bars = histories["DX-Y.NYB"]
    treasury_yield_bars = histories["^TNX"]
    russell_3000_above_20d = fetch_russell_3000_above_20d(refresh=refresh)
    high_yield_oas = fetch_fred_observation(HIGH_YIELD_OAS_SERIES_ID, refresh=refresh)
    demark = evaluate_demark(symbol, bars)
    participation = evaluate_market_participation(russell_3000_above_20d)
    technology = evaluate_technology_support(tech_bars, spy_bars)
    defensive = evaluate_defensive_rotation(utilities_bars, staples_bars, spy_bars)
    credit = evaluate_credit_risk(high_yield_oas)
    shock = evaluate_dollar_yield_shock(dollar_bars, treasury_yield_bars)
    overall_score, overall_regime, summary = _combine_market_signals(
        demark,
        participation,
        technology,
        defensive,
        credit,
        shock,
    )

    report = MarketConditionReport(
        generated_at=datetime.utcnow(),
        ticker=symbol,
        overall_regime=overall_regime,
        overall_score=overall_score,
        summary=summary,
        demark=demark,
        structural_weakness=participation,
        technology_support=technology,
        defensive_rotation=defensive,
        credit_risk=credit,
        dollar_yield_shock=shock,
        fed_liquidity_notes=[
            "Fed/liquidity regime is documented in the worksheet but not scored yet.",
            "Next implementation should translate liquidity inputs into measurable rules.",
        ],
    )
    write_market_condition(report)
    return report


def _cached_market_condition(symbol: str) -> Optional[MarketConditionReport]:
    payload = store.get_market_condition_report(symbol)
    if not payload or not payload.get("generated_at"):
        return None
    generated_at = datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    if (datetime.utcnow() - generated_at).total_seconds() > MARKET_CONDITION_TTL_SECONDS:
        return None
    try:
        return _report_from_payload(payload)
    except (KeyError, TypeError):
        return None


def _report_from_payload(payload: dict) -> MarketConditionReport:
    fields = dict(payload)
    fields["generated_at"] = datetime.fromisoformat(fields["generated_at"].replace("Z", "+00:00")).replace(
        tzinfo=None
    )
    fields["demark"] = DeMarkSignal(**fields["demark"])
    for key in (
        "structural_weakness",
        "technology_support",
        "defensive_rotation",
        "credit_risk",
        "dollar_yield_shock",
    ):
        fields[key] = MarketSignal(**fields[key])
    return MarketConditionReport(**fields)


def _combine_market_signals(
    demark: DeMarkSignal,
    participation: MarketSignal,
    technology: MarketSignal,
    defensive: MarketSignal,
    credit: MarketSignal,
    shock: MarketSignal,
):
    score = round(
        (demark.score * 0.10)
        + (participation.score * 0.10)
        + (technology.score * 0.30)
        + (defensive.score * 0.30)
        + (credit.score * 0.12)
        + (shock.score * 0.08),
        1,
    )
    if score >= 60:
        regime = "bullish"
    elif score <= 40:
        regime = "bearish"
    else:
        regime = "neutral"

    summary = (
        f"Overall market condition is {regime}: DeMark is {demark.regime} "
        f"while market participation is {participation.regime}, technology support is {technology.regime}, "
        f"defensive rotation is {defensive.regime}, credit risk is {credit.regime}, "
        f"and dollar/yield shock is {shock.regime}."
    )
    return score, regime, summary


def evaluate_market_participation(breadth: BreadthObservation) -> MarketSignal:
    if breadth.value is None:
        return MarketSignal(
            name="Russell 3000 20D breadth participation",
            score=50.0,
            regime="neutral",
            reasons=[breadth.error or "Market participation needs the latest Russell 3000 % above 20D value."],
            metrics={},
        )

    value = breadth.value
    score, regime, regime_note = _score_russell_3000_20d_breadth(value, breadth.monthly_low, breadth.monthly_high)

    reasons = [
        f"Russell 3000 stocks above their 20D moving average is {value:.1f}%.",
        regime_note,
        "Bull regime: 55-85% with a 40-45% support floor; neutral regime: 35-65% churn; bear regime: 10-45% with a 50-55% ceiling.",
    ]

    return MarketSignal(
        name="Russell 3000 20D breadth participation",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "russell_3000_above_20d_percent": round(value, 4),
            "previous_close": breadth.previous_close,
            "weekly_high": breadth.weekly_high,
            "weekly_low": breadth.weekly_low,
            "monthly_high": breadth.monthly_high,
            "monthly_low": breadth.monthly_low,
        },
    )


def evaluate_structural_weakness(equal_weight_bars: List[PriceBar], cap_weight_bars: List[PriceBar]) -> MarketSignal:
    return MarketSignal(
        name="Legacy market participation",
        score=50.0,
        regime="neutral",
        reasons=["Legacy RSP/SPY participation is no longer used. Current app uses Russell 3000 20D breadth."],
        metrics={},
    )


def _score_russell_3000_20d_breadth(
    value: float,
    monthly_low: Optional[float],
    monthly_high: Optional[float],
) -> Tuple[float, str, str]:
    if value >= 85:
        return 70.0, "bullish", "Breadth is above the 85% froth ceiling: structurally strong, but short-term stretched."

    if value >= 55:
        if monthly_low is not None and monthly_low >= 40:
            return 82.0, "bullish", "Breadth is in the 55-85% bull range and the recent floor held above the 40-45% high-floor zone."
        if monthly_low is not None and monthly_low < 35 and monthly_high is not None and monthly_high >= 65:
            return 55.0, "neutral", "Breadth recovered into the upper range, but the recent floor broke below neutral support, suggesting transition-market churn."
        return 68.0, "bullish", "Breadth is in the 55-85% bull range, though the high-floor support behavior is not fully confirmed."

    if value >= 45:
        if monthly_high is not None and monthly_high <= 55:
            return 38.0, "bearish", "Breadth is trapped near the 50-55% bear-market rally ceiling."
        return 50.0, "neutral", "Breadth is near the 50% midpoint where neutral and transition markets often churn."

    if value >= 35:
        if monthly_low is not None and monthly_low >= 40:
            return 60.0, "bullish", "Breadth is testing the 40-45% bull-market support floor."
        if monthly_high is not None and monthly_high <= 55:
            return 35.0, "bearish", "Breadth is in the bear-market range and has not reclaimed the 50-55% ceiling."
        return 45.0, "neutral", "Breadth is in the lower part of the neutral 35-65% churn range."

    if value >= 30:
        return 42.0, "neutral", "Breadth is near the 30% neutral-market support area where mean-reversion bounces can appear."

    if value < 15:
        return 18.0, "bearish", "Breadth is below the 15% systemic capitulation zone."

    return 28.0, "bearish", "Breadth is in the 10-45% bear-market range, showing broad participation in downside pressure."


def evaluate_technology_support(tech_bars: List[PriceBar], benchmark_bars: List[PriceBar]) -> MarketSignal:
    tech_closes = _closes(tech_bars)
    if len(tech_closes) < 200:
        return MarketSignal(
            name="QQQ technology support",
            score=50.0,
            regime="neutral",
            reasons=["Technology support signal needs at least 200 QQQ price bars."],
            metrics={},
        )

    current_close = tech_closes[-1]
    sma_20 = _average(tech_closes[-20:])
    sma_50 = _average(tech_closes[-50:])
    sma_200 = _average(tech_closes[-200:])

    price_above_200d = current_close > sma_200
    sma_50_above_200d = sma_50 > sma_200
    sma_20_above_200d = sma_20 > sma_200
    true_count = sum([price_above_200d, sma_50_above_200d, sma_20_above_200d])

    if true_count == 3:
        regime = "bullish"
    elif true_count == 2:
        regime = "neutral"
    else:
        regime = "bearish"

    score_by_count = {
        3: 100.0,
        2: 50.0,
        1: 25.0,
        0: 0.0,
    }
    score = score_by_count[true_count]

    reasons = [
        f"QQQ price above 200D moving average: {price_above_200d}.",
        f"QQQ 50D moving average above 200D moving average: {sma_50_above_200d}.",
        f"QQQ 20D moving average above 200D moving average: {sma_20_above_200d}.",
        f"Technology checklist passed {true_count} of 3 checks.",
    ]

    return MarketSignal(
        name="QQQ technology support",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "last_close": round(current_close, 4),
            "sma_20": round(sma_20, 4) if sma_20 is not None else None,
            "sma_50": round(sma_50, 4) if sma_50 is not None else None,
            "sma_200": round(sma_200, 4) if sma_200 is not None else None,
            "price_above_200d": price_above_200d,
            "sma_50_above_200d": sma_50_above_200d,
            "sma_20_above_200d": sma_20_above_200d,
            "true_count": true_count,
        },
    )


def evaluate_defensive_rotation(
    utilities_bars: List[PriceBar],
    staples_bars: List[PriceBar],
    benchmark_bars: List[PriceBar],
) -> MarketSignal:
    utilities_ratio = _aligned_close_ratios(utilities_bars, benchmark_bars)
    staples_ratio = _aligned_close_ratios(staples_bars, benchmark_bars)

    if len(utilities_ratio) < 60 or len(staples_ratio) < 60:
        return MarketSignal(
            name="Defensive rotation",
            score=50.0,
            regime="neutral",
            reasons=["Defensive rotation needs at least 60 aligned trading days for XLU/SPY and XLP/SPY."],
            metrics={},
        )

    utilities_20d = _period_return(utilities_ratio, 20)
    utilities_60d = _period_return(utilities_ratio, 60)
    staples_20d = _period_return(staples_ratio, 20)
    staples_60d = _period_return(staples_ratio, 60)
    average_20d = _average([utilities_20d, staples_20d])
    average_60d = _average([utilities_60d, staples_60d])

    risk_appetite_score = 50.0
    risk_appetite_score += _inverted_scaled_component(average_20d, full_at=-3, floor_at=3, weight=25)
    risk_appetite_score += _inverted_scaled_component(average_60d, full_at=-5, floor_at=5, weight=25)
    risk_appetite_score -= 25.0
    score = round(max(0.0, min(100.0, risk_appetite_score)), 1)

    if score >= 60:
        regime = "bullish"
    elif score <= 40:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"XLU/SPY trend: 1M {_format_ratio(utilities_20d)}, 3M {_format_ratio(utilities_60d)}.",
        f"XLP/SPY trend: 1M {_format_ratio(staples_20d)}, 3M {_format_ratio(staples_60d)}.",
        f"Average defensive relative trend: 1M {_format_ratio(average_20d)}, 3M {_format_ratio(average_60d)}.",
    ]

    return MarketSignal(
        name="Defensive rotation",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "xlu_spy_return_20d_percent": utilities_20d,
            "xlu_spy_return_60d_percent": utilities_60d,
            "xlp_spy_return_20d_percent": staples_20d,
            "xlp_spy_return_60d_percent": staples_60d,
            "average_defensive_return_20d_percent": average_20d,
            "average_defensive_return_60d_percent": average_60d,
        },
    )


def evaluate_credit_risk(high_yield_oas: FredObservation) -> MarketSignal:
    if high_yield_oas.value is None:
        return MarketSignal(
            name="High yield OAS credit risk",
            score=50.0,
            regime="neutral",
            reasons=[high_yield_oas.error or "Credit risk signal needs the latest high yield OAS value."],
            metrics={"series_id": high_yield_oas.series_id},
        )

    spread = high_yield_oas.value
    spread_gap = spread - HIGH_YIELD_OAS_THRESHOLD
    score = round(max(0.0, min(100.0, 50.0 - ((spread_gap / HIGH_YIELD_OAS_THRESHOLD) * 50.0))), 1)

    if spread < HIGH_YIELD_OAS_THRESHOLD:
        regime = "bullish"
    elif spread > HIGH_YIELD_OAS_THRESHOLD:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"ICE BofA US High Yield OAS is {spread:.2f} as of {high_yield_oas.date or 'latest available date'}.",
        f"Below {HIGH_YIELD_OAS_THRESHOLD:.1f} is bullish; above {HIGH_YIELD_OAS_THRESHOLD:.1f} is bearish.",
    ]

    return MarketSignal(
        name="High yield OAS credit risk",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "series_id": high_yield_oas.series_id,
            "observation_date": high_yield_oas.date,
            "oas": spread,
            "threshold": HIGH_YIELD_OAS_THRESHOLD,
            "spread_gap": round(spread_gap, 4),
        },
    )


def evaluate_dollar_yield_shock(dollar_bars: List[PriceBar], treasury_yield_bars: List[PriceBar]) -> MarketSignal:
    dollar_closes = _closes(dollar_bars)
    yield_closes = _closes(treasury_yield_bars)
    if not dollar_closes or not yield_closes:
        return MarketSignal(
            name="Dollar/yield shock",
            score=50.0,
            regime="neutral",
            reasons=["Dollar/yield shock needs current DXY and 10-year Treasury yield values."],
            metrics={},
        )

    dxy = dollar_closes[-1]
    ten_year_yield = yield_closes[-1]
    dxy_bearish = dxy > DXY_THRESHOLD
    yield_bearish = ten_year_yield > TEN_YEAR_YIELD_THRESHOLD
    dxy_bullish = dxy < DXY_THRESHOLD
    yield_bullish = ten_year_yield < TEN_YEAR_YIELD_THRESHOLD

    if dxy_bullish and yield_bullish:
        score = 100.0
        regime = "bullish"
    elif dxy_bearish and yield_bearish:
        score = 0.0
        regime = "bearish"
    else:
        score = 50.0
        regime = "neutral"

    reasons = [
        f"DXY is {dxy:.2f}; above {DXY_THRESHOLD:.0f} is bearish and below {DXY_THRESHOLD:.0f} is bullish.",
        f"10-year Treasury yield is {ten_year_yield:.2f}; above {TEN_YEAR_YIELD_THRESHOLD:.1f} is bearish and below {TEN_YEAR_YIELD_THRESHOLD:.1f} is bullish.",
        "Both bearish makes this signal bearish; one bearish makes it neutral; both bullish makes it bullish.",
    ]

    return MarketSignal(
        name="Dollar/yield shock",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "dxy": round(dxy, 4),
            "dxy_threshold": DXY_THRESHOLD,
            "ten_year_yield": round(ten_year_yield, 4),
            "ten_year_yield_threshold": TEN_YEAR_YIELD_THRESHOLD,
        },
    )


def write_market_condition(report: MarketConditionReport) -> None:
    payload = asdict(report)
    payload["generated_at"] = report.generated_at.isoformat(timespec="seconds") + "Z"
    store.set_market_condition_report(report.ticker, payload, payload["generated_at"])


def evaluate_demark(ticker: str, bars: List[PriceBar]) -> DeMarkSignal:
    if len(bars) < 20:
        return DeMarkSignal(
            ticker=ticker,
            score=50.0,
            regime="neutral",
            setup_side="none",
            setup_count=0,
            countdown_side="none",
            countdown_count=0,
            exhaustion="insufficient data",
            reasons=["DeMark evaluation needs at least 20 daily bars."],
        )

    setup_side, setup_count = _current_setup(bars)
    countdown_side, countdown_count = _current_countdown(bars)
    score, regime, exhaustion = _score_demark(setup_side, setup_count, countdown_side, countdown_count)

    reasons = [
        f"Current setup: {setup_side} {setup_count}/9.",
        f"Current countdown: {countdown_side} {countdown_count}/13.",
        _interpretation(setup_side, setup_count, countdown_side, countdown_count),
    ]

    return DeMarkSignal(
        ticker=ticker,
        score=score,
        regime=regime,
        setup_side=setup_side,
        setup_count=setup_count,
        countdown_side=countdown_side,
        countdown_count=countdown_count,
        exhaustion=exhaustion,
        reasons=reasons,
    )


def _current_setup(bars: List[PriceBar]) -> Tuple[str, int]:
    sell_count = 0
    buy_count = 0

    for index in range(4, len(bars)):
        close = bars[index].close
        prior_close = bars[index - 4].close
        if close is None or prior_close is None:
            sell_count = 0
            buy_count = 0
            continue

        if close > prior_close:
            sell_count += 1
            buy_count = 0
        elif close < prior_close:
            buy_count += 1
            sell_count = 0
        else:
            sell_count = 0
            buy_count = 0

        sell_count = min(sell_count, 9)
        buy_count = min(buy_count, 9)

    if sell_count:
        return "sell", sell_count
    if buy_count:
        return "buy", buy_count
    return "none", 0


def _current_countdown(bars: List[PriceBar]) -> Tuple[str, int]:
    last_completed_side: Optional[str] = None
    last_completed_index: Optional[int] = None
    sell_setup = 0
    buy_setup = 0

    for index in range(4, len(bars)):
        close = bars[index].close
        prior_close = bars[index - 4].close
        if close is None or prior_close is None:
            sell_setup = 0
            buy_setup = 0
            continue

        if close > prior_close:
            sell_setup = min(sell_setup + 1, 9)
            buy_setup = 0
        elif close < prior_close:
            buy_setup = min(buy_setup + 1, 9)
            sell_setup = 0
        else:
            sell_setup = 0
            buy_setup = 0

        if sell_setup == 9:
            last_completed_side = "sell"
            last_completed_index = index
        elif buy_setup == 9:
            last_completed_side = "buy"
            last_completed_index = index

    if last_completed_side is None or last_completed_index is None:
        return "none", 0

    count = 0
    for index in range(last_completed_index + 1, len(bars)):
        close = bars[index].close
        comparison_bar = bars[index - 2] if index >= 2 else None
        if close is None or comparison_bar is None:
            continue
        if last_completed_side == "sell" and comparison_bar.high is not None and close >= comparison_bar.high:
            count += 1
        elif last_completed_side == "buy" and comparison_bar.low is not None and close <= comparison_bar.low:
            count += 1
        count = min(count, 13)

    return last_completed_side, count


def _score_demark(setup_side: str, setup_count: int, countdown_side: str, countdown_count: int):
    score = 50.0

    if setup_side == "sell":
        score -= min(18.0, setup_count * 2.0)
    elif setup_side == "buy":
        score += min(18.0, setup_count * 2.0)

    if countdown_side == "sell":
        score -= min(32.0, countdown_count * 2.5)
    elif countdown_side == "buy":
        score += min(32.0, countdown_count * 2.5)

    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 60:
        regime = "bullish"
    elif score <= 40:
        regime = "bearish"
    else:
        regime = "neutral"

    if countdown_count >= 13:
        exhaustion = f"{countdown_side} countdown exhaustion"
    elif setup_count >= 9:
        exhaustion = f"{setup_side} setup exhaustion"
    else:
        exhaustion = "no completed exhaustion signal"

    return score, regime, exhaustion


def _interpretation(setup_side: str, setup_count: int, countdown_side: str, countdown_count: int) -> str:
    if countdown_count >= 13:
        return f"Completed {countdown_side} countdown suggests structural trend exhaustion."
    if setup_count >= 9:
        return f"Completed {setup_side} setup suggests short-term trend exhaustion."
    if setup_side in {"buy", "sell"}:
        return f"{setup_side.title()} setup is developing but has not completed."
    if countdown_side in {"buy", "sell"} and countdown_count:
        return f"{countdown_side.title()} countdown is active but incomplete."
    return "No active DeMark exhaustion signal."


def _aligned_close_ratios(numerator_bars: List[PriceBar], denominator_bars: List[PriceBar]) -> List[float]:
    denominator_by_time = {
        bar.timestamp: bar.close
        for bar in denominator_bars
        if bar.close not in (None, 0)
    }
    ratios = []
    for bar in numerator_bars:
        denominator_close = denominator_by_time.get(bar.timestamp)
        if bar.close is None or denominator_close in (None, 0):
            continue
        ratios.append(bar.close / denominator_close)
    return ratios


def _closes(bars: List[PriceBar]) -> List[float]:
    return [bar.close for bar in bars if bar.close is not None]


def _average(values: List[float]) -> Optional[float]:
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return None
    return sum(clean_values) / len(clean_values)


def _period_return(values: List[float], days: int) -> Optional[float]:
    if len(values) <= days:
        return None
    starting_value = values[-(days + 1)]
    ending_value = values[-1]
    if starting_value == 0:
        return None
    return ((ending_value - starting_value) / starting_value) * 100


def _ratio_percent(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator in (None, 0):
        return None
    return ((numerator / denominator) - 1) * 100


def _scaled_component(value: Optional[float], full_at: float, floor_at: float, weight: float) -> float:
    if value is None:
        return 0.0
    if value >= full_at:
        return weight
    if value <= floor_at:
        return 0.0
    position = (value - floor_at) / (full_at - floor_at)
    return max(0.0, min(weight, position * weight))


def _inverted_scaled_component(value: Optional[float], full_at: float, floor_at: float, weight: float) -> float:
    if value is None:
        return 0.0
    if value <= full_at:
        return weight
    if value >= floor_at:
        return 0.0
    position = (floor_at - value) / (floor_at - full_at)
    return max(0.0, min(weight, position * weight))


def _format_ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f}%"

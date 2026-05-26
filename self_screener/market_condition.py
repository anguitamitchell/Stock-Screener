import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from self_screener.models import DeMarkSignal, MarketConditionReport, MarketSignal, PriceBar
from self_screener.providers.yahoo_prices import fetch_price_history


REPORT_DIR = Path("data") / "reports"


def build_market_condition(ticker: str = "SPY", refresh: bool = False) -> MarketConditionReport:
    symbol = ticker.upper()
    bars = fetch_price_history(symbol, refresh=refresh)
    equal_weight_bars = fetch_price_history("RSP", refresh=refresh)
    tech_bars = fetch_price_history("QQQ", refresh=refresh)
    utilities_bars = fetch_price_history("XLU", refresh=refresh)
    staples_bars = fetch_price_history("XLP", refresh=refresh)
    high_yield_bars = fetch_price_history("HYG", refresh=refresh)
    aggregate_bond_bars = fetch_price_history("AGG", refresh=refresh)
    dollar_bars = fetch_price_history("UUP", refresh=refresh)
    treasury_yield_bars = fetch_price_history("^TNX", refresh=refresh)
    demark = evaluate_demark(symbol, bars)
    participation = evaluate_market_participation(equal_weight_bars, bars)
    technology = evaluate_technology_support(tech_bars, bars)
    defensive = evaluate_defensive_rotation(utilities_bars, staples_bars, bars)
    credit = evaluate_credit_risk(high_yield_bars, aggregate_bond_bars)
    shock = evaluate_dollar_yield_shock(dollar_bars, treasury_yield_bars)
    overall_score, overall_regime, summary = _combine_market_signals(
        demark,
        participation,
        technology,
        defensive,
        credit,
        shock,
    )

    return MarketConditionReport(
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


def _combine_market_signals(
    demark: DeMarkSignal,
    participation: MarketSignal,
    technology: MarketSignal,
    defensive: MarketSignal,
    credit: MarketSignal,
    shock: MarketSignal,
):
    score = round(
        (demark.score * 0.28)
        + (participation.score * 0.10)
        + (technology.score * 0.30)
        + (defensive.score * 0.12)
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


def evaluate_market_participation(equal_weight_bars: List[PriceBar], cap_weight_bars: List[PriceBar]) -> MarketSignal:
    ratio_values = _aligned_close_ratios(equal_weight_bars, cap_weight_bars)
    if not ratio_values:
        return MarketSignal(
            name="RSP/SPY market participation",
            score=50.0,
            regime="neutral",
            reasons=["Market participation needs current RSP and SPY closes."],
            metrics={},
        )

    current_ratio = ratio_values[-1]
    ratio_gap = current_ratio - 1.0
    score = round(max(0.0, min(100.0, 50.0 + (ratio_gap * 50.0))), 1)

    if current_ratio > 1:
        regime = "bullish"
    elif current_ratio < 1:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"Current RSP/SPY is {current_ratio:.3f}.",
        "Above 1.000 means more market participation and is bullish; below 1.000 means less market participation and is bearish.",
    ]

    return MarketSignal(
        name="RSP/SPY market participation",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "current_ratio": round(current_ratio, 6),
            "ratio_gap": round(ratio_gap, 6),
        },
    )


def evaluate_structural_weakness(equal_weight_bars: List[PriceBar], cap_weight_bars: List[PriceBar]) -> MarketSignal:
    return evaluate_market_participation(equal_weight_bars, cap_weight_bars)


def evaluate_technology_support(tech_bars: List[PriceBar], benchmark_bars: List[PriceBar]) -> MarketSignal:
    tech_closes = _closes(tech_bars)
    relative_values = _aligned_close_ratios(tech_bars, benchmark_bars)
    if len(tech_closes) < 200 or len(relative_values) < 60:
        return MarketSignal(
            name="QQQ technology support",
            score=50.0,
            regime="neutral",
            reasons=["Technology support signal needs at least 200 QQQ bars and 60 relative-strength bars."],
            metrics={},
        )

    current_close = tech_closes[-1]
    sma_50 = _average(tech_closes[-50:])
    sma_200 = _average(tech_closes[-200:])
    close_vs_200 = _ratio_percent(current_close, sma_200)
    sma_50_vs_200 = _ratio_percent(sma_50, sma_200)
    return_20d = _period_return(tech_closes, 20)
    return_60d = _period_return(tech_closes, 60)
    relative_return_60d = _period_return(relative_values, 60)

    score = 50.0
    score += _scaled_component(close_vs_200, full_at=8, floor_at=-8, weight=25)
    score += _scaled_component(sma_50_vs_200, full_at=4, floor_at=-4, weight=20)
    score += _scaled_component(return_20d, full_at=6, floor_at=-6, weight=15)
    score += _scaled_component(return_60d, full_at=12, floor_at=-12, weight=15)
    score += _scaled_component(relative_return_60d, full_at=5, floor_at=-5, weight=15)
    score -= 45.0
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 60:
        regime = "bullish"
    elif score <= 40:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"QQQ is {_format_ratio(close_vs_200)} vs its 200D average.",
        f"QQQ 50D/200D trend is {_format_ratio(sma_50_vs_200)}.",
        f"QQQ trend: 1M {_format_ratio(return_20d)}, 3M {_format_ratio(return_60d)}, 3M vs SPY {_format_ratio(relative_return_60d)}.",
    ]

    return MarketSignal(
        name="QQQ technology support",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "last_close": round(current_close, 4),
            "sma_50": round(sma_50, 4) if sma_50 is not None else None,
            "sma_200": round(sma_200, 4) if sma_200 is not None else None,
            "close_vs_200d_percent": close_vs_200,
            "sma_50_vs_200d_percent": sma_50_vs_200,
            "return_20d_percent": return_20d,
            "return_60d_percent": return_60d,
            "relative_return_60d_percent": relative_return_60d,
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


def evaluate_credit_risk(high_yield_bars: List[PriceBar], aggregate_bond_bars: List[PriceBar]) -> MarketSignal:
    credit_ratio = _aligned_close_ratios(high_yield_bars, aggregate_bond_bars)
    if not credit_ratio:
        return MarketSignal(
            name="HYG/AGG credit risk",
            score=50.0,
            regime="neutral",
            reasons=["Credit risk signal needs current HYG and AGG closes."],
            metrics={},
        )

    current_ratio = credit_ratio[-1]
    ratio_gap = current_ratio - 1.0
    score = round(max(0.0, min(100.0, 50.0 - (ratio_gap * 50.0))), 1)

    if current_ratio < 1:
        regime = "bullish"
    elif current_ratio > 1:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"Current HYG/AGG is {current_ratio:.3f}.",
        "Below 1.000 is bullish for credit risk appetite; above 1.000 is bearish.",
    ]

    return MarketSignal(
        name="HYG/AGG credit risk",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "current_ratio": round(current_ratio, 6),
            "ratio_gap": round(ratio_gap, 6),
        },
    )


def evaluate_dollar_yield_shock(dollar_bars: List[PriceBar], treasury_yield_bars: List[PriceBar]) -> MarketSignal:
    dollar_closes = _closes(dollar_bars)
    yield_closes = _closes(treasury_yield_bars)
    if len(dollar_closes) < 60 or len(yield_closes) < 60:
        return MarketSignal(
            name="Dollar/yield shock",
            score=50.0,
            regime="neutral",
            reasons=["Dollar/yield shock needs at least 60 bars for UUP and ^TNX."],
            metrics={},
        )

    dollar_20d = _period_return(dollar_closes, 20)
    dollar_60d = _period_return(dollar_closes, 60)
    yield_20d = _period_return(yield_closes, 20)
    yield_60d = _period_return(yield_closes, 60)
    shock_20d = _average([dollar_20d, yield_20d])
    shock_60d = _average([dollar_60d, yield_60d])

    score = 50.0
    score += _inverted_scaled_component(shock_20d, full_at=-3, floor_at=5, weight=25)
    score += _inverted_scaled_component(shock_60d, full_at=-5, floor_at=10, weight=25)
    score -= 25.0
    score = round(max(0.0, min(100.0, score)), 1)

    if score >= 60:
        regime = "bullish"
    elif score <= 40:
        regime = "bearish"
    else:
        regime = "neutral"

    reasons = [
        f"UUP trend: 1M {_format_ratio(dollar_20d)}, 3M {_format_ratio(dollar_60d)}.",
        f"10Y yield proxy trend: 1M {_format_ratio(yield_20d)}, 3M {_format_ratio(yield_60d)}.",
        f"Combined shock pressure: 1M {_format_ratio(shock_20d)}, 3M {_format_ratio(shock_60d)}.",
    ]

    return MarketSignal(
        name="Dollar/yield shock",
        score=score,
        regime=regime,
        reasons=reasons,
        metrics={
            "uup_return_20d_percent": dollar_20d,
            "uup_return_60d_percent": dollar_60d,
            "tnx_return_20d_percent": yield_20d,
            "tnx_return_60d_percent": yield_60d,
            "combined_shock_20d_percent": shock_20d,
            "combined_shock_60d_percent": shock_60d,
        },
    )


def write_market_condition(report: MarketConditionReport) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{report.ticker}_market_condition.json"
    payload = asdict(report)
    payload["generated_at"] = report.generated_at.isoformat(timespec="seconds") + "Z"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


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

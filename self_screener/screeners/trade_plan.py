from typing import Optional

from self_screener.models import PriceSnapshot, TradePlan


def build_trade_plan(price: Optional[PriceSnapshot], rating: str, technical_label: str) -> TradePlan:
    if price is None or price.error or price.last_close is None:
        return TradePlan(
            label="unavailable",
            notes=["Entry and exit levels need a valid price snapshot."],
        )

    close = price.last_close
    atr = price.atr_14 or max(close * 0.025, 0.01)
    support = _nearest_support(price)
    resistance = _nearest_resistance(price)
    entry_low, entry_high, breakout_trigger = _entry_levels(price, rating, technical_label, atr, support, resistance)
    stop_loss = _stop_level(entry_low, support, atr)
    target_1, target_2 = _target_levels(entry_high, stop_loss, resistance, atr)
    reward_risk = _reward_risk(entry_high, stop_loss, target_1)

    label = _plan_label(rating, technical_label, close, entry_low, entry_high)
    notes = [
        _entry_note(label, entry_low, entry_high, breakout_trigger),
        _exit_note(stop_loss, target_1, target_2),
        "Levels are mechanical estimates from delayed technical data, not a guaranteed fill or recommendation.",
    ]

    return TradePlan(
        entry_low=_round_price(entry_low),
        entry_high=_round_price(entry_high),
        breakout_trigger=_round_price(breakout_trigger),
        stop_loss=_round_price(stop_loss),
        target_1=_round_price(target_1),
        target_2=_round_price(target_2),
        reward_risk=round(reward_risk, 2) if reward_risk is not None else None,
        label=label,
        notes=notes,
    )


def _entry_levels(
    price: PriceSnapshot,
    rating: str,
    technical_label: str,
    atr: float,
    support: Optional[float],
    resistance: Optional[float],
) -> tuple:
    close = price.last_close or 0
    if rating == "Buy" and technical_label in {"bullish", "constructive"}:
        anchor = support or price.sma_20 or close
        entry_low = min(close, max(anchor, close - atr))
        entry_high = min(close * 1.01, entry_low + atr)
        breakout_trigger = (resistance + 0.1 * atr) if resistance and resistance > close else close + 0.5 * atr
        return entry_low, entry_high, breakout_trigger

    if rating == "Sell" or technical_label == "weak":
        reclaim_level = _first_above(close, [price.sma_20, price.sma_50, price.sma_200])
        entry_low = reclaim_level or close + atr
        entry_high = entry_low + atr
        breakout_trigger = entry_high
        return entry_low, entry_high, breakout_trigger

    anchor = price.sma_20 or price.sma_50 or support or close
    entry_low = min(close, anchor)
    entry_high = max(entry_low, min(close * 1.01, entry_low + atr))
    breakout_trigger = (resistance + 0.1 * atr) if resistance and resistance > close else entry_high
    return entry_low, entry_high, breakout_trigger


def _nearest_support(price: PriceSnapshot) -> Optional[float]:
    close = price.last_close
    candidates = [price.sma_20, price.sma_50, price.sma_200, price.low_20d, price.low_60d]
    below = [value for value in candidates if value is not None and (close is None or value <= close)]
    if below:
        return max(below)
    clean = [value for value in candidates if value is not None]
    return min(clean) if clean else None


def _nearest_resistance(price: PriceSnapshot) -> Optional[float]:
    close = price.last_close
    candidates = [price.high_20d, price.high_60d, price.sma_20, price.sma_50, price.sma_200]
    above = [value for value in candidates if value is not None and (close is None or value >= close)]
    if above:
        return min(above)
    clean = [value for value in candidates if value is not None]
    return max(clean) if clean else None


def _stop_level(entry_low: float, support: Optional[float], atr: float) -> float:
    support_stop = support - (0.35 * atr) if support is not None else entry_low - atr
    volatility_stop = entry_low - (1.25 * atr)
    return max(support_stop, volatility_stop)


def _target_levels(entry_high: float, stop_loss: float, resistance: Optional[float], atr: float) -> tuple:
    risk = max(entry_high - stop_loss, 0.01)
    risk_target = entry_high + min(2 * risk, 4 * atr)
    target_1 = max(resistance or 0, risk_target)
    target_2 = entry_high + min(3 * risk, 6 * atr)
    return target_1, max(target_2, target_1)


def _reward_risk(entry_high: float, stop_loss: float, target_1: float) -> Optional[float]:
    risk = entry_high - stop_loss
    reward = target_1 - entry_high
    if risk <= 0:
        return None
    return reward / risk


def _plan_label(rating: str, technical_label: str, close: float, entry_low: float, entry_high: float) -> str:
    if rating == "Sell" or technical_label == "weak":
        return "wait for reclaim"
    if entry_low <= close <= entry_high:
        return "near entry"
    if close > entry_high:
        return "wait for pullback"
    return "watch breakout"


def _entry_note(label: str, entry_low: float, entry_high: float, breakout_trigger: float) -> str:
    return (
        f"{label.title()}: preferred entry zone is ${_round_price(entry_low)}-${_round_price(entry_high)}; "
        f"breakout confirmation above ${_round_price(breakout_trigger)}."
    )


def _exit_note(stop_loss: float, target_1: float, target_2: float) -> str:
    return (
        f"Risk exit below ${_round_price(stop_loss)}; target exits near "
        f"${_round_price(target_1)} and ${_round_price(target_2)}."
    )


def _first_above(close: float, values: list) -> Optional[float]:
    above = [value for value in values if value is not None and value > close]
    return min(above) if above else None


def _round_price(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 2)

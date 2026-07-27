"""Per-lot verdicts: was this buy a good entry, and what should you do now?

Pure functions over already-loaded data — the API layer supplies the
screener rows (entry-time snapshot row and latest report row for the
ticker), the current quote, and optional price-history bars. Every input is
optional; missing data degrades to a labeled partial verdict, never an
exception.

Entry grades (vs the trade plan in effect when the lot was bought):
    in_zone         bought inside the plan's entry zone (disciplined)
    breakout_entry  bought at/above the breakout trigger (acceptable)
    chased          bought between the zone and the trigger (paid up
                    without confirmation)
    below_zone      bought below the entry zone (deep pullback / knife)
    below_stop      bought at/below the stop (plan already invalidated)
    no_screen_data  no plan available for this ticker
"""

from datetime import datetime, timezone
from typing import List, Optional

ENTRY_LABELS = {
    "in_zone": "Disciplined entry",
    "breakout_entry": "Breakout entry",
    "chased": "Chased above the zone",
    "below_zone": "Bought below the zone",
    "below_stop": "Bought below the stop",
    "no_screen_data": "No screen data",
    "unclassified": "Unclassified entry",
}

STANCE_LABELS = {
    "stop_breached": "Stop breached — exit signal",
    "take_profit": "Target 2 reached — take profit",
    "trim_or_raise_stop": "Target 1 reached — trim or raise stop",
    "sell_signal": "Screener rates this a Sell",
    "watch_downgrade": "Rating downgraded since entry — watch",
    "hold": "Hold",
    "unknown": "No current quote",
}

RATING_ORDER = {"Buy": 2, "Hold": 1, "Sell": 0}


def review_lot(
    trade: dict,
    entry_row: Optional[dict] = None,
    latest_row: Optional[dict] = None,
    quote_price: Optional[float] = None,
    ticker_bars: Optional[List[dict]] = None,
    spy_bars: Optional[List[dict]] = None,
) -> dict:
    """trade needs {price, trade_date}; rows are rated_holding dicts from a
    screen report (holding/semantic/technical/trade_plan/rating/final_score)."""
    entry_price = float(trade["price"])

    plan_row, plan_source = _pick_plan_row(entry_row, latest_row)
    plan = (plan_row or {}).get("trade_plan") or None
    entry_grade, entry_detail = _grade_entry(entry_price, plan)

    rating_then = (entry_row or {}).get("rating")
    rating_now = (latest_row or {}).get("rating")
    score_then = (entry_row or {}).get("final_score")
    score_now = (latest_row or {}).get("final_score")

    entry_label = ENTRY_LABELS.get(entry_grade, entry_grade)
    if entry_grade == "in_zone" and rating_then == "Buy":
        entry_detail = f"{entry_detail} The screener rated it a Buy at the time — disciplined entry."
    elif entry_grade == "in_zone" and rating_then == "Sell":
        entry_label = "In zone, against the rating"
        entry_detail = f"{entry_detail} But the screener rated it a Sell at the time."

    latest_plan = (latest_row or {}).get("trade_plan") or None
    stance, stance_reasons = _current_stance(quote_price, latest_plan, rating_then, rating_now)

    stop_at_entry = _level(plan, "stop_loss")
    r_multiple = None
    if quote_price is not None and stop_at_entry is not None and entry_price > stop_at_entry:
        r_multiple = round((quote_price - entry_price) / (entry_price - stop_at_entry), 2)

    pl_percent = (
        round(((quote_price - entry_price) / entry_price) * 100, 2)
        if quote_price is not None and entry_price
        else None
    )

    return {
        "entry_grade": entry_grade,
        "entry_label": entry_label,
        "entry_detail": entry_detail,
        "plan_source": plan_source,
        "plan": plan,
        "stance": stance,
        "stance_label": STANCE_LABELS.get(stance, stance),
        "stance_reasons": stance_reasons,
        "rating_then": rating_then,
        "rating_now": rating_now,
        "score_then": score_then,
        "score_now": score_now,
        "score_change": (
            round(score_now - score_then, 1) if score_then is not None and score_now is not None else None
        ),
        "pl_percent": pl_percent,
        "r_multiple": r_multiple,
        "vs_spy_percent": vs_benchmark_percent(trade.get("trade_date"), ticker_bars, spy_bars),
    }


def _pick_plan_row(entry_row: Optional[dict], latest_row: Optional[dict]):
    if entry_row and entry_row.get("trade_plan"):
        return entry_row, "snapshot"
    if latest_row and latest_row.get("trade_plan"):
        return latest_row, "latest"
    return None, "none"


def _grade_entry(price: float, plan: Optional[dict]):
    if not plan:
        return "no_screen_data", "No trade plan was available for this ticker, so the entry can't be graded."

    stop = _level(plan, "stop_loss")
    entry_low = _level(plan, "entry_low")
    entry_high = _level(plan, "entry_high")
    trigger = _level(plan, "breakout_trigger")

    if stop is not None and price <= stop:
        return "below_stop", (
            f"Entry at {price:g} was at or below the plan's stop ({stop:g}) — the setup was already invalidated."
        )
    if entry_low is not None and entry_high is not None and entry_low <= price <= entry_high:
        return "in_zone", f"Entry at {price:g} landed inside the plan's entry zone ({entry_low:g}–{entry_high:g})."
    if trigger is not None and price >= trigger:
        return "breakout_entry", (
            f"Entry at {price:g} was at or above the breakout trigger ({trigger:g}) — momentum-confirmed, but with less cushion."
        )
    if entry_high is not None and trigger is not None and entry_high < price < trigger:
        return "chased", (
            f"Entry at {price:g} was above the zone (≤{entry_high:g}) but below the trigger ({trigger:g}) — paid up without confirmation."
        )
    if entry_low is not None and price < entry_low:
        return "below_zone", (
            f"Entry at {price:g} was below the entry zone ({entry_low:g}–{entry_high:g}) — a deeper pullback than the plan wanted."
        )
    return "unclassified", "The plan was missing the levels needed to grade this entry."


def _current_stance(
    quote: Optional[float],
    plan: Optional[dict],
    rating_then: Optional[str],
    rating_now: Optional[str],
):
    if quote is None:
        return "unknown", ["No current quote is available for this ticker."]

    reasons = []
    stop = _level(plan, "stop_loss")
    target_1 = _level(plan, "target_1")
    target_2 = _level(plan, "target_2")

    if stop is not None and quote <= stop:
        return "stop_breached", [f"Price {quote:g} is at or below the plan's stop ({stop:g})."]
    if target_2 is not None and quote >= target_2:
        return "take_profit", [f"Price {quote:g} has reached target 2 ({target_2:g})."]
    if target_1 is not None and quote >= target_1:
        return "trim_or_raise_stop", [f"Price {quote:g} has reached target 1 ({target_1:g})."]
    if rating_now == "Sell":
        return "sell_signal", ["The latest screen rates this holding a Sell."]
    if (
        rating_then in RATING_ORDER
        and rating_now in RATING_ORDER
        and RATING_ORDER[rating_now] < RATING_ORDER[rating_then]
    ):
        return "watch_downgrade", [f"Rating slipped from {rating_then} at entry to {rating_now} now."]

    if stop is not None:
        reasons.append(f"Price {quote:g} is above the stop ({stop:g}).")
    if target_1 is not None:
        reasons.append(f"Target 1 ({target_1:g}) has not been reached yet.")
    if rating_now:
        reasons.append(f"The latest screen rates it {rating_now}.")
    if not reasons:
        reasons.append("No exit condition has triggered.")
    return "hold", reasons


def vs_benchmark_percent(
    trade_date: Optional[str],
    ticker_bars: Optional[List[dict]],
    benchmark_bars: Optional[List[dict]],
) -> Optional[float]:
    """Ticker return minus benchmark return from the first bar on/after the
    trade date through the latest bar. None (with no error) when either
    history doesn't reach back to the trade date."""
    if not trade_date or not ticker_bars or not benchmark_bars:
        return None
    try:
        start = datetime.strptime(trade_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None

    ticker_return = _return_since(ticker_bars, start)
    benchmark_return = _return_since(benchmark_bars, start)
    if ticker_return is None or benchmark_return is None:
        return None
    return round(ticker_return - benchmark_return, 2)


def _return_since(bars: List[dict], start_timestamp: float) -> Optional[float]:
    closes = [
        bar.get("close")
        for bar in bars
        if bar.get("timestamp") is not None and bar.get("close") is not None and bar["timestamp"] >= start_timestamp
    ]
    if len(closes) < 2:
        return None
    # If the trade predates the cached history window, the "first" bar would
    # silently be the window start, overstating the comparison — detect that
    # by checking the earliest cached bar is actually before the trade date.
    earliest = min(bar["timestamp"] for bar in bars if bar.get("timestamp") is not None)
    if earliest > start_timestamp + 5 * 24 * 3600:
        return None
    return ((closes[-1] - closes[0]) / closes[0]) * 100


def _level(plan: Optional[dict], key: str) -> Optional[float]:
    if not plan:
        return None
    value = plan.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None

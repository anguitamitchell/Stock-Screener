import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from typing import Optional

from self_screener.storage import store

BREADTH_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
BREADTH_SERIES_KEY = "barchart:R3TW"
RUSSELL_3000_ABOVE_20D_SYMBOL = "$R3TW"
RUSSELL_3000_ABOVE_20D_URL = "https://www.barchart.com/stocks/quotes/%24R3TW/overview"


@dataclass(frozen=True)
class BreadthObservation:
    symbol: str
    value: Optional[float] = None
    trade_time: str = ""
    previous_close: Optional[float] = None
    weekly_high: Optional[float] = None
    weekly_low: Optional[float] = None
    monthly_high: Optional[float] = None
    monthly_low: Optional[float] = None
    source: str = "Barchart: Russell 3000 Stocks Above 20-Day Average"
    error: Optional[str] = None


def fetch_russell_3000_above_20d(refresh: bool = False) -> BreadthObservation:
    if not refresh:
        cached = store.get_macro_observation(BREADTH_SERIES_KEY, max_age_seconds=BREADTH_CACHE_MAX_AGE_SECONDS)
        if cached is not None:
            return BreadthObservation(**cached)

    try:
        observation = _fetch_barchart_observation()
    except Exception as exc:
        cached = store.get_macro_observation_any(BREADTH_SERIES_KEY)
        if cached is not None and cached.get("value") is not None:
            return BreadthObservation(**cached)
        return BreadthObservation(symbol=RUSSELL_3000_ABOVE_20D_SYMBOL, error=str(exc))

    store.set_macro_observation(BREADTH_SERIES_KEY, asdict(observation))
    return observation


def _fetch_barchart_observation() -> BreadthObservation:
    request = urllib.request.Request(
        RUSSELL_3000_ABOVE_20D_URL,
        headers={
            "User-Agent": "Mozilla/5.0 SignalDeck ETF Screener/0.1",
            "Accept": "text/html",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8", "ignore")

    value = _extract_float(text, r'"lastPrice":"(-?\d+(?:\.\d+)?)"')
    if value is None:
        value = _extract_float(text, r'&quot;lastPrice&quot;:&quot;(-?\d+(?:\.\d+)?)&quot;')

    quote = _extract_inline_quote(text)
    previous_close = _float_or_none(quote.get("previousClose"))
    weekly_high = _float_or_none(quote.get("weeklyPreviousHigh"))
    weekly_low = _float_or_none(quote.get("weeklyPreviousLow"))
    monthly_high = _float_or_none(quote.get("monthlyPreviousHigh"))
    monthly_low = _float_or_none(quote.get("monthlyPreviousLow"))
    trade_time = str(quote.get("tradeTime") or _extract_text(text, r'"tradeTime":"([^"]+)"') or "")

    if value is None:
        raise ValueError("Could not find latest $R3TW breadth value on Barchart.")

    return BreadthObservation(
        symbol=RUSSELL_3000_ABOVE_20D_SYMBOL,
        value=round(value, 4),
        trade_time=trade_time,
        previous_close=previous_close,
        weekly_high=weekly_high,
        weekly_low=weekly_low,
        monthly_high=monthly_high,
        monthly_low=monthly_low,
    )


def _extract_inline_quote(text: str) -> dict:
    match = re.search(r'id="barchart-www-inline-data">(\{.*?\})</script>', text, re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload.get(RUSSELL_3000_ABOVE_20D_SYMBOL, {}).get("quote", {}) or {}


def _extract_float(text: str, pattern: str) -> Optional[float]:
    value = _extract_text(text, pattern)
    return _float_or_none(value)


def _extract_text(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(1) if match else None


def _float_or_none(value) -> Optional[float]:
    if value in (None, "", "N/A", "-"):
        return None
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None



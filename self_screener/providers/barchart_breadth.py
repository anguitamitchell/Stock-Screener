import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


CACHE_DIR = Path("data") / "cache" / "breadth"
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
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "R3TW.json"

    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    try:
        observation = _fetch_barchart_observation()
    except Exception as exc:
        if cache_path.exists():
            cached = _read_cache(cache_path)
            if cached.value is not None:
                return cached
        return BreadthObservation(symbol=RUSSELL_3000_ABOVE_20D_SYMBOL, error=str(exc))

    _write_cache(cache_path, observation)
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


def _read_cache(path: Path) -> BreadthObservation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BreadthObservation(**payload)


def _write_cache(path: Path, observation: BreadthObservation) -> None:
    path.write_text(json.dumps(asdict(observation), indent=2) + "\n", encoding="utf-8")

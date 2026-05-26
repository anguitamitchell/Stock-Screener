import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from self_screener.models import PriceBar, PriceSnapshot


CACHE_DIR = Path("data") / "cache" / "prices"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class PriceFetchError(RuntimeError):
    """Raised when historical price data cannot be fetched or parsed."""


def fetch_price_snapshot(ticker: str, refresh: bool = False, sleep_seconds: float = 0.0) -> PriceSnapshot:
    normalized = _normalize_ticker(ticker)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{normalized}.json"

    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    if sleep_seconds:
        time.sleep(sleep_seconds)

    try:
        payload = _fetch_chart_payload(normalized)
        snapshot = _snapshot_from_payload(normalized, payload)
    except Exception as exc:
        snapshot = PriceSnapshot(
            ticker=normalized,
            source="Yahoo Finance chart endpoint",
            error=str(exc),
        )

    _write_cache(cache_path, snapshot)
    return snapshot


def fetch_price_history(ticker: str, refresh: bool = False, sleep_seconds: float = 0.0) -> List[PriceBar]:
    normalized = _normalize_ticker(ticker)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{normalized}_history.json"

    if cache_path.exists() and not refresh:
        return _read_history_cache(cache_path)

    if sleep_seconds:
        time.sleep(sleep_seconds)

    payload = _fetch_chart_payload(normalized)
    bars = _history_from_payload(payload)
    _write_history_cache(cache_path, bars)
    return bars


def _fetch_chart_payload(ticker: str) -> Dict:
    query = urllib.parse.urlencode(
        {
            "range": "1y",
            "interval": "1d",
            "includePrePost": "false",
            "events": "history",
        }
    )
    url = f"{YAHOO_CHART_URL.format(ticker=urllib.parse.quote(ticker))}?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ETF Screener Prototype",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _snapshot_from_payload(ticker: str, payload: Dict) -> PriceSnapshot:
    chart = payload.get("chart", {})
    error = chart.get("error")
    if error:
        raise PriceFetchError(error.get("description") or str(error))

    results = chart.get("result") or []
    if not results:
        raise PriceFetchError("No chart result returned.")

    result = results[0]
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    clean_highs = [float(value) for value in highs if value is not None]
    clean_lows = [float(value) for value in lows if value is not None]
    clean_closes = [float(value) for value in closes if value is not None]
    clean_volumes = [float(value) for value in volumes if value is not None]

    if not clean_closes:
        raise PriceFetchError("No close prices returned.")

    last_close = clean_closes[-1]
    previous_close = clean_closes[-2] if len(clean_closes) >= 2 else None

    return PriceSnapshot(
        ticker=ticker,
        last_close=round(last_close, 4),
        previous_close=round(previous_close, 4) if previous_close is not None else None,
        change_percent=_percent_change(last_close, previous_close),
        average_volume_20d=_average(clean_volumes[-20:]),
        sma_20=_average(clean_closes[-20:]),
        sma_50=_average(clean_closes[-50:]),
        sma_200=_average(clean_closes[-200:]),
        close_vs_sma_20_percent=_close_vs_average(last_close, clean_closes[-20:]),
        close_vs_sma_50_percent=_close_vs_average(last_close, clean_closes[-50:]),
        close_vs_sma_200_percent=_close_vs_average(last_close, clean_closes[-200:]),
        return_5d_percent=_period_return(clean_closes, 5),
        return_20d_percent=_period_return(clean_closes, 20),
        return_60d_percent=_period_return(clean_closes, 60),
        return_120d_percent=_period_return(clean_closes, 120),
        low_20d=_min_value(clean_lows[-20:]),
        high_20d=_max_value(clean_highs[-20:]),
        low_60d=_min_value(clean_lows[-60:]),
        high_60d=_max_value(clean_highs[-60:]),
        atr_14=_atr(highs, lows, closes, 14),
        trading_days=len(timestamps),
        source="Yahoo Finance chart endpoint",
    )


def _history_from_payload(payload: Dict) -> List[PriceBar]:
    chart = payload.get("chart", {})
    error = chart.get("error")
    if error:
        raise PriceFetchError(error.get("description") or str(error))

    results = chart.get("result") or []
    if not results:
        raise PriceFetchError("No chart result returned.")

    result = results[0]
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    timestamps = result.get("timestamp") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []

    bars: List[PriceBar] = []
    for index, timestamp in enumerate(timestamps):
        close = _indexed_value(closes, index)
        high = _indexed_value(highs, index)
        low = _indexed_value(lows, index)
        if close is None or high is None or low is None:
            continue
        bars.append(
            PriceBar(
                timestamp=int(timestamp),
                open=_indexed_value(opens, index),
                high=high,
                low=low,
                close=close,
                volume=_indexed_value(volumes, index),
            )
        )

    if not bars:
        raise PriceFetchError("No complete price bars returned.")
    return bars


def _average(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _min_value(values: List[float]) -> Optional[float]:
    return round(min(values), 4) if values else None


def _max_value(values: List[float]) -> Optional[float]:
    return round(max(values), 4) if values else None


def _atr(highs: List, lows: List, closes: List, days: int) -> Optional[float]:
    true_ranges = []
    previous_close = None
    for high, low, close in zip(highs, lows, closes):
        if high is None or low is None or close is None:
            continue
        high_value = float(high)
        low_value = float(low)
        close_value = float(close)
        if previous_close is None:
            true_range = high_value - low_value
        else:
            true_range = max(
                high_value - low_value,
                abs(high_value - previous_close),
                abs(low_value - previous_close),
            )
        true_ranges.append(true_range)
        previous_close = close_value
    return _average(true_ranges[-days:])


def _percent_change(current: float, previous: Optional[float]) -> Optional[float]:
    if previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 4)


def _close_vs_average(close: float, values: List[float]) -> Optional[float]:
    average = _average(values)
    if average in (None, 0):
        return None
    return round(((close - average) / average) * 100, 4)


def _period_return(values: List[float], days: int) -> Optional[float]:
    if len(values) <= days:
        return None
    starting_value = values[-(days + 1)]
    ending_value = values[-1]
    if starting_value == 0:
        return None
    return round(((ending_value - starting_value) / starting_value) * 100, 4)


def _indexed_value(values: List, index: int) -> Optional[float]:
    if index >= len(values) or values[index] is None:
        return None
    return float(values[index])


def _normalize_ticker(ticker: str) -> str:
    if ticker.upper() == "DX-Y.NYB":
        return "DX-Y.NYB"
    return ticker.upper().replace(".", "-")


def _write_cache(path: Path, snapshot: PriceSnapshot) -> None:
    payload = {
        "cached_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "snapshot": snapshot.__dict__,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_cache(path: Path) -> PriceSnapshot:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fields = PriceSnapshot.__dataclass_fields__
    snapshot = {key: value for key, value in payload["snapshot"].items() if key in fields}
    return PriceSnapshot(**snapshot)


def _write_history_cache(path: Path, bars: List[PriceBar]) -> None:
    payload = {
        "cached_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "bars": [bar.__dict__ for bar in bars],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_history_cache(path: Path) -> List[PriceBar]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [PriceBar(**bar) for bar in payload.get("bars", [])]

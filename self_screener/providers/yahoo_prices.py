import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from self_screener.models import PriceBar, PriceSnapshot
from self_screener.storage import store

PRICE_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
PREFETCH_MAX_WORKERS = 6


class PriceFetchError(RuntimeError):
    """Raised when historical price data cannot be fetched or parsed."""


def fetch_price_snapshot(ticker: str, refresh: bool = False, sleep_seconds: float = 0.0) -> PriceSnapshot:
    normalized = _normalize_ticker(ticker)

    if not refresh:
        cached = store.get_price_snapshot(normalized, max_age_seconds=PRICE_CACHE_MAX_AGE_SECONDS)
        if cached is not None:
            return _snapshot_from_cache(cached)

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

    store.set_price_snapshot(normalized, snapshot.__dict__)
    return snapshot


def fetch_price_history(ticker: str, refresh: bool = False, sleep_seconds: float = 0.0) -> List[PriceBar]:
    normalized = _normalize_ticker(ticker)

    if not refresh:
        cached = store.get_price_history(normalized, max_age_seconds=PRICE_CACHE_MAX_AGE_SECONDS)
        if cached is not None:
            return [PriceBar(**bar) for bar in cached]

    if sleep_seconds:
        time.sleep(sleep_seconds)

    payload = _fetch_chart_payload(normalized)
    bars = _history_from_payload(payload)
    store.set_price_history(normalized, [bar.__dict__ for bar in bars])
    return bars


def prefetch_price_snapshots(
    tickers: List[str],
    refresh: bool = False,
    max_age_seconds: int = PRICE_CACHE_MAX_AGE_SECONDS,
) -> Dict[str, PriceSnapshot]:
    """Warm the snapshot cache for many tickers at once.

    One bulk cache read, concurrent Yahoo fetches for the misses, one bulk
    cache write. Threads only perform HTTP — every storage call stays on the
    calling thread. Keys in the returned dict are the tickers as passed in.
    Failed fetches produce error snapshots, matching fetch_price_snapshot.
    """
    normalized_by_input = {ticker: _normalize_ticker(ticker) for ticker in tickers}
    unique = list(dict.fromkeys(normalized_by_input.values()))

    cached = {} if refresh else store.get_price_snapshots(unique, max_age_seconds=max_age_seconds)
    misses = [ticker for ticker in unique if ticker not in cached]

    fetched: Dict[str, PriceSnapshot] = {}
    if misses:
        with ThreadPoolExecutor(max_workers=PREFETCH_MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_chart_payload, ticker): ticker for ticker in misses}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    fetched[ticker] = _snapshot_from_payload(ticker, future.result())
                except Exception as exc:
                    fetched[ticker] = PriceSnapshot(
                        ticker=ticker,
                        source="Yahoo Finance chart endpoint",
                        error=str(exc),
                    )
        store.set_price_snapshots({ticker: snapshot.__dict__ for ticker, snapshot in fetched.items()})

    results: Dict[str, PriceSnapshot] = {}
    for original, normalized in normalized_by_input.items():
        if normalized in fetched:
            results[original] = fetched[normalized]
        elif normalized in cached:
            results[original] = _snapshot_from_cache(cached[normalized])
    return results


def prefetch_price_histories(
    tickers: List[str],
    refresh: bool = False,
    max_age_seconds: int = PRICE_CACHE_MAX_AGE_SECONDS,
) -> Dict[str, List[PriceBar]]:
    """Bulk equivalent of fetch_price_history, same thread discipline as
    prefetch_price_snapshots. Unlike snapshots, a failed history fetch raises
    (after caching whatever succeeded), matching fetch_price_history.
    """
    normalized_by_input = {ticker: _normalize_ticker(ticker) for ticker in tickers}
    unique = list(dict.fromkeys(normalized_by_input.values()))

    cached = {} if refresh else store.get_price_histories(unique, max_age_seconds=max_age_seconds)
    misses = [ticker for ticker in unique if ticker not in cached]

    fetched: Dict[str, List[PriceBar]] = {}
    first_error: Optional[Exception] = None
    if misses:
        with ThreadPoolExecutor(max_workers=PREFETCH_MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_chart_payload, ticker): ticker for ticker in misses}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    fetched[ticker] = _history_from_payload(future.result())
                except Exception as exc:
                    if first_error is None:
                        first_error = exc
        store.set_price_histories(
            {ticker: [bar.__dict__ for bar in bars] for ticker, bars in fetched.items()}
        )
        if first_error is not None:
            raise first_error

    results: Dict[str, List[PriceBar]] = {}
    for original, normalized in normalized_by_input.items():
        if normalized in fetched:
            results[original] = fetched[normalized]
        elif normalized in cached:
            results[original] = [PriceBar(**bar) for bar in cached[normalized]]
    return results


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


def _snapshot_from_cache(cached: dict) -> PriceSnapshot:
    fields = PriceSnapshot.__dataclass_fields__
    return PriceSnapshot(**{key: value for key, value in cached.items() if key in fields})

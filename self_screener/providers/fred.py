import csv
import re
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Optional

from self_screener.storage import store

MACRO_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
GOVSPENDING_SERIES_URL = "https://govspending.org/series/{series_id}/"


@dataclass(frozen=True)
class FredObservation:
    series_id: str
    date: str = ""
    value: Optional[float] = None
    source: str = "FRED"
    error: Optional[str] = None


def fetch_fred_observation(series_id: str, refresh: bool = False) -> FredObservation:
    normalized = series_id.upper()
    series_key = f"fred:{normalized}"

    if not refresh:
        cached = store.get_macro_observation(series_key, max_age_seconds=MACRO_CACHE_MAX_AGE_SECONDS)
        if cached is not None:
            return FredObservation(**cached)

    try:
        observation = _fetch_latest_observation_with_retry(normalized)
    except Exception as exc:
        cached = store.get_macro_observation_any(series_key)
        if cached is not None and cached.get("value") is not None:
            return FredObservation(**cached)
        return FredObservation(series_id=normalized, error=str(exc))

    store.set_macro_observation(series_key, asdict(observation))
    return observation


def _fetch_latest_observation_with_retry(series_id: str, attempts: int = 3) -> FredObservation:
    last_error = None
    for attempt in range(attempts):
        try:
            return _fetch_latest_observation(series_id)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.3 * (attempt + 1))
    try:
        return _fetch_govspending_observation(series_id)
    except Exception:
        raise last_error


def _fetch_latest_observation(series_id: str) -> FredObservation:
    query = urllib.parse.urlencode(
        {
            "id": series_id,
            "cosd": (date.today() - timedelta(days=120)).isoformat(),
        }
    )
    request = urllib.request.Request(
        f"{FRED_CSV_URL}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0 SignalDeck ETF Screener/0.1",
            "Accept": "text/csv",
            "Connection": "close",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        rows = list(csv.DictReader(response.read().decode("utf-8").splitlines()))

    for row in reversed(rows):
        raw_value = row.get(series_id)
        if raw_value in (None, "", "."):
            continue
        return FredObservation(
            series_id=series_id,
            date=row.get("observation_date", ""),
            value=round(float(raw_value), 4),
            source="FRED: ICE BofA US High Yield Index Option-Adjusted Spread",
        )

    raise ValueError(f"No valid observations returned for {series_id}.")


def _fetch_govspending_observation(series_id: str) -> FredObservation:
    request = urllib.request.Request(
        GOVSPENDING_SERIES_URL.format(series_id=urllib.parse.quote(series_id)),
        headers={
            "User-Agent": "Mozilla/5.0 SignalDeck ETF Screener/0.1",
            "Accept": "text/html",
            "Connection": "close",
        },
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8", "ignore")

    match = re.search(
        r"As of ([A-Z][a-z]+ \d{1,2}, \d{4}).{0,180}?current .*? is (\d+(?:\.\d+)?) Percent",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"No fallback observation found for {series_id}.")

    return FredObservation(
        series_id=series_id,
        date=match.group(1),
        value=round(float(match.group(2)), 4),
        source="GovSpending mirror of FRED: ICE BofA US High Yield Index Option-Adjusted Spread",
    )



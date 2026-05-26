import csv
import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


CACHE_DIR = Path("data") / "cache" / "macro"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


@dataclass(frozen=True)
class FredObservation:
    series_id: str
    date: str = ""
    value: Optional[float] = None
    source: str = "FRED"
    error: Optional[str] = None


def fetch_fred_observation(series_id: str, refresh: bool = False) -> FredObservation:
    normalized = series_id.upper()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{normalized}.json"

    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    try:
        observation = _fetch_latest_observation_with_retry(normalized)
    except Exception as exc:
        if cache_path.exists():
            cached = _read_cache(cache_path)
            if cached.value is not None:
                return cached
        observation = FredObservation(series_id=normalized, error=str(exc))

    _write_cache(cache_path, observation)
    return observation


def _fetch_latest_observation_with_retry(series_id: str, attempts: int = 3) -> FredObservation:
    last_error = None
    for attempt in range(attempts):
        try:
            return _fetch_latest_observation(series_id)
        except Exception as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(0.75 * (attempt + 1))
    raise last_error


def _fetch_latest_observation(series_id: str) -> FredObservation:
    query = urllib.parse.urlencode({"id": series_id})
    request = urllib.request.Request(
        f"{FRED_CSV_URL}?{query}",
        headers={
            "User-Agent": "SignalDeck ETF Screener/0.1",
            "Accept": "text/csv",
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


def _read_cache(path: Path) -> FredObservation:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return FredObservation(**payload)


def _write_cache(path: Path, observation: FredObservation) -> None:
    path.write_text(json.dumps(asdict(observation), indent=2) + "\n", encoding="utf-8")

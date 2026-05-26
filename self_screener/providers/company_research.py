import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict

from self_screener.models import CompanyResearch


CACHE_DIR = Path("data") / "cache" / "research"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"


def fetch_company_research(
    ticker: str,
    company_name: str = "",
    refresh: bool = False,
    sleep_seconds: float = 0.0,
) -> CompanyResearch:
    normalized = _normalize_ticker(ticker)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{normalized}.json"

    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    if sleep_seconds:
        time.sleep(sleep_seconds)

    try:
        research = _fetch_profile(normalized, company_name)
    except Exception as exc:
        research = CompanyResearch(
            ticker=normalized,
            name=company_name,
            source="Yahoo Finance company profile/search",
            error=str(exc),
        )

    _write_cache(cache_path, research)
    return research


def _fetch_profile(ticker: str, company_name: str) -> CompanyResearch:
    errors = []
    try:
        profile = _fetch_quote_summary(ticker)
    except Exception as exc:
        profile = {}
        errors.append(str(exc))

    try:
        search = _fetch_search_result(ticker)
    except Exception as exc:
        search = {}
        errors.append(str(exc))

    name = (
        profile.get("longBusinessSummaryName")
        or search.get("longname")
        or search.get("shortname")
        or company_name
    )
    sector = profile.get("sector") or search.get("sector") or ""
    industry = profile.get("industry") or search.get("industry") or ""
    summary = profile.get("longBusinessSummary") or ""
    source_parts = ["Yahoo Finance company profile/search"]

    if not summary:
        try:
            summary = _fetch_wikipedia_summary(name or company_name or ticker)
            if summary:
                source_parts.append("Wikipedia summary")
        except Exception as exc:
            errors.append(str(exc))

    if not (name or sector or industry or summary):
        detail = "; ".join(errors) if errors else "No company profile fields returned."
        raise RuntimeError(detail)

    return CompanyResearch(
        ticker=ticker,
        name=name,
        sector=sector,
        industry=industry,
        summary=summary,
        source=" + ".join(source_parts),
    )


def _fetch_quote_summary(ticker: str) -> Dict:
    query = urllib.parse.urlencode({"modules": "assetProfile"})
    url = f"{YAHOO_SUMMARY_URL.format(ticker=urllib.parse.quote(ticker))}?{query}"
    payload = _fetch_json(url)
    results = payload.get("quoteSummary", {}).get("result") or []
    if not results:
        return {}
    return results[0].get("assetProfile") or {}


def _fetch_search_result(ticker: str) -> Dict:
    query = urllib.parse.urlencode({"q": ticker, "quotesCount": 1, "newsCount": 0})
    payload = _fetch_json(f"{YAHOO_SEARCH_URL}?{query}")
    quotes = payload.get("quotes") or []
    return quotes[0] if quotes else {}


def _fetch_wikipedia_summary(query_text: str) -> str:
    title = _search_wikipedia_title(query_text)
    if not title:
        return ""

    query = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "titles": title,
            "format": "json",
        }
    )
    payload = _fetch_json(f"{WIKIPEDIA_API_URL}?{query}", user_agent="SignalDeck ETF Screener/0.1")
    pages = payload.get("query", {}).get("pages", {})
    if not pages:
        return ""
    page = next(iter(pages.values()))
    return " ".join((page.get("extract") or "").split())


def _search_wikipedia_title(query_text: str) -> str:
    query = urllib.parse.urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query_text,
            "srlimit": "1",
            "format": "json",
        }
    )
    payload = _fetch_json(f"{WIKIPEDIA_API_URL}?{query}", user_agent="SignalDeck ETF Screener/0.1")
    results = payload.get("query", {}).get("search") or []
    return results[0].get("title", "") if results else ""


def _fetch_json(url: str, user_agent: str = "Mozilla/5.0 ETF Screener Prototype") -> Dict:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _normalize_ticker(ticker: str) -> str:
    return ticker.upper().replace(".", "-")


def _write_cache(path: Path, research: CompanyResearch) -> None:
    payload = {
        "cached_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "research": research.__dict__,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_cache(path: Path) -> CompanyResearch:
    payload = json.loads(path.read_text(encoding="utf-8"))
    fields = CompanyResearch.__dataclass_fields__
    research = {key: value for key, value in payload["research"].items() if key in fields}
    return CompanyResearch(**research)

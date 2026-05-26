import html
import json
import re
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from xml.etree import ElementTree

from self_screener.models import Holding


SSGA_HOLDINGS_URLS: Dict[str, str] = {
    "SPY": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
    "DIA": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-dia.xlsx",
    "XLK": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xlk.xlsx",
    "XLF": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xlf.xlsx",
    "XLE": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xle.xlsx",
    "XLU": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xlu.xlsx",
    "XLY": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xly.xlsx",
    "XLP": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xlp.xlsx",
    "XLV": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xlv.xlsx",
    "XLI": "https://www.ssga.com/us/en/individual/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-xli.xlsx",
}
GRNY_HOLDINGS_URL = "https://grannyshots.com/fundstrat-granny-shots-us-large-cap-etf/"
STOCKANALYSIS_GRNY_HOLDINGS_URL = "https://stockanalysis.com/etf/grny/holdings/"
STOCKANALYSIS_QQQ_HOLDINGS_URL = "https://stockanalysis.com/etf/qqq/holdings/"
NASDAQ_100_URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100?assetclass=stocks"

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"


class HoldingsFetchError(RuntimeError):
    """Raised when ETF holdings cannot be fetched or parsed."""


@dataclass(frozen=True)
class HoldingsSource:
    symbol: str
    provider: str
    fetcher: Callable[[bool], List[Holding]]
    source: str
    quality: str
    quality_note: str
    public_weighted_label: str = "StockAnalysis"
    placeholder_label: str = "placeholder residual weight"


def _build_source_registry() -> Dict[str, HoldingsSource]:
    registry = {
        symbol: HoldingsSource(
            symbol=symbol,
            provider="State Street",
            fetcher=lambda refresh, ticker=symbol: _fetch_ssga_holdings(ticker, refresh=refresh),
            source="State Street public daily holdings workbook",
            quality="official_daily_workbook",
            quality_note="Holdings and weights come from the ETF sponsor's public daily workbook.",
        )
        for symbol in SSGA_HOLDINGS_URLS
    }
    registry["GRNY"] = HoldingsSource(
        symbol="GRNY",
        provider="Granny Shots + StockAnalysis",
        fetcher=_fetch_grny_holdings,
        source="Official Granny Shots holdings page plus StockAnalysis public top holdings weights; residual tickers use placeholder weights",
        quality="mixed_public_sources",
        quality_note=(
            "GRNY uses public weighted holdings where available. Theme-list residual tickers are included with placeholder weights, "
            "so use ranking directionally rather than as exact portfolio math."
        ),
    )
    registry["QQQ"] = HoldingsSource(
        symbol="QQQ",
        provider="Nasdaq + StockAnalysis",
        fetcher=_fetch_qqq_holdings,
        source="Nasdaq-100 public constituent list plus StockAnalysis public top holdings weights; residual tickers use placeholder weights",
        quality="mixed_public_sources",
        quality_note=(
            "QQQ uses the Nasdaq-100 constituent list as the full screen universe, with public top-holding weights where available. "
            "Residual constituents use placeholder weights, so use ranking directionally rather than as exact portfolio math."
        ),
    )
    return registry


def supported_etfs() -> List[str]:
    return sorted(HOLDINGS_SOURCES)


def holdings_source(symbol: str) -> HoldingsSource:
    normalized = symbol.upper()
    try:
        return HOLDINGS_SOURCES[normalized]
    except KeyError as exc:
        supported = ", ".join(supported_etfs())
        raise HoldingsFetchError(f"{normalized} is not supported yet. Supported ETFs: {supported}") from exc


def fetch_holdings(etf: str, refresh: bool = False) -> List[Holding]:
    return holdings_source(etf).fetcher(refresh)


def holdings_quality_metadata(etf: str, holdings: List[Holding]) -> dict:
    source = holdings_source(etf)
    metadata = {
        "holdings_provider": source.provider,
        "holdings_quality": source.quality,
        "holdings_quality_note": source.quality_note,
    }
    if source.quality != "mixed_public_sources":
        return metadata

    metadata["public_weighted_holdings"] = sum(
        1 for holding in holdings if (holding.sector or "").startswith(source.public_weighted_label)
    )
    metadata["placeholder_weighted_holdings"] = sum(
        1 for holding in holdings if source.placeholder_label in (holding.sector or "")
    )
    return metadata


def _fetch_ssga_holdings(symbol: str, refresh: bool = False) -> List[Holding]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol}_holdings.json"

    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    workbook_path = _download_workbook(symbol, SSGA_HOLDINGS_URLS[symbol], refresh=refresh)
    rows = _read_xlsx_rows(workbook_path)
    holdings = _parse_holdings_rows(rows)

    if not holdings:
        raise HoldingsFetchError(f"No holdings were parsed for {symbol}.")

    _write_cache(cache_path, holdings)
    return holdings


def _fetch_grny_holdings(refresh: bool = False) -> List[Holding]:
    symbol = "GRNY"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol}_holdings.json"
    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    official_html = _download_text(GRNY_HOLDINGS_URL)
    official_tickers = _parse_grny_tickers(official_html)
    weighted_holdings = _fetch_stockanalysis_grny_holdings()
    weighted_by_ticker = {holding.ticker: holding for holding in weighted_holdings}
    tickers = _merge_tickers([holding.ticker for holding in weighted_holdings], official_tickers)
    if not tickers:
        raise HoldingsFetchError("No holdings were parsed for GRNY.")

    missing_tickers = [ticker for ticker in tickers if ticker not in weighted_by_ticker]
    known_weight = sum(holding.weight or 0 for holding in weighted_holdings)
    placeholder_weight = round(max(0, 100 - known_weight) / len(missing_tickers), 6) if missing_tickers else None
    holdings = []
    for ticker in tickers:
        weighted = weighted_by_ticker.get(ticker)
        if weighted:
            holdings.append(weighted)
            continue
        holdings.append(
            Holding(
                ticker=ticker,
                name=ticker,
                weight=placeholder_weight,
                sector="Official theme list; placeholder residual weight",
            )
        )
    _write_cache(cache_path, holdings)
    return holdings


def _fetch_qqq_holdings(refresh: bool = False) -> List[Holding]:
    symbol = "QQQ"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol}_holdings.json"
    if cache_path.exists() and not refresh:
        return _read_cache(cache_path)

    index_holdings = _fetch_nasdaq100_holdings()
    weighted_holdings = _fetch_stockanalysis_holdings(STOCKANALYSIS_QQQ_HOLDINGS_URL)
    weighted_by_ticker = {holding.ticker: holding for holding in weighted_holdings}
    tickers = _merge_tickers([holding.ticker for holding in weighted_holdings], [holding.ticker for holding in index_holdings])
    if not tickers:
        raise HoldingsFetchError("No holdings were parsed for QQQ.")

    index_by_ticker = {holding.ticker: holding for holding in index_holdings}
    missing_tickers = [ticker for ticker in tickers if ticker not in weighted_by_ticker]
    known_weight = sum(holding.weight or 0 for holding in weighted_holdings)
    placeholder_weight = round(max(0, 100 - known_weight) / len(missing_tickers), 6) if missing_tickers else None
    holdings = []
    for ticker in tickers:
        weighted = weighted_by_ticker.get(ticker)
        if weighted:
            holdings.append(weighted)
            continue
        index_holding = index_by_ticker.get(ticker)
        holdings.append(
            Holding(
                ticker=ticker,
                name=index_holding.name if index_holding else ticker,
                weight=placeholder_weight,
                sector="Nasdaq-100 constituent list; placeholder residual weight",
            )
        )
    _write_cache(cache_path, holdings)
    return holdings


def _fetch_stockanalysis_grny_holdings() -> List[Holding]:
    try:
        return _fetch_stockanalysis_holdings(STOCKANALYSIS_GRNY_HOLDINGS_URL)
    except HoldingsFetchError:
        return []


def _fetch_stockanalysis_holdings(url: str) -> List[Holding]:
    html_text = _download_text(url)
    return _parse_stockanalysis_rows(html_text)


def _fetch_nasdaq100_holdings() -> List[Holding]:
    request = urllib.request.Request(
        NASDAQ_100_URL,
        headers={
            "User-Agent": "Mozilla/5.0 ETF Screener Prototype",
            "Accept": "application/json,*/*",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        raise HoldingsFetchError(f"Could not download Nasdaq-100 constituents: {exc}") from exc

    rows = payload.get("data", {}).get("data", {}).get("rows", [])
    holdings: List[Holding] = []
    for row in rows:
        ticker = str(row.get("symbol", "")).replace(".", "-").upper().strip()
        if not re.fullmatch(r"[A-Z-]{1,6}", ticker):
            continue
        name = _clean_company_name(str(row.get("companyName", ""))) or ticker
        holdings.append(
            Holding(
                ticker=ticker,
                name=name,
                sector="Nasdaq-100 constituent list; placeholder residual weight",
            )
        )
    return holdings


def _download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ETF Screener Prototype",
            "Accept": "text/html,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise HoldingsFetchError(f"Could not download holdings page: {exc}") from exc


def _parse_grny_tickers(html_text: str) -> List[str]:
    text = html.unescape(re.sub(r"<[^>]+>", "\n", html_text))
    match = re.search(
        r"GRNY Holdings\s+.*?4 Themes(?P<body>.*?)As of [A-Z][a-z]+ \d{1,2}, \d{4}",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return []

    body = match.group("body")
    candidates = re.findall(r"\b[A-Z]{1,5}\b", body)
    excluded = {"GRNY", "THEMES"}
    seen = set()
    tickers: List[str] = []
    for ticker in candidates:
        if ticker in excluded or ticker in seen:
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tickers


def _parse_stockanalysis_rows(html_text: str) -> List[Holding]:
    table_match = re.search(r"<table\b.*?</table>", html_text, flags=re.DOTALL | re.IGNORECASE)
    if not table_match:
        return []

    rows = re.findall(r"<tr\b.*?</tr>", table_match.group(0), flags=re.DOTALL | re.IGNORECASE)
    holdings: List[Holding] = []
    for row in rows:
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, flags=re.DOTALL | re.IGNORECASE)
        if len(cells) < 5:
            continue
        ticker = _strip_html(cells[1]).replace(".", "-").upper()
        if not re.fullmatch(r"[A-Z-]{1,6}", ticker):
            continue
        name = _strip_html(cells[2])
        weight = _parse_float(_strip_html(cells[3]))
        shares = _parse_float(_strip_html(cells[4]))
        holdings.append(
            Holding(
                ticker=ticker,
                name=name or ticker,
                weight=weight,
                shares_held=shares,
                sector="StockAnalysis public top holdings weight",
            )
        )
    return holdings


def _clean_company_name(value: str) -> str:
    value = re.sub(r"\bCommon Stock\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bClass [A-Z]\b", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" ,")


def _strip_html(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", value))).strip()


def _merge_tickers(*groups: List[str]) -> List[str]:
    seen = set()
    merged: List[str] = []
    for group in groups:
        for ticker in group:
            normalized = ticker.upper().replace(".", "-")
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _download_workbook(symbol: str, url: str, refresh: bool) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    workbook_path = RAW_DIR / f"{symbol}_holdings.xlsx"

    if workbook_path.exists() and not refresh:
        return workbook_path

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 ETF Screener Prototype",
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            workbook_path.write_bytes(response.read())
    except Exception as exc:
        raise HoldingsFetchError(f"Could not download holdings for {symbol}: {exc}") from exc

    return workbook_path


def _read_xlsx_rows(path: Path) -> List[List[str]]:
    try:
        with zipfile.ZipFile(path) as workbook:
            shared_strings = _read_shared_strings(workbook)
            sheet_name = _first_sheet_name(workbook)
            sheet_xml = workbook.read(sheet_name)
    except Exception as exc:
        raise HoldingsFetchError(f"Could not read workbook {path}: {exc}") from exc

    root = ElementTree.fromstring(sheet_xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: List[List[str]] = []

    for row in root.findall(".//x:sheetData/x:row", namespace):
        values: List[str] = []
        for cell in row.findall("x:c", namespace):
            value = _cell_value(cell, shared_strings, namespace)
            values.append(value)
        if any(value.strip() for value in values):
            rows.append(values)

    return rows


def _read_shared_strings(workbook: zipfile.ZipFile) -> List[str]:
    try:
        xml = workbook.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ElementTree.fromstring(xml)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: List[str] = []

    for item in root.findall("x:si", namespace):
        text_parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        strings.append("".join(text_parts))

    return strings


def _first_sheet_name(workbook: zipfile.ZipFile) -> str:
    candidates = sorted(
        name
        for name in workbook.namelist()
        if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
    )
    if not candidates:
        raise HoldingsFetchError("Workbook does not contain a worksheet.")
    return candidates[0]


def _cell_value(cell: ElementTree.Element, shared_strings: List[str], namespace: Dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")

    if cell_type == "inlineStr":
        text_nodes = cell.findall(".//x:t", namespace)
        return "".join(node.text or "" for node in text_nodes)

    value_node = cell.find("x:v", namespace)
    if value_node is None or value_node.text is None:
        return ""

    raw_value = value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)]
        except (ValueError, IndexError):
            return raw_value

    return raw_value


def _parse_holdings_rows(rows: Iterable[List[str]]) -> List[Holding]:
    header: Optional[List[str]] = None
    holdings: List[Holding] = []

    for row in rows:
        normalized = [_normalize_header(value) for value in row]
        if _looks_like_header(normalized):
            header = normalized
            continue

        if header is None:
            continue

        record = _row_to_record(header, row)
        holding = _record_to_holding(record)
        if holding:
            holdings.append(holding)

    return holdings


def _looks_like_header(row: List[str]) -> bool:
    header_text = " ".join(row)
    return "ticker" in header_text and ("weight" in header_text or "weight (%)" in header_text)


def _row_to_record(header: List[str], row: List[str]) -> Dict[str, str]:
    record: Dict[str, str] = {}
    for index, column in enumerate(header):
        if index < len(row):
            record[column] = row[index].strip()
    return record


def _record_to_holding(record: Dict[str, str]) -> Optional[Holding]:
    ticker = _first_value(record, ["ticker", "identifier", "sedol"])
    name = _first_value(record, ["name", "security name", "holding name"])

    if not ticker or not name or ticker.upper() in {"-", "CASH_USD", "USD"}:
        return None

    if "CONTRA" in name.upper():
        return None

    return Holding(
        ticker=ticker.upper(),
        name=name,
        weight=_parse_float(_first_value(record, ["weight", "weight (%)", "weight percent"])),
        shares_held=_parse_float(_first_value(record, ["shares held", "shares"])),
        sector=_first_value(record, ["sector"]),
    )


def _first_value(record: Dict[str, str], keys: List[str]) -> Optional[str]:
    for key in keys:
        value = record.get(key)
        if value:
            return value
    return None


def _normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _parse_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    cleaned = value.replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _write_cache(path: Path, holdings: List[Holding]) -> None:
    payload = {
        "cached_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "holdings": [holding.__dict__ for holding in holdings],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _read_cache(path: Path) -> List[Holding]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [Holding(**item) for item in payload.get("holdings", [])]


HOLDINGS_SOURCES = _build_source_registry()

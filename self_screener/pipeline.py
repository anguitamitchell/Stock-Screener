import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from self_screener.models import EtfScreenReport, RatedHolding
from self_screener.market_condition import build_market_condition
from self_screener.providers.ssga_holdings import (
    fetch_holdings,
    holdings_quality_metadata,
    holdings_source,
)
from self_screener.providers.yahoo_prices import fetch_price_snapshot
from self_screener.screeners import rating, semantic, technical, trade_plan


REPORT_DIR = Path("data") / "reports"


def screen_etf(
    etf: str,
    limit: Optional[int] = None,
    refresh: bool = False,
    include_prices: bool = False,
    include_market: bool = False,
) -> EtfScreenReport:
    symbol = etf.upper()
    holdings = fetch_holdings(symbol, refresh=refresh)
    selected_holdings = holdings[:limit] if limit else holdings
    benchmark_price = fetch_price_snapshot(symbol, refresh=refresh) if include_prices else None
    market_condition = build_market_condition("SPY", refresh=refresh) if include_market else None

    rated = []
    for holding in selected_holdings:
        price_snapshot = fetch_price_snapshot(holding.ticker, refresh=refresh) if include_prices else None
        semantic_result = semantic.screen_holding(holding, refresh=refresh)
        technical_result = technical.screen_holding(holding, price_snapshot, benchmark_price)
        final_score, final_rating, reasons = rating.classify(semantic_result, technical_result)
        trade_plan_result = trade_plan.build_trade_plan(
            price_snapshot,
            final_rating,
            technical_result.label,
        )
        rated.append(
            RatedHolding(
                holding=holding,
                price=price_snapshot,
                semantic=semantic_result,
                technical=technical_result,
                trade_plan=trade_plan_result,
                final_score=final_score,
                rating=final_rating,
                rating_reasons=reasons,
            )
        )
    rated.sort(key=lambda item: item.final_score if item.final_score is not None else -1, reverse=True)

    return EtfScreenReport(
        etf=symbol,
        generated_at=datetime.utcnow(),
        source=holdings_source(symbol).source,
        holdings_count=len(holdings),
        rated_holdings=rated,
        market_condition=market_condition,
        metadata={
            "note": "Screening criteria are placeholders until user-defined rules are added.",
            "prices": "included" if include_prices else "not included",
            "market_condition": "included" if include_market else "not included",
            "market_condition_benchmark": "SPY" if include_market else "",
            "benchmark": symbol if include_prices else "",
            "benchmark_return_20d_percent": benchmark_price.return_20d_percent if benchmark_price else None,
            "benchmark_return_60d_percent": benchmark_price.return_60d_percent if benchmark_price else None,
            **holdings_quality_metadata(symbol, holdings),
        },
    )


def write_report(report: EtfScreenReport) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{report.etf}_screen.json"
    path.write_text(json.dumps(_report_to_json(report), indent=2), encoding="utf-8")
    return path


def _report_to_json(report: EtfScreenReport) -> dict:
    payload = asdict(report)
    payload = _serialize_datetimes(payload)
    return payload


def _serialize_datetimes(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds") + "Z"
    if isinstance(value, dict):
        return {key: _serialize_datetimes(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_datetimes(item) for item in value]
    return value

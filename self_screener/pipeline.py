from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import List, Optional

from self_screener.models import (
    DeMarkSignal,
    EtfScreenReport,
    Holding,
    MarketConditionReport,
    MarketSignal,
    PriceSnapshot,
    RatedHolding,
    ScreenResult,
    TradePlan,
)
from self_screener.market_condition import build_market_condition
from self_screener.providers.ssga_holdings import (
    fetch_holdings,
    holdings_quality_metadata,
    holdings_source,
)
from self_screener.providers.yahoo_prices import fetch_price_snapshot
from self_screener.screeners import rating, semantic, technical, trade_plan
from self_screener.storage import store


@dataclass
class ScreenProgress:
    """Mutable, step-at-a-time state for a screen run.

    screen_etf() drives this to completion in one call for CLI use; the
    Vercel job worker (api/jobs.py) instead calls screen_next_holding() once
    per invocation and checkpoints the progress in between.
    """

    symbol: str
    source: str
    holdings_count: int
    remaining: List[Holding]
    rated: List[RatedHolding] = field(default_factory=list)
    benchmark_price: Optional[PriceSnapshot] = None
    market_condition: Optional[MarketConditionReport] = None
    metadata: dict = field(default_factory=dict)
    refresh: bool = False
    include_prices: bool = False


def start_screen(
    etf: str,
    limit: Optional[int] = None,
    refresh: bool = False,
    include_prices: bool = False,
    include_market: bool = False,
) -> ScreenProgress:
    symbol = etf.upper()
    holdings = fetch_holdings(symbol, refresh=refresh)
    selected_holdings = holdings[:limit] if limit else holdings
    benchmark_price = fetch_price_snapshot(symbol, refresh=refresh) if include_prices else None
    market_condition = build_market_condition(symbol, refresh=refresh) if include_market else None

    metadata = {
        "note": "Screening criteria are placeholders until user-defined rules are added.",
        "prices": "included" if include_prices else "not included",
        "market_condition": "included" if include_market else "not included",
        "market_condition_benchmark": "SPY" if include_market else "",
        "benchmark": symbol if include_prices else "",
        "benchmark_return_20d_percent": benchmark_price.return_20d_percent if benchmark_price else None,
        "benchmark_return_60d_percent": benchmark_price.return_60d_percent if benchmark_price else None,
        **holdings_quality_metadata(symbol, holdings),
    }

    return ScreenProgress(
        symbol=symbol,
        source=holdings_source(symbol).source,
        holdings_count=len(holdings),
        remaining=list(selected_holdings),
        benchmark_price=benchmark_price,
        market_condition=market_condition,
        metadata=metadata,
        refresh=refresh,
        include_prices=include_prices,
    )


def screen_next_holding(progress: ScreenProgress) -> ScreenProgress:
    holding = progress.remaining.pop(0)
    price_snapshot = fetch_price_snapshot(holding.ticker, refresh=progress.refresh) if progress.include_prices else None
    semantic_result = semantic.screen_holding(holding, refresh=progress.refresh)
    technical_result = technical.screen_holding(
        holding,
        price_snapshot,
        progress.benchmark_price,
        progress.market_condition.demark if progress.market_condition else None,
    )
    final_score, final_rating, reasons = rating.classify(semantic_result, technical_result)
    trade_plan_result = trade_plan.build_trade_plan(
        price_snapshot,
        final_rating,
        technical_result.label,
    )
    progress.rated.append(
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
    return progress


def is_screen_complete(progress: ScreenProgress) -> bool:
    return not progress.remaining


def finish_screen(progress: ScreenProgress) -> EtfScreenReport:
    rated = sorted(
        progress.rated,
        key=lambda item: item.final_score if item.final_score is not None else -1,
        reverse=True,
    )
    return EtfScreenReport(
        etf=progress.symbol,
        generated_at=datetime.utcnow(),
        source=progress.source,
        holdings_count=progress.holdings_count,
        rated_holdings=rated,
        market_condition=progress.market_condition,
        metadata=progress.metadata,
    )


def screen_etf(
    etf: str,
    limit: Optional[int] = None,
    refresh: bool = False,
    include_prices: bool = False,
    include_market: bool = False,
) -> EtfScreenReport:
    progress = start_screen(etf, limit=limit, refresh=refresh, include_prices=include_prices, include_market=include_market)
    while not is_screen_complete(progress):
        progress = screen_next_holding(progress)
    return finish_screen(progress)


def write_report(report: EtfScreenReport) -> None:
    payload = _report_to_json(report)
    store.set_screen_report(report.etf, payload, payload["generated_at"])


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


def serialize_progress(progress: ScreenProgress) -> dict:
    """JSON-safe snapshot of in-flight screen state, for screen_jobs.cursor."""
    return {
        "symbol": progress.symbol,
        "source": progress.source,
        "holdings_count": progress.holdings_count,
        "remaining": [asdict(holding) for holding in progress.remaining],
        "rated": [_serialize_datetimes(asdict(rated)) for rated in progress.rated],
        "benchmark_price": asdict(progress.benchmark_price) if progress.benchmark_price else None,
        "market_condition": _serialize_datetimes(asdict(progress.market_condition))
        if progress.market_condition
        else None,
        "metadata": progress.metadata,
        "refresh": progress.refresh,
        "include_prices": progress.include_prices,
    }


def deserialize_progress(cursor: dict) -> ScreenProgress:
    market_condition = None
    if cursor.get("market_condition"):
        fields = dict(cursor["market_condition"])
        fields["generated_at"] = datetime.fromisoformat(fields["generated_at"].replace("Z", "+00:00")).replace(
            tzinfo=None
        )
        fields["demark"] = DeMarkSignal(**fields["demark"])
        for key in (
            "structural_weakness",
            "technology_support",
            "defensive_rotation",
            "credit_risk",
            "dollar_yield_shock",
        ):
            fields[key] = MarketSignal(**fields[key])
        market_condition = MarketConditionReport(**fields)

    return ScreenProgress(
        symbol=cursor["symbol"],
        source=cursor["source"],
        holdings_count=cursor["holdings_count"],
        remaining=[Holding(**item) for item in cursor["remaining"]],
        rated=[_deserialize_rated_holding(item) for item in cursor["rated"]],
        benchmark_price=PriceSnapshot(**cursor["benchmark_price"]) if cursor.get("benchmark_price") else None,
        market_condition=market_condition,
        metadata=cursor.get("metadata", {}),
        refresh=cursor.get("refresh", False),
        include_prices=cursor.get("include_prices", False),
    )


def _deserialize_rated_holding(data: dict) -> RatedHolding:
    return RatedHolding(
        holding=Holding(**data["holding"]),
        price=PriceSnapshot(**data["price"]) if data.get("price") else None,
        semantic=ScreenResult(**data["semantic"]),
        technical=ScreenResult(**data["technical"]),
        trade_plan=TradePlan(**data["trade_plan"]) if data.get("trade_plan") else None,
        final_score=data["final_score"],
        rating=data["rating"],
        rating_reasons=data["rating_reasons"],
    )

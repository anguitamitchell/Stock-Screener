"""Position/lot engine: derives portfolio state from the manual trade log.

Pure functions over trade dicts — no storage or network access — so the
whole module is unit-testable and the API layer can pre-validate a new or
removed trade by simply re-running build_positions on the hypothetical list.

Lot matching is FIFO: each buy opens a lot; each sell consumes the oldest
open lots first. Buy fees are folded into the lot's per-share cost; sell
fees are spread across the shares sold.
"""

from typing import Dict, List, Optional

SHARE_EPSILON = 1e-9


class OversellError(ValueError):
    """A sell consumes more shares than are open for that ticker at that point."""


def build_positions(trades: List[dict]) -> List[dict]:
    """Derives per-ticker positions from a chronological trade list.

    Trades must already be ordered oldest-first (store.list_trades() does
    this). Raises OversellError if any sell exceeds the open shares at its
    point in time — callers validate hypothetical trade lists with this.
    """
    by_ticker: Dict[str, List[dict]] = {}
    for trade in trades:
        by_ticker.setdefault(str(trade["ticker"]).upper(), []).append(trade)

    positions = []
    for ticker in sorted(by_ticker):
        positions.append(_build_position(ticker, by_ticker[ticker]))
    return positions


def _build_position(ticker: str, trades: List[dict]) -> dict:
    open_lots: List[dict] = []
    realized_pl = 0.0

    for trade in trades:
        shares = float(trade["shares"])
        price = float(trade["price"])
        fees = float(trade.get("fees") or 0.0)

        if trade["side"] == "buy":
            open_lots.append(
                {
                    "trade_id": trade.get("id"),
                    "trade_date": trade.get("trade_date"),
                    "shares": shares,
                    "shares_open": shares,
                    "price": price,
                    "cost_per_share": price + (fees / shares),
                    "notes": trade.get("notes"),
                }
            )
            continue

        # sell: consume oldest lots first
        remaining = shares
        net_sell_per_share = price - (fees / shares)
        while remaining > SHARE_EPSILON:
            if not open_lots:
                raise OversellError(
                    f"Sell of {shares:g} {ticker} on {trade.get('trade_date')} exceeds open shares."
                )
            lot = open_lots[0]
            matched = min(lot["shares_open"], remaining)
            realized_pl += matched * (net_sell_per_share - lot["cost_per_share"])
            lot["shares_open"] -= matched
            remaining -= matched
            if lot["shares_open"] <= SHARE_EPSILON:
                open_lots.pop(0)

    total_shares = sum(lot["shares_open"] for lot in open_lots)
    cost_basis = sum(lot["shares_open"] * lot["cost_per_share"] for lot in open_lots)
    return {
        "ticker": ticker,
        "shares": round(total_shares, 6),
        "cost_basis": round(cost_basis, 2),
        "avg_cost": round(cost_basis / total_shares, 4) if total_shares > SHARE_EPSILON else None,
        "realized_pl": round(realized_pl, 2),
        "lots": [
            {
                "trade_id": lot["trade_id"],
                "trade_date": lot["trade_date"],
                "shares_open": round(lot["shares_open"], 6),
                "price": lot["price"],
                "cost_per_share": round(lot["cost_per_share"], 4),
                "notes": lot["notes"],
            }
            for lot in open_lots
        ],
    }


def apply_quote(position: dict, last_close: Optional[float]) -> dict:
    """Adds market value / unrealized P&L to a position (and its lots) in place."""
    shares = position["shares"]
    if last_close is None or shares <= SHARE_EPSILON:
        position["market_value"] = None
        position["unrealized_pl"] = None
        position["unrealized_pl_percent"] = None
        for lot in position["lots"]:
            lot["unrealized_pl"] = None
            lot["unrealized_pl_percent"] = None
        return position

    position["market_value"] = round(shares * last_close, 2)
    position["unrealized_pl"] = round(position["market_value"] - position["cost_basis"], 2)
    position["unrealized_pl_percent"] = (
        round((position["unrealized_pl"] / position["cost_basis"]) * 100, 2)
        if position["cost_basis"]
        else None
    )
    for lot in position["lots"]:
        lot_cost = lot["shares_open"] * lot["cost_per_share"]
        lot["unrealized_pl"] = round(lot["shares_open"] * last_close - lot_cost, 2)
        lot["unrealized_pl_percent"] = round((lot["unrealized_pl"] / lot_cost) * 100, 2) if lot_cost else None
    return position


def portfolio_totals(positions: List[dict]) -> dict:
    market_value = sum(p["market_value"] for p in positions if p.get("market_value") is not None)
    cost_basis = sum(p["cost_basis"] for p in positions if p["shares"] > SHARE_EPSILON)
    valued_cost = sum(
        p["cost_basis"] for p in positions if p.get("market_value") is not None and p["shares"] > SHARE_EPSILON
    )
    unrealized = market_value - valued_cost
    return {
        "market_value": round(market_value, 2),
        "cost_basis": round(cost_basis, 2),
        "unrealized_pl": round(unrealized, 2),
        "unrealized_pl_percent": round((unrealized / valued_cost) * 100, 2) if valued_cost else None,
        "realized_pl": round(sum(p["realized_pl"] for p in positions), 2),
    }

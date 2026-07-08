import os

from flask import Flask, jsonify, request

from self_screener.market_condition import build_market_condition
from self_screener.models import Holding
from self_screener.providers.ssga_holdings import HoldingsFetchError, fetch_holdings, supported_etfs
from self_screener.screeners.semantic import build_research_evidence, load_theme_config
from self_screener.storage import store

app = Flask(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


@app.before_request
def _require_auth():
    if request.path == "/api/health":
        return None
    token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({"error": "unauthorized"}), 401


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/themes")
def themes():
    return jsonify(load_theme_config().get("themes", {}))


@app.get("/api/etfs")
def etfs():
    return jsonify(supported_etfs())


@app.get("/api/curated")
def curated():
    return jsonify({"items": _curated_items()})


@app.get("/api/research")
def research():
    ticker = (request.args.get("ticker") or "").upper()
    name = request.args.get("name", "")
    refresh = (request.args.get("refresh", "false")).lower() == "true"
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400

    evidence = build_research_evidence(Holding(ticker=ticker, name=name), refresh=refresh)
    return jsonify(evidence), (400 if evidence.get("error") else 200)


@app.get("/api/research/bulk")
def research_bulk():
    etf = (request.args.get("etf") or "SPY").upper()
    limit_value = request.args.get("limit", "25")
    refresh = (request.args.get("refresh", "true")).lower() == "true"
    limit = int(limit_value) if limit_value else None

    try:
        holdings = fetch_holdings(etf, refresh=False)
    except HoldingsFetchError as exc:
        return jsonify({"error": str(exc)}), 400

    selected_holdings = holdings[:limit] if limit else holdings
    results = []
    for holding in selected_holdings:
        evidence = build_research_evidence(holding, refresh=refresh)
        results.append(
            {
                "ticker": holding.ticker,
                "research_status": evidence.get("research_status", "unreliable"),
                "confidence": evidence.get("confidence", 0),
                "error": evidence.get("error"),
                "matches": len(evidence.get("matches", [])),
            }
        )

    return jsonify(
        {
            "ok": True,
            "etf": etf,
            "processed": len(results),
            "auto_approved": sum(1 for item in results if item["research_status"] == "auto_approved"),
            "unreliable": sum(1 for item in results if item["research_status"] != "auto_approved"),
            "errors": sum(1 for item in results if item.get("error")),
            "results": results,
        }
    )


@app.get("/api/review")
def get_review():
    return jsonify(store.get_review_queue())


@app.post("/api/review")
def save_review():
    payload = request.get_json(force=True, silent=True) or {}
    ticker = str(payload.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400

    payload["ticker"] = ticker
    store.upsert_review_item(ticker, payload)
    queue = store.get_review_queue()
    return jsonify({"ok": True, "ticker": ticker, "review_count": len(queue["items"])})


@app.post("/api/review/promote")
def promote_review():
    payload = request.get_json(force=True, silent=True) or {}
    ticker = str(payload.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400

    queue = store.get_review_queue()
    review_item = next((item for item in queue["items"] if item.get("ticker") == ticker), None)
    if not review_item:
        return jsonify({"error": f"{ticker} is not in the review queue."}), 404

    config = load_theme_config()
    allowed_themes = set(config.get("themes", {}))
    exposures = _review_theme_exposures(review_item, allowed_themes)
    if not exposures:
        return jsonify({"error": f"{ticker} has no valid themes to promote."}), 400

    semantic = review_item.get("semantic", {})
    score = float(semantic.get("score") or review_item.get("final_score") or 50)
    score = max(0, min(100, score))
    store.upsert_company(
        ticker,
        {
            "score": round(score, 1),
            "themes": exposures,
            "notes": review_item.get("decision_summary") or "Promoted from the local semantic review queue.",
            "confidence": 1.0,
            "research_status": "curated",
            "source": "Promoted from local review queue",
        },
    )
    store.promote_review_item(ticker)
    return jsonify({"ok": True, "ticker": ticker, "themes": exposures})


@app.post("/api/review/clear")
def clear_review():
    payload = request.get_json(force=True, silent=True) or {}
    ticker = str(payload.get("ticker", "")).upper().strip()
    if not ticker:
        return jsonify({"error": "Ticker is required."}), 400

    removed = store.clear_review_item(ticker)
    queue = store.get_review_queue()
    return jsonify({"ok": True, "ticker": ticker, "removed": removed, "review_count": len(queue["items"])})


@app.post("/api/semantic")
def save_semantic():
    payload = request.get_json(force=True, silent=True) or {}
    try:
        ticker = str(payload["ticker"]).upper()
        score = float(payload["score"])
        themes_list = [str(theme) for theme in payload.get("themes", [])]
        notes = str(payload.get("notes", "")).strip()
        if score < 0 or score > 100:
            raise ValueError("Score must be between 0 and 100.")
        if not themes_list:
            raise ValueError("Select at least one semantic theme.")
    except Exception as exc:
        return jsonify({"error": f"Invalid semantic update: {exc}"}), 400

    config = load_theme_config()
    allowed_themes = set(config.get("themes", {}))
    invalid_themes = [theme for theme in themes_list if theme not in allowed_themes]
    if invalid_themes:
        return jsonify({"error": f"Invalid semantic theme: {', '.join(invalid_themes)}"}), 400

    store.upsert_company(
        ticker,
        {
            "score": round(score, 1),
            "themes": themes_list,
            "notes": notes or "Curated in the dashboard.",
            "confidence": 1.0,
            "research_status": "curated",
            "source": "Dashboard review",
        },
    )
    return jsonify({"ok": True, "ticker": ticker})


@app.get("/api/market/history")
def market_history():
    etf = (request.args.get("etf") or "SPY").upper()
    limit = int(request.args.get("limit", "5") or "5")
    items = store.list_screen_snapshots(etf, limit=limit)
    history = []
    for payload in items:
        market = payload.get("market_condition") or {}
        if not market:
            continue
        history.append(
            {
                "generated_at": payload.get("generated_at"),
                "overall_score": market.get("overall_score"),
                "overall_regime": market.get("overall_regime"),
                "demark": _compact_signal(market.get("demark")),
                "structure": _compact_signal(market.get("structural_weakness")),
                "tech": _compact_signal(market.get("technology_support")),
                "credit": _compact_signal(market.get("credit_risk")),
            }
        )
    for index, item in enumerate(history):
        next_item = history[index + 1] if index + 1 < len(history) else None
        item["score_change"] = round(item["overall_score"] - next_item["overall_score"], 1) if next_item else None
    return jsonify({"items": history})


@app.get("/api/market/scores")
def market_scores():
    requested = request.args.get("etfs", "")
    refresh = (request.args.get("refresh", "false")).lower() == "true"
    allowed = supported_etfs()
    if requested:
        etfs = [item.strip().upper() for item in requested.split(",") if item.strip().upper() in allowed]
    else:
        etfs = allowed

    items = []
    for etf in etfs:
        try:
            report = build_market_condition(etf, refresh=refresh)
            items.append(
                {"etf": etf, "score": report.overall_score, "regime": report.overall_regime, "summary": report.summary}
            )
        except Exception as exc:
            items.append({"etf": etf, "score": None, "regime": "unavailable", "error": str(exc)})

    items.sort(key=lambda item: item["score"] if item["score"] is not None else -1, reverse=True)
    return jsonify({"items": items})


@app.get("/api/runs")
def runs():
    etf = (request.args.get("etf") or "SPY").upper()
    limit = int(request.args.get("limit", "5") or "5")
    items = store.list_screen_snapshots(etf, limit=limit)
    results = []
    for payload in items:
        rows = payload.get("rated_holdings", [])
        if not rows:
            continue
        counts = _rating_counts(rows)
        market = payload.get("market_condition") or {}
        high_certainty = sum(1 for row in rows if _row_confidence(row) >= 0.85)
        results.append(
            {
                "etf": payload.get("etf"),
                "generated_at": payload.get("generated_at"),
                "screen_size": len(rows),
                "holdings_count": payload.get("holdings_count"),
                "buy": counts.get("Buy", 0),
                "hold": counts.get("Hold", 0),
                "sell": counts.get("Sell", 0),
                "average_final_score": round(_average_score(rows), 1),
                "high_certainty_percent": round((high_certainty / len(rows)) * 100, 1) if rows else 0,
                "market_score": market.get("overall_score"),
                "market_regime": market.get("overall_regime"),
                "top_buys": [
                    row.get("holding", {}).get("ticker")
                    for row in rows
                    if row.get("rating") == "Buy" and row.get("holding", {}).get("ticker")
                ][:5],
            }
        )
    return jsonify({"items": results})


def _curated_items() -> list:
    config = load_theme_config()
    theme_labels = {
        key: details.get("label", key) if isinstance(details, dict) else key
        for key, details in config.get("themes", {}).items()
    }
    items = []
    for ticker, entry in config.get("companies", {}).items():
        if not isinstance(entry, dict):
            continue
        themes_map = _company_theme_exposures(entry.get("themes", {}))
        items.append(
            {
                "ticker": ticker,
                "score": entry.get("score"),
                "source": entry.get("source", ""),
                "notes": entry.get("notes", ""),
                "theme_count": len(themes_map),
                "themes": [
                    {"key": theme, "label": theme_labels.get(theme, theme), "exposure": exposure}
                    for theme, exposure in themes_map.items()
                ],
            }
        )
    return sorted(
        items,
        key=lambda item: ("promoted" not in str(item.get("source", "")).lower(), str(item.get("ticker", ""))),
    )


def _company_theme_exposures(themes_value) -> dict:
    if isinstance(themes_value, dict):
        return {str(theme): _clean_exposure(exposure) for theme, exposure in themes_value.items()}
    if isinstance(themes_value, list):
        return {str(theme): "direct" for theme in themes_value}
    return {}


def _review_theme_exposures(review_item: dict, allowed_themes: set) -> dict:
    semantic = review_item.get("semantic", {})
    exposures = semantic.get("exposures") if isinstance(semantic, dict) else {}
    if isinstance(exposures, dict):
        clean_exposures = {
            str(theme): _clean_exposure(exposure)
            for theme, exposure in exposures.items()
            if str(theme) in allowed_themes
        }
        if clean_exposures:
            return clean_exposures

    labels = {details.get("label"): key for key, details in load_theme_config().get("themes", {}).items()}
    clean_exposures = {}
    for label in review_item.get("themes", []):
        label_text = str(label)
        exposure = "direct"
        if "(" in label_text and label_text.endswith(")"):
            label_text, exposure_text = label_text.rsplit("(", 1)
            exposure = exposure_text.rstrip(")").strip()
        theme_key = labels.get(label_text.strip())
        if theme_key in allowed_themes:
            clean_exposures[theme_key] = _clean_exposure(exposure)
    return clean_exposures


def _clean_exposure(value) -> str:
    exposure = str(value).lower().strip()
    return exposure if exposure in {"direct", "indirect", "weak"} else "direct"


def _compact_signal(signal) -> dict:
    signal = signal or {}
    return {"score": signal.get("score"), "regime": signal.get("regime")}


def _rating_counts(rows: list) -> dict:
    counts: dict = {}
    for row in rows:
        rating = row.get("rating")
        counts[rating] = counts.get(rating, 0) + 1
    return counts


def _average_score(rows: list) -> float:
    scores = [row.get("final_score") for row in rows if row.get("final_score") is not None]
    return sum(scores) / len(scores) if scores else 0.0


def _row_confidence(row: dict) -> float:
    try:
        return float(row.get("semantic", {}).get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0

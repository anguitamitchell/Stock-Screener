import os
import time

from flask import Flask, jsonify, request

from self_screener.pipeline import (
    deserialize_progress,
    finish_screen,
    is_screen_complete,
    screen_next_holding,
    serialize_progress,
    start_screen,
    _report_to_json,
)
from self_screener.storage import store

app = Flask(__name__)

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")
# Vercel Hobby caps functions at 10s; leave headroom for response overhead.
# Bump this (and the Function Max Duration in Vercel project settings) if
# the account is ever upgraded to Pro (up to 300s).
CHUNK_TIME_BUDGET_SECONDS = 7
STALE_LOCK_SECONDS = 60


@app.before_request
def _require_auth():
    if request.path == "/api/jobs/health":
        return None
    token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
    if not ADMIN_TOKEN or token != ADMIN_TOKEN:
        return jsonify({"error": "unauthorized"}), 401


@app.get("/api/jobs/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/jobs")
def create_job():
    payload = request.get_json(force=True, silent=True) or {}
    etf = str(payload.get("etf", "")).upper().strip()
    if not etf:
        return jsonify({"error": "etf is required"}), 400

    params = {
        "etf": etf,
        "limit": payload.get("limit"),
        "refresh": bool(payload.get("refresh", False)),
        "include_prices": bool(payload.get("include_prices", True)),
        "include_market": bool(payload.get("include_market", True)),
    }
    job_id = store.create_job(etf, params)
    return jsonify({"job_id": job_id}), 201


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    job = store.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(_public_job(job))


@app.post("/api/jobs/<job_id>/continue")
def continue_job(job_id):
    job = store.claim_job(job_id, stale_after_seconds=STALE_LOCK_SECONDS)
    if job is None:
        return jsonify({"error": "job not found, or already being processed"}), 409
    if job["status"] in ("done", "error"):
        return jsonify(_public_job(job))

    try:
        _run_chunk(job)
    except Exception as exc:
        store.update_job(job_id, status="error", error=str(exc), locked_at=None)

    job = store.get_job(job_id)
    return jsonify(_public_job(job))


def _run_chunk(job: dict) -> None:
    job_id = job["id"]
    params = job["params"]
    deadline = time.monotonic() + CHUNK_TIME_BUDGET_SECONDS

    cursor = job.get("cursor")
    if cursor is None:
        progress = start_screen(
            params["etf"],
            limit=params.get("limit"),
            refresh=params.get("refresh", False),
            include_prices=params.get("include_prices", True),
            include_market=params.get("include_market", True),
        )
    else:
        progress = deserialize_progress(cursor)

    while not is_screen_complete(progress) and time.monotonic() < deadline:
        progress = screen_next_holding(progress)
        store.update_job(
            job_id,
            progress={
                "processed": len(progress.rated),
                "total": len(progress.rated) + len(progress.remaining),
                "current_ticker": progress.remaining[0].ticker if progress.remaining else None,
            },
        )

    if is_screen_complete(progress):
        report = finish_screen(progress)
        payload = _report_to_json(report)
        payload["comparison"] = _compare_to_previous_snapshot(report.etf, payload)
        store.set_screen_report(report.etf, payload, payload["generated_at"])
        store.save_screen_snapshot(report.etf, len(report.rated_holdings), payload["generated_at"], payload)
        store.update_job(job_id, status="done", result=payload, cursor=None, locked_at=None)
    else:
        store.update_job(job_id, cursor=serialize_progress(progress), locked_at=None)


def _compare_to_previous_snapshot(etf: str, payload: dict) -> dict:
    current_rows = payload.get("rated_holdings", [])
    previous = store.get_latest_snapshot_by_size(etf, len(current_rows))
    if not previous:
        return {"available": False, "summary": "No previous snapshot yet."}

    previous_rows = previous.get("rated_holdings", [])
    current_by_ticker = _rows_by_ticker(current_rows)
    previous_by_ticker = _rows_by_ticker(previous_rows)

    current_buy = {ticker for ticker, row in current_by_ticker.items() if row.get("rating") == "Buy"}
    previous_buy = {ticker for ticker, row in previous_by_ticker.items() if row.get("rating") == "Buy"}
    current_counts = _rating_counts(current_rows)
    previous_counts = _rating_counts(previous_rows)

    movers = []
    current_ranks = {row.get("holding", {}).get("ticker"): index + 1 for index, row in enumerate(current_rows)}
    previous_ranks = {row.get("holding", {}).get("ticker"): index + 1 for index, row in enumerate(previous_rows)}
    for ticker, current_rank in current_ranks.items():
        previous_rank = previous_ranks.get(ticker)
        if previous_rank is None:
            continue
        change = previous_rank - current_rank
        if change:
            movers.append({"ticker": ticker, "rank_change": change})
    movers.sort(key=lambda item: abs(item["rank_change"]), reverse=True)

    avg_change = _average_score(current_rows) - _average_score(previous_rows)
    return {
        "available": True,
        "previous_generated_at": previous.get("generated_at"),
        "buy_change": current_counts.get("Buy", 0) - previous_counts.get("Buy", 0),
        "hold_change": current_counts.get("Hold", 0) - previous_counts.get("Hold", 0),
        "sell_change": current_counts.get("Sell", 0) - previous_counts.get("Sell", 0),
        "average_final_score_change": round(avg_change, 1),
        "new_buys": sorted(current_buy - previous_buy)[:5],
        "dropped_buys": sorted(previous_buy - current_buy)[:5],
        "biggest_movers": movers[:5],
    }


def _rows_by_ticker(rows: list) -> dict:
    return {row.get("holding", {}).get("ticker"): row for row in rows if row.get("holding", {}).get("ticker")}


def _rating_counts(rows: list) -> dict:
    counts: dict = {}
    for row in rows:
        rating = row.get("rating")
        counts[rating] = counts.get(rating, 0) + 1
    return counts


def _average_score(rows: list) -> float:
    scores = [row.get("final_score") for row in rows if row.get("final_score") is not None]
    return sum(scores) / len(scores) if scores else 0.0


def _public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "etf": job["etf"],
        "status": job["status"],
        "progress": job.get("progress") or {},
        "result": job.get("result"),
        "error": job.get("error"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }

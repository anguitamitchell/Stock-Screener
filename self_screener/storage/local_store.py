"""Filesystem-backed StorageAdapter — used for local dev and the CLI.

Mirrors the directory layout the original filesystem-only code used
(data/cache/..., data/reports/..., data/review/..., config/semantic_themes.json)
so `data/` stays a familiar, gitignored scratch area.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from self_screener.storage.base import StorageAdapter

DATA_DIR = Path("data")
HOLDINGS_CACHE_DIR = DATA_DIR / "cache"
PRICE_CACHE_DIR = DATA_DIR / "cache" / "prices"
RESEARCH_CACHE_DIR = DATA_DIR / "cache" / "research"
MACRO_CACHE_DIR = DATA_DIR / "cache" / "macro"
REPORT_DIR = DATA_DIR / "reports"
SNAPSHOT_DIR = DATA_DIR / "reports" / "snapshots"
REVIEW_QUEUE_PATH = DATA_DIR / "review" / "semantic_review_queue.json"
TRADES_PATH = DATA_DIR / "portfolio" / "trades.json"
THEME_CONFIG_PATH = Path("config") / "semantic_themes.json"
JOBS_DIR = DATA_DIR / "jobs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_seconds(cached_at: str) -> float:
    parsed = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _read_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _sanitize_key(key: str) -> str:
    return key.replace(":", "_").replace("/", "_")


class LocalStore(StorageAdapter):
    # -- ETF holdings -----------------------------------------------------
    def get_holdings(self, symbol: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        payload = _read_json(HOLDINGS_CACHE_DIR / f"{symbol}_holdings.json")
        if not payload:
            return None
        if max_age_seconds is not None and _age_seconds(payload["cached_at"]) > max_age_seconds:
            return None
        return payload["holdings"]

    def set_holdings(self, symbol: str, holdings: List[dict], source: str = "") -> None:
        _write_json(
            HOLDINGS_CACHE_DIR / f"{symbol}_holdings.json",
            {"cached_at": _now_iso(), "holdings": holdings, "source": source},
        )

    # -- Price snapshot / history ------------------------------------------
    def get_price_snapshot(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        payload = _read_json(PRICE_CACHE_DIR / f"{ticker}.json")
        if not payload:
            return None
        if max_age_seconds is not None and _age_seconds(payload["cached_at"]) > max_age_seconds:
            return None
        return payload["snapshot"]

    def set_price_snapshot(self, ticker: str, snapshot: dict) -> None:
        _write_json(PRICE_CACHE_DIR / f"{ticker}.json", {"cached_at": _now_iso(), "snapshot": snapshot})

    def get_price_snapshots(self, tickers: List[str], max_age_seconds: Optional[int] = None) -> Dict[str, dict]:
        results: Dict[str, dict] = {}
        for ticker in tickers:
            snapshot = self.get_price_snapshot(ticker, max_age_seconds=max_age_seconds)
            if snapshot is not None:
                results[ticker] = snapshot
        return results

    def set_price_snapshots(self, snapshots: Dict[str, dict]) -> None:
        for ticker, snapshot in snapshots.items():
            self.set_price_snapshot(ticker, snapshot)

    def get_price_history(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        payload = _read_json(PRICE_CACHE_DIR / f"{ticker}_history.json")
        if not payload:
            return None
        if max_age_seconds is not None and _age_seconds(payload["cached_at"]) > max_age_seconds:
            return None
        return payload["bars"]

    def set_price_history(self, ticker: str, bars: List[dict]) -> None:
        _write_json(PRICE_CACHE_DIR / f"{ticker}_history.json", {"cached_at": _now_iso(), "bars": bars})

    def get_price_histories(self, tickers: List[str], max_age_seconds: Optional[int] = None) -> Dict[str, List[dict]]:
        results: Dict[str, List[dict]] = {}
        for ticker in tickers:
            bars = self.get_price_history(ticker, max_age_seconds=max_age_seconds)
            if bars is not None:
                results[ticker] = bars
        return results

    def set_price_histories(self, histories: Dict[str, List[dict]]) -> None:
        for ticker, bars in histories.items():
            self.set_price_history(ticker, bars)

    # -- Company research ---------------------------------------------------
    def get_research(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        payload = _read_json(RESEARCH_CACHE_DIR / f"{ticker}.json")
        if not payload:
            return None
        if max_age_seconds is not None and _age_seconds(payload["cached_at"]) > max_age_seconds:
            return None
        return payload["research"]

    def set_research(self, ticker: str, research: dict) -> None:
        _write_json(RESEARCH_CACHE_DIR / f"{ticker}.json", {"cached_at": _now_iso(), "research": research})

    # -- Macro / breadth observations ---------------------------------------
    def get_macro_observation(self, series_key: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        payload = _read_json(MACRO_CACHE_DIR / f"{_sanitize_key(series_key)}.json")
        if not payload:
            return None
        if max_age_seconds is not None and _age_seconds(payload["cached_at"]) > max_age_seconds:
            return None
        return payload["observation"]

    def get_macro_observation_any(self, series_key: str) -> Optional[dict]:
        payload = _read_json(MACRO_CACHE_DIR / f"{_sanitize_key(series_key)}.json")
        return payload["observation"] if payload else None

    def set_macro_observation(self, series_key: str, observation: dict) -> None:
        _write_json(
            MACRO_CACHE_DIR / f"{_sanitize_key(series_key)}.json",
            {"cached_at": _now_iso(), "observation": observation},
        )

    # -- Screen / market-condition reports (latest wins) ---------------------
    def get_screen_report(self, etf: str) -> Optional[dict]:
        payload = _read_json(REPORT_DIR / f"{etf}_screen.json")
        return payload["report"] if payload else None

    def set_screen_report(self, etf: str, report: dict, generated_at: str) -> None:
        _write_json(
            REPORT_DIR / f"{etf}_screen.json",
            {"generated_at": generated_at, "updated_at": _now_iso(), "report": report},
        )

    def get_market_condition_report(self, ticker: str) -> Optional[dict]:
        payload = _read_json(REPORT_DIR / f"{ticker}_market_condition.json")
        return payload["report"] if payload else None

    def set_market_condition_report(self, ticker: str, report: dict, generated_at: str) -> None:
        _write_json(
            REPORT_DIR / f"{ticker}_market_condition.json",
            {"generated_at": generated_at, "updated_at": _now_iso(), "report": report},
        )

    # -- Screen run/snapshot history -----------------------------------------
    def save_screen_snapshot(self, etf: str, screen_size: int, generated_at: str, payload: dict) -> None:
        stamp = generated_at.replace(":", "").replace("-", "").replace("T", "_").replace("Z", "") or "latest"
        _write_json(
            SNAPSHOT_DIR / f"{etf}_{stamp}.json",
            {"etf": etf, "screen_size": screen_size, "generated_at": generated_at, "payload": payload},
        )

    def list_screen_snapshots(self, etf: str, limit: int = 5) -> List[dict]:
        if not SNAPSHOT_DIR.exists():
            return []
        items = []
        for path in reversed(sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))):
            payload = _read_json(path)
            if payload:
                items.append(payload["payload"])
            if len(items) >= limit:
                break
        return items

    def get_latest_snapshot_by_size(self, etf: str, screen_size: int) -> Optional[dict]:
        if not SNAPSHOT_DIR.exists():
            return None
        for path in reversed(sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))):
            payload = _read_json(path)
            if payload and payload.get("screen_size") == screen_size:
                return payload["payload"]
        return None

    # -- Semantic review queue ------------------------------------------------
    def get_review_queue(self) -> dict:
        payload = _read_json(REVIEW_QUEUE_PATH)
        if not payload or not isinstance(payload, dict):
            return {"items": []}
        payload.setdefault("items", [])
        return payload

    def upsert_review_item(self, ticker: str, item: dict) -> None:
        queue = self.get_review_queue()
        items = [existing for existing in queue["items"] if existing.get("ticker") != ticker]
        item = dict(item)
        item["ticker"] = ticker
        item["updated_at"] = _now_iso()
        items.insert(0, item)
        queue["items"] = items
        _write_json(REVIEW_QUEUE_PATH, queue)

    def promote_review_item(self, ticker: str) -> None:
        queue = self.get_review_queue()
        for item in queue["items"]:
            if item.get("ticker") == ticker:
                item["promoted"] = True
                item["promoted_at"] = _now_iso()
        _write_json(REVIEW_QUEUE_PATH, queue)

    def clear_review_item(self, ticker: str) -> int:
        queue = self.get_review_queue()
        original_count = len(queue["items"])
        queue["items"] = [item for item in queue["items"] if item.get("ticker") != ticker]
        _write_json(REVIEW_QUEUE_PATH, queue)
        return original_count - len(queue["items"])

    # -- Semantic theme config (themes + curated companies) -------------------
    def get_theme_config(self) -> dict:
        payload = _read_json(THEME_CONFIG_PATH)
        if not payload:
            return {"themes": {}, "companies": {}}
        return payload

    def upsert_company(self, ticker: str, entry: dict) -> None:
        config = self.get_theme_config()
        config.setdefault("companies", {})[ticker] = entry
        _write_json(THEME_CONFIG_PATH, config)

    # -- Portfolio trades ------------------------------------------------------
    def list_trades(self) -> List[dict]:
        payload = _read_json(TRADES_PATH)
        trades = payload.get("trades", []) if isinstance(payload, dict) else []
        return sorted(trades, key=lambda t: (t.get("trade_date", ""), t.get("created_at", "")))

    def insert_trade(self, trade: dict) -> dict:
        trades = self.list_trades()
        row = dict(trade)
        row["id"] = str(uuid.uuid4())
        row["created_at"] = _now_iso()
        trades.append(row)
        _write_json(TRADES_PATH, {"trades": trades})
        return row

    def delete_trade(self, trade_id: str) -> int:
        trades = self.list_trades()
        remaining = [t for t in trades if t.get("id") != trade_id]
        _write_json(TRADES_PATH, {"trades": remaining})
        return len(trades) - len(remaining)

    # -- Report lookups for the portfolio verdict engine ----------------------
    def list_screen_reports(self) -> List[dict]:
        if not REPORT_DIR.exists():
            return []
        items = []
        for path in REPORT_DIR.glob("*_screen.json"):
            payload = _read_json(path)
            if not payload or "report" not in payload:
                continue
            report = payload["report"]
            items.append(
                {
                    "etf": report.get("etf") or path.stem.replace("_screen", ""),
                    "generated_at": payload.get("generated_at", ""),
                    "report": report,
                }
            )
        return sorted(items, key=lambda item: item["generated_at"], reverse=True)

    def get_snapshot_before(self, etf: str, generated_at: str) -> Optional[dict]:
        if not SNAPSHOT_DIR.exists():
            return None
        for path in reversed(sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))):
            payload = _read_json(path)
            if payload and payload.get("generated_at", "") <= generated_at:
                return payload["payload"]
        return None

    # -- Background screen jobs -----------------------------------------------
    def create_job(self, etf: str, params: dict) -> str:
        job_id = str(uuid.uuid4())
        now = _now_iso()
        job = {
            "id": job_id,
            "etf": etf,
            "status": "pending",
            "params": params,
            "progress": {},
            "result": None,
            "error": None,
            "cursor": None,
            "attempt_count": 0,
            "locked_at": None,
            "created_at": now,
            "updated_at": now,
        }
        _write_json(JOBS_DIR / f"{job_id}.json", job)
        return job_id

    def get_job(self, job_id: str) -> Optional[dict]:
        return _read_json(JOBS_DIR / f"{job_id}.json")

    def claim_job(self, job_id: str, stale_after_seconds: int) -> Optional[dict]:
        job = self.get_job(job_id)
        if not job:
            return None
        if job["status"] in ("done", "error"):
            return job
        locked_at = job.get("locked_at")
        if locked_at and _age_seconds(locked_at) < stale_after_seconds:
            return None
        job["status"] = "running"
        job["locked_at"] = _now_iso()
        job["attempt_count"] = job.get("attempt_count", 0) + 1
        job["updated_at"] = _now_iso()
        _write_json(JOBS_DIR / f"{job_id}.json", job)
        return job

    def update_job(self, job_id: str, **fields) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = _now_iso()
        _write_json(JOBS_DIR / f"{job_id}.json", job)

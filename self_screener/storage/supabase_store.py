"""Supabase-backed StorageAdapter — used in production (Vercel).

Talks to Supabase exclusively with the service-role key. Every table has
row-level security enabled with no policies, so only the service role
(which bypasses RLS) can read or write — this client must never be
constructed with the anon key, and its instances must stay server-side.
"""

import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from postgrest.types import ReturnMethod
from supabase import Client, create_client

from self_screener.storage.base import StorageAdapter

# Fire-and-forget writes skip PostgREST's default row echo, which otherwise
# downloads every written row (screen job rows include the multi-KB cursor).
_MINIMAL = ReturnMethod.minimal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _age_seconds(cached_at: str) -> float:
    parsed = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _strip_none(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if value is not None}


def _first(rows: List[dict]) -> Optional[dict]:
    return rows[0] if rows else None


class SupabaseStore(StorageAdapter):
    def __init__(self, url: Optional[str] = None, service_role_key: Optional[str] = None):
        url = url or os.environ["SUPABASE_URL"]
        key = service_role_key or os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        self.client: Client = create_client(url, key)

    def _fresh_row(self, table: str, match: dict, max_age_seconds: Optional[int]) -> Optional[dict]:
        query = self.client.table(table).select("*")
        for column, value in match.items():
            query = query.eq(column, value)
        row = _first(query.execute().data)
        if not row:
            return None
        if max_age_seconds is not None and _age_seconds(row["cached_at"]) > max_age_seconds:
            return None
        return row

    # -- ETF holdings -----------------------------------------------------
    def get_holdings(self, symbol: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        row = self._fresh_row("holdings_cache", {"symbol": symbol}, max_age_seconds)
        return row["holdings"] if row else None

    def set_holdings(self, symbol: str, holdings: List[dict], source: str = "") -> None:
        self.client.table("holdings_cache").upsert(
            {"symbol": symbol, "holdings": holdings, "source": source, "cached_at": _now_iso()},
            on_conflict="symbol",
            returning=_MINIMAL,
        ).execute()

    # -- Price snapshot / history ------------------------------------------
    def get_price_snapshot(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        row = self._fresh_row("price_snapshot_cache", {"ticker": ticker}, max_age_seconds)
        return row["snapshot"] if row else None

    def set_price_snapshot(self, ticker: str, snapshot: dict) -> None:
        self.client.table("price_snapshot_cache").upsert(
            {"ticker": ticker, "snapshot": snapshot, "cached_at": _now_iso()},
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    def get_price_snapshots(self, tickers: List[str], max_age_seconds: Optional[int] = None) -> Dict[str, dict]:
        if not tickers:
            return {}
        rows = (
            self.client.table("price_snapshot_cache").select("*").in_("ticker", list(tickers)).execute().data
        )
        results: Dict[str, dict] = {}
        for row in rows:
            if max_age_seconds is not None and _age_seconds(row["cached_at"]) > max_age_seconds:
                continue
            results[row["ticker"]] = row["snapshot"]
        return results

    def set_price_snapshots(self, snapshots: Dict[str, dict]) -> None:
        if not snapshots:
            return
        now = _now_iso()
        rows = [{"ticker": ticker, "snapshot": snapshot, "cached_at": now} for ticker, snapshot in snapshots.items()]
        self.client.table("price_snapshot_cache").upsert(
            rows, on_conflict="ticker", returning=_MINIMAL
        ).execute()

    def get_price_history(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        row = self._fresh_row("price_history_cache", {"ticker": ticker}, max_age_seconds)
        return row["bars"] if row else None

    def set_price_history(self, ticker: str, bars: List[dict]) -> None:
        self.client.table("price_history_cache").upsert(
            {"ticker": ticker, "bars": bars, "cached_at": _now_iso()},
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    def get_price_histories(self, tickers: List[str], max_age_seconds: Optional[int] = None) -> Dict[str, List[dict]]:
        if not tickers:
            return {}
        rows = (
            self.client.table("price_history_cache").select("*").in_("ticker", list(tickers)).execute().data
        )
        results: Dict[str, List[dict]] = {}
        for row in rows:
            if max_age_seconds is not None and _age_seconds(row["cached_at"]) > max_age_seconds:
                continue
            results[row["ticker"]] = row["bars"]
        return results

    def set_price_histories(self, histories: Dict[str, List[dict]]) -> None:
        if not histories:
            return
        now = _now_iso()
        rows = [{"ticker": ticker, "bars": bars, "cached_at": now} for ticker, bars in histories.items()]
        self.client.table("price_history_cache").upsert(
            rows, on_conflict="ticker", returning=_MINIMAL
        ).execute()

    # -- Company research ---------------------------------------------------
    def get_research(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        row = self._fresh_row("company_research_cache", {"ticker": ticker}, max_age_seconds)
        return row["research"] if row else None

    def set_research(self, ticker: str, research: dict) -> None:
        self.client.table("company_research_cache").upsert(
            {"ticker": ticker, "research": research, "cached_at": _now_iso()},
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    # -- Macro / breadth observations ---------------------------------------
    def get_macro_observation(self, series_key: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        row = self._fresh_row("macro_observation_cache", {"series_key": series_key}, max_age_seconds)
        return row["observation"] if row else None

    def get_macro_observation_any(self, series_key: str) -> Optional[dict]:
        row = self._fresh_row("macro_observation_cache", {"series_key": series_key}, None)
        return row["observation"] if row else None

    def set_macro_observation(self, series_key: str, observation: dict) -> None:
        self.client.table("macro_observation_cache").upsert(
            {"series_key": series_key, "observation": observation, "cached_at": _now_iso()},
            on_conflict="series_key",
            returning=_MINIMAL,
        ).execute()

    # -- Screen / market-condition reports (latest wins) ---------------------
    def get_screen_report(self, etf: str) -> Optional[dict]:
        row = _first(self.client.table("screen_reports").select("*").eq("etf", etf).execute().data)
        return row["report"] if row else None

    def set_screen_report(self, etf: str, report: dict, generated_at: str) -> None:
        self.client.table("screen_reports").upsert(
            {"etf": etf, "report": report, "generated_at": generated_at, "updated_at": _now_iso()},
            on_conflict="etf",
            returning=_MINIMAL,
        ).execute()

    def get_market_condition_report(self, ticker: str) -> Optional[dict]:
        row = _first(
            self.client.table("market_condition_reports").select("*").eq("ticker", ticker).execute().data
        )
        return row["report"] if row else None

    def set_market_condition_report(self, ticker: str, report: dict, generated_at: str) -> None:
        self.client.table("market_condition_reports").upsert(
            {"ticker": ticker, "report": report, "generated_at": generated_at, "updated_at": _now_iso()},
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    # -- Screen run/snapshot history -----------------------------------------
    def save_screen_snapshot(self, etf: str, screen_size: int, generated_at: str, payload: dict) -> None:
        self.client.table("screen_snapshots").insert(
            {"etf": etf, "screen_size": screen_size, "generated_at": generated_at, "payload": payload},
            returning=_MINIMAL,
        ).execute()

    def list_screen_snapshots(self, etf: str, limit: int = 5) -> List[dict]:
        rows = (
            self.client.table("screen_snapshots")
            .select("payload")
            .eq("etf", etf)
            .order("generated_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        return [row["payload"] for row in rows]

    def get_latest_snapshot_by_size(self, etf: str, screen_size: int) -> Optional[dict]:
        row = _first(
            self.client.table("screen_snapshots")
            .select("payload")
            .eq("etf", etf)
            .eq("screen_size", screen_size)
            .order("generated_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return row["payload"] if row else None

    # -- Semantic review queue ------------------------------------------------
    def get_review_queue(self) -> dict:
        rows = (
            self.client.table("semantic_review_queue")
            .select("*")
            .order("updated_at", desc=True)
            .execute()
            .data
        )
        items = []
        for row in rows:
            item = dict(row["item"])
            item["ticker"] = row["ticker"]
            item["updated_at"] = row["updated_at"]
            if row.get("promoted"):
                item["promoted"] = True
                item["promoted_at"] = row.get("promoted_at")
            items.append(item)
        return {"items": items}

    def upsert_review_item(self, ticker: str, item: dict) -> None:
        item = dict(item)
        item["ticker"] = ticker
        item["updated_at"] = _now_iso()
        self.client.table("semantic_review_queue").upsert(
            {"ticker": ticker, "item": item, "updated_at": item["updated_at"]},
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    def promote_review_item(self, ticker: str) -> None:
        self.client.table("semantic_review_queue").update(
            {"promoted": True, "promoted_at": _now_iso()}, returning=_MINIMAL
        ).eq("ticker", ticker).execute()

    def clear_review_item(self, ticker: str) -> int:
        response = self.client.table("semantic_review_queue").delete().eq("ticker", ticker).execute()
        return len(response.data or [])

    # -- Semantic theme config (themes + curated companies) -------------------
    def get_theme_config(self) -> dict:
        theme_rows = self.client.table("semantic_themes").select("*").execute().data
        company_rows = self.client.table("semantic_companies").select("*").execute().data
        themes = {row["theme_key"]: {"label": row["label"], "weight": row["weight"]} for row in theme_rows}
        companies = {}
        for row in company_rows:
            entry = _strip_none(
                {
                    "score": row.get("score"),
                    "themes": row.get("themes"),
                    "notes": row.get("notes"),
                    "confidence": row.get("confidence"),
                    "source": row.get("source"),
                    "research_status": row.get("research_status"),
                }
            )
            companies[row["ticker"]] = entry
        return {"themes": themes, "companies": companies}

    def upsert_company(self, ticker: str, entry: dict) -> None:
        self.client.table("semantic_companies").upsert(
            {
                "ticker": ticker,
                "score": entry.get("score"),
                "themes": entry.get("themes", {}),
                "notes": entry.get("notes"),
                "confidence": entry.get("confidence"),
                "source": entry.get("source"),
                "research_status": entry.get("research_status"),
                "updated_at": _now_iso(),
            },
            on_conflict="ticker",
            returning=_MINIMAL,
        ).execute()

    # -- Portfolio trades ------------------------------------------------------
    def list_trades(self) -> List[dict]:
        return (
            self.client.table("portfolio_trades")
            .select("*")
            .order("trade_date", desc=False)
            .order("created_at", desc=False)
            .execute()
            .data
        )

    def insert_trade(self, trade: dict) -> dict:
        return _first(self.client.table("portfolio_trades").insert(trade).execute().data)

    def delete_trade(self, trade_id: str) -> int:
        response = self.client.table("portfolio_trades").delete().eq("id", trade_id).execute()
        return len(response.data or [])

    # -- Report lookups for the portfolio verdict engine ----------------------
    def list_screen_reports(self) -> List[dict]:
        return (
            self.client.table("screen_reports")
            .select("etf, generated_at, report")
            .order("generated_at", desc=True)
            .execute()
            .data
        )

    def get_snapshot_before(self, etf: str, generated_at: str) -> Optional[dict]:
        row = _first(
            self.client.table("screen_snapshots")
            .select("payload")
            .eq("etf", etf)
            .lte("generated_at", generated_at)
            .order("generated_at", desc=True)
            .limit(1)
            .execute()
            .data
        )
        return row["payload"] if row else None

    # -- Background screen jobs -----------------------------------------------
    def create_job(self, etf: str, params: dict) -> str:
        row = _first(
            self.client.table("screen_jobs")
            .insert({"etf": etf, "status": "pending", "params": params})
            .execute()
            .data
        )
        return row["id"]

    def get_job(self, job_id: str) -> Optional[dict]:
        return _first(self.client.table("screen_jobs").select("*").eq("id", job_id).execute().data)

    def claim_job(self, job_id: str, stale_after_seconds: int) -> Optional[dict]:
        job = self.get_job(job_id)
        if not job:
            return None
        if job["status"] in ("done", "error"):
            return job
        locked_at = job.get("locked_at")
        if locked_at and _age_seconds(locked_at) < stale_after_seconds:
            return None
        row = _first(
            self.client.table("screen_jobs")
            .update(
                {
                    "status": "running",
                    "locked_at": _now_iso(),
                    "attempt_count": job.get("attempt_count", 0) + 1,
                }
            )
            .eq("id", job_id)
            .execute()
            .data
        )
        return row

    def update_job(self, job_id: str, **fields) -> None:
        self.client.table("screen_jobs").update(fields, returning=_MINIMAL).eq("id", job_id).execute()

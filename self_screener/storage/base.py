"""Storage adapter interface backing SignalDeck's cache/report persistence.

Both local_store (filesystem, for local dev/CLI use) and supabase_store
(production) implement this same set of methods so provider, screener, and
pipeline code can stay agnostic to which backend is active.

For every cache-style method, ``max_age_seconds=None`` means "return
whatever is cached, regardless of age" (a cache miss only if nothing has
ever been written) — matching the original filesystem code's "exists and
not refresh" behavior. Passing a number enforces that freshness window
instead, returning None (cache miss) if the entry is older.
"""

from typing import Dict, List, Optional


class StorageAdapter:
    # -- ETF holdings -----------------------------------------------------
    def get_holdings(self, symbol: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        raise NotImplementedError

    def set_holdings(self, symbol: str, holdings: List[dict], source: str = "") -> None:
        raise NotImplementedError

    # -- Price snapshot / history ------------------------------------------
    def get_price_snapshot(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        raise NotImplementedError

    def set_price_snapshot(self, ticker: str, snapshot: dict) -> None:
        raise NotImplementedError

    def get_price_history(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[List[dict]]:
        raise NotImplementedError

    def set_price_history(self, ticker: str, bars: List[dict]) -> None:
        raise NotImplementedError

    # -- Company research ---------------------------------------------------
    def get_research(self, ticker: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        raise NotImplementedError

    def set_research(self, ticker: str, research: dict) -> None:
        raise NotImplementedError

    # -- Macro / breadth observations ---------------------------------------
    def get_macro_observation(self, series_key: str, max_age_seconds: Optional[int] = None) -> Optional[dict]:
        raise NotImplementedError

    def get_macro_observation_any(self, series_key: str) -> Optional[dict]:
        """TTL-ignoring read, for FRED/Barchart's stale-fallback-on-fetch-failure path."""
        raise NotImplementedError

    def set_macro_observation(self, series_key: str, observation: dict) -> None:
        raise NotImplementedError

    # -- Screen / market-condition reports (latest wins) ---------------------
    def get_screen_report(self, etf: str) -> Optional[dict]:
        raise NotImplementedError

    def set_screen_report(self, etf: str, report: dict, generated_at: str) -> None:
        raise NotImplementedError

    def get_market_condition_report(self, ticker: str) -> Optional[dict]:
        raise NotImplementedError

    def set_market_condition_report(self, ticker: str, report: dict, generated_at: str) -> None:
        raise NotImplementedError

    # -- Screen run/snapshot history -----------------------------------------
    def save_screen_snapshot(self, etf: str, screen_size: int, generated_at: str, payload: dict) -> None:
        raise NotImplementedError

    def list_screen_snapshots(self, etf: str, limit: int = 5) -> List[dict]:
        """Most-recent-first."""
        raise NotImplementedError

    def get_latest_snapshot_by_size(self, etf: str, screen_size: int) -> Optional[dict]:
        raise NotImplementedError

    # -- Semantic review queue ------------------------------------------------
    def get_review_queue(self) -> dict:
        """Returns {"items": [...]}, most-recently-updated first."""
        raise NotImplementedError

    def upsert_review_item(self, ticker: str, item: dict) -> None:
        raise NotImplementedError

    def promote_review_item(self, ticker: str) -> None:
        raise NotImplementedError

    def clear_review_item(self, ticker: str) -> int:
        """Returns the number of items removed (0 or 1)."""
        raise NotImplementedError

    # -- Semantic theme config (themes + curated companies) -------------------
    def get_theme_config(self) -> dict:
        """Returns {"themes": {key: {label, weight}}, "companies": {ticker: {...}}}."""
        raise NotImplementedError

    def upsert_company(self, ticker: str, entry: dict) -> None:
        raise NotImplementedError

    # -- Background screen jobs (execution model for long-running screens) ---
    def create_job(self, etf: str, params: dict) -> str:
        """Returns the new job's id."""
        raise NotImplementedError

    def get_job(self, job_id: str) -> Optional[dict]:
        raise NotImplementedError

    def claim_job(self, job_id: str, stale_after_seconds: int) -> Optional[dict]:
        """Claims the job for processing (sets status='running', locked_at=now()).

        Returns the job row if claimed or already terminal (done/error) — the
        caller should skip re-running a terminal job. Returns None if the job
        doesn't exist, or is actively locked by a non-stale in-flight claim.
        """
        raise NotImplementedError

    def update_job(self, job_id: str, **fields) -> None:
        raise NotImplementedError

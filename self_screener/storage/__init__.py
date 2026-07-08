"""Picks the active StorageAdapter: Supabase when configured, filesystem otherwise.

Provider/screener/pipeline code imports `store` from here rather than
choosing a backend itself.
"""

import os

from self_screener.storage.base import StorageAdapter

_store: "StorageAdapter | None" = None


def get_storage() -> StorageAdapter:
    global _store
    if _store is None:
        if os.environ.get("SUPABASE_URL"):
            from self_screener.storage.supabase_store import SupabaseStore

            _store = SupabaseStore()
        else:
            from self_screener.storage.local_store import LocalStore

            _store = LocalStore()
    return _store


store = get_storage()

"""One-time loader: config/semantic_themes.json -> Supabase semantic_themes/semantic_companies.

Idempotent by default — company rows already present in Supabase (e.g. edited
or promoted live via the running app) are left untouched. Pass --force to
overwrite them with the seed file's values instead.

Usage: python3 scripts/seed_supabase.py [--force]
Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the environment.
"""

import argparse
import json
from pathlib import Path

from self_screener.storage.supabase_store import SupabaseStore

THEME_CONFIG_PATH = Path("config") / "semantic_themes.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing company rows instead of skipping them.",
    )
    args = parser.parse_args()

    config = json.loads(THEME_CONFIG_PATH.read_text(encoding="utf-8"))
    store = SupabaseStore()

    themes = config.get("themes", {})
    for theme_key, details in themes.items():
        store.client.table("semantic_themes").upsert(
            {
                "theme_key": theme_key,
                "label": details.get("label", theme_key),
                "weight": details.get("weight", 0),
            },
            on_conflict="theme_key",
        ).execute()
    print(f"Seeded {len(themes)} themes.")

    existing_tickers = set()
    if not args.force:
        existing_tickers = {
            row["ticker"] for row in store.client.table("semantic_companies").select("ticker").execute().data
        }

    seeded = 0
    skipped = 0
    for ticker, entry in config.get("companies", {}).items():
        if not args.force and ticker in existing_tickers:
            skipped += 1
            continue
        store.upsert_company(ticker, entry)
        seeded += 1
    print(f"Seeded {seeded} companies ({skipped} skipped as already present; pass --force to overwrite).")


if __name__ == "__main__":
    main()

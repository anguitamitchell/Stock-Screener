-- SignalDeck: replaces the local filesystem data/ tree with Supabase tables.
-- All tables are accessed exclusively via the service-role key from Vercel
-- serverless functions (see self_screener/storage/supabase_store.py) — the
-- frontend never talks to Supabase directly, so RLS is enabled with no
-- policies on every table: only the service role (which bypasses RLS) can
-- read or write.

-- holdings_cache: replaces data/cache/{SYMBOL}_holdings.json
create table if not exists holdings_cache (
    symbol text primary key,
    holdings jsonb not null,
    source text,
    cached_at timestamptz not null default now()
);
alter table holdings_cache enable row level security;

-- price_snapshot_cache: replaces data/cache/prices/{TICKER}.json
create table if not exists price_snapshot_cache (
    ticker text primary key,
    snapshot jsonb not null,
    cached_at timestamptz not null default now()
);
alter table price_snapshot_cache enable row level security;

-- price_history_cache: replaces data/cache/prices/{TICKER}_history.json
create table if not exists price_history_cache (
    ticker text primary key,
    bars jsonb not null,
    cached_at timestamptz not null default now()
);
alter table price_history_cache enable row level security;

-- company_research_cache: replaces data/cache/research/{TICKER}.json
create table if not exists company_research_cache (
    ticker text primary key,
    research jsonb not null,
    cached_at timestamptz not null default now()
);
alter table company_research_cache enable row level security;

-- macro_observation_cache: replaces data/cache/macro/*.json and
-- data/cache/breadth/R3TW.json. series_key distinguishes sources, e.g.
-- "fred:BAMLH0A0HYM2" or "barchart:R3TW".
create table if not exists macro_observation_cache (
    series_key text primary key,
    observation jsonb not null,
    cached_at timestamptz not null default now()
);
alter table macro_observation_cache enable row level security;

-- screen_reports: replaces data/reports/{ETF}_screen.json (latest wins)
create table if not exists screen_reports (
    etf text primary key,
    report jsonb not null,
    generated_at timestamptz not null,
    updated_at timestamptz not null default now()
);
alter table screen_reports enable row level security;

-- market_condition_reports: replaces data/reports/{TICKER}_market_condition.json
create table if not exists market_condition_reports (
    ticker text primary key,
    report jsonb not null,
    generated_at timestamptz not null,
    updated_at timestamptz not null default now()
);
alter table market_condition_reports enable row level security;

-- screen_snapshots: replaces data/reports/snapshots/{ETF}_{timestamp}.json.
-- screen_size preserves the existing "compare like-for-like run sizes"
-- lookup used by web_app.py's _latest_snapshot.
create table if not exists screen_snapshots (
    id uuid primary key default gen_random_uuid(),
    etf text not null,
    screen_size integer not null,
    generated_at timestamptz not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);
create index if not exists screen_snapshots_etf_generated_at_idx
    on screen_snapshots (etf, generated_at desc);
create index if not exists screen_snapshots_etf_size_generated_at_idx
    on screen_snapshots (etf, screen_size, generated_at desc);
alter table screen_snapshots enable row level security;

-- semantic_review_queue: replaces data/review/semantic_review_queue.json
create table if not exists semantic_review_queue (
    ticker text primary key,
    item jsonb not null,
    promoted boolean not null default false,
    promoted_at timestamptz,
    updated_at timestamptz not null default now()
);
alter table semantic_review_queue enable row level security;

-- semantic_themes / semantic_companies: replace config/semantic_themes.json.
-- The JSON file stays in git as reviewable seed data; scripts/seed_supabase.py
-- loads it once. Runtime reads/writes go through these tables only.
create table if not exists semantic_themes (
    theme_key text primary key,
    label text not null,
    weight integer not null
);
alter table semantic_themes enable row level security;

create table if not exists semantic_companies (
    ticker text primary key,
    score numeric not null,
    themes jsonb not null default '{}'::jsonb,
    notes text,
    confidence numeric,
    source text,
    updated_at timestamptz not null default now()
);
alter table semantic_companies enable row level security;

-- screen_jobs: new — backs the background job/polling execution model for
-- long-running ETF screens (no filesystem equivalent existed before).
create table if not exists screen_jobs (
    id uuid primary key default gen_random_uuid(),
    etf text not null,
    status text not null default 'pending'
        check (status in ('pending', 'running', 'done', 'error')),
    params jsonb not null default '{}'::jsonb,
    progress jsonb not null default '{}'::jsonb,
    result jsonb,
    error text,
    cursor jsonb,
    attempt_count integer not null default 0,
    locked_at timestamptz,
    locked_by text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists screen_jobs_status_updated_at_idx
    on screen_jobs (status, updated_at);
alter table screen_jobs enable row level security;

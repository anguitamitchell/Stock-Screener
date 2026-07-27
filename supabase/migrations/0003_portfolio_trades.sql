-- Manual trade log for the portfolio view. Positions, lots, and P&L are all
-- derived server-side with FIFO matching (self_screener/portfolio.py);
-- nothing derived is persisted.
create table if not exists portfolio_trades (
    id uuid primary key default gen_random_uuid(),
    ticker text not null,
    side text not null check (side in ('buy', 'sell')),
    trade_date date not null,
    shares numeric not null check (shares > 0),
    price numeric not null check (price >= 0),
    fees numeric not null default 0 check (fees >= 0),
    notes text,
    created_at timestamptz not null default now()
);

create index if not exists portfolio_trades_ticker_date_idx
    on portfolio_trades (ticker, trade_date, created_at);

-- Same posture as every other table: RLS on, no policies — only the
-- service-role key (server-side) can touch it.
alter table portfolio_trades enable row level security;

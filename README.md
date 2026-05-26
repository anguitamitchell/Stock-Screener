# Self Generating Screener

A local ETF screening dashboard foundation. The first version uses `SPY` as the starting universe, fetches ETF holdings, keeps a local cache, and produces a ranked report with semantic, technical, and market-condition scores.

## What works now

- Fetch SPY holdings from State Street's public daily holdings workbook.
- Fetch supported SPDR ETF holdings from State Street public daily holdings workbooks.
- Fetch GRNY holdings from the official Granny Shots ETF holdings page, with public top-holding weights from StockAnalysis where available.
- Fetch QQQ holdings using the Nasdaq-100 public constituent list, with public top-holding weights from StockAnalysis where available.
- Use a source registry so each supported ETF declares its provider, source label, and quality caveat in one place.
- Cache raw and normalized holdings locally.
- Optionally fetch delayed historical price snapshots for each holding.
- Run a semantic screener based on your five narrative themes.
- Run a draft technical score based on trend, momentum, and relative strength versus SPY.
- Estimate mechanical entry zones, breakout triggers, stop exits, and target exits for every screened stock with price data.
- Produce a JSON report and optional local HTML dashboard.
- Run an interactive local app with ETF input, market backdrop, ranked table, stock drawer, source caveats, and semantic research evidence.

## Quick Start

```bash
python3 -m self_screener.cli screen SPY --limit 25
```

Include price inputs:

```bash
python3 -m self_screener.cli screen SPY --limit 25 --prices
```

Create a local dashboard:

```bash
python3 -m self_screener.cli screen SPY --limit 25 --prices --dashboard
```

Include the market backdrop in the dashboard:

```bash
python3 -m self_screener.cli screen SPY --limit 25 --prices --market --dashboard
```

Create a market condition report:

```bash
python3 -m self_screener.cli market --ticker SPY
```

Start the interactive local app:

```bash
python3 -m self_screener.cli serve
```

Start the browser-opening local app launcher:

```bash
python3 run_signaldeck.py
```

## Windows Packaging

Windows packaging instructions live in `packaging/README_WINDOWS.md`.

Short version on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
```

The distributable folder will be:

```text
dist\SignalDeck\
```

Supported ETFs currently include `SPY`, `GRNY`, `QQQ`, `DIA`, `XLK`, `XLF`, `XLE`, `XLU`, `XLY`, `XLP`, `XLV`, and `XLI`. `GRNY` uses the official Granny Shots holdings page plus StockAnalysis public top-holding weights. `QQQ` uses the Nasdaq-100 constituent list plus StockAnalysis public top-holding weights. Any residual tickers are included with placeholder weights, and the dashboard shows that caveat in a **Data Source** card.

Inside the app, click a stock row to open its drawer. The drawer shows semantic research evidence, certainty, technical breakdown, and rating logic for that holding.

Use the **Research Gaps** tab to work through stocks that still rely on fallback semantic logic or are missing a usable profile.

Semantic research is auto-approved by default when confidence is high enough. Low-confidence results stay in the ranking, but they are flagged as **Research not reliable** so they can be reviewed before you trust the narrative score.

For uncurated stocks, the app now attempts a lightweight company research pull from Yahoo Finance company profile/search data. If Yahoo does not return a full business summary, the app falls back to a Wikipedia intro summary. It caches sector, industry, and business summary fields under `data/cache/research/`, then scans that text for evidence tied to your five semantic themes.

Click a stock row to inspect the **Research evidence** panel. Use **Refresh Research** to force a fresh profile pull for that stock without refreshing the whole ETF.

Use **Mark for Review** in the stock drawer to save a local semantic review note for that ticker. Review items are stored in `data/review/semantic_review_queue.json`, with the current scores, confidence, themes, quality caveat, and decision summary. The latest saved review items appear in the sidebar's **Research** panel. From there, use **Promote** to turn a review item into a trusted curated semantic mapping.

The sidebar also shows the latest curated semantic mappings and lets you **Clear** review queue items once they are no longer needed. Click a curated mapping to inspect its score, source, notes, themes, and exposure tiers in the drawer.

Use **Refresh All Research** to bulk-refresh Yahoo/Wiki profile research for every stock in the current screen. Start with Top 25 or Top 100 before running a full ETF research pass.

Research evidence is graded by theme strength: **strong**, **moderate**, or **weak**. Strong and moderate evidence carry more weight in semantic scoring, while weak-only matches are less likely to be auto-approved.

Research evidence also includes an exposure tier: **direct**, **indirect**, or **weak**. Direct exposure receives the most semantic credit, indirect exposure receives partial credit, and weak/noisy exposure receives limited credit.

Curated semantic mappings support the same exposure tiers. A company can use the legacy list format, which defaults to direct exposure, or an object such as `"themes": {"enterprise_ai_leadership": "direct", "grid_data_center_demand": "indirect"}`.

Semantic theme matching includes basic context guardrails. For example, consumer beverage phrases like "energy drink" do not count as **Energy-security tailwinds** just because they contain the word "energy".

Run `python3 scripts/check_semantic_guardrails.py` to verify the current semantic guardrails against known tricky cases like `MNST`, `CELH`, `PEP`, `XOM`, and `NEE`.

The stock drawer's **Research evidence** panel also shows guardrail diagnostics when a theme was blocked, including the blocked theme, terms/context found, and the reason.

The live app shows research certainty separately from final score. Use **High Certainty**, **Medium**, and **Low** tabs to filter by confidence without changing the buy/hold/sell ranking formula.

The table includes a compact ETF summary card with Buy/Hold/Sell counts, average final/semantic/technical scores, clean Buy count, low-confidence count, and a one-sentence action readout.

The table also includes a **Quality** badge for each stock. It highlights the most important caveat first, such as **Clean setup**, **Low confidence**, **Weak technicals**, **Poor reward/risk**, **Mixed signal**, **Technical only**, or **Missing price**.

Use the quality filter tabs to isolate those groups, such as **Clean Setup**, **Low Confidence**, **Weak Technicals**, **Poor R/R**, **Mixed Signal**, or **Technical Only**.

Click table headers to sort the current filtered view. Useful sorts include **Final**, **Semantic**, **Technical**, **Certainty**, **3M vs SPY**, **Entry**, **R/R**, **Quality**, and **Rating**.

The app also shows a **Data Source** card under the ETF summary. For ordinary State Street ETFs, this confirms the sponsor workbook source; for GRNY and QQQ, it calls out the mixed public-source approach and how many holdings use placeholder residual weights. The app shortcut list comes from the same source registry, so future ETFs only need to be registered once.

The stock drawer includes a **Technical breakdown** with bullish/neutral/bearish labels for trend, momentum, and relative strength inputs.

The table and drawer include an **Entry / Exit Plan** for each stock with price data. These levels are mechanical technical estimates from delayed data: entry zone, breakout trigger, risk exit, target exits, and estimated reward/risk. They are not guaranteed fills or investment recommendations.

Each successful app screen saves a timestamped JSON snapshot under `data/reports/snapshots/`, and the dashboard shows the saved snapshot time/path under the table status.

The market backdrop card also shows recent market-condition history from saved screen snapshots, including overall score, regime, key signal scores, and score change versus the prior snapshot.

The sidebar shows recent saved screen runs for the current ETF. Click a run to inspect Buy/Hold/Sell counts, average final score, market score, high-certainty percentage, top Buys, and the snapshot path.

When a matching prior snapshot exists for the same ETF and screen size, the app shows a compact **Since Last Run** card with Buy count change, average final-score change, new Buys, dropped Buys, and biggest rank movers.

The app remembers the last ETF and screen size in the browser, and shows supported recent ETF shortcuts near the run controls.

The output report is written to:

```text
data/reports/SPY_screen.json
```

The dashboard is written to:

```text
data/dashboards/SPY_dashboard.html
```

## Project Layout

```text
self_screener/
  cli.py
  dashboard.py
  web_app.py
  models.py
  pipeline.py
  providers/
    ssga_holdings.py
    yahoo_prices.py
  screeners/
    semantic.py
    technical.py
    rating.py
docs/
  technical_worksheet.md
  market_condition_worksheet.md
  semantic_worksheet.md
config/
  semantic_themes.json
```

## Next Criteria Step

Keep refining the semantic and technical criteria in plain English. We will translate each criterion into:

- required data inputs
- scoring logic
- buy / hold / sell impact
- explanation text for the dashboard

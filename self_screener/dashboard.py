import html
from collections import Counter
from pathlib import Path

from self_screener.models import EtfScreenReport, RatedHolding


DASHBOARD_DIR = Path("data") / "dashboards"


def write_dashboard(report: EtfScreenReport) -> Path:
    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    path = DASHBOARD_DIR / f"{report.etf}_dashboard.html"
    path.write_text(_render_dashboard(report), encoding="utf-8")
    return path


def _render_dashboard(report: EtfScreenReport) -> str:
    rating_counts = Counter(item.rating for item in report.rated_holdings)
    price_count = sum(1 for item in report.rated_holdings if item.price and not item.price.error)
    average_weight = _average([item.holding.weight for item in report.rated_holdings])
    generated_at = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    benchmark_returns = _benchmark_returns(report)
    market_panel = _render_market_panel(report)
    rating_options = "\n".join(
        f'<option value="{_esc(rating)}">{_esc(rating)}</option>'
        for rating in sorted(rating_counts)
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_esc(report.etf)} Screener Dashboard</title>
  <style>
    :root {{
      --ink: #17201a;
      --muted: #667067;
      --paper: #f5f2ea;
      --panel: #fffdf7;
      --line: #ded8c9;
      --accent: #0f6b5f;
      --accent-soft: #dceee8;
      --warn: #8a5a00;
      --bad: #a33b28;
      --good: #126b43;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(15, 107, 95, 0.09), transparent 34%),
        linear-gradient(180deg, #fbf8f0 0%, var(--paper) 100%);
      font-family: Georgia, "Times New Roman", serif;
    }}

    main {{
      width: min(1380px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }}

    .topbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 20px;
      align-items: end;
      margin-bottom: 20px;
    }}

    h1 {{
      margin: 0;
      font-size: clamp(30px, 5vw, 58px);
      line-height: 0.95;
      letter-spacing: 0;
    }}

    .timestamp {{
      color: var(--muted);
      font-size: 14px;
      text-align: right;
      font-family: "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .summary {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 18px;
    }}

    .metric {{
      min-height: 94px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 247, 0.78);
    }}

    .metric span {{
      display: block;
      color: var(--muted);
      font: 700 11px/1.2 "Avenir Next", "Trebuchet MS", sans-serif;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}

    .metric strong {{
      display: block;
      margin-top: 8px;
      font-size: 28px;
      line-height: 1;
    }}

    .market {{
      display: grid;
      grid-template-columns: minmax(220px, 280px) 1fr;
      gap: 12px;
      margin-bottom: 18px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255, 253, 247, 0.86);
    }}

    .market h2 {{
      margin: 0;
      font-size: 24px;
      line-height: 1;
      letter-spacing: 0;
    }}

    .market-score {{
      margin-top: 10px;
      font: 800 30px/1 "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .market-summary {{
      margin: 0 0 12px;
      color: var(--muted);
      font: 14px/1.45 "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .signal-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 8px;
    }}

    .signal {{
      min-height: 72px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbf7ec;
      font-family: "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .signal span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}

    .signal strong {{
      display: block;
      margin-top: 6px;
      font-size: 15px;
      line-height: 1.2;
    }}

    .toolbar {{
      display: grid;
      grid-template-columns: minmax(260px, 420px) 180px 1fr;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
      color: var(--muted);
      font: 14px/1.4 "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .control {{
      width: 100%;
      min-height: 40px;
      padding: 8px 11px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--ink);
      background: var(--panel);
      font: 14px/1.2 "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    .count {{
      justify-self: end;
      text-align: right;
    }}

    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 18px 50px rgba(44, 37, 22, 0.08);
    }}

    table {{
      width: 100%;
      min-width: 1080px;
      border-collapse: collapse;
      font-family: "Avenir Next", "Trebuchet MS", sans-serif;
    }}

    th {{
      position: sticky;
      top: 0;
      z-index: 1;
      padding: 12px 12px;
      color: #354139;
      background: #ece6d8;
      font-size: 11px;
      letter-spacing: 0.07em;
      text-align: left;
      text-transform: uppercase;
      white-space: nowrap;
    }}

    th button {{
      width: 100%;
      padding: 0;
      border: 0;
      color: inherit;
      background: transparent;
      cursor: pointer;
      font: inherit;
      letter-spacing: inherit;
      text-align: inherit;
      text-transform: inherit;
    }}

    th button::after {{
      content: "  ↕";
      color: #7d7462;
    }}

    th button[data-direction="asc"]::after {{
      content: "  ↑";
      color: var(--accent);
    }}

    th button[data-direction="desc"]::after {{
      content: "  ↓";
      color: var(--accent);
    }}

    td {{
      padding: 13px 12px;
      border-top: 1px solid var(--line);
      font-size: 14px;
      vertical-align: top;
    }}

    tr:hover td {{
      background: #f8f3e8;
    }}

    .ticker {{
      font-weight: 800;
      font-size: 15px;
    }}

    .name {{
      color: var(--muted);
      font-size: 12px;
      margin-top: 2px;
      max-width: 230px;
    }}

    .num {{
      font-variant-numeric: tabular-nums;
      text-align: right;
      white-space: nowrap;
    }}

    .pill {{
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 4px 9px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}

    .pill.needs {{
      background: #f0e6c8;
      color: var(--warn);
    }}

    .positive {{
      color: var(--good);
      font-weight: 800;
    }}

    .negative {{
      color: var(--bad);
      font-weight: 800;
    }}

    .reason {{
      color: var(--muted);
      max-width: 310px;
      font-size: 12px;
      line-height: 1.35;
    }}

    @media (max-width: 760px) {{
      main {{
        width: min(100% - 20px, 1380px);
        padding-top: 18px;
      }}

      .topbar {{
        grid-template-columns: 1fr;
        gap: 8px;
      }}

      .timestamp {{
        text-align: left;
      }}

      .summary {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .market {{
        grid-template-columns: 1fr;
      }}

      .signal-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}

      .toolbar {{
        grid-template-columns: 1fr;
      }}

      .count {{
        justify-self: start;
        text-align: left;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="topbar">
      <div>
        <h1>{_esc(report.etf)} Screener</h1>
      </div>
      <div class="timestamp">Generated {generated_at}</div>
    </section>

    <section class="summary" aria-label="Dashboard summary">
      <div class="metric"><span>Total ETF Holdings</span><strong>{report.holdings_count}</strong></div>
      <div class="metric"><span>Displayed</span><strong>{len(report.rated_holdings)}</strong></div>
      <div class="metric"><span>With Price Inputs</span><strong>{price_count}</strong></div>
      <div class="metric"><span>Average Weight</span><strong>{_format_percent(average_weight)}</strong></div>
    </section>

    {market_panel}

    <section class="toolbar">
      <input class="control" id="searchInput" type="search" placeholder="Search ticker or company" aria-label="Search ticker or company">
      <select class="control" id="ratingFilter" aria-label="Filter by rating">
        <option value="all">All ratings</option>
        {rating_options}
      </select>
      <div class="count"><span id="visibleCount">{len(report.rated_holdings)}</span> shown • Ratings: {_esc(_format_rating_counts(rating_counts))}</div>
    </section>

    <section class="table-wrap">
      <table id="holdingsTable">
        <thead>
          <tr>
            <th><button type="button" data-sort="text">Holding</button></th>
            <th class="num"><button type="button" data-sort="number">ETF Weight</button></th>
            <th class="num"><button type="button" data-sort="number">Last Close</button></th>
            <th class="num"><button type="button" data-sort="number">Day Change</button></th>
            <th class="num"><button type="button" data-sort="number">vs 20D</button></th>
            <th class="num"><button type="button" data-sort="number">vs 50D</button></th>
            <th class="num"><button type="button" data-sort="number">vs 200D</button></th>
            <th class="num"><button type="button" data-sort="number">1M Return</button></th>
            <th class="num"><button type="button" data-sort="number">3M Return</button></th>
            <th class="num"><button type="button" data-sort="number">3M vs SPY</button></th>
            <th class="num"><button type="button" data-sort="number">Tech Score</button></th>
            <th class="num"><button type="button" data-sort="number">Semantic Score</button></th>
            <th><button type="button" data-sort="text">Research</button></th>
            <th class="num"><button type="button" data-sort="number">Final Score</button></th>
            <th><button type="button" data-sort="text">Technical</button></th>
            <th><button type="button" data-sort="text">Semantic</button></th>
            <th><button type="button" data-sort="text">Rating</button></th>
          </tr>
        </thead>
        <tbody>
          {_render_rows(report.rated_holdings, benchmark_returns)}
        </tbody>
      </table>
    </section>
  </main>
  <script>
    const table = document.getElementById("holdingsTable");
    const tbody = table.querySelector("tbody");
    const searchInput = document.getElementById("searchInput");
    const ratingFilter = document.getElementById("ratingFilter");
    const visibleCount = document.getElementById("visibleCount");
    const rows = Array.from(tbody.querySelectorAll("tr"));

    function applyFilters() {{
      const query = searchInput.value.trim().toLowerCase();
      const rating = ratingFilter.value;
      let count = 0;

      rows.forEach((row) => {{
        const matchesSearch = row.dataset.search.includes(query);
        const matchesRating = rating === "all" || row.dataset.rating === rating;
        const visible = matchesSearch && matchesRating;
        row.hidden = !visible;
        if (visible) count += 1;
      }});

      visibleCount.textContent = count;
    }}

    function sortByColumn(index, type, button) {{
      const currentDirection = button.dataset.direction === "asc" ? "desc" : "asc";
      table.querySelectorAll("th button").forEach((item) => delete item.dataset.direction);
      button.dataset.direction = currentDirection;

      const sorted = rows.slice().sort((left, right) => {{
        const leftCell = left.children[index];
        const rightCell = right.children[index];
        const leftValue = type === "number" ? Number(leftCell.dataset.value || "-Infinity") : leftCell.textContent.trim().toLowerCase();
        const rightValue = type === "number" ? Number(rightCell.dataset.value || "-Infinity") : rightCell.textContent.trim().toLowerCase();

        if (leftValue < rightValue) return currentDirection === "asc" ? -1 : 1;
        if (leftValue > rightValue) return currentDirection === "asc" ? 1 : -1;
        return 0;
      }});

      sorted.forEach((row) => tbody.appendChild(row));
      rows.splice(0, rows.length, ...sorted);
      applyFilters();
    }}

    table.querySelectorAll("th button").forEach((button, index) => {{
      button.addEventListener("click", () => sortByColumn(index, button.dataset.sort, button));
    }});

    searchInput.addEventListener("input", applyFilters);
    ratingFilter.addEventListener("change", applyFilters);
    applyFilters();
  </script>
</body>
</html>
"""


def _render_market_panel(report: EtfScreenReport) -> str:
    market = report.market_condition
    if market is None:
        return ""

    signals = [
        ("DeMark", market.demark.score, market.demark.regime),
        ("Structure", market.structural_weakness.score, market.structural_weakness.regime),
        ("Tech", market.technology_support.score, market.technology_support.regime),
        ("Defensives", market.defensive_rotation.score, market.defensive_rotation.regime),
        ("Credit", market.credit_risk.score, market.credit_risk.regime),
        ("Dollar/Yield", market.dollar_yield_shock.score, market.dollar_yield_shock.regime),
    ]
    signal_cards = "\n".join(
        f'<div class="signal"><span>{_esc(label)}</span><strong>{_format_score(score, regime)}</strong></div>'
        for label, score, regime in signals
    )

    return f"""<section class="market" aria-label="Market condition">
  <div>
    <h2>Market Backdrop</h2>
    <div class="market-score">{_format_score(market.overall_score, market.overall_regime)}</div>
  </div>
  <div>
    <p class="market-summary">{_esc(market.summary)}</p>
    <div class="signal-grid">{signal_cards}</div>
  </div>
</section>"""


def _render_rows(items, benchmark_returns):
    return "\n".join(_render_row(item, benchmark_returns) for item in items)


def _render_row(item: RatedHolding, benchmark_returns) -> str:
    price = item.price
    technical_reason = " ".join(item.technical.reasons[:2]) if item.technical.reasons else ""
    semantic_reason = item.semantic.reasons[0] if item.semantic.reasons else ""
    relative_3m = _relative_value(
        price.return_60d_percent if price else None,
        benchmark_returns.get("return_60d_percent"),
    )

    search_text = f"{item.holding.ticker} {item.holding.name}".lower()

    return f"""<tr data-search="{_esc(search_text)}" data-rating="{_esc(item.rating)}">
  <td data-value="{_esc(item.holding.ticker)}"><div class="ticker">{_esc(item.holding.ticker)}</div><div class="name">{_esc(item.holding.name)}</div></td>
  <td class="num" data-value="{_data_value(item.holding.weight)}">{_format_percent(item.holding.weight)}</td>
  <td class="num" data-value="{_data_value(price.last_close if price else None)}">{_format_money(price.last_close if price else None)}</td>
  <td class="num {_number_class(price.change_percent if price else None)}" data-value="{_data_value(price.change_percent if price else None)}">{_format_percent(price.change_percent if price else None)}</td>
  <td class="num {_number_class(price.close_vs_sma_20_percent if price else None)}" data-value="{_data_value(price.close_vs_sma_20_percent if price else None)}">{_format_percent(price.close_vs_sma_20_percent if price else None)}</td>
  <td class="num {_number_class(price.close_vs_sma_50_percent if price else None)}" data-value="{_data_value(price.close_vs_sma_50_percent if price else None)}">{_format_percent(price.close_vs_sma_50_percent if price else None)}</td>
  <td class="num {_number_class(price.close_vs_sma_200_percent if price else None)}" data-value="{_data_value(price.close_vs_sma_200_percent if price else None)}">{_format_percent(price.close_vs_sma_200_percent if price else None)}</td>
  <td class="num {_number_class(price.return_20d_percent if price else None)}" data-value="{_data_value(price.return_20d_percent if price else None)}">{_format_percent(price.return_20d_percent if price else None)}</td>
  <td class="num {_number_class(price.return_60d_percent if price else None)}" data-value="{_data_value(price.return_60d_percent if price else None)}">{_format_percent(price.return_60d_percent if price else None)}</td>
  <td class="num {_number_class(relative_3m)}" data-value="{_data_value(relative_3m)}">{_format_percent(relative_3m)}</td>
  <td class="num" data-value="{_data_value(item.technical.score)}">{_format_score(item.technical.score, item.technical.label)}</td>
  <td class="num" data-value="{_data_value(item.semantic.score)}">{_format_score(item.semantic.score, item.semantic.label)}</td>
  <td data-value="{_esc(item.semantic.research_status)}">{_esc(_research_label(item))}</td>
  <td class="num" data-value="{_data_value(item.final_score)}">{_format_plain_score(item.final_score)}</td>
  <td><div class="reason">{_esc(technical_reason)}</div></td>
  <td><div class="reason">{_esc(semantic_reason)}</div></td>
  <td><span class="pill needs">{_esc(item.rating)}</span></td>
</tr>"""


def _average(values):
    clean_values = [value for value in values if value is not None]
    if not clean_values:
        return None
    return sum(clean_values) / len(clean_values)


def _research_label(item: RatedHolding) -> str:
    if item.semantic.research_status == "curated":
        return "Curated"
    if item.semantic.research_status == "auto_approved":
        return "Auto-approved"
    return "Research not reliable"


def _benchmark_returns(report: EtfScreenReport):
    return {
        "return_20d_percent": report.metadata.get("benchmark_return_20d_percent"),
        "return_60d_percent": report.metadata.get("benchmark_return_60d_percent"),
    }


def _relative_value(value, benchmark_value):
    if value is None or benchmark_value is None:
        return None
    return value - benchmark_value


def _format_percent(value):
    if value is None:
        return "-"
    return f"{value:.2f}%"


def _format_money(value):
    if value is None:
        return "-"
    return f"${value:,.2f}"


def _format_score(value, label):
    if value is None:
        return "-"
    return f"{value:.1f} {label}"


def _format_plain_score(value):
    if value is None:
        return "-"
    return f"{value:.1f}"


def _format_rating_counts(counts: Counter) -> str:
    if not counts:
        return "none"
    return ", ".join(f"{label}: {count}" for label, count in sorted(counts.items()))


def _number_class(value):
    if value is None:
        return ""
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return ""


def _data_value(value):
    if value is None:
        return ""
    return str(value)


def _esc(value) -> str:
    return html.escape(str(value), quote=True)

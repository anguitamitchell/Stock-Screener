import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from self_screener.models import Holding
from self_screener.pipeline import _report_to_json, screen_etf
from self_screener.providers.ssga_holdings import fetch_holdings
from self_screener.providers.ssga_holdings import HoldingsFetchError
from self_screener.providers.ssga_holdings import supported_etfs
from self_screener.screeners.semantic import (
    THEME_CONFIG_PATH,
    build_research_evidence,
    load_theme_config,
    theme_config_status,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
SNAPSHOT_DIR = Path("data") / "reports" / "snapshots"
REVIEW_QUEUE_PATH = Path("data") / "review" / "semantic_review_queue.json"


class ServerStartupError(RuntimeError):
    """Raised when the local dashboard cannot start."""


def run_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    try:
        server = ThreadingHTTPServer((host, port), ScreenerRequestHandler)
    except OSError as exc:
        if exc.errno in {48, 98, 10048}:
            raise ServerStartupError(
                f"SignalDeck could not start because port {port} is already in use. "
                f"Close the existing SignalDeck window/server or open http://{host}:{port}/."
            ) from exc
        raise ServerStartupError(f"SignalDeck could not start on {host}:{port}: {exc}") from exc
    print(f"Local dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSignalDeck stopped.")
    finally:
        server.server_close()


class ScreenerRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(APP_HTML)
            return
        if parsed.path == "/api/screen":
            self._handle_screen(parsed.query)
            return
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "semantic_config": theme_config_status()})
            return
        if parsed.path == "/api/themes":
            self._send_json(_load_semantic_config().get("themes", {}))
            return
        if parsed.path == "/api/research":
            self._handle_research(parsed.query)
            return
        if parsed.path == "/api/research/bulk":
            self._handle_bulk_research(parsed.query)
            return
        if parsed.path == "/api/review":
            self._send_json(_load_review_queue())
            return
        if parsed.path == "/api/curated":
            self._send_json({"items": _curated_items()})
            return
        if parsed.path == "/api/market/history":
            self._handle_market_history(parsed.query)
            return
        if parsed.path == "/api/runs":
            self._handle_runs(parsed.query)
            return
        self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/semantic":
            self._handle_save_semantic()
            return
        if parsed.path == "/api/review":
            self._handle_save_review()
            return
        if parsed.path == "/api/review/promote":
            self._handle_promote_review()
            return
        if parsed.path == "/api/review/clear":
            self._handle_clear_review()
            return
        if parsed.path == "/api/shutdown":
            self._handle_shutdown()
            return
        self.send_error(404, "Not found")

    def log_message(self, format, *args):
        return

    def _handle_screen(self, query: str) -> None:
        params = parse_qs(query)
        etf = params.get("etf", ["SPY"])[0].upper()
        limit_value = params.get("limit", [""])[0]
        refresh = params.get("refresh", ["false"])[0].lower() == "true"
        limit = int(limit_value) if limit_value else None

        try:
            report = screen_etf(
                etf,
                limit=limit,
                refresh=refresh,
                include_prices=True,
                include_market=True,
            )
        except HoldingsFetchError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        except Exception as exc:
            self._send_json({"error": f"Screen failed: {exc}"}, status=500)
            return

        payload = _report_to_json(report)
        payload["comparison"] = _compare_to_previous_snapshot(payload)
        snapshot_path = _write_screen_snapshot(payload)
        payload.setdefault("metadata", {})["snapshot_path"] = str(snapshot_path)
        payload["snapshot_saved_at"] = payload.get("generated_at")
        self._send_json(payload)

    def _handle_research(self, query: str) -> None:
        params = parse_qs(query)
        ticker = params.get("ticker", [""])[0].upper()
        name = params.get("name", [""])[0]
        refresh = params.get("refresh", ["false"])[0].lower() == "true"
        if not ticker:
            self._send_json({"error": "Ticker is required."}, status=400)
            return

        evidence = build_research_evidence(Holding(ticker=ticker, name=name), refresh=refresh)
        self._send_json(evidence, status=400 if evidence.get("error") else 200)

    def _handle_bulk_research(self, query: str) -> None:
        params = parse_qs(query)
        etf = params.get("etf", ["SPY"])[0].upper()
        limit_value = params.get("limit", ["25"])[0]
        refresh = params.get("refresh", ["true"])[0].lower() == "true"
        limit = int(limit_value) if limit_value else None

        try:
            holdings = fetch_holdings(etf, refresh=False)
            selected_holdings = holdings[:limit] if limit else holdings
        except HoldingsFetchError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        results = []
        for holding in selected_holdings:
            evidence = build_research_evidence(holding, refresh=refresh)
            results.append(
                {
                    "ticker": holding.ticker,
                    "research_status": evidence.get("research_status", "unreliable"),
                    "confidence": evidence.get("confidence", 0),
                    "error": evidence.get("error"),
                    "matches": len(evidence.get("matches", [])),
                }
            )

        self._send_json(
            {
                "ok": True,
                "etf": etf,
                "processed": len(results),
                "auto_approved": sum(1 for item in results if item["research_status"] == "auto_approved"),
                "unreliable": sum(1 for item in results if item["research_status"] != "auto_approved"),
                "errors": sum(1 for item in results if item.get("error")),
                "results": results,
            }
        )

    def _handle_market_history(self, query: str) -> None:
        params = parse_qs(query)
        etf = params.get("etf", ["SPY"])[0].upper()
        limit_value = params.get("limit", ["5"])[0]
        limit = int(limit_value) if limit_value else 5
        self._send_json({"items": _market_history_items(etf, limit=limit)})

    def _handle_runs(self, query: str) -> None:
        params = parse_qs(query)
        etf = params.get("etf", ["SPY"])[0].upper()
        limit_value = params.get("limit", ["5"])[0]
        limit = int(limit_value) if limit_value else 5
        self._send_json({"items": _screen_run_items(etf, limit=limit)})

    def _handle_save_semantic(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            ticker = str(payload["ticker"]).upper()
            score = float(payload["score"])
            themes = [str(theme) for theme in payload.get("themes", [])]
            notes = str(payload.get("notes", "")).strip()
            if score < 0 or score > 100:
                raise ValueError("Score must be between 0 and 100.")
            if not themes:
                raise ValueError("Select at least one semantic theme.")
        except Exception as exc:
            self._send_json({"error": f"Invalid semantic update: {exc}"}, status=400)
            return

        config = _load_semantic_config()
        allowed_themes = set(config.get("themes", {}))
        invalid_themes = [theme for theme in themes if theme not in allowed_themes]
        if invalid_themes:
            self._send_json(
                {"error": f"Invalid semantic theme: {', '.join(invalid_themes)}"},
                status=400,
            )
            return

        config.setdefault("companies", {})[ticker] = {
            "score": round(score, 1),
            "themes": themes,
            "notes": notes or "Curated in the local dashboard.",
            "confidence": 1.0,
            "research_status": "curated",
            "source": "Local dashboard review",
        }
        _write_semantic_config(config)
        self._send_json({"ok": True, "ticker": ticker})

    def _handle_save_review(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            ticker = str(payload["ticker"]).upper().strip()
            if not ticker:
                raise ValueError("Ticker is required.")
        except Exception as exc:
            self._send_json({"error": f"Invalid review item: {exc}"}, status=400)
            return

        queue = _load_review_queue()
        items = [item for item in queue.get("items", []) if item.get("ticker") != ticker]
        payload["ticker"] = ticker
        payload["updated_at"] = _current_timestamp()
        items.insert(0, payload)
        queue["items"] = items
        _write_review_queue(queue)
        self._send_json({"ok": True, "ticker": ticker, "review_count": len(items)})

    def _handle_promote_review(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            ticker = str(payload["ticker"]).upper().strip()
            if not ticker:
                raise ValueError("Ticker is required.")
        except Exception as exc:
            self._send_json({"error": f"Invalid promotion request: {exc}"}, status=400)
            return

        queue = _load_review_queue()
        review_item = next((item for item in queue.get("items", []) if item.get("ticker") == ticker), None)
        if not review_item:
            self._send_json({"error": f"{ticker} is not in the review queue."}, status=404)
            return

        config = _load_semantic_config()
        allowed_themes = set(config.get("themes", {}))
        exposures = _review_theme_exposures(review_item, allowed_themes)
        if not exposures:
            self._send_json({"error": f"{ticker} has no valid themes to promote."}, status=400)
            return

        semantic = review_item.get("semantic", {})
        score = float(semantic.get("score") or review_item.get("final_score") or 50)
        score = max(0, min(100, score))
        config.setdefault("companies", {})[ticker] = {
            "score": round(score, 1),
            "themes": exposures,
            "notes": review_item.get("decision_summary") or "Promoted from the local semantic review queue.",
            "confidence": 1.0,
            "research_status": "curated",
            "source": "Promoted from local review queue",
        }
        _write_semantic_config(config)

        for item in queue.get("items", []):
            if item.get("ticker") == ticker:
                item["promoted_at"] = _current_timestamp()
                item["promoted"] = True
        _write_review_queue(queue)
        self._send_json({"ok": True, "ticker": ticker, "themes": exposures})

    def _handle_clear_review(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            ticker = str(payload["ticker"]).upper().strip()
            if not ticker:
                raise ValueError("Ticker is required.")
        except Exception as exc:
            self._send_json({"error": f"Invalid clear request: {exc}"}, status=400)
            return

        queue = _load_review_queue()
        original_count = len(queue.get("items", []))
        queue["items"] = [item for item in queue.get("items", []) if item.get("ticker") != ticker]
        _write_review_queue(queue)
        self._send_json(
            {
                "ok": True,
                "ticker": ticker,
                "removed": original_count - len(queue["items"]),
                "review_count": len(queue["items"]),
            }
        )

    def _handle_shutdown(self) -> None:
        self._send_json({"ok": True})
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def _send_html(self, body: str) -> None:
        html = body.replace("__SUPPORTED_ETFS__", json.dumps(supported_etfs()))
        encoded = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_json(self, payload, status: int = 200) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _load_semantic_config():
    return load_theme_config()


def _write_semantic_config(config) -> None:
    THEME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    THEME_CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def _curated_items() -> list:
    config = _load_semantic_config()
    theme_labels = {
        key: details.get("label", key) if isinstance(details, dict) else key
        for key, details in config.get("themes", {}).items()
    }
    items = []
    for ticker, entry in config.get("companies", {}).items():
        if not isinstance(entry, dict):
            continue
        themes = _company_theme_exposures(entry.get("themes", {}))
        items.append(
            {
                "ticker": ticker,
                "score": entry.get("score"),
                "source": entry.get("source", ""),
                "notes": entry.get("notes", ""),
                "theme_count": len(themes),
                "themes": [
                    {
                        "key": theme,
                        "label": theme_labels.get(theme, theme),
                        "exposure": exposure,
                    }
                    for theme, exposure in themes.items()
                ],
            }
        )
    promoted_first = sorted(
        items,
        key=lambda item: (
            "promoted" not in str(item.get("source", "")).lower(),
            str(item.get("ticker", "")),
        ),
    )
    return promoted_first


def _company_theme_exposures(themes) -> dict:
    if isinstance(themes, dict):
        return {str(theme): _clean_exposure(exposure) for theme, exposure in themes.items()}
    if isinstance(themes, list):
        return {str(theme): "direct" for theme in themes}
    return {}


def _load_review_queue() -> dict:
    if not REVIEW_QUEUE_PATH.exists():
        return {"items": []}
    try:
        data = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"items": []}
    if not isinstance(data, dict):
        return {"items": []}
    data.setdefault("items", [])
    return data


def _write_review_queue(queue: dict) -> None:
    REVIEW_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_QUEUE_PATH.write_text(json.dumps(queue, indent=2) + "\n", encoding="utf-8")


def _review_theme_exposures(review_item: dict, allowed_themes: set[str]) -> dict:
    semantic = review_item.get("semantic", {})
    exposures = semantic.get("exposures") if isinstance(semantic, dict) else {}
    if isinstance(exposures, dict):
        clean_exposures = {
            str(theme): _clean_exposure(exposure)
            for theme, exposure in exposures.items()
            if str(theme) in allowed_themes
        }
        if clean_exposures:
            return clean_exposures

    labels = {details.get("label"): key for key, details in _load_semantic_config().get("themes", {}).items()}
    clean_exposures = {}
    for label in review_item.get("themes", []):
        label_text = str(label)
        exposure = "direct"
        if "(" in label_text and label_text.endswith(")"):
            label_text, exposure_text = label_text.rsplit("(", 1)
            exposure = exposure_text.rstrip(")").strip()
        theme_key = labels.get(label_text.strip())
        if theme_key in allowed_themes:
            clean_exposures[theme_key] = _clean_exposure(exposure)
    return clean_exposures


def _clean_exposure(value) -> str:
    exposure = str(value).lower().strip()
    return exposure if exposure in {"direct", "indirect", "weak"} else "direct"


def _current_timestamp() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _write_screen_snapshot(payload: dict) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = str(payload.get("generated_at", "")).replace(":", "").replace("-", "")
    generated_at = generated_at.replace("T", "_").replace("Z", "") or "latest"
    etf = str(payload.get("etf", "ETF")).upper()
    path = SNAPSHOT_DIR / f"{etf}_{generated_at}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _market_history_items(etf: str, limit: int = 5) -> list:
    if not SNAPSHOT_DIR.exists():
        return []
    items = []
    for path in reversed(sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        market = payload.get("market_condition") or {}
        if not market:
            continue
        items.append(_market_history_item(payload, market))
        if len(items) >= limit:
            break
    for index, item in enumerate(items):
        next_item = items[index + 1] if index + 1 < len(items) else None
        item["score_change"] = round(item["overall_score"] - next_item["overall_score"], 1) if next_item else None
    return items


def _market_history_item(payload: dict, market: dict) -> dict:
    return {
        "generated_at": payload.get("generated_at"),
        "overall_score": market.get("overall_score"),
        "overall_regime": market.get("overall_regime"),
        "demark": _compact_signal(market.get("demark")),
        "structure": _compact_signal(market.get("structural_weakness")),
        "tech": _compact_signal(market.get("technology_support")),
        "credit": _compact_signal(market.get("credit_risk")),
    }


def _compact_signal(signal) -> dict:
    signal = signal or {}
    return {
        "score": signal.get("score"),
        "regime": signal.get("regime"),
    }


def _screen_run_items(etf: str, limit: int = 5) -> list:
    if not SNAPSHOT_DIR.exists():
        return []
    items = []
    for path in reversed(sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows = payload.get("rated_holdings", [])
        if not rows:
            continue
        items.append(_screen_run_item(payload, path))
        if len(items) >= limit:
            break
    return items


def _screen_run_item(payload: dict, path: Path) -> dict:
    rows = payload.get("rated_holdings", [])
    counts = _rating_counts(rows)
    market = payload.get("market_condition") or {}
    high_certainty = sum(1 for row in rows if _row_confidence(row) >= 0.85)
    return {
        "id": path.stem,
        "path": str(path),
        "etf": payload.get("etf"),
        "generated_at": payload.get("generated_at"),
        "screen_size": len(rows),
        "holdings_count": payload.get("holdings_count"),
        "buy": counts.get("Buy", 0),
        "hold": counts.get("Hold", 0),
        "sell": counts.get("Sell", 0),
        "average_final_score": round(_average_score(rows), 1),
        "high_certainty_percent": round((high_certainty / len(rows)) * 100, 1) if rows else 0,
        "market_score": market.get("overall_score"),
        "market_regime": market.get("overall_regime"),
        "top_buys": [
            row.get("holding", {}).get("ticker")
            for row in rows
            if row.get("rating") == "Buy" and row.get("holding", {}).get("ticker")
        ][:5],
    }


def _row_confidence(row: dict) -> float:
    try:
        return float(row.get("semantic", {}).get("confidence", 0))
    except (TypeError, ValueError):
        return 0.0


def _compare_to_previous_snapshot(payload: dict) -> dict:
    current_rows = payload.get("rated_holdings", [])
    previous = _latest_snapshot(str(payload.get("etf", "ETF")).upper(), len(current_rows))
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


def _latest_snapshot(etf: str, screen_size: int) -> dict:
    if not SNAPSHOT_DIR.exists():
        return {}
    paths = sorted(SNAPSHOT_DIR.glob(f"{etf}_*.json"))
    for path in reversed(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if len(payload.get("rated_holdings", [])) == screen_size:
            return payload
    return {}


def _rows_by_ticker(rows: list) -> dict:
    return {row.get("holding", {}).get("ticker"): row for row in rows if row.get("holding", {}).get("ticker")}


def _rating_counts(rows: list) -> dict:
    counts = {}
    for row in rows:
        rating = row.get("rating")
        counts[rating] = counts.get(rating, 0) + 1
    return counts


def _average_score(rows: list) -> float:
    scores = [row.get("final_score") for row in rows if row.get("final_score") is not None]
    return sum(scores) / len(scores) if scores else 0.0


APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SignalDeck Screener</title>
  <style>
    :root {
      --bg: #070807;
      --panel: #121412;
      --panel-2: #191c18;
      --panel-3: #0d0f0d;
      --line: #272c26;
      --text: #f1f4ec;
      --muted: #899184;
      --green: #ccff3f;
      --mint: #3be0a1;
      --blue: #7ba7ff;
      --red: #ff6565;
      --amber: #e0b84d;
      --purple: #9d6cff;
    }

    * { box-sizing: border-box; }

    * {
      scrollbar-width: thin;
      scrollbar-color: rgba(204,255,63,0.26) rgba(255,255,255,0.04);
    }

    *::-webkit-scrollbar {
      width: 10px;
      height: 10px;
    }

    *::-webkit-scrollbar-track {
      background: rgba(255,255,255,0.035);
      border-radius: 999px;
    }

    *::-webkit-scrollbar-thumb {
      border: 2px solid rgba(5,6,5,0.92);
      border-radius: 999px;
      background: rgba(204,255,63,0.22);
    }

    *::-webkit-scrollbar-thumb:hover {
      background: rgba(204,255,63,0.38);
    }

    *::-webkit-scrollbar-corner {
      background: transparent;
    }

    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      background:
        radial-gradient(circle at 12% 0%, rgba(204, 255, 63, 0.18), transparent 30%),
        radial-gradient(circle at 85% 4%, rgba(59, 224, 161, 0.12), transparent 24%),
        linear-gradient(135deg, #050605 0%, #0b0d0c 58%, #121510 100%);
      font-family: ui-sans-serif, "Avenir Next", "Segoe UI", sans-serif;
    }

    button, input, select { font: inherit; }

    .shell {
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      min-height: 100vh;
    }

    .rail {
      position: sticky;
      top: 0;
      display: flex;
      flex-direction: column;
      height: 100vh;
      padding: 20px 14px;
      border-right: 1px solid var(--line);
      background: rgba(8, 9, 8, 0.78);
      backdrop-filter: blur(18px);
    }

    .logo {
      display: grid;
      place-items: center;
      width: 46px;
      height: 46px;
      margin: 0 auto 28px;
      border-radius: 14px;
      color: #061006;
      background: var(--green);
      font-weight: 900;
    }

    .rail-item {
      display: grid;
      place-items: center;
      width: 46px;
      height: 46px;
      margin: 12px auto;
      border: 1px solid transparent;
      border-radius: 14px;
      color: var(--muted);
      background: transparent;
    }

    .rail-item.active {
      color: var(--green);
      border-color: #3b452f;
      background: #171d12;
    }

    .rail-spacer {
      flex: 1;
    }

    .rail-item.quit {
      cursor: pointer;
      color: var(--red);
    }

    .rail-item.quit:hover {
      border-color: rgba(255, 101, 101, 0.46);
      background: rgba(255, 101, 101, 0.10);
    }

    .app {
      width: 100%;
      min-width: 0;
      padding: 24px;
    }

    .topbar {
      display: grid;
      grid-template-columns: 1fr minmax(360px, 620px);
      gap: 18px;
      align-items: center;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0;
      font-size: 38px;
      line-height: 1;
      letter-spacing: 0;
    }

    .subtitle {
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
    }

    .runbar {
      display: grid;
      grid-template-columns: 92px 108px 1fr auto;
      gap: 10px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(18, 20, 18, 0.86);
      box-shadow: 0 24px 80px rgba(0,0,0,0.28);
    }

    .run-stack {
      display: grid;
      gap: 8px;
    }

    .recent-etfs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }

    .shortcut {
      min-height: 30px;
      border: 1px solid #252b25;
      border-radius: 999px;
      padding: 0 11px;
      color: var(--muted);
      background: rgba(18, 20, 18, 0.74);
      cursor: pointer;
      font-size: 12px;
      font-weight: 850;
    }

    .shortcut:hover {
      color: #071006;
      border-color: var(--green);
      background: var(--green);
    }

    .field, .select {
      width: 100%;
      min-height: 42px;
      border: 1px solid #252b25;
      border-radius: 12px;
      color: var(--text);
      background: #0a0c0a;
      padding: 0 12px;
      outline: none;
    }

    .btn {
      min-height: 42px;
      border: 0;
      border-radius: 12px;
      padding: 0 18px;
      color: #071006;
      background: var(--green);
      font-weight: 850;
      cursor: pointer;
    }

    .btn.secondary {
      color: var(--text);
      background: #20251e;
    }

    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 18px;
      align-items: start;
    }

    .grid > * {
      min-width: 0;
    }

    .hero {
      display: grid;
      grid-template-columns: 1.3fr 0.7fr;
      gap: 14px;
      margin-bottom: 18px;
    }

    .panel {
      border: 1px solid var(--line);
      border-radius: 22px;
      background: linear-gradient(180deg, rgba(25, 28, 24, 0.96), rgba(13, 15, 13, 0.96));
      box-shadow: 0 24px 80px rgba(0,0,0,0.22);
    }

    .score-card {
      position: relative;
      min-height: 260px;
      overflow: hidden;
      padding: 22px;
      background:
        radial-gradient(ellipse 72% 52% at 72% 104%, rgba(204,255,63,0.18) 0%, rgba(204,255,63,0.09) 34%, rgba(204,255,63,0.025) 58%, rgba(204,255,63,0) 82%),
        radial-gradient(ellipse 52% 42% at 88% 18%, rgba(59,224,161,0.08) 0%, rgba(59,224,161,0.025) 52%, rgba(59,224,161,0) 78%),
        linear-gradient(180deg, rgba(25, 28, 24, 0.96), rgba(13, 15, 13, 0.96));
    }

    .eyebrow {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .big-score {
      margin-top: 14px;
      font-size: 72px;
      line-height: 0.92;
      letter-spacing: 0;
    }

    .regime {
      display: inline-flex;
      margin-top: 16px;
      padding: 8px 12px;
      border-radius: 999px;
      color: var(--green);
      background: rgba(204,255,63,0.11);
      font-weight: 850;
    }

    .market-copy {
      position: relative;
      margin-top: 18px;
      max-width: 760px;
      color: #b7beb2;
      line-height: 1.5;
      z-index: 1;
    }

    .market-history {
      position: relative;
      z-index: 1;
      display: grid;
      gap: 8px;
      margin-top: 18px;
    }

    .history-row {
      display: grid;
      grid-template-columns: 74px 1fr auto;
      gap: 10px;
      align-items: center;
      padding: 9px 10px;
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 12px;
      background: rgba(7, 8, 7, 0.34);
      color: #cbd2c4;
      font-size: 12px;
    }

    .history-row strong {
      font-size: 16px;
      font-variant-numeric: tabular-nums;
    }

    .history-signals {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font-size: 11px;
    }

    .mini-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }

    .mini {
      appearance: none;
      text-align: left;
      color: inherit;
      cursor: pointer;
      min-height: 112px;
      padding: 14px;
      border: 1px solid #293028;
      border-radius: 18px;
      background: #10130f;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }

    .mini:hover,
    .mini.active {
      border-color: rgba(204,255,63,0.52);
      background:
        radial-gradient(circle at 88% 12%, rgba(204,255,63,0.12), transparent 42%),
        #10150f;
      transform: translateY(-1px);
    }

    .mini strong {
      display: block;
      margin-top: 14px;
      font-size: 24px;
    }

    .signal-detail-head {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: start;
      margin-bottom: 12px;
    }

    .signal-detail-head h3 {
      margin: 0;
      font-size: 18px;
    }

    .signal-detail-head p {
      margin: 8px 0 0;
      color: #b7beb2;
      line-height: 1.45;
    }

    .signal-reasons {
      margin: 12px 0 0;
      padding-left: 18px;
      color: #c8cec2;
      line-height: 1.45;
    }

    .metric-strip {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }

    .metric-strip div {
      padding: 9px;
      border: 1px solid #252b25;
      border-radius: 12px;
      background: rgba(7, 8, 7, 0.42);
    }

    .metric-strip span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .metric-strip strong {
      display: block;
      margin-top: 4px;
      font-size: 15px;
    }

    .filters {
      display: grid;
      gap: 10px;
      margin-bottom: 12px;
    }

    .filter-group {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }

    .filter-label {
      min-width: 74px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .tab {
      min-height: 32px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 0 12px;
      color: var(--muted);
      background: #10130f;
      cursor: pointer;
      font-size: 12px;
      font-weight: 750;
    }

    .tab.active {
      color: #071006;
      background: var(--green);
      border-color: var(--green);
      font-weight: 850;
    }

    .table-panel {
      min-width: 0;
      padding: 14px;
    }

    .tools {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) auto;
      gap: 10px;
      margin-bottom: 12px;
    }

    .summary-card {
      display: grid;
      grid-template-columns: minmax(0, 1fr) repeat(5, minmax(72px, max-content));
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
      padding: 14px;
      border: 1px solid #252b25;
      border-radius: 18px;
      background:
        radial-gradient(circle at 100% 0%, rgba(204,255,63,0.10), transparent 32%),
        #0f120f;
    }

    .summary-copy {
      min-width: 0;
      color: #c7cec2;
      line-height: 1.45;
    }

    .summary-stat {
      min-width: 72px;
      text-align: right;
    }

    .summary-stat span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }

    .summary-stat strong {
      display: block;
      margin-top: 4px;
      font-size: 20px;
      font-variant-numeric: tabular-nums;
    }

    .compare-card {
      display: none;
      grid-template-columns: 1fr repeat(3, minmax(92px, auto));
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
      padding: 12px 14px;
      border: 1px solid #252b25;
      border-radius: 16px;
      background: #0b0e0b;
    }

    .compare-card.visible { display: grid; }

    .source-card {
      display: none;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
      margin-bottom: 12px;
      padding: 12px 14px;
      border: 1px solid #252b25;
      border-radius: 16px;
      background:
        radial-gradient(circle at 96% 12%, rgba(59,224,161,0.10), transparent 34%),
        #0b0e0b;
    }

    .source-card.visible { display: grid; }

    .source-card.warning {
      border-color: rgba(224, 184, 77, 0.36);
      background:
        radial-gradient(circle at 96% 12%, rgba(224,184,77,0.14), transparent 34%),
        #100f0a;
    }

    .table-wrap {
      width: 100%;
      overflow: auto;
      scrollbar-color: rgba(204,255,63,0.20) rgba(255,255,255,0.025);
      max-height: calc(100vh - 430px);
      min-height: 360px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(5,6,5,0.36);
    }

    .table-wrap::-webkit-scrollbar {
      width: 9px;
      height: 9px;
    }

    .table-wrap::-webkit-scrollbar-track {
      background: rgba(255,255,255,0.025);
    }

    .table-wrap::-webkit-scrollbar-thumb {
      border: 2px solid #090c08;
      background: rgba(204,255,63,0.18);
    }

    table {
      width: 100%;
      min-width: 1380px;
      border-collapse: collapse;
    }

    th {
      position: sticky;
      top: 0;
      z-index: 1;
      padding: 12px 14px;
      color: var(--muted);
      background: #0d100d;
      font-size: 11px;
      letter-spacing: 0.07em;
      text-align: left;
      text-transform: uppercase;
      white-space: nowrap;
      border-bottom: 1px solid var(--line);
    }

    th.sortable {
      cursor: pointer;
      user-select: none;
    }

    th.sortable::after {
      content: "↕";
      margin-left: 6px;
      color: #4a5447;
      font-size: 10px;
    }

    th.sortable.active::after {
      content: attr(data-indicator);
      color: var(--green);
    }

    td {
      padding: 13px 14px;
      border-bottom: 1px solid #20251f;
      color: #dbe0d5;
      font-size: 14px;
      vertical-align: middle;
    }

    tr { cursor: pointer; }
    tr:hover td { background: #151a14; }

    .ticker {
      color: var(--text);
      font-weight: 900;
    }

    .company {
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }

    .num {
      text-align: right;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 4px 9px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 900;
    }

    .buy { color: #071006; background: var(--green); }
    .hold { color: #1d1602; background: var(--amber); }
    .sell { color: #270606; background: var(--red); }
    .needs { color: #d7d2c6; background: #343832; }
    .research { color: #071006; background: var(--mint); }
    .warn { color: #231800; background: var(--amber); }
    .quality { color: #d7d2c6; background: #343832; }
    .pos { color: var(--mint); }
    .neg { color: var(--red); }
    .tone-bullish { color: var(--mint); }
    .tone-bearish { color: var(--red); }
    .tone-neutral { color: var(--text); }

    .side {
      position: sticky;
      top: 24px;
      display: grid;
      gap: 14px;
    }

    .watch-panel {
      padding: 16px;
    }

    .watch-list {
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }

    .watch-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      padding: 12px;
      border: 1px solid #252b25;
      border-radius: 14px;
      background: #0f120f;
    }

    button.watch-row {
      width: 100%;
      appearance: none;
      text-align: left;
      color: inherit;
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }

    button.watch-row:hover {
      border-color: rgba(204,255,63,0.48);
      background:
        radial-gradient(circle at 92% 8%, rgba(204,255,63,0.10), transparent 42%),
        #10150f;
      transform: translateY(-1px);
    }

    .queue-card {
      margin-top: 14px;
      padding: 14px;
      border: 1px solid #33402d;
      border-radius: 16px;
      background:
        radial-gradient(circle at 88% 18%, rgba(204,255,63,0.14), transparent 42%),
        #0f120f;
    }

    .queue-card strong {
      display: block;
      margin-bottom: 6px;
      font-size: 28px;
    }

    .queue-card p {
      margin: 0 0 12px;
      color: var(--muted);
      line-height: 1.4;
    }

    .queue-card.compact {
      padding: 12px;
    }

    .queue-card.compact strong {
      margin-bottom: 2px;
      font-size: 22px;
    }

    .queue-card.compact p {
      margin-bottom: 10px;
      font-size: 13px;
    }

    .research-tabs {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 12px;
    }

    .research-tabs .tab {
      min-height: 30px;
      padding: 0 8px;
      font-size: 11px;
    }

    .research-scroll {
      max-height: 300px;
      overflow: auto;
      padding-right: 2px;
    }

    .review-list {
      display: grid;
      gap: 9px;
      margin-top: 12px;
    }

    .review-row {
      width: 100%;
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid #252b25;
      border-radius: 14px;
      color: inherit;
      background: rgba(15, 18, 15, 0.88);
    }

    button.review-row {
      appearance: none;
      text-align: left;
      cursor: pointer;
      transition: border-color 160ms ease, background 160ms ease, transform 160ms ease;
    }

    button.review-row:hover {
      border-color: rgba(204,255,63,0.48);
      background:
        radial-gradient(circle at 92% 8%, rgba(204,255,63,0.10), transparent 42%),
        rgba(15, 18, 15, 0.94);
      transform: translateY(-1px);
    }

    .review-row-head {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: start;
    }

    .review-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      color: var(--muted);
      font-size: 12px;
    }

    .review-actions {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .review-empty {
      margin-top: 12px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .drawer {
      position: fixed;
      inset: 0 0 0 auto;
      width: min(520px, 100%);
      padding: 20px;
      background: #0b0d0b;
      border-left: 1px solid var(--line);
      box-shadow: -24px 0 80px rgba(0,0,0,0.35);
      transform: translateX(105%);
      transition: transform 180ms ease;
      z-index: 20;
      overflow-y: auto;
    }

    .drawer.open { transform: translateX(0); }

    .drawer-head {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: start;
      margin-bottom: 16px;
    }

    .drawer-section {
      margin-top: 14px;
    }

    .drawer-section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 900;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .stock-hero {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 14px;
      align-items: start;
      padding: 16px;
      border: 1px solid rgba(204,255,63,0.25);
      border-radius: 18px;
      background:
        radial-gradient(circle at 92% 10%, rgba(204,255,63,0.12), transparent 38%),
        #10130f;
    }

    .stock-hero-score {
      text-align: right;
    }

    .stock-hero-score strong {
      display: block;
      font-size: 42px;
      line-height: 0.95;
      font-variant-numeric: tabular-nums;
    }

    .stock-hero .chip-row {
      margin-top: 12px;
    }

    .close {
      width: 38px;
      height: 38px;
      border: 1px solid var(--line);
      border-radius: 12px;
      color: var(--text);
      background: #151815;
      cursor: pointer;
    }

    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .detail-card {
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
    }

    .detail-card span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }

    .score-stack {
      display: grid;
      gap: 12px;
    }

    .score-row {
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #10130f;
    }

    .score-row-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
      color: #dfe5da;
      font-weight: 850;
    }

    .bar {
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: #232922;
    }

    .bar span {
      display: block;
      height: 100%;
      width: var(--value);
      border-radius: inherit;
      background: linear-gradient(90deg, var(--green), var(--mint));
    }

    .formula {
      margin-top: 8px;
      color: var(--muted);
      font-size: 12px;
    }

    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 10px;
    }

    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 28px;
      padding: 5px 9px;
      border: 1px solid #33402d;
      border-radius: 999px;
      color: var(--green);
      background: rgba(204,255,63,0.08);
      font-size: 12px;
      font-weight: 800;
    }

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
      margin-top: 10px;
    }

    .metric-tile {
      display: grid;
      grid-template-rows: auto auto 1fr;
      align-items: start;
      padding: 10px;
      border: 1px solid #252b25;
      border-radius: 12px;
      background: #0b0e0b;
    }

    .metric-tile span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 6px;
    }

    .metric-tile strong {
      font-size: 14px;
      font-variant-numeric: tabular-nums;
    }

    .metric-tile strong.bullish { color: var(--mint); }
    .metric-tile strong.bearish { color: var(--red); }
    .metric-tile strong.neutral { color: var(--text); }

    .metric-tile em {
      display: inline-flex;
      align-self: end;
      align-items: center;
      justify-content: center;
      margin-top: 7px;
      box-sizing: border-box;
      height: 24px;
      width: 100%;
      padding: 3px 9px;
      border-radius: 999px;
      color: #071006;
      background: rgba(48, 255, 188, 0.92);
      font-size: 11px;
      font-style: normal;
      font-weight: 900;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .metric-tile em.neutral {
      color: var(--text);
      background: rgba(255,255,255,0.12);
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.16);
    }

    .metric-tile em.bearish {
      color: #270606;
      background: var(--red);
    }

    .reason-box {
      margin-top: 10px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #10130f;
      color: #c7cec2;
      line-height: 1.5;
    }

    .research-head {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      margin-bottom: 10px;
    }

    .research-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin: 10px 0;
    }

    .research-item {
      padding: 10px;
      border: 1px solid #252b25;
      border-radius: 12px;
      background: #0b0e0b;
    }

    .research-item span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 6px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-weight: 800;
    }

    .research-list {
      display: grid;
      gap: 9px;
      margin-top: 10px;
    }

    .research-match {
      padding: 10px;
      border: 1px solid #33402d;
      border-radius: 12px;
      background: rgba(204,255,63,0.06);
    }

    .strength {
      display: inline-flex;
      margin-left: 8px;
      padding: 3px 7px;
      border-radius: 999px;
      color: #071006;
      background: var(--green);
      font-size: 11px;
      font-weight: 900;
      text-transform: uppercase;
    }

    .strength.moderate { background: var(--amber); }
    .strength.indirect { background: var(--amber); }
    .strength.direct { background: var(--green); }
    .strength.weak { color: #d7d2c6; background: #343832; }

    .warning-box {
      border-color: rgba(224, 184, 77, 0.42);
      background:
        radial-gradient(circle at 92% 8%, rgba(224,184,77,0.16), transparent 34%),
        #14120c;
    }

    .edit-form {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }

    .edit-form label {
      display: grid;
      gap: 7px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }

    .edit-form input,
    .edit-form textarea {
      width: 100%;
      border: 1px solid #252b25;
      border-radius: 12px;
      color: var(--text);
      background: #080a08;
      padding: 10px 12px;
      outline: none;
      text-transform: none;
      letter-spacing: 0;
      font-weight: 600;
    }

    .edit-form textarea {
      min-height: 96px;
      resize: vertical;
      line-height: 1.45;
    }

    .check-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 8px;
    }

    .check {
      display: flex;
      align-items: center;
      gap: 9px;
      padding: 9px 10px;
      border: 1px solid #252b25;
      border-radius: 12px;
      color: #cbd2c4;
      background: #080a08;
      font-size: 13px;
      font-weight: 700;
      text-transform: none;
      letter-spacing: 0;
    }

    .check input {
      width: auto;
    }

    .status {
      color: var(--muted);
      font-size: 13px;
    }

    @media (max-width: 1600px) {
      .shell { grid-template-columns: 76px minmax(0, 1fr); }
      .rail { padding: 16px 10px; }
      .app { padding: 20px; }
      .topbar {
        grid-template-columns: minmax(280px, 1fr) minmax(360px, 560px);
        gap: 14px;
      }
      .grid {
        grid-template-columns: minmax(0, 1fr) 320px;
        gap: 14px;
      }
      .hero {
        grid-template-columns: 1.2fr 0.8fr;
        gap: 12px;
      }
      .score-card {
        min-height: 236px;
        padding: 18px;
      }
      .big-score { font-size: 64px; }
      .mini-grid { gap: 8px; padding: 12px; }
      .mini {
        min-height: 96px;
        padding: 12px;
      }
      .mini strong {
        margin-top: 10px;
        font-size: 22px;
      }
      .summary-card {
        grid-template-columns: minmax(0, 1fr) repeat(5, minmax(68px, max-content));
        gap: 8px;
      }
      table { min-width: 1240px; }
      th, td { padding: 11px 12px; }
      .watch-panel { padding: 14px; }
      .research-scroll { max-height: 240px; }
    }

    @media (max-width: 1400px) {
      .grid { grid-template-columns: 1fr; }
      .side {
        position: static;
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
      .table-wrap {
        max-height: min(620px, calc(100vh - 360px));
      }
    }

    @media (max-width: 1260px) {
      .topbar, .hero { grid-template-columns: 1fr; }
      .run-stack { justify-items: start; }
      .recent-etfs { justify-content: flex-start; }
      .side { grid-template-columns: 1fr; }
      .summary-card, .source-card, .compare-card {
        grid-template-columns: 1fr 1fr;
      }
      .summary-copy {
        grid-column: 1 / -1;
      }
    }

    @media (max-height: 850px) and (min-width: 1121px) {
      .app { padding-top: 18px; padding-bottom: 18px; }
      .topbar { margin-bottom: 14px; }
      .hero { margin-bottom: 14px; }
      .score-card { min-height: 220px; }
      .big-score { font-size: 58px; }
      .regime { margin-top: 12px; }
      .market-copy {
        margin-top: 14px;
        line-height: 1.42;
      }
      .table-wrap {
        min-height: 300px;
        max-height: calc(100vh - 360px);
      }
    }

    @media (max-width: 1120px) {
      .shell { grid-template-columns: 1fr; }
      .rail { display: none; }
      .topbar, .grid, .hero { grid-template-columns: 1fr; }
      .side { position: static; }
      .table-wrap { max-height: 640px; }
    }

    @media (max-width: 720px) {
      .app { padding: 14px; }
      .runbar { grid-template-columns: 1fr 1fr; }
      .big-score { font-size: 54px; }
      .mini-grid { grid-template-columns: 1fr; }
      .tools { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <nav class="rail">
      <div class="logo">SD</div>
      <div class="rail-item active">⌁</div>
      <div class="rail-item">◇</div>
      <div class="rail-item">◎</div>
      <div class="rail-item">⌕</div>
      <div class="rail-spacer"></div>
      <button class="rail-item quit" id="quitAppButton" type="button" title="Quit SignalDeck">×</button>
    </nav>

    <main class="app">
      <section class="topbar">
        <div>
          <h1>SignalDeck</h1>
          <div class="subtitle">ETF narrative and technical screener</div>
        </div>
        <div class="run-stack">
          <form class="runbar" id="runForm">
            <input class="field" id="etfInput" value="SPY" aria-label="ETF ticker">
            <select class="select" id="limitInput" aria-label="Screen size">
              <option value="25">Top 25</option>
              <option value="100">Top 100</option>
              <option value="">Full ETF</option>
            </select>
            <input class="field" id="searchInput" placeholder="Search ticker, company, rating, theme" aria-label="Search">
            <button class="btn" id="runButton" type="submit">Run</button>
          </form>
          <div class="recent-etfs" id="recentEtfs"></div>
        </div>
      </section>

      <section class="grid">
        <div>
          <section class="hero">
            <div class="panel score-card">
              <div class="eyebrow">Market backdrop</div>
              <div class="big-score" id="marketScore">--</div>
              <div class="regime" id="marketRegime">Loading</div>
              <div class="market-copy" id="marketSummary">Run a screen to load the current market condition.</div>
              <div class="market-history" id="marketHistory"></div>
            </div>
            <div class="panel mini-grid" id="signalGrid"></div>
          </section>

          <section class="filters" id="tabs">
            <div class="filter-group">
              <span class="filter-label">View</span>
              <button class="tab active" data-rating="all">All</button>
              <button class="tab" data-rating="Buy">Buy</button>
              <button class="tab" data-rating="Hold">Hold</button>
              <button class="tab" data-rating="Sell">Sell</button>
              <button class="tab" data-rating="research">Research Gaps</button>
            </div>
            <div class="filter-group">
              <span class="filter-label">Certainty</span>
              <button class="tab" data-rating="high-certainty">High</button>
              <button class="tab" data-rating="medium-certainty">Medium</button>
              <button class="tab" data-rating="low-certainty">Low</button>
            </div>
            <div class="filter-group">
              <span class="filter-label">Quality</span>
              <button class="tab" data-rating="quality-clean">Clean</button>
              <button class="tab" data-rating="quality-low-confidence">Low Confidence</button>
              <button class="tab" data-rating="quality-weak-technicals">Weak Technicals</button>
              <button class="tab" data-rating="quality-poor-reward-risk">Poor R/R</button>
              <button class="tab" data-rating="quality-mixed-signal">Mixed Signal</button>
              <button class="tab" data-rating="quality-technical-only">Technical Only</button>
            </div>
          </section>

          <section class="panel table-panel">
            <div class="summary-card" id="screenSummary">
              <div class="summary-copy">Run a screen to summarize the current ETF view.</div>
            </div>
            <div class="source-card" id="sourceSummary"></div>
            <div class="compare-card" id="comparisonSummary"></div>
            <div class="tools">
              <div>
                <div class="status" id="status">Ready</div>
                <div class="status" id="snapshotStatus"></div>
              </div>
              <button class="btn secondary" id="refreshButton" type="button">Refresh Data</button>
            </div>
            <div class="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th class="sortable" data-sort="rank">Rank</th>
                    <th class="sortable" data-sort="ticker">Holding</th>
                    <th class="num sortable active" data-sort="final" data-indicator="↓">Final</th>
                    <th class="num sortable" data-sort="semantic">Semantic</th>
                    <th class="sortable" data-sort="research">Research</th>
                    <th class="num sortable" data-sort="certainty">Certainty</th>
                    <th class="num sortable" data-sort="technical">Technical</th>
                    <th class="num sortable" data-sort="relative">3M vs SPY</th>
                    <th class="num sortable" data-sort="entry">Entry</th>
                    <th class="num sortable" data-sort="rewardRisk">R/R</th>
                    <th class="sortable" data-sort="quality">Quality</th>
                    <th class="sortable" data-sort="rating">Rating</th>
                  </tr>
                </thead>
                <tbody id="holdingsBody"></tbody>
              </table>
            </div>
          </section>
        </div>

        <aside class="side">
          <section class="panel watch-panel">
            <div class="eyebrow">Leaders</div>
            <div class="watch-list" id="leadersList"></div>
          </section>
          <section class="panel watch-panel">
            <div class="eyebrow">Distribution</div>
            <div class="watch-list" id="distributionList"></div>
          </section>
          <section class="panel watch-panel">
            <div class="eyebrow">Runs</div>
            <div class="watch-list" id="runsList"></div>
          </section>
          <section class="panel watch-panel">
            <div class="eyebrow">Research</div>
            <div id="researchQueue"></div>
          </section>
        </aside>
      </section>
    </main>
  </div>

  <aside class="drawer" id="drawer">
    <div class="drawer-head">
      <div>
        <div class="eyebrow" id="drawerTicker">Ticker</div>
        <h1 id="drawerName">Company</h1>
      </div>
      <button class="close" id="drawerClose" type="button">×</button>
    </div>
    <div id="drawerContent"></div>
  </aside>

  <script>
    const SUPPORTED_ETFS = __SUPPORTED_ETFS__;
    const state = {
      report: null,
      rows: [],
      filter: "all",
      query: "",
      themes: {},
      reviewItems: [],
      curatedItems: [],
      marketHistory: [],
      runs: [],
      researchPanelTab: "review",
      activeMarketSignal: null,
      drawerMode: null,
      sort: { key: "final", direction: "desc" }
    };
    const els = {
      form: document.getElementById("runForm"),
      etf: document.getElementById("etfInput"),
      limit: document.getElementById("limitInput"),
      search: document.getElementById("searchInput"),
      run: document.getElementById("runButton"),
      refresh: document.getElementById("refreshButton"),
      recentEtfs: document.getElementById("recentEtfs"),
      status: document.getElementById("status"),
      snapshotStatus: document.getElementById("snapshotStatus"),
      body: document.getElementById("holdingsBody"),
      marketScore: document.getElementById("marketScore"),
      marketRegime: document.getElementById("marketRegime"),
      marketSummary: document.getElementById("marketSummary"),
      marketHistory: document.getElementById("marketHistory"),
      signalGrid: document.getElementById("signalGrid"),
      screenSummary: document.getElementById("screenSummary"),
      sourceSummary: document.getElementById("sourceSummary"),
      comparisonSummary: document.getElementById("comparisonSummary"),
      leaders: document.getElementById("leadersList"),
      distribution: document.getElementById("distributionList"),
      runs: document.getElementById("runsList"),
      researchQueue: document.getElementById("researchQueue"),
      drawer: document.getElementById("drawer"),
      drawerTicker: document.getElementById("drawerTicker"),
      drawerName: document.getElementById("drawerName"),
      drawerContent: document.getElementById("drawerContent"),
      drawerClose: document.getElementById("drawerClose"),
      quit: document.getElementById("quitAppButton")
    };

    function fmt(value, digits = 1) {
      if (value === null || value === undefined) return "--";
      return Number(value).toFixed(digits);
    }

    function pct(value) {
      if (value === null || value === undefined) return "--";
      return `${Number(value).toFixed(2)}%`;
    }

    function cls(value) {
      if (value > 0) return "pos";
      if (value < 0) return "neg";
      return "";
    }

    function pillClass(rating) {
      if (rating === "Buy") return "pill buy";
      if (rating === "Hold") return "pill hold";
      if (rating === "Sell") return "pill sell";
      return "pill needs";
    }

    async function runScreen(refresh = false) {
      const etf = els.etf.value.trim() || "SPY";
      const limit = els.limit.value;
      const params = new URLSearchParams({ etf, refresh: String(refresh) });
      if (limit) params.set("limit", limit);
      els.status.textContent = refresh ? "Refreshing data..." : "Running screen...";
      els.run.disabled = true;
      els.refresh.disabled = true;

      try {
        const response = await fetch(`/api/screen?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Screen failed");
        state.report = payload;
        state.rows = payload.rated_holdings || [];
        els.status.textContent = `${payload.etf}: ${state.rows.length} of ${payload.holdings_count} holdings`;
        els.snapshotStatus.textContent = snapshotText(payload);
        saveRunPreferences(payload.etf);
        render();
        loadMarketHistory(payload.etf);
        loadRuns(payload.etf);
      } catch (error) {
        els.status.textContent = error.message;
      } finally {
        els.run.disabled = false;
        els.refresh.disabled = false;
      }
    }

    async function loadThemes() {
      const response = await fetch("/api/themes");
      state.themes = await response.json();
    }

    async function loadReviewQueue() {
      try {
        const response = await fetch("/api/review");
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Review queue unavailable");
        state.reviewItems = payload.items || [];
        renderSidebar();
      } catch (error) {
        state.reviewItems = [];
      }
    }

    async function loadCuratedItems() {
      try {
        const response = await fetch("/api/curated");
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Curated list unavailable");
        state.curatedItems = payload.items || [];
        renderSidebar();
      } catch (error) {
        state.curatedItems = [];
      }
    }

    async function loadMarketHistory(etf) {
      try {
        const params = new URLSearchParams({ etf: etf || els.etf.value.trim() || "SPY", limit: "5" });
        const response = await fetch(`/api/market/history?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Market history unavailable");
        state.marketHistory = payload.items || [];
        renderMarketHistory();
      } catch (error) {
        state.marketHistory = [];
        renderMarketHistory();
      }
    }

    async function loadRuns(etf) {
      try {
        const params = new URLSearchParams({ etf: etf || els.etf.value.trim() || "SPY", limit: "5" });
        const response = await fetch(`/api/runs?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Runs unavailable");
        state.runs = payload.items || [];
        renderRuns();
      } catch (error) {
        state.runs = [];
        renderRuns();
      }
    }

    function snapshotText(payload) {
      const path = payload?.metadata?.snapshot_path;
      const savedAt = payload?.snapshot_saved_at || payload?.generated_at;
      if (!path || !savedAt) return "";
      return `Saved snapshot ${formatTime(savedAt)} · ${path}`;
    }

    function formatTime(value) {
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    }

    function loadRunPreferences() {
      const savedEtf = localStorage.getItem("signaldeck:lastEtf");
      const savedLimit = localStorage.getItem("signaldeck:lastLimit");
      if (savedEtf) els.etf.value = savedEtf;
      if (savedLimit !== null) els.limit.value = savedLimit;
      renderRecentEtfs();
    }

    function saveRunPreferences(etf) {
      const normalized = (etf || els.etf.value || "SPY").trim().toUpperCase();
      const current = recentEtfs().filter((item) => item !== normalized);
      const next = [normalized, ...current].slice(0, 6);
      localStorage.setItem("signaldeck:lastEtf", normalized);
      localStorage.setItem("signaldeck:lastLimit", els.limit.value);
      localStorage.setItem("signaldeck:recentEtfs", JSON.stringify(next));
      renderRecentEtfs();
    }

    function recentEtfs() {
      try {
        const values = JSON.parse(localStorage.getItem("signaldeck:recentEtfs") || "[]");
        const supported = Array.isArray(values) ? values.filter((item) => SUPPORTED_ETFS.includes(item)) : [];
        return [...supported, ...SUPPORTED_ETFS.filter((item) => !supported.includes(item))];
      } catch {
        return SUPPORTED_ETFS;
      }
    }

    function renderRecentEtfs() {
      els.recentEtfs.innerHTML = recentEtfs().map((etf) => `
        <button class="shortcut" type="button" data-etf="${escapeHtml(etf)}">${escapeHtml(etf)}</button>
      `).join("");
    }

    function render() {
      renderMarket();
      renderScreenSummary();
      renderSourceSummary();
      renderComparisonSummary();
      renderTable();
      renderSidebar();
    }

    function renderMarket() {
      const market = state.report?.market_condition;
      if (!market) return;
      els.marketScore.textContent = fmt(market.overall_score);
      els.marketRegime.textContent = market.overall_regime;
      els.marketRegime.className = `regime ${toneClass(market.overall_regime)}`;
      els.marketSummary.textContent = market.summary;
      renderMarketHistory();
      const signals = marketSignals(market).sort((a, b) => b.weightValue - a.weightValue);
      els.signalGrid.innerHTML = `
        ${signals.map((signal) => `
        <button class="mini ${isActiveMarketDrawer(signal.key) ? "active" : ""}" type="button" data-market-signal="${signal.key}" aria-expanded="${isActiveMarketDrawer(signal.key)}">
          <div class="eyebrow">${escapeHtml(signal.label)}</div>
          <strong>${fmt(signal.score)} <span class="${toneClass(signal.regime)}">${escapeHtml(signal.regime)}</span></strong>
        </button>
      `).join("")}
      `;
      document.querySelectorAll("[data-market-signal]").forEach((button) => {
        button.addEventListener("click", () => {
          state.activeMarketSignal = button.dataset.marketSignal;
          renderMarket();
          const signal = marketSignals(state.report?.market_condition).find((item) => item.key === state.activeMarketSignal);
          if (signal) openMarketSignalDrawer(signal);
        });
      });
    }

    function renderMarketHistory() {
      if (!els.marketHistory) return;
      const items = state.marketHistory || [];
      if (!items.length) {
        els.marketHistory.innerHTML = '<div class="formula">Market history will appear after a few saved screen runs.</div>';
        return;
      }
      els.marketHistory.innerHTML = `
        <div class="eyebrow">Market history</div>
        ${items.map((item) => `
          <div class="history-row">
            <span>${formatTime(item.generated_at)}</span>
            <div>
              <strong>${fmt(item.overall_score)}</strong>
              <span class="${toneClass(item.overall_regime)}">${escapeHtml(item.overall_regime || "neutral")}</span>
              <div class="history-signals">
                <span>D ${fmt(item.demark?.score)}</span>
                <span>P ${fmt(item.structure?.score)}</span>
                <span>T ${fmt(item.tech?.score)}</span>
                <span>C ${fmt(item.credit?.score)}</span>
              </div>
            </div>
            <strong class="${cls(item.score_change)}">${item.score_change === null || item.score_change === undefined ? "--" : signed(item.score_change)}</strong>
          </div>
        `).join("")}
      `;
    }

    function marketSignals(market) {
      return [
        {
          key: "demark",
          label: "DeMark",
          score: market.demark.score,
          regime: market.demark.regime,
          signal: market.demark,
          weight: "10%",
          weightValue: 10,
          method: "Exhaustion score using the current TD Setup count and TD Countdown count. A completed sell setup/countdown pressures the score lower; a completed buy setup/countdown supports the score."
        },
        {
          key: "structure",
          label: "Market Participation",
          score: market.structural_weakness.score,
          regime: market.structural_weakness.regime,
          signal: market.structural_weakness,
          weight: "10%",
          weightValue: 10,
          method: "Market participation score using the current RSP/SPY ratio. Above 1.000 means more market participation and is bullish; below 1.000 means less market participation and is bearish."
        },
        {
          key: "tech",
          label: "Tech",
          score: market.technology_support.score,
          regime: market.technology_support.regime,
          signal: market.technology_support,
          weight: "30%",
          weightValue: 30,
          method: "Technology score using QQQ only: price above 200D, 50D above 200D, and 20D above 200D. Three true is bullish, two true is neutral, and one or zero true is bearish."
        },
        {
          key: "defensives",
          label: "SPY/Defensives",
          score: market.defensive_rotation.score,
          regime: market.defensive_rotation.regime,
          signal: market.defensive_rotation,
          weight: "30%",
          weightValue: 30,
          method: "Risk-appetite score using XLU/SPY and XLP/SPY. Defensive sectors outperforming is treated as caution, so this signal is inverted."
        },
        {
          key: "credit",
          label: "Credit",
          score: market.credit_risk.score,
          regime: market.credit_risk.regime,
          signal: market.credit_risk,
          weight: "12%",
          weightValue: 12,
          method: "Credit risk score using ICE BofA US High Yield Index Option-Adjusted Spread from FRED. Below 3.5 is bullish; above 3.5 is bearish."
        },
        {
          key: "dollarYield",
          label: "Dollar/Yield",
          score: market.dollar_yield_shock.score,
          regime: market.dollar_yield_shock.regime,
          signal: market.dollar_yield_shock,
          weight: "8%",
          weightValue: 8,
          method: "Dollar/yield score using DXY and the 10-year Treasury yield. DXY above 100 is bearish; 10-year yield above 4.5 is bearish. Both bearish makes the signal bearish, one bearish is neutral, and both bullish is bullish."
        }
      ].sort((a, b) => b.weightValue - a.weightValue);
    }

    function openMarketSignalDrawer(signal) {
      state.drawerMode = "market";
      state.activeMarketSignal = signal.key;
      els.drawerTicker.textContent = "Market Signal";
      els.drawerName.textContent = signal.label;
      els.drawerContent.innerHTML = marketSignalDetailHtml(signal);
      els.drawer.classList.add("open");
      renderMarket();
    }

    function openCuratedDrawer(item) {
      state.drawerMode = "curated";
      state.activeMarketSignal = null;
      renderMarket();
      const themes = item.themes || [];
      const source = item.source || "Curated mapping";
      const notes = item.notes || "Trusted semantic mapping.";
      els.drawerTicker.textContent = "Curated Mapping";
      els.drawerName.textContent = item.ticker || "Ticker";
      els.drawerContent.innerHTML = `
        <div class="score-stack">
          ${scoreRow("Semantic score", item.score, "curated", "Trusted narrative score from the local semantic config")}
        </div>
        <div class="detail-grid">
          <div class="detail-card"><span>Source</span><strong>${escapeHtml(source)}</strong></div>
          <div class="detail-card"><span>Themes</span><strong>${themes.length}</strong></div>
          <div class="detail-card"><span>Status</span><strong>Trusted</strong></div>
          <div class="detail-card"><span>Config</span><strong>Local</strong></div>
        </div>
        <div class="reason-box">
          <strong>Why this matters</strong><br>
          Curated mappings override lightweight auto-research and tell SignalDeck to treat this stock as a trusted semantic match in future screens.
        </div>
        <div class="reason-box">
          <strong>Themes and exposure</strong>
          <div class="metric-grid">
            ${themes.length ? themes.map((theme) => tradePlanTile(theme.label || theme.key, theme.exposure || "direct", "exposure")).join("") : tradePlanTile("No themes", "--", "review")}
          </div>
        </div>
        <div class="reason-box">
          <strong>Notes</strong><br>
          ${escapeHtml(notes)}
        </div>
      `;
      els.drawer.classList.add("open");
    }

    function openRunDrawer(run) {
      state.drawerMode = "run";
      state.activeMarketSignal = null;
      renderMarket();
      els.drawerTicker.textContent = "Saved Run";
      els.drawerName.textContent = `${run.etf || "ETF"} · ${formatTime(run.generated_at)}`;
      els.drawerContent.innerHTML = `
        <div class="stock-hero">
          <div>
            <div class="eyebrow">Run summary</div>
            <div class="chip-row">
              <span class="pill buy">${run.buy} Buy</span>
              <span class="pill hold">${run.hold} Hold</span>
              <span class="pill sell">${run.sell} Sell</span>
            </div>
            <div class="formula">${run.screen_size} screened from ${run.holdings_count || run.screen_size} holdings.</div>
          </div>
          <div class="stock-hero-score">
            <span class="company">Avg final</span>
            <strong>${fmt(run.average_final_score)}</strong>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Market</div>
          <div class="detail-grid">
            <div class="detail-card"><span>Market score</span><strong>${fmt(run.market_score)}</strong></div>
            <div class="detail-card"><span>Regime</span><strong class="${toneClass(run.market_regime)}">${escapeHtml(run.market_regime || "neutral")}</strong></div>
            <div class="detail-card"><span>High certainty</span><strong>${fmt(run.high_certainty_percent, 0)}%</strong></div>
            <div class="detail-card"><span>Snapshot</span><strong>${escapeHtml(formatTime(run.generated_at))}</strong></div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Top buys</div>
          <div class="reason-box">
            <div class="chip-row">${(run.top_buys || []).map((ticker) => `<span class="chip">${escapeHtml(ticker)}</span>`).join("") || '<span class="chip">None</span>'}</div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Snapshot path</div>
          <div class="reason-box">
            <div class="formula">${escapeHtml(run.path || "")}</div>
          </div>
        </div>
      `;
      els.drawer.classList.add("open");
    }

    function isActiveMarketDrawer(signalKey) {
      return state.drawerMode === "market" && state.activeMarketSignal === signalKey && els.drawer.classList.contains("open");
    }

    function marketSignalDetailHtml(signal) {
      const details = signal.signal || {};
      const reasons = details.reasons || [];
      const metrics = marketMetricRows(signal.key, details);
      return `
        <div class="reason-box">
          <div class="signal-detail-head">
            <div>
              <div class="eyebrow">Signal detail</div>
              <h3>${escapeHtml(signal.label)}: ${fmt(signal.score)} <span class="${toneClass(signal.regime)}">${escapeHtml(signal.regime)}</span></h3>
              <p>${escapeHtml(signal.method)}</p>
            </div>
            <span class="pill quality">Weight ${escapeHtml(signal.weight)}</span>
          </div>
          <div class="formula">Score scale: 60+ bullish, 41-59 neutral, 40 or below bearish. The overall market score blends this signal with the other market signals.</div>
        </div>
        <div class="reason-box">
          <strong>Current read</strong>
          ${reasons.length ? `<ul class="signal-reasons">${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : `<div class="formula">No detailed reasons returned for this signal.</div>`}
        </div>
        <div class="reason-box">
          <strong>Inputs</strong>
          ${metrics.length ? `<div class="metric-strip">${metrics.map(([name, value]) => `<div><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`).join("")}</div>` : ""}
        </div>
      `;
    }

    function marketMetricRows(key, signal) {
      const metrics = signal.metrics || {};
      if (key === "demark") {
        return [
          ["Setup", `${signal.setup_side || "none"} ${signal.setup_count ?? 0}/9`],
          ["Countdown", `${signal.countdown_side || "none"} ${signal.countdown_count ?? 0}/13`],
          ["Exhaustion", signal.exhaustion || "neutral"]
        ];
      }
      return Object.entries(metrics).slice(0, 6).map(([name, value]) => [
        metricLabel(name),
        formatMetricValue(name, value)
      ]);
    }

    function metricLabel(name) {
      return name
        .replaceAll("_", " ")
        .replaceAll("percent", "%")
        .replaceAll("sma", "SMA")
        .replaceAll("spy", "SPY")
        .replaceAll("xlu", "XLU")
        .replaceAll("xlp", "XLP")
        .replaceAll("hyg", "HYG")
        .replaceAll("agg", "AGG")
        .replaceAll("lqd", "LQD")
        .replaceAll("oas", "OAS")
        .replaceAll("fred", "FRED")
        .replaceAll("uup", "UUP")
        .replaceAll("dxy", "DXY")
        .replaceAll("tnx", "TNX");
    }

    function formatMetricValue(name, value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
      if (name.includes("percent")) return pct(value);
      return Number(value).toFixed(Math.abs(Number(value)) >= 10 ? 2 : 4);
    }

    function toneClass(value) {
      const text = String(value || "").toLowerCase();
      if (text.includes("bullish")) return "tone-bullish";
      if (text.includes("bearish")) return "tone-bearish";
      return "tone-neutral";
    }

    function filteredRows() {
      const query = state.query.toLowerCase();
      return state.rows.filter((row) => {
        const h = row.holding;
        const text = `${h.ticker} ${h.name} ${row.rating} ${row.semantic.label} ${researchStatusLabel(row)} ${row.technical.label} ${row.semantic.reasons.join(" ")}`.toLowerCase();
        const matchesRating =
          state.filter === "all" ||
          row.rating === state.filter ||
          (state.filter === "research" && needsResearch(row)) ||
          (state.filter === "high-certainty" && certaintyBucket(row) === "high") ||
          (state.filter === "medium-certainty" && certaintyBucket(row) === "medium") ||
          (state.filter === "low-certainty" && certaintyBucket(row) === "low") ||
          (state.filter.startsWith("quality-") && qualityKey(row) === state.filter.replace("quality-", ""));
        return matchesRating && text.includes(query);
      });
    }

    function renderScreenSummary() {
      if (!state.rows.length) return;
      const rows = state.rows;
      const buyCount = rows.filter((row) => row.rating === "Buy").length;
      const holdCount = rows.filter((row) => row.rating === "Hold").length;
      const sellCount = rows.filter((row) => row.rating === "Sell").length;
      const avgFinal = average(rows.map((row) => row.final_score));
      const avgSemantic = average(rows.map((row) => row.semantic?.score));
      const avgTechnical = average(rows.map((row) => row.technical?.score));
      const highCertaintyPct = rows.length ? (rows.filter((row) => certaintyBucket(row) === "high").length / rows.length) * 100 : 0;
      const cleanBuys = rows.filter((row) => row.rating === "Buy" && qualityKey(row) === "clean").length;
      const lowConfidence = rows.filter((row) => !isResearchReliable(row)).length;
      const market = state.report?.market_condition?.overall_regime || "neutral";
      const tone = buyCount >= sellCount * 0.35 ? "constructive" : buyCount >= rows.length * 0.08 ? "selective" : "cautious";
      const action = screenActionText({ tone, buyCount, cleanBuys, lowConfidence, highCertaintyPct, market });
      const summary = `${state.report?.etf || "ETF"} screens ${tone}: ${buyCount} Buy, ${holdCount} Hold, ${sellCount} Sell. ${action}`;

      els.screenSummary.innerHTML = `
        <div class="summary-copy">${summary}</div>
        <div class="summary-stat"><span>Avg final</span><strong>${fmt(avgFinal)}</strong></div>
        <div class="summary-stat"><span>Semantic</span><strong>${fmt(avgSemantic)}</strong></div>
        <div class="summary-stat"><span>Technical</span><strong>${fmt(avgTechnical)}</strong></div>
        <div class="summary-stat"><span>Clean buys</span><strong>${cleanBuys}</strong></div>
        <div class="summary-stat"><span>Low conf</span><strong>${lowConfidence}</strong></div>
      `;
    }

    function screenActionText({ tone, buyCount, cleanBuys, lowConfidence, highCertaintyPct, market }) {
      if (!buyCount) return `No buys cleared the current bar; focus on research cleanup and wait for better technical confirmation.`;
      if (cleanBuys) return `${cleanBuys} clean Buy setup${cleanBuys === 1 ? "" : "s"} deserve first review; market backdrop is ${market}.`;
      if (lowConfidence > buyCount) return `Most opportunities still need narrative confidence; review low-confidence Buys before trusting the rank.`;
      if (highCertaintyPct < 15) return `The list is usable but research certainty is thin, so treat the ranks as a watchlist first.`;
      return `Use the top Buys as a focused watchlist and confirm entries against the trade plan.`;
    }

    function renderSourceSummary() {
      const report = state.report;
      if (!report || !state.rows.length) {
        els.sourceSummary.classList.remove("visible");
        els.sourceSummary.innerHTML = "";
        return;
      }
      const meta = report.metadata || {};
      const isMixed = meta.holdings_quality === "mixed_public_sources";
      const publicCount = Number(meta.public_weighted_holdings || 0);
      const placeholderCount = Number(meta.placeholder_weighted_holdings || 0);
      const countText = isMixed
        ? `${publicCount} public weighted · ${placeholderCount} placeholder`
        : `${state.rows.length} screened`;
      els.sourceSummary.className = `source-card visible${isMixed ? " warning" : ""}`;
      els.sourceSummary.innerHTML = `
        <div>
          <div class="eyebrow">Data source</div>
          <div class="summary-copy">${escapeHtml(report.source || "Unknown source")}</div>
          <div class="formula">${escapeHtml(meta.holdings_quality_note || "")}</div>
        </div>
        <div class="summary-stat"><span>Quality</span><strong>${escapeHtml(countText)}</strong></div>
      `;
    }

    function average(values) {
      const clean = values.filter((value) => value !== null && value !== undefined && !Number.isNaN(Number(value)));
      if (!clean.length) return null;
      return clean.reduce((sum, value) => sum + Number(value), 0) / clean.length;
    }

    function renderComparisonSummary() {
      const comparison = state.report?.comparison;
      if (!comparison?.available) {
        els.comparisonSummary.classList.remove("visible");
        els.comparisonSummary.innerHTML = "";
        return;
      }
      const movers = comparison.biggest_movers || [];
      const moverText = movers.length
        ? movers.slice(0, 3).map((item) => `${item.ticker} ${item.rank_change > 0 ? "+" : ""}${item.rank_change}`).join(", ")
        : "No rank movers";
      const newBuys = comparison.new_buys?.length ? comparison.new_buys.join(", ") : "None";
      const droppedBuys = comparison.dropped_buys?.length ? comparison.dropped_buys.join(", ") : "None";
      els.comparisonSummary.classList.add("visible");
      els.comparisonSummary.innerHTML = `
        <div class="summary-copy">Since last run: Buy ${signed(comparison.buy_change)}, avg final ${signed(comparison.average_final_score_change)}.</div>
        <div class="summary-stat"><span>New buys</span><strong>${newBuys}</strong></div>
        <div class="summary-stat"><span>Dropped</span><strong>${droppedBuys}</strong></div>
        <div class="summary-stat"><span>Movers</span><strong>${moverText}</strong></div>
      `;
    }

    function signed(value) {
      const number = Number(value || 0);
      return `${number > 0 ? "+" : ""}${number}`;
    }

    function renderTable() {
      const rows = sortedRows(filteredRows());
      updateSortHeaders();
      els.body.innerHTML = rows.map((row, index) => {
        const price = row.price || {};
        const rel = relative3m(row);
        const plan = row.trade_plan || {};
        const quality = qualityBadge(row);
        return `
          <tr data-index="${state.rows.indexOf(row)}">
            <td class="num">${index + 1}</td>
            <td><div class="ticker">${row.holding.ticker}</div><div class="company">${row.holding.name}</div></td>
            <td class="num">${fmt(row.final_score)}</td>
            <td class="num">${fmt(row.semantic.score)} <span class="company">${row.semantic.label}</span></td>
            <td><span class="${researchPillClass(row)}">${researchStatusLabel(row)}</span></td>
            <td class="num">${fmt(confidence(row) * 100, 0)}% <span class="company">${certaintyLabel(row)}</span></td>
            <td class="num">${fmt(row.technical.score)} <span class="company">${row.technical.label}</span></td>
            <td class="num ${cls(rel)}">${pct(rel)}</td>
            <td class="num">${priceRange(plan.entry_low, plan.entry_high)} <span class="company">${escapeHtml(plan.label || "")}</span></td>
            <td class="num">${plan.reward_risk ? `${fmt(plan.reward_risk, 2)}x` : "--"} <span class="company">${money(plan.stop_loss)} stop</span></td>
            <td><span class="${quality.className}">${escapeHtml(quality.label)}</span></td>
            <td><span class="${pillClass(row.rating)}">${row.rating}</span></td>
          </tr>
        `;
      }).join("");

      els.body.querySelectorAll("tr").forEach((tr) => {
        tr.addEventListener("click", () => openDrawer(state.rows[Number(tr.dataset.index)]));
      });
    }

    function sortedRows(rows) {
      const direction = state.sort.direction === "asc" ? 1 : -1;
      return [...rows].sort((a, b) => {
        const left = sortValue(a, state.sort.key);
        const right = sortValue(b, state.sort.key);
        const result = compareValues(left, right);
        return result * direction;
      });
    }

    function sortValue(row, key) {
      const qualityOrder = {
        "clean": 1,
        "technical-only": 2,
        "mixed-signal": 3,
        "poor-reward-risk": 4,
        "weak-technicals": 5,
        "low-confidence": 6,
        "missing-price": 7
      };
      const ratingOrder = { Buy: 3, Hold: 2, Sell: 1 };
      if (key === "rank") return state.rows.indexOf(row);
      if (key === "ticker") return row.holding?.ticker || "";
      if (key === "final") return row.final_score;
      if (key === "semantic") return row.semantic?.score;
      if (key === "research") return researchStatusLabel(row);
      if (key === "certainty") return confidence(row);
      if (key === "technical") return row.technical?.score;
      if (key === "relative") return relative3m(row);
      if (key === "entry") return row.trade_plan?.entry_low;
      if (key === "rewardRisk") return row.trade_plan?.reward_risk;
      if (key === "quality") return qualityOrder[qualityKey(row)] || 99;
      if (key === "rating") return ratingOrder[row.rating] || 0;
      return "";
    }

    function compareValues(left, right) {
      const leftMissing = left === null || left === undefined || Number.isNaN(left);
      const rightMissing = right === null || right === undefined || Number.isNaN(right);
      if (leftMissing && rightMissing) return 0;
      if (leftMissing) return -1;
      if (rightMissing) return 1;
      if (typeof left === "string" || typeof right === "string") {
        return String(left).localeCompare(String(right));
      }
      return Number(left) - Number(right);
    }

    function updateSortHeaders() {
      document.querySelectorAll("th.sortable").forEach((header) => {
        const active = header.dataset.sort === state.sort.key;
        header.classList.toggle("active", active);
        header.dataset.indicator = state.sort.direction === "asc" ? "↑" : "↓";
      });
    }

    function relative3m(row) {
      const benchmark = state.report?.metadata?.benchmark_return_60d_percent;
      const value = row.price?.return_60d_percent;
      if (benchmark === null || benchmark === undefined || value === null || value === undefined) return null;
      return value - benchmark;
    }

    function renderSidebar() {
      const researchRows = state.rows.filter(needsResearch);
      const lowConfidenceRows = state.rows.filter((row) => !needsResearch(row) && !isResearchReliable(row));
      els.leaders.innerHTML = state.rows.slice(0, 5).map((row) => `
        <div class="watch-row">
          <div><div class="ticker">${row.holding.ticker}</div><div class="company">${row.rating}</div></div>
          <strong>${fmt(row.final_score)}</strong>
        </div>
      `).join("");

      const counts = state.rows.reduce((acc, row) => {
        acc[row.rating] = (acc[row.rating] || 0) + 1;
        return acc;
      }, {});
      const certaintyCounts = state.rows.reduce((acc, row) => {
        const bucket = certaintyBucket(row);
        acc[bucket] = (acc[bucket] || 0) + 1;
        return acc;
      }, {});
      const distributionRows = [
        ["Buy", counts.Buy || 0, "Current screen"],
        ["Hold", counts.Hold || 0, "Current screen"],
        ["Sell", counts.Sell || 0, "Current screen"],
        ["High certainty", certaintyCounts.high || 0, "85%+"],
        ["Medium certainty", certaintyCounts.medium || 0, "65%-84%"],
        ["Low certainty", certaintyCounts.low || 0, "Below 65%"],
      ];
      els.distribution.innerHTML = distributionRows.map(([label, value, note]) => `
        <div class="watch-row">
          <div><div class="ticker">${label}</div><div class="company">${note}</div></div>
          <strong>${value}</strong>
        </div>
      `).join("");
      renderRuns();

      els.researchQueue.innerHTML = `
        <div class="queue-card compact">
          <strong>${state.rows.length}</strong>
          <p>Refresh Yahoo/Wiki research for every stock in the current screen.</p>
          <div class="formula">${researchRows.length} true research gaps · ${lowConfidenceRows.length} researched low-confidence names.</div>
          <div class="chip-row">
            <button class="btn" id="refreshAllResearchButton" type="button" ${state.rows.length ? "" : "disabled"}>Refresh All Research</button>
            <button class="btn secondary" id="nextResearchButton" type="button" ${researchRows.length ? "" : "disabled"}>Open Gap</button>
          </div>
          <div class="status" id="bulkResearchStatus"></div>
        </div>
        <div class="queue-card compact">
          <div class="research-tabs">
            <button class="tab ${state.researchPanelTab === "review" ? "active" : ""}" data-research-panel-tab="review" type="button">Review ${state.reviewItems.length}</button>
            <button class="tab ${state.researchPanelTab === "curated" ? "active" : ""}" data-research-panel-tab="curated" type="button">Curated ${state.curatedItems.length}</button>
          </div>
          <div class="research-scroll">
            ${state.researchPanelTab === "curated" ? curatedListHtml() : reviewQueueHtml()}
          </div>
        </div>
      `;
      document.getElementById("refreshAllResearchButton")?.addEventListener("click", runBulkResearch);
      document.getElementById("nextResearchButton")?.addEventListener("click", () => {
        const next = state.rows.find(needsResearch);
        if (next) openDrawer(next);
      });
      document.querySelectorAll("[data-review-ticker]").forEach((button) => {
        button.addEventListener("click", () => {
          const row = state.rows.find((item) => item.holding.ticker === button.dataset.reviewTicker);
          if (row) openDrawer(row);
        });
      });
      document.querySelectorAll("[data-promote-ticker]").forEach((button) => {
        button.addEventListener("click", () => promoteReviewItem(button.dataset.promoteTicker, button));
      });
      document.querySelectorAll("[data-clear-review]").forEach((button) => {
        button.addEventListener("click", () => clearReviewItem(button.dataset.clearReview, button));
      });
      document.querySelectorAll("[data-research-panel-tab]").forEach((button) => {
        button.addEventListener("click", () => {
          state.researchPanelTab = button.dataset.researchPanelTab;
          renderSidebar();
        });
      });
      document.querySelectorAll("[data-curated-ticker]").forEach((button) => {
        button.addEventListener("click", () => {
          const item = state.curatedItems.find((curated) => curated.ticker === button.dataset.curatedTicker);
          if (item) openCuratedDrawer(item);
        });
      });
    }

    function renderRuns() {
      if (!els.runs) return;
      const runs = state.runs || [];
      if (!runs.length) {
        els.runs.innerHTML = '<div class="review-empty">Saved screen runs will appear after you run an ETF screen.</div>';
        return;
      }
      els.runs.innerHTML = runs.map((run) => `
        <button class="watch-row run-row" type="button" data-run-id="${escapeHtml(run.id)}">
          <div>
            <div class="ticker">${escapeHtml(formatTime(run.generated_at))}</div>
            <div class="company">${escapeHtml(run.etf || "ETF")} · ${run.screen_size} screened · ${escapeHtml(run.market_regime || "neutral")}</div>
          </div>
          <strong>${fmt(run.average_final_score)}</strong>
        </button>
      `).join("");
      document.querySelectorAll("[data-run-id]").forEach((button) => {
        button.addEventListener("click", () => {
          const run = state.runs.find((item) => item.id === button.dataset.runId);
          if (run) openRunDrawer(run);
        });
      });
    }

    function reviewQueueHtml() {
      const items = state.reviewItems.slice(0, 5);
      if (!items.length) {
        return '<div class="review-empty">No saved review items yet. Open a stock drawer and use Mark for Review when something deserves a closer look.</div>';
      }
      return `
        <div class="review-list">
          ${items.map((item) => {
            const rowExists = state.rows.some((row) => row.holding.ticker === item.ticker);
            const confidence = Number(item.semantic?.confidence ?? 0) * 100;
            const promoted = Boolean(item.promoted);
            return `
              <div class="review-row">
                <div class="review-row-head">
                  <div>
                    <div class="ticker">${escapeHtml(item.ticker || "--")}</div>
                    <div class="company">${escapeHtml(item.name || "Unknown company")}</div>
                  </div>
                  <strong>${fmt(item.final_score)}</strong>
                </div>
                <div class="review-meta">
                  <span>${escapeHtml(item.etf || "ETF")}</span>
                  <span>${escapeHtml(item.rating || "Rating")}</span>
                  <span>${fmt(confidence, 0)}% certainty</span>
                  <span>${escapeHtml(item.quality?.label || "Review")}</span>
                  ${promoted ? "<span>Curated</span>" : ""}
                </div>
                <div class="formula">${escapeHtml(item.decision_risk || item.quality?.reason || "Saved for semantic review.")}</div>
                <div class="review-actions">
                  <button class="btn secondary" data-review-ticker="${escapeHtml(item.ticker || "")}" type="button" ${rowExists ? "" : "disabled"}>Open</button>
                  <button class="btn" data-promote-ticker="${escapeHtml(item.ticker || "")}" type="button" ${promoted ? "disabled" : ""}>${promoted ? "Curated" : "Promote"}</button>
                  <button class="btn secondary" data-clear-review="${escapeHtml(item.ticker || "")}" type="button">Clear</button>
                </div>
                <div class="status" id="promoteStatus-${escapeHtml(item.ticker || "")}"></div>
              </div>
            `;
          }).join("")}
          ${state.reviewItems.length > items.length ? `<div class="formula">Showing latest ${items.length} of ${state.reviewItems.length} saved review items.</div>` : ""}
        </div>
      `;
    }

    function curatedListHtml() {
      const items = state.curatedItems.slice(0, 5);
      if (!items.length) {
        return '<div class="review-empty">No curated mappings yet. Promote review items when the semantic match looks right.</div>';
      }
      return `
        <div class="review-list">
          ${items.map((item) => {
            const themeText = (item.themes || []).slice(0, 2).map((theme) => `${theme.label} (${theme.exposure})`).join(", ");
            return `
              <button class="review-row curated-row" data-curated-ticker="${escapeHtml(item.ticker || "")}" type="button">
                <div class="review-row-head">
                  <div>
                    <div class="ticker">${escapeHtml(item.ticker || "--")}</div>
                    <div class="company">${escapeHtml(item.source || "Curated mapping")}</div>
                  </div>
                  <strong>${fmt(item.score)}</strong>
                </div>
                <div class="formula">${escapeHtml(themeText || "No themes listed.")}</div>
                <div class="review-meta">
                  <span>${item.theme_count || 0} themes</span>
                  <span>${escapeHtml(truncateText(item.notes || "Trusted semantic mapping.", 88))}</span>
                </div>
              </button>
            `;
          }).join("")}
          ${state.curatedItems.length > items.length ? `<div class="formula">Showing latest ${items.length} of ${state.curatedItems.length} curated names.</div>` : ""}
        </div>
      `;
    }

    async function promoteReviewItem(ticker, button) {
      const status = document.getElementById(`promoteStatus-${ticker}`);
      if (button) button.disabled = true;
      if (status) status.textContent = "Promoting to curated...";

      try {
        const response = await fetch("/api/review/promote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker })
        });
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Promotion failed");
        if (status) status.textContent = "Curated. Updating screen...";
        state.researchPanelTab = "curated";
        await loadThemes();
        await loadReviewQueue();
        await loadCuratedItems();
        await runScreen(true);
      } catch (error) {
        if (status) status.textContent = error.message;
        if (button) button.disabled = false;
      }
    }

    async function clearReviewItem(ticker, button) {
      if (button) button.disabled = true;
      try {
        const response = await fetch("/api/review/clear", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker })
        });
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Clear failed");
        await loadReviewQueue();
      } catch (error) {
        if (button) {
          button.textContent = "Error";
          button.disabled = false;
        }
      }
    }

    async function runBulkResearch() {
      const button = document.getElementById("refreshAllResearchButton");
      const status = document.getElementById("bulkResearchStatus");
      const etf = els.etf.value.trim() || "SPY";
      const limit = els.limit.value || "";
      const screenSize = state.rows.length || "current screen";
      if (button) button.disabled = true;
      if (status) status.textContent = `Refreshing research for ${screenSize} stocks...`;

      try {
        const params = new URLSearchParams({ etf, refresh: "true" });
        if (limit) params.set("limit", limit);
        const response = await fetch(`/api/research/bulk?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Research refresh failed");
        if (status) {
          status.textContent = `Refreshed ${payload.processed}; auto-approved ${payload.auto_approved}; low confidence ${payload.unreliable}; errors ${payload.errors}. Updating screen...`;
        }
        await runScreen(true);
      } catch (error) {
        if (status) status.textContent = error.message;
      } finally {
        if (button) button.disabled = false;
      }
    }

    function needsResearch(row) {
      const reasons = row.semantic?.reasons || [];
      const firstReason = reasons[0] || "";
      const source = row.semantic?.source || "";
      return (
        row.semantic?.research_status === "unreviewed" ||
        source === "Local auto-research fallback" ||
        firstReason.includes("No curated mapping")
      );
    }

    function isResearchReliable(row) {
      return row.semantic?.research_status === "curated" || row.semantic?.research_status === "auto_approved";
    }

    function confidence(row) {
      return Number(row.semantic?.confidence ?? 0);
    }

    function certaintyBucket(row) {
      const value = confidence(row);
      if (value >= 0.85) return "high";
      if (value >= 0.65) return "medium";
      return "low";
    }

    function certaintyLabel(row) {
      const bucket = certaintyBucket(row);
      if (bucket === "high") return "high";
      if (bucket === "medium") return "medium";
      return "low";
    }

    function researchStatusLabel(row) {
      if (row.semantic?.research_status === "curated") return "Curated";
      if (row.semantic?.research_status === "auto_approved") return "Auto-approved";
      if (!needsResearch(row)) return "Low confidence";
      return "Not reliable";
    }

    function researchPillClass(row) {
      if (row.semantic?.research_status === "curated") return "pill buy";
      if (row.semantic?.research_status === "auto_approved") return "pill research";
      return "pill warn";
    }

    function qualityBadge(row) {
      const semanticScore = Number(row.semantic?.score ?? 0);
      const technicalScore = Number(row.technical?.score ?? 0);
      const rewardRisk = Number(row.trade_plan?.reward_risk ?? 0);

      if (!row.price || row.price.error) {
        return { key: "missing-price", label: "Missing price", className: "pill warn", reason: "Technical and entry/exit signals need a valid price snapshot." };
      }
      if (!isResearchReliable(row)) {
        return { key: "low-confidence", label: "Low confidence", className: "pill warn", reason: "Research exists, but the semantic evidence is not strong enough to fully trust yet." };
      }
      if (technicalScore < 45) {
        return { key: "weak-technicals", label: "Weak technicals", className: "pill sell", reason: "The technical score is weak, so timing may be poor even if the narrative is interesting." };
      }
      if (rewardRisk && rewardRisk < 1.5) {
        return { key: "poor-reward-risk", label: "Poor reward/risk", className: "pill warn", reason: "The estimated target does not offer much upside versus the risk exit." };
      }
      if (semanticScore >= 65 && technicalScore < 60) {
        return { key: "mixed-signal", label: "Mixed signal", className: "pill hold", reason: "The narrative is strong, but technical confirmation is not fully aligned." };
      }
      if (semanticScore < 45 && technicalScore >= 75) {
        return { key: "technical-only", label: "Technical only", className: "pill hold", reason: "The chart is strong, but the stock does not clearly match your semantic themes." };
      }
      return { key: "clean", label: "Clean setup", className: "pill quality", reason: "No major caveat is showing across research confidence, technicals, and reward/risk." };
    }

    function qualityKey(row) {
      return qualityBadge(row).key;
    }

    function openDrawer(row) {
      state.drawerMode = "stock";
      state.activeMarketSignal = null;
      renderMarket();
      const price = row.price || {};
      const plan = row.trade_plan || {};
      const semanticThemes = extractThemes(row.semantic.reasons?.[0]);
      const decision = decisionSummary(row, semanticThemes);
      const quality = qualityBadge(row);
      const reliable = isResearchReliable(row);
      const tech = row.technical?.exposures || {};
      const technicalBuckets = [
        ["DeMark", `${tech.demark_points || "--"}/10`, `ETF DeMark ${tech.demark_source_score || "--"}`, toneFromScore(tech.demark_source_score)],
        ["Momentum", `${tech.momentum_points || "--"}/70`, `${tech.momentum_true_count || "0"}/3 ${tech.momentum_label || ""}`, toneFromSignal(tech.momentum_label)],
        ["Price", `${tech.price_points || "--"}/10`, `Price > 20D ${tech.price_above_20d || "missing"}`, toneFromBooleanText(tech.price_above_20d)],
        ["Moving Average", `${tech.moving_average_points || "--"}/10`, `20D > 200D ${tech.sma_20_above_200d || "missing"}`, toneFromBooleanText(tech.sma_20_above_200d)]
      ];
      els.drawerTicker.textContent = row.holding.ticker;
      els.drawerName.textContent = row.holding.name;
      els.drawerContent.innerHTML = `
        <div class="stock-hero">
          <div>
            <div class="eyebrow">Decision overview</div>
            <div class="chip-row">
              <span class="${pillClass(row.rating)}">${row.rating}</span>
              <span class="${quality.className}">${escapeHtml(quality.label)}</span>
              <span class="${researchPillClass(row)}">${researchStatusLabel(row)}</span>
            </div>
            <div class="formula">${escapeHtml(quality.reason)}</div>
          </div>
          <div class="stock-hero-score">
            <span class="company">Final</span>
            <strong>${fmt(row.final_score)}</strong>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Score stack</div>
          <div class="score-stack">
            ${scoreRow("Semantic score", row.semantic.score, row.semantic.label, "Narrative fit to your five themes")}
            ${scoreRow("Technical score", row.technical.score, row.technical.label, "DeMark 10%, Momentum 70%, Price 10%, Moving Average 10%")}
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Decision</div>
          <div class="reason-box">
            ${escapeHtml(decision.summary)}
            <div class="formula"><strong>Main risk:</strong> ${escapeHtml(decision.risk)}</div>
            <div class="chip-row review-action">
              <button class="btn secondary" id="markReviewButton" type="button">Mark for Review</button>
              <span class="status" id="markReviewStatus"></span>
            </div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Snapshot</div>
          <div class="detail-grid">
            <div class="detail-card"><span>Last Close</span><strong>$${fmt(price.last_close, 2)}</strong></div>
            <div class="detail-card"><span>ETF Weight</span><strong>${pct(row.holding.weight)}</strong></div>
            <div class="detail-card"><span>3M vs SPY</span><strong class="${cls(relative3m(row))}">${pct(relative3m(row))}</strong></div>
            <div class="detail-card"><span>Confidence</span><strong>${fmt(confidence(row) * 100, 0)}%</strong></div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Trade plan</div>
          <div class="reason-box">
            <div class="metric-grid">
              ${tradePlanTile("Entry zone", priceRange(plan.entry_low, plan.entry_high), plan.label || "watch", tradePlanTone(plan.label))}
              ${tradePlanTile("Breakout", money(plan.breakout_trigger), "confirm", "bullish")}
              ${tradePlanTile("Risk exit", money(plan.stop_loss), "stop", "bearish")}
              ${tradePlanTile("Target 1", money(plan.target_1), "trim", "neutral")}
              ${tradePlanTile("Target 2", money(plan.target_2), "stretch", "bearish")}
              ${tradePlanTile("Reward/Risk", plan.reward_risk ? `${fmt(plan.reward_risk, 2)}x` : "--", "estimate", rewardRiskTone(plan.reward_risk))}
            </div>
            <div class="formula">${(plan.notes || []).map(escapeHtml).join("<br>")}</div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Narrative</div>
          ${reliable ? "" : `
            <div class="reason-box warning-box">
              <strong>Research not reliable</strong><br>
              This stock is still auto-scored, but the semantic research does not have enough confidence yet. Treat the ranking as provisional until reviewed.
            </div>
          `}
          <div class="reason-box">
            <strong>Semantic themes</strong>
            <div class="chip-row">${semanticThemeChips(row, semanticThemes)}</div>
            <div class="formula">${escapeHtml(row.semantic.reasons?.[1] || "")}</div>
            <div class="formula">Source: ${escapeHtml(row.semantic.source || "Unknown")}</div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Evidence</div>
          <div class="reason-box">
            <div class="research-head">
              <strong>Research evidence</strong>
              <button class="btn secondary" id="refreshResearchButton" type="button">Refresh Research</button>
            </div>
            <div id="researchEvidence">Loading research evidence...</div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Technical</div>
          <div class="reason-box">
            <div class="metric-grid">
              ${technicalBuckets.map(([label, value, tag, tone]) => tradePlanTile(label, value, tag, tone)).join("")}
            </div>
            <div class="formula">${row.technical.reasons.join("<br>")}</div>
          </div>
        </div>
        <div class="drawer-section">
          <div class="drawer-section-title">Logic</div>
          <div class="reason-box">${row.rating_reasons.join("<br>")}</div>
        </div>
      `;
      els.drawer.classList.add("open");
      document.getElementById("refreshResearchButton").addEventListener("click", () => loadResearchEvidence(row, true));
      document.getElementById("markReviewButton").addEventListener("click", () => markForReview(row, semanticThemes, decision, quality));
      loadResearchEvidence(row, false);
    }

    async function markForReview(row, semanticThemes, decision, quality) {
      const button = document.getElementById("markReviewButton");
      const status = document.getElementById("markReviewStatus");
      if (button) button.disabled = true;
      if (status) status.textContent = "Saving review note...";

      const payload = {
        ticker: row.holding?.ticker,
        name: row.holding?.name,
        etf: state.report?.etf || els.etf.value.trim() || "SPY",
        rating: row.rating,
        final_score: row.final_score,
        semantic: {
          score: row.semantic?.score,
          label: row.semantic?.label,
          confidence: row.semantic?.confidence,
          research_status: row.semantic?.research_status,
          source: row.semantic?.source,
          exposures: row.semantic?.exposures || {},
          reasons: row.semantic?.reasons || []
        },
        technical: {
          score: row.technical?.score,
          label: row.technical?.label,
          reasons: row.technical?.reasons || []
        },
        quality: {
          label: quality.label,
          reason: quality.reason
        },
        themes: semanticThemeLabels(row, semanticThemes),
        decision_summary: decision.summary,
        decision_risk: decision.risk
      };

      try {
        const response = await fetch("/api/review", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok || result.error) throw new Error(result.error || "Review save failed");
        if (status) status.textContent = `Saved to review queue (${result.review_count})`;
        await loadReviewQueue();
      } catch (error) {
        if (status) status.textContent = error.message;
      } finally {
        if (button) button.disabled = false;
      }
    }

    async function loadResearchEvidence(row, refresh = false) {
      const target = document.getElementById("researchEvidence");
      const button = document.getElementById("refreshResearchButton");
      if (!target) return;
      target.textContent = refresh ? "Refreshing research..." : "Loading research evidence...";
      if (button) button.disabled = true;

      const params = new URLSearchParams({
        ticker: row.holding.ticker,
        name: row.holding.name,
        refresh: String(refresh)
      });

      try {
        const response = await fetch(`/api/research?${params.toString()}`);
        const payload = await response.json();
        if (!response.ok || payload.error) throw new Error(payload.error || "Research failed");
        target.innerHTML = researchEvidenceHtml(payload);
        if (refresh) {
          await runScreen(true);
          const updated = state.rows.find((item) => item.holding.ticker === row.holding.ticker);
          if (updated) openDrawer(updated);
        }
      } catch (error) {
        target.innerHTML = `<div class="formula">Research unavailable: ${escapeHtml(error.message)}</div>`;
      } finally {
        if (button) button.disabled = false;
      }
    }

    function researchEvidenceHtml(payload) {
      const matches = payload.matches || [];
      const guardrails = payload.guardrails || [];
      const matchHtml = matches.length
        ? matches.map((match) => `
            <div class="research-match">
              <strong>${escapeHtml(match.theme_label)}</strong>
              <span class="strength ${escapeHtml(match.exposure || "weak")}">${escapeHtml(match.exposure || "weak")}</span>
              <span class="strength ${escapeHtml(match.strength || "weak")}">${escapeHtml(match.strength || "weak")}</span>
              <div class="chip-row">${(match.terms || []).map((term) => `<span class="chip">${escapeHtml(term)}</span>`).join("")}</div>
            </div>
          `).join("")
        : '<div class="formula">No direct theme evidence found in the profile text.</div>';
      const guardrailHtml = guardrails.length
        ? guardrails.map((item) => `
            <div class="research-match warning-box">
              <strong>${escapeHtml(item.theme_label)}</strong>
              <div class="formula">${escapeHtml(item.reason || "Blocked by semantic guardrail.")}</div>
              <div class="chip-row">${(item.terms || []).map((term) => `<span class="chip">${escapeHtml(term)}</span>`).join("")}</div>
            </div>
          `).join("")
        : '<div class="formula">No guardrails were triggered for this profile.</div>';

      return `
        <div class="research-grid">
          <div class="research-item"><span>Sector</span><strong>${escapeHtml(payload.sector || "Unknown")}</strong></div>
          <div class="research-item"><span>Industry</span><strong>${escapeHtml(payload.industry || "Unknown")}</strong></div>
          <div class="research-item"><span>Confidence</span><strong>${fmt(Number(payload.confidence || 0) * 100, 0)}%</strong></div>
          <div class="research-item"><span>Status</span><strong>${payload.research_status === "auto_approved" ? "Auto-approved" : "Not reliable"}</strong></div>
        </div>
        <div class="formula">Source: ${escapeHtml(payload.source || "Unknown")}</div>
        <div class="formula">${escapeHtml(payload.summary || "No business summary returned.")}</div>
        <div class="research-list">${matchHtml}</div>
        <div class="research-list">
          <div class="eyebrow">Guardrails</div>
          ${guardrailHtml}
        </div>
      `;
    }

    function technicalMetricTile(label, value, threshold) {
      const signal = technicalSignal(value, threshold);
      return `
        <div class="metric-tile">
          <span>${label}</span>
          <strong class="${signal}">${pct(value)}</strong>
          <em class="${signal}">${signal}</em>
        </div>
      `;
    }

    function tradePlanTile(label, value, tag, tone = null) {
      const indicatorTone = tone || toneFromSignal(tag) || "neutral";
      return `
        <div class="metric-tile">
          <span>${label}</span>
          <strong class="${indicatorTone}">${escapeHtml(value)}</strong>
          <em class="${indicatorTone}">${escapeHtml(tag)}</em>
        </div>
      `;
    }

    function toneFromSignal(value) {
      const text = String(value || "").toLowerCase();
      if (["bullish", "buy", "positive", "true", "confirm", "near entry", "clean", "curated", "direct"].some((term) => text.includes(term))) {
        return "bullish";
      }
      if (["bearish", "sell", "negative", "false", "weak", "stop", "stretch", "risk", "wait for reclaim"].some((term) => text.includes(term))) {
        return "bearish";
      }
      if (["hold", "neutral", "watch", "trim", "estimate", "indirect", "review", "mixed", "pullback"].some((term) => text.includes(term))) {
        return "neutral";
      }
      return null;
    }

    function toneFromBooleanText(value) {
      const text = String(value || "").toLowerCase();
      if (text === "true") return "bullish";
      if (text === "false") return "bearish";
      return "neutral";
    }

    function toneFromScore(value) {
      const score = Number(value);
      if (Number.isNaN(score)) return "neutral";
      if (score >= 60) return "bullish";
      if (score <= 40) return "bearish";
      return "neutral";
    }

    function tradePlanTone(label) {
      const text = String(label || "").toLowerCase();
      if (text.includes("near entry") || text.includes("breakout")) return "bullish";
      if (text.includes("reclaim")) return "bearish";
      return "neutral";
    }

    function rewardRiskTone(value) {
      const ratio = Number(value);
      if (Number.isNaN(ratio)) return "neutral";
      if (ratio >= 2) return "bullish";
      if (ratio < 1.5) return "bearish";
      return "neutral";
    }

    function truncateText(value, maxLength) {
      const text = String(value || "");
      return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
    }

    function decisionSummary(row, semanticThemes) {
      const ticker = row.holding?.ticker || "This stock";
      const rating = row.rating || "Hold";
      const themeLabels = semanticThemeLabels(row, semanticThemes);
      const themeText = themeLabels.length
        ? `matches ${themeLabels.slice(0, 3).join(", ")}`
        : "does not yet show a strong match to your core themes";
      const certainty = certaintyLabel(row);
      const technical = row.technical?.label || "unknown";
      const plan = row.trade_plan || {};
      const entry = priceRange(plan.entry_low, plan.entry_high);
      const stop = money(plan.stop_loss);
      const market = state.report?.market_condition?.overall_regime || "neutral";
      const summary = `${ticker} is rated ${rating} because it ${themeText}, has ${certainty}-certainty research, and its technical setup is ${technical}. Preferred entry is ${entry}, with risk exit below ${stop}. Current market backdrop is ${market}.`;
      return {
        summary,
        risk: decisionRisk(row, semanticThemes, market)
      };
    }

    function decisionRisk(row, semanticThemes, market) {
      if (!isResearchReliable(row)) {
        return "semantic evidence is low-confidence, so the narrative fit may be overstated or missing important context.";
      }
      if ((row.technical?.score ?? 0) < 45) {
        return "technical momentum is weak, so the stock may need a reclaim or base before the setup improves.";
      }
      if (row.rating === "Buy" && String(market).toLowerCase().includes("bearish")) {
        return "the stock screens well, but the broader market backdrop is bearish.";
      }
      if ((semanticThemes || []).length === 0) {
        return "there is no clear match to your preferred semantic themes yet.";
      }
      if ((row.trade_plan?.reward_risk ?? 0) < 1.5) {
        return "the current entry/exit setup has limited estimated reward versus risk.";
      }
      return "the setup depends on the narrative and technical trend continuing to hold.";
    }

    function technicalSignal(value, threshold) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "neutral";
      const number = Number(value);
      if (number >= threshold) return "bullish";
      if (number <= -threshold) return "bearish";
      return "neutral";
    }

    function scoreRow(label, value, detail, formula) {
      const safeValue = value ?? 0;
      const width = Math.max(0, Math.min(100, Number(safeValue)));
      return `
        <div class="score-row">
          <div class="score-row-head"><span>${label}</span><span>${fmt(value)} ${detail}</span></div>
          <div class="bar" style="--value: ${width}%"><span></span></div>
          <div class="formula">${formula}</div>
        </div>
      `;
    }

    function money(value) {
      if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
      return `$${Number(value).toFixed(2)}`;
    }

    function priceRange(low, high) {
      if (low === null || low === undefined || high === null || high === undefined) return "--";
      return `${money(low)}-${money(high)}`;
    }

    function extractThemes(reason) {
      if (!reason) return [];
      const cleaned = reason.replace("Matched themes:", "").replace("Fallback themes:", "").replace(".", "").trim();
      if (!cleaned || cleaned === "none") return [];
      return cleaned.split(",").map((item) => item.trim()).filter(Boolean);
    }

    function semanticThemeLabels(row, fallbackThemes) {
      const exposures = row.semantic?.exposures || {};
      const labels = Object.entries(exposures).map(([themeKey, exposure]) => {
        const label = state.themes?.[themeKey]?.label || themeKey;
        return `${label} (${exposure})`;
      });
      return labels.length ? labels : fallbackThemes;
    }

    function semanticThemeChips(row, fallbackThemes) {
      const labels = semanticThemeLabels(row, fallbackThemes);
      return labels.map((theme) => `<span class="chip">${escapeHtml(theme)}</span>`).join("") || '<span class="chip">Needs research</span>';
    }

    function escapeHtml(value) {
      return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    }

    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      runScreen(false);
    });

    els.refresh.addEventListener("click", () => runScreen(true));
    els.quit.addEventListener("click", async () => {
      if (!window.confirm("Quit SignalDeck?")) return;
      els.status.textContent = "Closing SignalDeck...";
      try {
        await fetch("/api/shutdown", { method: "POST" });
      } finally {
        document.body.innerHTML = `
          <div style="min-height:100vh;display:grid;place-items:center;padding:24px;background:#070807;color:#f1f4ec;font-family:Segoe UI,sans-serif;">
            <div style="max-width:520px;text-align:center;">
              <h1 style="margin:0 0 12px;font-size:42px;">SignalDeck is closed</h1>
              <p style="color:#899184;line-height:1.5;">You can close this browser tab now. Double-click SignalDeck again whenever you want to reopen the dashboard.</p>
            </div>
          </div>
        `;
      }
    });
    els.limit.addEventListener("change", () => {
      localStorage.setItem("signaldeck:lastLimit", els.limit.value);
    });
    els.recentEtfs.addEventListener("click", (event) => {
      const shortcut = event.target.closest(".shortcut");
      if (!shortcut) return;
      els.etf.value = shortcut.dataset.etf;
      runScreen(false);
    });
    els.search.addEventListener("input", () => {
      state.query = els.search.value;
      renderTable();
    });
    document.getElementById("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest(".tab");
      if (!tab) return;
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      state.filter = tab.dataset.rating;
      renderTable();
    });
    document.querySelector("thead").addEventListener("click", (event) => {
      const header = event.target.closest("th.sortable");
      if (!header) return;
      const key = header.dataset.sort;
      if (state.sort.key === key) {
        state.sort.direction = state.sort.direction === "asc" ? "desc" : "asc";
      } else {
        state.sort.key = key;
        state.sort.direction = key === "ticker" || key === "research" || key === "quality" ? "asc" : "desc";
      }
      renderTable();
    });
    els.drawerClose.addEventListener("click", () => {
      els.drawer.classList.remove("open");
      state.drawerMode = null;
      state.activeMarketSignal = null;
      renderMarket();
    });

    loadRunPreferences();
    loadThemes()
      .then(() => loadReviewQueue())
      .then(() => loadCuratedItems())
      .then(() => runScreen(false));
  </script>
</body>
</html>"""

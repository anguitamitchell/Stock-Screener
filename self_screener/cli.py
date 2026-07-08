import argparse
import sys
from typing import List, Optional

from self_screener.dashboard import write_dashboard
from self_screener.market_condition import build_market_condition, write_market_condition
from self_screener.pipeline import screen_etf, write_report
from self_screener.providers.ssga_holdings import HoldingsFetchError, supported_etfs

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ETF screening dashboard foundation.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    screen_parser = subparsers.add_parser("screen", help="Fetch ETF holdings and run screening pipeline.")
    screen_parser.add_argument(
        "etf",
        help=f"ETF ticker. Supported: {', '.join(supported_etfs())}.",
    )
    screen_parser.add_argument("--limit", type=int, default=None, help="Limit the number of holdings in the report.")
    screen_parser.add_argument("--refresh", action="store_true", help="Refresh cached holdings from the source.")
    screen_parser.add_argument("--prices", action="store_true", help="Include delayed historical price snapshots.")
    screen_parser.add_argument("--dashboard", action="store_true", help="Write a local HTML dashboard.")
    screen_parser.add_argument("--market", action="store_true", help="Include market condition backdrop.")

    market_parser = subparsers.add_parser("market", help="Generate a market condition report.")
    market_parser.add_argument("--ticker", default="SPY", help="Market proxy ticker, default SPY.")
    market_parser.add_argument("--refresh", action="store_true", help="Refresh cached market data.")

    serve_parser = subparsers.add_parser("serve", help="Start the local interactive dashboard.")
    serve_parser.add_argument("--host", default=DEFAULT_HOST, help="Host for the local server.")
    serve_parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port for the local server.")

    args = parser.parse_args(argv)

    if args.command == "screen":
        try:
            report = screen_etf(
                args.etf,
                limit=args.limit,
                refresh=args.refresh,
                include_prices=args.prices,
                include_market=args.market,
            )
        except HoldingsFetchError as exc:
            print(f"Holdings error: {exc}", file=sys.stderr)
            return 1

        write_report(report)
        print(f"{report.etf}: prepared {len(report.rated_holdings)} of {report.holdings_count} holdings")
        if report.market_condition:
            print(
                f"Market: {report.market_condition.overall_score} "
                f"{report.market_condition.overall_regime}"
            )
        print(f"Report: saved ({report.etf}_screen)")
        if args.dashboard:
            dashboard_path = write_dashboard(report)
            print(f"Dashboard: {dashboard_path}")
        return 0

    if args.command == "market":
        report = build_market_condition(args.ticker, refresh=args.refresh)
        write_market_condition(report)
        print(f"{report.ticker}: Market {report.overall_score} {report.overall_regime}")
        print(f"DeMark: {report.demark.score} {report.demark.regime}")
        print(f"Structure: {report.structural_weakness.score} {report.structural_weakness.regime}")
        print(f"Technology: {report.technology_support.score} {report.technology_support.regime}")
        print(f"Defensives: {report.defensive_rotation.score} {report.defensive_rotation.regime}")
        print(f"Credit: {report.credit_risk.score} {report.credit_risk.regime}")
        print(f"Dollar/Yield: {report.dollar_yield_shock.score} {report.dollar_yield_shock.regime}")
        print(f"Market condition report: saved ({report.ticker}_market_condition)")
        return 0

    if args.command == "serve":
        from self_screener.web_app import ServerStartupError, run_server

        try:
            run_server(args.host, args.port)
        except ServerStartupError as exc:
            print(exc, file=sys.stderr)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

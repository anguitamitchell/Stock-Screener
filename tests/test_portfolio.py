import unittest

from self_screener.portfolio import OversellError, apply_quote, build_positions, portfolio_totals


def trade(ticker, side, date, shares, price, fees=0.0, id=None):
    return {
        "id": id or f"{ticker}-{side}-{date}",
        "ticker": ticker,
        "side": side,
        "trade_date": date,
        "shares": shares,
        "price": price,
        "fees": fees,
    }


class BuildPositionsTest(unittest.TestCase):
    def test_single_buy(self):
        [position] = build_positions([trade("AMD", "buy", "2026-06-01", 10, 100.0)])
        self.assertEqual(position["shares"], 10)
        self.assertEqual(position["avg_cost"], 100.0)
        self.assertEqual(position["cost_basis"], 1000.0)
        self.assertEqual(position["realized_pl"], 0.0)
        self.assertEqual(len(position["lots"]), 1)

    def test_buy_fees_fold_into_cost(self):
        [position] = build_positions([trade("AMD", "buy", "2026-06-01", 10, 100.0, fees=5.0)])
        self.assertEqual(position["avg_cost"], 100.5)
        self.assertEqual(position["cost_basis"], 1005.0)

    def test_partial_sell_consumes_oldest_lot_first(self):
        [position] = build_positions(
            [
                trade("AMD", "buy", "2026-06-01", 10, 100.0),
                trade("AMD", "buy", "2026-06-10", 10, 120.0),
                trade("AMD", "sell", "2026-06-20", 5, 130.0),
            ]
        )
        self.assertEqual(position["shares"], 15)
        self.assertEqual(position["realized_pl"], 150.0)  # 5 * (130 - 100)
        self.assertEqual(position["lots"][0]["shares_open"], 5)  # first lot half-consumed
        self.assertEqual(position["lots"][1]["shares_open"], 10)

    def test_multi_lot_sell_spans_lots(self):
        [position] = build_positions(
            [
                trade("AMD", "buy", "2026-06-01", 10, 100.0),
                trade("AMD", "buy", "2026-06-10", 10, 120.0),
                trade("AMD", "sell", "2026-06-20", 15, 130.0),
            ]
        )
        self.assertEqual(position["shares"], 5)
        # 10*(130-100) + 5*(130-120) = 350
        self.assertEqual(position["realized_pl"], 350.0)
        self.assertEqual(len(position["lots"]), 1)
        self.assertEqual(position["lots"][0]["shares_open"], 5)

    def test_sell_fees_reduce_realized(self):
        [position] = build_positions(
            [
                trade("AMD", "buy", "2026-06-01", 10, 100.0),
                trade("AMD", "sell", "2026-06-20", 10, 110.0, fees=10.0),
            ]
        )
        # 10 * ((110 - 1) - 100) = 90
        self.assertEqual(position["realized_pl"], 90.0)
        self.assertEqual(position["shares"], 0)
        self.assertIsNone(position["avg_cost"])

    def test_oversell_raises(self):
        with self.assertRaises(OversellError):
            build_positions(
                [
                    trade("AMD", "buy", "2026-06-01", 10, 100.0),
                    trade("AMD", "sell", "2026-06-20", 11, 130.0),
                ]
            )

    def test_sell_with_no_position_raises(self):
        with self.assertRaises(OversellError):
            build_positions([trade("AMD", "sell", "2026-06-20", 1, 130.0)])

    def test_tickers_are_independent_and_sorted(self):
        positions = build_positions(
            [
                trade("NVDA", "buy", "2026-06-01", 2, 400.0),
                trade("AMD", "buy", "2026-06-02", 3, 100.0),
            ]
        )
        self.assertEqual([p["ticker"] for p in positions], ["AMD", "NVDA"])


class ValuationTest(unittest.TestCase):
    def test_apply_quote(self):
        [position] = build_positions([trade("AMD", "buy", "2026-06-01", 10, 100.0)])
        apply_quote(position, 110.0)
        self.assertEqual(position["market_value"], 1100.0)
        self.assertEqual(position["unrealized_pl"], 100.0)
        self.assertEqual(position["unrealized_pl_percent"], 10.0)
        self.assertEqual(position["lots"][0]["unrealized_pl"], 100.0)

    def test_apply_quote_missing(self):
        [position] = build_positions([trade("AMD", "buy", "2026-06-01", 10, 100.0)])
        apply_quote(position, None)
        self.assertIsNone(position["market_value"])
        self.assertIsNone(position["unrealized_pl"])

    def test_totals_mix_valued_and_unvalued(self):
        positions = build_positions(
            [
                trade("AMD", "buy", "2026-06-01", 10, 100.0),
                trade("NVDA", "buy", "2026-06-01", 2, 400.0),
                trade("XLE", "buy", "2026-05-01", 5, 80.0),
                trade("XLE", "sell", "2026-06-01", 5, 90.0),
            ]
        )
        by_ticker = {p["ticker"]: p for p in positions}
        apply_quote(by_ticker["AMD"], 110.0)
        apply_quote(by_ticker["NVDA"], None)
        totals = portfolio_totals(positions)
        self.assertEqual(totals["market_value"], 1100.0)
        self.assertEqual(totals["cost_basis"], 1800.0)  # AMD 1000 + NVDA 800
        self.assertEqual(totals["unrealized_pl"], 100.0)  # only the valued position
        self.assertEqual(totals["realized_pl"], 50.0)  # closed XLE


if __name__ == "__main__":
    unittest.main()

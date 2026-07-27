import unittest
from datetime import datetime, timezone

from self_screener.screeners.lot_review import review_lot, vs_benchmark_percent

PLAN = {
    "entry_low": 90.0,
    "entry_high": 100.0,
    "breakout_trigger": 110.0,
    "stop_loss": 80.0,
    "target_1": 120.0,
    "target_2": 140.0,
    "reward_risk": 2.0,
}


def row(rating="Buy", score=75.0, plan=PLAN):
    return {"rating": rating, "final_score": score, "trade_plan": plan, "holding": {"ticker": "AMD"}}


def buy(price, date="2026-06-01"):
    return {"price": price, "trade_date": date}


class EntryGradeTest(unittest.TestCase):
    def grade(self, price, entry_row=None):
        return review_lot(buy(price), entry_row=entry_row or row(), latest_row=row())

    def test_in_zone(self):
        verdict = self.grade(95.0)
        self.assertEqual(verdict["entry_grade"], "in_zone")
        self.assertEqual(verdict["plan_source"], "snapshot")
        self.assertIn("disciplined", verdict["entry_detail"].lower())

    def test_in_zone_against_sell_rating(self):
        verdict = review_lot(buy(95.0), entry_row=row(rating="Sell"), latest_row=row())
        self.assertEqual(verdict["entry_grade"], "in_zone")
        self.assertEqual(verdict["entry_label"], "In zone, against the rating")

    def test_breakout(self):
        self.assertEqual(self.grade(112.0)["entry_grade"], "breakout_entry")

    def test_chased(self):
        self.assertEqual(self.grade(105.0)["entry_grade"], "chased")

    def test_below_zone(self):
        self.assertEqual(self.grade(85.0)["entry_grade"], "below_zone")

    def test_below_stop(self):
        self.assertEqual(self.grade(78.0)["entry_grade"], "below_stop")

    def test_no_screen_data(self):
        verdict = review_lot(buy(95.0))
        self.assertEqual(verdict["entry_grade"], "no_screen_data")
        self.assertEqual(verdict["plan_source"], "none")

    def test_latest_plan_fallback(self):
        verdict = review_lot(buy(95.0), entry_row=None, latest_row=row())
        self.assertEqual(verdict["entry_grade"], "in_zone")
        self.assertEqual(verdict["plan_source"], "latest")


class StanceTest(unittest.TestCase):
    def stance(self, quote, entry_row=None, latest_row=None):
        return review_lot(
            buy(95.0),
            entry_row=entry_row or row(),
            latest_row=latest_row or row(),
            quote_price=quote,
        )

    def test_no_quote(self):
        self.assertEqual(self.stance(None)["stance"], "unknown")

    def test_stop_breached(self):
        self.assertEqual(self.stance(79.0)["stance"], "stop_breached")

    def test_take_profit(self):
        self.assertEqual(self.stance(141.0)["stance"], "take_profit")

    def test_trim(self):
        self.assertEqual(self.stance(125.0)["stance"], "trim_or_raise_stop")

    def test_sell_signal(self):
        verdict = self.stance(100.0, latest_row=row(rating="Sell"))
        self.assertEqual(verdict["stance"], "sell_signal")

    def test_downgrade(self):
        verdict = self.stance(100.0, entry_row=row(rating="Buy"), latest_row=row(rating="Hold"))
        self.assertEqual(verdict["stance"], "watch_downgrade")

    def test_hold(self):
        verdict = self.stance(100.0)
        self.assertEqual(verdict["stance"], "hold")
        self.assertTrue(verdict["stance_reasons"])

    def test_metrics(self):
        verdict = self.stance(110.0)
        self.assertEqual(verdict["pl_percent"], 15.79)  # (110-95)/95
        self.assertEqual(verdict["r_multiple"], 1.0)  # (110-95)/(95-80)
        self.assertEqual(verdict["score_change"], 0.0)


class VsBenchmarkTest(unittest.TestCase):
    @staticmethod
    def bars(start_day, closes):
        base = datetime(2026, 6, start_day, tzinfo=timezone.utc).timestamp()
        return [{"timestamp": base + i * 86400, "close": close} for i, close in enumerate(closes)]

    def test_outperformance(self):
        ticker = self.bars(1, [100, 110])  # +10%
        spy = self.bars(1, [100, 105])  # +5%
        self.assertEqual(vs_benchmark_percent("2026-06-01", ticker, spy), 5.0)

    def test_trade_predates_history_window(self):
        ticker = self.bars(20, [100, 110])
        spy = self.bars(20, [100, 105])
        self.assertIsNone(vs_benchmark_percent("2026-01-01", ticker, spy))

    def test_missing_bars(self):
        self.assertIsNone(vs_benchmark_percent("2026-06-01", None, self.bars(1, [100, 105])))

    def test_bad_date(self):
        self.assertIsNone(vs_benchmark_percent("junk", self.bars(1, [1, 2]), self.bars(1, [1, 2])))


if __name__ == "__main__":
    unittest.main()

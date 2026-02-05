import unittest
from datetime import datetime

from tradingagents.utils.market_session import (
    describe_us_market_session,
    format_market_session_context,
    get_us_eastern_tzinfo,
)
from tradingagents.graph.propagation import Propagator


class TestMarketSessionContext(unittest.TestCase):
    def _dt_et(self, y, m, d, hh, mm):
        tz = get_us_eastern_tzinfo()
        if tz is None:
            self.skipTest("US/Eastern tzinfo unavailable (install tzdata or pytz)")
        return datetime(y, m, d, hh, mm, tzinfo=tz)

    def test_session_labels(self):
        # Mon 2026-02-02
        self.assertEqual(describe_us_market_session(self._dt_et(2026, 2, 2, 5, 0))["session_label"], "PRE-MARKET")
        self.assertEqual(describe_us_market_session(self._dt_et(2026, 2, 2, 10, 0))["session_label"], "REGULAR-HOURS")
        self.assertEqual(describe_us_market_session(self._dt_et(2026, 2, 2, 17, 0))["session_label"], "AFTER-MARKET")
        self.assertEqual(describe_us_market_session(self._dt_et(2026, 2, 2, 22, 0))["session_label"], "OVERNIGHT")

        # Sat 2026-02-07
        self.assertEqual(describe_us_market_session(self._dt_et(2026, 2, 7, 10, 0))["session_label"], "WEEKEND")

    def test_context_mentions_extended_hours_when_closed(self):
        desc = describe_us_market_session(self._dt_et(2026, 2, 2, 17, 0))
        ctx = format_market_session_context(desc)
        self.assertIn("Regular market is CLOSED", ctx)
        self.assertIn("EXTENDED-HOURS constraint", ctx)

    def test_propagator_injects_context(self):
        state = Propagator().create_initial_state("AAPL", "2026-02-05")
        self.assertIn("market_session_context", state)
        self.assertTrue(str(state["market_session_context"]).strip())
        self.assertIn("CURRENT MARKET SESSION CONTEXT", state["market_session_context"])


if __name__ == "__main__":
    unittest.main()

import unittest

from tradingagents.agents.utils.time_horizon import get_time_horizon_spec
from tradingagents.graph.propagation import Propagator


class TestTimeHorizon(unittest.TestCase):
    def test_default_spec_when_missing(self):
        self.assertEqual(get_time_horizon_spec(None).key, "1-2 months")

    def test_unicode_dash_normalization(self):
        self.assertEqual(get_time_horizon_spec("1–2 weeks").key, "1-2 weeks")

    def test_propagator_injects_time_horizon_default(self):
        state = Propagator().create_initial_state("AAPL", "2026-02-05")
        self.assertEqual(state.get("time_horizon"), "1-2 months")

    def test_propagator_respects_selection(self):
        state = Propagator().create_initial_state("AAPL", "2026-02-05", time_horizon="2-3 months")
        self.assertEqual(state.get("time_horizon"), "2-3 months")


if __name__ == "__main__":
    unittest.main()


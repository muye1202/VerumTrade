import unittest

from tradingagents.execution.alpaca_executor import normalize_order_inputs


class TestExtendedHoursNormalization(unittest.TestCase):
    def test_extended_hours_limit_day_ok(self):
        spec, err = normalize_order_inputs(
            default_order_type="market",
            default_time_in_force="DAY",
            side="BUY",
            current_price=100.0,
            limit_price_offset_pct=0.01,
            agent_order_type="LIMIT",
            agent_time_in_force="DAY",
            agent_extended_hours=True,
            agent_limit_price=99.5,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(spec)
        self.assertTrue(spec.extended_hours)

    def test_extended_hours_rejects_market(self):
        spec, err = normalize_order_inputs(
            default_order_type="market",
            default_time_in_force="DAY",
            side="BUY",
            current_price=100.0,
            limit_price_offset_pct=0.01,
            agent_order_type="MARKET",
            agent_extended_hours=True,
        )
        self.assertIsNone(spec)
        self.assertIsNotNone(err)
        self.assertIn("ORDER_TYPE=LIMIT", err)

    def test_extended_hours_rejects_gtc(self):
        spec, err = normalize_order_inputs(
            default_order_type="limit",
            default_time_in_force="DAY",
            side="BUY",
            current_price=100.0,
            limit_price_offset_pct=0.01,
            agent_order_type="LIMIT",
            agent_time_in_force="GTC",
            agent_extended_hours=True,
            agent_limit_price=99.5,
        )
        self.assertIsNone(spec)
        self.assertIsNotNone(err)
        self.assertIn("TIME_IN_FORCE=DAY", err)


if __name__ == "__main__":
    unittest.main()


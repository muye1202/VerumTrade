import unittest

from tradingagents.execution.alpaca_executor import normalize_order_inputs


class TestAlpacaExecutorOrderTypeParsing(unittest.TestCase):
    def test_alpaca_executor_normalize_order_type_from_llm(self):
        cases = [
            ("market", "MARKET"),
            ("MARKET", "MARKET"),
            ("MKT", "MARKET"),
            ("market order", "MARKET"),
            ("market-order", "MARKET"),
            ("Limit", "LIMIT"),
            ("LMT", "LIMIT"),
            ("limit order", "LIMIT"),
            ("limit-order", "LIMIT"),
            ("STOP", "STOP"),
            ("stop order", "STOP"),
            ("stop-order", "STOP"),
            ("stop limit", "STOP_LIMIT"),
            ("stop-limit", "STOP_LIMIT"),
            ("stop limit order", "STOP_LIMIT"),
            ("stop-limit order", "STOP_LIMIT"),
            ("TRAILING_STOP", "TRAILING_STOP"),
            ("trailing stop", "TRAILING_STOP"),
            ("trailing stop order", "TRAILING_STOP"),
            ("trailing-stop order", "TRAILING_STOP"),
        ]

        for agent_order_type, expected in cases:
            with self.subTest(agent_order_type=agent_order_type, expected=expected):
                kwargs = {
                    "default_order_type": "market",
                    "default_time_in_force": "DAY",
                    "side": "BUY",
                    "current_price": 100.0,
                    "limit_price_offset_pct": 0.01,
                    "agent_order_type": agent_order_type,
                }

                if expected == "LIMIT":
                    kwargs["agent_limit_price"] = 99.5
                elif expected == "STOP":
                    kwargs["agent_stop_price"] = 95.0
                elif expected == "STOP_LIMIT":
                    kwargs["agent_stop_price"] = 95.0
                    kwargs["agent_limit_price"] = 94.5
                elif expected == "TRAILING_STOP":
                    kwargs["agent_trail_percent"] = 3

                spec, err = normalize_order_inputs(**kwargs)
                self.assertIsNone(err)
                self.assertIsNotNone(spec)
                self.assertEqual(spec.order_type, expected)


if __name__ == "__main__":
    unittest.main()

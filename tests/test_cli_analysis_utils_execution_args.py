import unittest

from tradingagents.execution.execution_kwargs import executor_kwargs_from_structured


class TestCliExecutionArgForwarding(unittest.TestCase):
    def test_limit_order_type_is_forwarded(self):
        structured = {
            "order_type": "LIMIT",
            "time_in_force": "DAY",
            "stop_price": 256.40,
            "trail_percent": None,
            "trail_price": None,
        }

        kwargs = executor_kwargs_from_structured(structured)
        self.assertEqual(kwargs.get("agent_order_type"), "LIMIT")
        self.assertEqual(kwargs.get("agent_time_in_force"), "DAY")
        self.assertEqual(kwargs.get("agent_stop_price"), 256.40)
        self.assertIsNone(kwargs.get("agent_trail_percent"))
        self.assertIsNone(kwargs.get("agent_trail_price"))

    def test_missing_structured_fields_default_to_none(self):
        kwargs = executor_kwargs_from_structured({"order_type": "MARKET"})
        self.assertEqual(kwargs.get("agent_order_type"), "MARKET")
        self.assertIsNone(kwargs.get("agent_time_in_force"))
        self.assertIsNone(kwargs.get("agent_stop_price"))
        self.assertIsNone(kwargs.get("agent_trail_percent"))
        self.assertIsNone(kwargs.get("agent_trail_price"))


if __name__ == "__main__":
    unittest.main()


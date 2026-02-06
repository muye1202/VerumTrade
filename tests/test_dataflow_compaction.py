import unittest

from tradingagents.dataflows.config import get_config, set_config


class TestDataflowCompaction(unittest.TestCase):
    def setUp(self):
        self._orig = get_config()
        set_config({"tool_response_max_chars": 200})

    def tearDown(self):
        set_config(self._orig)

    def test_compacts_large_text_payload(self):
        try:
            from tradingagents.dataflows.interface import _compact_tool_output
        except Exception as e:
            self.skipTest(f"interface dependencies unavailable: {e}")
        payload = "x" * 5000
        compacted = _compact_tool_output("get_news", payload)
        self.assertIsInstance(compacted, str)
        self.assertLessEqual(len(compacted), 200)
        self.assertIn("[TRUNCATED", compacted)

    def test_keeps_small_payload_unchanged(self):
        try:
            from tradingagents.dataflows.interface import _compact_tool_output
        except Exception as e:
            self.skipTest(f"interface dependencies unavailable: {e}")
        payload = "hello"
        compacted = _compact_tool_output("get_news", payload)
        self.assertEqual(compacted, payload)


if __name__ == "__main__":
    unittest.main()

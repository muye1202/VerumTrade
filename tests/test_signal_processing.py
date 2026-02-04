import unittest
from pathlib import Path

from tradingagents.graph.signal_processing import SignalProcessor


class TestSignalProcessorStructuredDecision(unittest.TestCase):
    def test_quantity_prefers_last_int(self):
        processor = SignalProcessor(quick_thinking_llm=None)  # Structured block parsing does not use the LLM

        text = """
---
FINAL TRANSACTION PROPOSAL:
- ACTION: BUY
- TICKER: AAPL
- QUANTITY: 10% of portfolio (~37 shares)
- ORDER_TYPE: MARKET
- LIMIT_PRICE: N/A
- STOP_LOSS: N/A
- TAKE_PROFIT: N/A
- TIME_HORIZON: 1-2 weeks
- CONFIDENCE: HIGH
- RATIONALE: Example rationale.
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["action"], "BUY")
        self.assertEqual(structured["ticker"], "AAPL")
        self.assertEqual(structured["quantity"], 37)

    def test_quantity_simple_int(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRADING DECISION:
- ACTION: SELL
- TICKER: TSLA
- QUANTITY: 15
- ORDER_TYPE: MARKET
- LIMIT_PRICE: N/A
- STOP_LOSS: N/A
- TAKE_PROFIT: N/A
- POSITION_SIZE_PCT: N/A
- TIME_HORIZON: 1-3 days
- CONFIDENCE: MEDIUM
- RATIONALE: Example rationale.
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["action"], "SELL")
        self.assertEqual(structured["ticker"], "TSLA")
        self.assertEqual(structured["quantity"], 15)

    def test_stop_order_parses_stop_price_and_tif(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRANSACTION PROPOSAL:
- ACTION: SELL
- TICKER: AAPL
- QUANTITY: 10
- ORDER_TYPE: STOP
- TIME_IN_FORCE: GTC
- STOP_PRICE: $187.50
- LIMIT_PRICE: N/A
- TRAIL_PERCENT: N/A
- TRAIL_PRICE: N/A
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["order_type"], "STOP")
        self.assertEqual(structured["time_in_force"], "GTC")
        self.assertEqual(structured["stop_price"], 187.50)

    def test_stop_limit_parses_both_prices(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRADING DECISION:
- ACTION: BUY
- TICKER: TSLA
- QUANTITY: 5
- ORDER_TYPE: STOP_LIMIT
- TIME_IN_FORCE: DAY
- STOP_PRICE: 200
- LIMIT_PRICE: 198.25
- TRAIL_PERCENT: N/A
- TRAIL_PRICE: N/A
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["order_type"], "STOP_LIMIT")
        self.assertEqual(structured["stop_price"], 200.0)
        self.assertEqual(structured["limit_price"], 198.25)

    def test_trailing_stop_percent(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRADING DECISION:
- ACTION: SELL
- TICKER: TSLA
- QUANTITY: 15
- ORDER_TYPE: TRAILING_STOP
- TIME_IN_FORCE: DAY
- TRAIL_PERCENT: 3
- TRAIL_PRICE: N/A
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["order_type"], "TRAILING_STOP")
        self.assertEqual(structured["trail_percent"], 3.0)
        self.assertIsNone(structured["trail_price"])

    def test_trailing_stop_price(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRADING DECISION:
- ACTION: SELL
- TICKER: TSLA
- QUANTITY: 15
- ORDER_TYPE: TRAILING_STOP
- TIME_IN_FORCE: DAY
- TRAIL_PERCENT: N/A
- TRAIL_PRICE: 1.25
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["order_type"], "TRAILING_STOP")
        self.assertEqual(structured["trail_price"], 1.25)
        self.assertIsNone(structured["trail_percent"])

    def test_trailing_stop_invalid_both_set(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = """
---
FINAL TRADING DECISION:
- ACTION: SELL
- TICKER: TSLA
- QUANTITY: 15
- ORDER_TYPE: TRAILING_STOP
- TIME_IN_FORCE: DAY
- TRAIL_PERCENT: 3
- TRAIL_PRICE: 1.25
---
"""
        structured = processor.extract_structured_decision(text)
        self.assertEqual(structured["order_type"], "TRAILING_STOP")
        self.assertIsNone(structured["trail_percent"])
        self.assertIsNone(structured["trail_price"])

    def test_parses_final_trade_decision_md_file_order_type_limit(self):
        processor = SignalProcessor(quick_thinking_llm=None)

        text = Path("results/AMC/2026-02-04/reports/final_trade_decision.md").read_text(encoding="utf-8")
        structured = processor.extract_structured_decision(text)

        self.assertEqual(structured["ticker"], "AMC")
        self.assertEqual(structured["order_type"], "LIMIT")
        self.assertEqual(structured["time_in_force"], "DAY")
        self.assertEqual(structured["limit_price"], 1.35)
        self.assertEqual(structured["stop_price"], 1.25)


if __name__ == "__main__":
    unittest.main()

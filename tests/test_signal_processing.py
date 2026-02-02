import unittest

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


if __name__ == "__main__":
    unittest.main()

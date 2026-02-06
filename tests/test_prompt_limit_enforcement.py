import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestPromptLimitEnforcement(unittest.TestCase):
    def test_trader_prompt_has_critical_hold_tp_sl_rules(self):
        content = (ROOT / "tradingagents/agents/trader/trader.py").read_text(encoding="utf-8")
        self.assertIn("**CRITICAL**: For EVERY action (BUY/SELL/HOLD)", content)
        self.assertIn("must still be numeric", content)
        self.assertIn("watch levels (invalidation/trigger levels for potential future activation)", content)
        self.assertIn(
            "- STOP_LOSS: [REQUIRED numeric price for BUY/SELL/HOLD]",
            content,
        )
        self.assertIn(
            "- TAKE_PROFIT: [REQUIRED numeric price for BUY/SELL/HOLD]",
            content,
        )

    def test_risk_manager_prompt_has_critical_hold_tp_sl_rules(self):
        content = (ROOT / "tradingagents/agents/managers/risk_manager.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("**CRITICAL**: For EVERY action (BUY/SELL/HOLD)", content)
        self.assertIn("watch levels (invalidation/trigger levels for potential future activation)", content)
        self.assertIn(
            "- STOP_LOSS: [REQUIRED numeric price for BUY/SELL/HOLD]",
            content,
        )
        self.assertIn(
            "- TAKE_PROFIT: [REQUIRED numeric price for BUY/SELL/HOLD]",
            content,
        )

    def test_all_analyst_prompts_have_critical_execution_note(self):
        analyst_files = [
            "tradingagents/agents/analysts/market_analyst.py",
            "tradingagents/agents/analysts/news_analyst.py",
            "tradingagents/agents/analysts/fundamentals_analyst.py",
            "tradingagents/agents/analysts/social_media_analyst.py",
        ]
        required_snippets = [
            "**CRITICAL** Execution note:",
            "MUST provide concrete numeric levels for: (1) entry/trigger, (2) stop-loss, (3) take-profit, and (4) holding horizon or time-stop",
            "If confidence is low, still provide bounded watch levels and explicit invalidation logic",
        ]

        for rel_path in analyst_files:
            with self.subTest(path=rel_path):
                content = (ROOT / rel_path).read_text(encoding="utf-8")
                for snippet in required_snippets:
                    self.assertIn(snippet, content)


if __name__ == "__main__":
    unittest.main()

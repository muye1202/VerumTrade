import unittest

from tradingagents.dataflows.config import get_config, set_config
from tradingagents.agents.utils.context_budget import estimate_tokens


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self):
        self.last_prompt = None
        self.last_tokens = 0

    def invoke(self, prompt):
        self.last_prompt = prompt
        self.last_tokens = estimate_tokens(prompt)
        return _Resp("ok")


class _Memory:
    def get_memories(self, _curr, n_matches=2):
        return [
            {"recommendation": "memory-a " * 800},
            {"recommendation": "memory-b " * 800},
        ][:n_matches]


class TestPromptSizesNodes(unittest.TestCase):
    def setUp(self):
        self._orig = get_config()
        set_config(
            {
                "prompt_soft_cap_tokens": 6000,
                "char_per_token_estimate": 4.0,
                "section_max_chars_report": 1200,
                "section_max_chars_history": 2000,
                "section_max_chars_response": 900,
                "section_max_chars_memory": 800,
                "section_max_chars_portfolio": 1000,
                "section_max_chars_trader_plan": 900,
            }
        )
        self.big = ("X" * 40000) + "\n" + ("Y" * 40000)
        self.mem = _Memory()
        self.llm = _FakeLLM()

    def tearDown(self):
        set_config(self._orig)

    def _base_state(self):
        return {
            "company_of_interest": "MSFT",
            "trade_date": "2026-02-06",
            "time_horizon": "1-2 months",
            "market_report": self.big,
            "sentiment_report": self.big,
            "news_report": self.big,
            "fundamentals_report": self.big,
            "investment_plan": self.big,
            "trader_investment_plan": self.big,
            "portfolio_context": self.big,
            "market_session_context": self.big,
            "investment_debate_state": {
                "history": self.big,
                "bull_history": self.big,
                "bear_history": self.big,
                "current_response": self.big,
                "count": 0,
            },
            "risk_debate_state": {
                "history": self.big,
                "risky_history": self.big,
                "safe_history": self.big,
                "neutral_history": self.big,
                "latest_speaker": "Risky",
                "current_risky_response": self.big,
                "current_safe_response": self.big,
                "current_neutral_response": self.big,
                "judge_decision": "",
                "count": 0,
            },
        }

    def test_high_risk_nodes_stay_under_soft_cap(self):
        try:
            from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
            from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
            from tradingagents.agents.managers.research_manager import create_research_manager
            from tradingagents.agents.trader.trader import create_trader
            from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
            from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
            from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
            from tradingagents.agents.managers.risk_manager import create_risk_manager
        except Exception as e:
            self.skipTest(f"agent dependencies unavailable: {e}")

        cap = 6000
        state = self._base_state()

        nodes = [
            create_bull_researcher(self.llm, self.mem),
            create_bear_researcher(self.llm, self.mem),
            create_research_manager(self.llm, self.mem),
            create_trader(self.llm, self.mem),
            create_risky_debator(self.llm),
            create_safe_debator(self.llm),
            create_neutral_debator(self.llm),
            create_risk_manager(self.llm, self.mem),
        ]

        for node in nodes:
            with self.subTest(node=str(node)):
                _ = node(state)
                self.assertLessEqual(self.llm.last_tokens, cap)


if __name__ == "__main__":
    unittest.main()

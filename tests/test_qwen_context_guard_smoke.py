import unittest

from tradingagents.dataflows.config import get_config, set_config
from tradingagents.agents.trader.trader import create_trader
from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
from tradingagents.agents.managers.risk_manager import create_risk_manager


class _Resp:
    def __init__(self, content):
        self.content = content


class _LLM:
    def invoke(self, _prompt):
        return _Resp(
            "FINAL TRADING DECISION:\n"
            "- ACTION: HOLD\n"
            "- TICKER: MSFT\n"
            "- QUANTITY: N/A\n"
            "- ORDER_TYPE: LIMIT\n"
            "- TIME_IN_FORCE: DAY\n"
            "- LIMIT_PRICE: 100\n"
            "- STOP_PRICE: N/A\n"
            "- TRAIL_PERCENT: N/A\n"
            "- TRAIL_PRICE: N/A\n"
            "- STOP_LOSS: 90\n"
            "- TAKE_PROFIT: 110\n"
            "- POSITION_SIZE_PCT: N/A\n"
            "- TIME_HORIZON: 1-2 weeks\n"
            "- CONFIDENCE: LOW\n"
            "- RATIONALE: smoke"
        )


class _Memory:
    def get_memories(self, _curr, n_matches=2):
        return [{"recommendation": "past lesson"} for _ in range(n_matches)]


class TestQwenContextGuardSmoke(unittest.TestCase):
    def setUp(self):
        self._orig = get_config()
        set_config(
            {
                "prompt_soft_cap_tokens": 6000,
                "section_max_chars_report": 1200,
                "section_max_chars_history": 2000,
                "section_max_chars_response": 900,
                "section_max_chars_memory": 800,
                "section_max_chars_portfolio": 1000,
                "section_max_chars_trader_plan": 900,
            }
        )

    def tearDown(self):
        set_config(self._orig)

    def test_pipeline_path_handles_large_inputs(self):
        huge = "Z" * 60000
        state = {
            "company_of_interest": "MSFT",
            "trade_date": "2026-02-06",
            "time_horizon": "1-2 months",
            "market_report": huge,
            "sentiment_report": huge,
            "news_report": huge,
            "fundamentals_report": huge,
            "investment_plan": huge,
            "trader_investment_plan": huge,
            "portfolio_context": huge,
            "market_session_context": huge,
            "investment_debate_state": {"history": huge, "current_response": huge, "count": 0},
            "risk_debate_state": {
                "history": huge,
                "risky_history": "",
                "safe_history": "",
                "neutral_history": "",
                "latest_speaker": "Risky",
                "current_risky_response": huge,
                "current_safe_response": huge,
                "current_neutral_response": huge,
                "judge_decision": "",
                "count": 0,
            },
        }
        llm = _LLM()
        mem = _Memory()

        trader = create_trader(llm, mem)
        risky = create_risky_debator(llm)
        safe = create_safe_debator(llm)
        neutral = create_neutral_debator(llm)
        judge = create_risk_manager(llm, mem)

        state.update(trader(state))
        state.update(risky(state))
        state.update(safe(state))
        state.update(neutral(state))
        out = judge(state)

        self.assertIn("final_trade_decision", out)
        self.assertIn("ACTION:", out["final_trade_decision"])


if __name__ == "__main__":
    unittest.main()


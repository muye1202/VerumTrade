import unittest

from tradingagents.agents.utils.context_budget import (
    cap_section,
    cap_sections_with_soft_token_cap,
    clip_middle,
    estimate_tokens,
    normalize_text,
)


class TestContextBudget(unittest.TestCase):
    def test_normalize_and_estimate_tokens(self):
        self.assertEqual(normalize_text("a\r\nb\r\n"), "a\nb")
        self.assertGreaterEqual(estimate_tokens("x" * 100), 1)

    def test_clip_middle_marks_truncation(self):
        s = "A" * 1000
        clipped = clip_middle(s, 120)
        self.assertLessEqual(len(clipped), 120)
        self.assertIn("[TRUNCATED", clipped)

    def test_cap_section_respects_length(self):
        s = "B" * 500
        capped = cap_section("demo", s, 80)
        self.assertLessEqual(len(capped), 80)

    def test_soft_cap_shrinks_low_priority_sections_first(self):
        sections = {
            "current_response": "C" * 4000,
            "trader_plan": "T" * 4000,
            "history_tail": "H" * 4000,
            "portfolio_context": "P" * 4000,
            "reports": "R" * 10000,
            "memories": "M" * 10000,
        }
        capped = cap_sections_with_soft_token_cap(
            sections, soft_cap_tokens=1200
        )
        self.assertLessEqual(estimate_tokens(capped), 1200)
        self.assertLessEqual(len(capped["memories"]), len(sections["memories"]))
        self.assertLessEqual(len(capped["reports"]), len(sections["reports"]))


if __name__ == "__main__":
    unittest.main()


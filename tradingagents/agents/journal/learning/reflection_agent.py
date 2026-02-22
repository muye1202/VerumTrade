"""
Reflection Agent — LLM-powered analysis of completed trades.

Takes a TradeThesis + TradeOutcome pair and produces structured lessons with
human-readable wisdom. Lessons are stored in ChromaDB for semantic retrieval
during future trading decisions.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from tradingagents.agents.journal.core.models import (
    TradeThesis,
    TradeOutcome,
    TradeLesson,
)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


REFLECTION_SYSTEM_PROMPT = """You are a trading coach analyzing a completed trade to extract lessons for future decisions.

Your goal is to produce STRUCTURED insights that can be stored and retrieved later. Be concise but specific.

Output a JSON object with EXACTLY this structure:
{
  "what_worked": ["list of specific things the agents/analysis got right"],
  "what_failed": ["list of specific things the agents/analysis got wrong"],
  "agent_accuracy": {
    "market": "1-sentence assessment of market analyst accuracy",
    "fundamentals": "1-sentence assessment of fundamentals analyst accuracy",
    "news": "1-sentence assessment of news analyst accuracy",
    "risk_judge": "1-sentence assessment of risk manager accuracy"
  },
  "most_accurate_agent": "market|fundamentals|news|risk_judge",
  "least_accurate_agent": "market|fundamentals|news|risk_judge",
  "regime_correct": true or false,
  "catalyst_materialized": true or false,
  "lesson": "One or two sentences that capture the key takeaway, suitable for memory retrieval",
  "category": "underscore_separated_category like momentum_in_uptrend or earnings_catalyst or mean_reversion or breakout_failure",
  "tags": ["list", "of", "3-5", "searchable", "tags"],
  "confidence": 0-100
}

Guidelines:
- Be specific about what worked/failed (e.g., "correctly identified support at $150" not just "good analysis")
- The lesson should be actionable (e.g., "In high-volatility regimes, tighten stops to 1.5% from 2%")
- Categories should be reusable patterns (momentum_in_uptrend, earnings_catalyst, gap_fill, mean_reversion, trend_reversal, breakout_failure)
- Tags should enable semantic search (ticker sector, regime type, catalyst type, outcome type)
- Confidence reflects how reliable/generalizable this lesson is (higher for clear patterns, lower for edge cases)

Output ONLY valid JSON, no markdown formatting or explanation."""


class ReflectionAgent:
    """
    LLM-powered agent that reflects on completed trades.
    
    Takes a thesis + outcome and produces a structured TradeLesson.
    """

    def __init__(
        self,
        llm: Optional["BaseChatModel"] = None,
        llm_provider: str = "openai",
        model_name: str = "gpt-4o-mini",
    ):
        """
        Initialize the reflection agent.

        Args:
            llm: Pre-configured LangChain chat model. If None, creates one.
            llm_provider: Provider if creating LLM ("openai", "anthropic", "google")
            model_name: Model name if creating LLM
        """
        if llm is not None:
            self.llm = llm
        else:
            self.llm = self._create_llm(llm_provider, model_name)

    def _create_llm(self, provider: str, model_name: str) -> "BaseChatModel":
        """Create an LLM based on provider."""
        provider = provider.lower()

        if provider == "openai":
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model_name, temperature=0.3)
        elif provider == "anthropic":
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model_name, temperature=0.3)
        elif provider == "google":
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(model=model_name, temperature=0.3)
        else:
            # Default to OpenAI
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model_name, temperature=0.3)

    def reflect(
        self,
        thesis: TradeThesis,
        outcome: TradeOutcome,
    ) -> TradeLesson:
        """
        Analyze a completed trade and produce a structured lesson.

        Args:
            thesis: The original trade thesis at entry
            outcome: The computed outcome when position closed

        Returns:
            TradeLesson with structured insights
        """
        prompt = self._build_reflection_prompt(thesis, outcome)

        try:
            messages = [
                ("system", REFLECTION_SYSTEM_PROMPT),
                ("human", prompt),
            ]
            response = self.llm.invoke(messages)
            content = response.content if hasattr(response, "content") else str(response)

            lesson_data = self._parse_reflection_response(content)

            # Build the lesson object
            lesson = TradeLesson(
                thesis_id=thesis.id,
                outcome_id=outcome.id,
                ticker=thesis.ticker,
                trade_date=thesis.trade_date,
                action=thesis.action,
                realized_pl_pct=outcome.realized_pl_pct,
                exit_reason=outcome.exit_reason,
                risk_multiple=outcome.risk_multiple,
                lesson_text=lesson_data.get("lesson", ""),
                what_worked=lesson_data.get("what_worked", []),
                what_failed=lesson_data.get("what_failed", []),
                agent_accuracy=lesson_data.get("agent_accuracy", {}),
                most_accurate_agent=lesson_data.get("most_accurate_agent"),
                least_accurate_agent=lesson_data.get("least_accurate_agent"),
                regime_correct=lesson_data.get("regime_correct"),
                catalyst_materialized=lesson_data.get("catalyst_materialized"),
                category=lesson_data.get("category", "uncategorized"),
                tags=lesson_data.get("tags", []),
                confidence=float(lesson_data.get("confidence", 50)),
            )

            logger.info(f"Generated lesson for {thesis.ticker}: {lesson.category}")
            return lesson

        except Exception as e:
            logger.error(f"Reflection failed for {thesis.ticker}: {e}")
            # Return a minimal lesson on error
            return TradeLesson(
                thesis_id=thesis.id,
                outcome_id=outcome.id,
                ticker=thesis.ticker,
                trade_date=thesis.trade_date,
                action=thesis.action,
                realized_pl_pct=outcome.realized_pl_pct,
                exit_reason=outcome.exit_reason,
                lesson_text=f"Reflection failed: {e}",
                category="error",
                confidence=0,
            )

    def _build_reflection_prompt(
        self,
        thesis: TradeThesis,
        outcome: TradeOutcome,
    ) -> str:
        """Build the prompt for the LLM."""
        # Format outcome metrics
        pl_str = f"{outcome.realized_pl_pct:.1f}%" if outcome.realized_pl_pct else "N/A"
        alpha_str = f"{outcome.alpha_pct:.1f}%" if outcome.alpha_pct else "N/A"
        mae_str = f"{outcome.max_adverse_excursion_pct:.1f}%" if outcome.max_adverse_excursion_pct else "N/A"
        mfe_str = f"{outcome.max_favorable_excursion_pct:.1f}%" if outcome.max_favorable_excursion_pct else "N/A"
        r_mult_str = f"{outcome.risk_multiple:.2f}R" if outcome.risk_multiple else "N/A"

        prompt = f"""## Trade Thesis (Entry)
- Ticker: {thesis.ticker}
- Date: {thesis.trade_date}
- Action: {thesis.action}
- Conviction: {thesis.conviction or 'N/A'}
- Entry Price: ${thesis.entry_price or 'N/A'}
- Stop Loss: ${thesis.stop_loss or 'N/A'}
- Target 1: ${thesis.target_1 or 'N/A'}
- Target 2: ${thesis.target_2 or 'N/A'}
- Catalyst: {thesis.catalyst or 'N/A'}
- Regime: {thesis.regime or 'N/A'}
- Key Risks: {thesis.key_risks or 'N/A'}
- Invalidation Trigger: {thesis.invalidation_trigger or 'N/A'}

## Agent Summaries at Entry
- Market Analyst: {thesis.market_analyst_summary or 'No summary available'}
- Fundamentals: {thesis.fundamentals_summary or 'No summary available'}
- News: {thesis.news_summary or 'No summary available'}
- Risk Judge: {thesis.risk_judge_summary or 'No summary available'}

## Outcome
- P&L: {pl_str}
- Exit Reason: {outcome.exit_reason or 'N/A'}
- R-Multiple: {r_mult_str}
- Alpha vs SPY: {alpha_str}
- Max Adverse Excursion: {mae_str}
- Max Favorable Excursion: {mfe_str}
- Holding Days: {outcome.holding_days if outcome.holding_days is not None else 'N/A'}
- Thesis Correct: {outcome.thesis_correct}
- Target Reached: {outcome.target_reached}
- Stop Triggered: {outcome.stop_triggered}

Analyze this trade and extract lessons. Output JSON only."""

        return prompt

    def _parse_reflection_response(self, response: str) -> Dict[str, Any]:
        """Parse the JSON response from the LLM."""
        # Clean up response (remove markdown code blocks if present)
        content = response.strip()
        if content.startswith("```"):
            # Remove markdown code block
            lines = content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse reflection JSON: {e}")
            # Try to extract partial data
            return {
                "lesson": content[:500] if content else "Parse error",
                "category": "parse_error",
                "confidence": 0,
            }


def create_reflection_callback(
    llm: Optional["BaseChatModel"] = None,
    lesson_memory: Optional["LessonMemory"] = None,
) -> callable:
    """
    Factory function to create an on_outcome_recorded callback.

    Usage:
        from tradingagents.agents.journal.learning.reflection_agent import create_reflection_callback
        from tradingagents.agents.journal.learning.lesson_memory import LessonMemory

        memory = LessonMemory()
        callback = create_reflection_callback(lesson_memory=memory)

        scheduler = JournalScheduler(
            store=store,
            executor=executor,
            on_outcome_recorded=callback,
        )
    """
    agent = ReflectionAgent(llm=llm)

    def on_outcome_recorded(thesis: TradeThesis, outcome: TradeOutcome) -> None:
        """Callback that runs reflection and stores the lesson."""
        try:
            lesson = agent.reflect(thesis, outcome)

            if lesson_memory is not None:
                lesson_memory.add_lesson(lesson)
                logger.info(f"Stored lesson for {thesis.ticker} in ChromaDB")
            else:
                logger.info(f"Generated lesson for {thesis.ticker} (no memory configured)")

        except Exception as e:
            logger.error(f"Reflection callback failed for {thesis.ticker}: {e}")

    return on_outcome_recorded

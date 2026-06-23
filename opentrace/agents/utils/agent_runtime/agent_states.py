from __future__ import annotations

from typing import Annotated, Any, Optional, Sequence, TypedDict

try:  # Optional dependency
    from langgraph.graph import MessagesState as _MessagesState  # type: ignore
except Exception:  # pragma: no cover
    class _MessagesState(TypedDict, total=False):
        messages: Sequence[Any]


# Researcher team state
class InvestDebateState(TypedDict):
    bull_history: Annotated[
        str, "Bullish Conversation history"
    ]  # Bullish Conversation history
    bear_history: Annotated[
        str, "Bearish Conversation history"
    ]  # Bullish Conversation history
    history: Annotated[str, "Conversation history"]  # Conversation history
    current_response: Annotated[str, "Latest response"]  # Last response
    judge_decision: Annotated[str, "Final judge decision"]  # Last response
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


# Risk management team state
class RiskDebateState(TypedDict):
    risky_history: Annotated[
        str, "Risky Agent's Conversation history"
    ]  # Conversation history
    safe_history: Annotated[
        str, "Safe Agent's Conversation history"
    ]  # Conversation history
    neutral_history: Annotated[
        str, "Neutral Agent's Conversation history"
    ]  # Conversation history
    history: Annotated[str, "Conversation history"]  # Conversation history
    latest_speaker: Annotated[str, "Analyst that spoke last"]
    current_risky_response: Annotated[
        str, "Latest response by the risky analyst"
    ]  # Last response
    current_safe_response: Annotated[
        str, "Latest response by the safe analyst"
    ]  # Last response
    current_neutral_response: Annotated[
        str, "Latest response by the neutral analyst"
    ]  # Last response
    judge_decision: Annotated[str, "Judge's decision"]
    count: Annotated[int, "Length of the current conversation"]  # Conversation length


class AgentState(_MessagesState):
    company_of_interest: Annotated[str, "Company that we are interested in trading"]
    trade_date: Annotated[str, "What date we are trading at"]
    time_horizon: Annotated[
        str,
        "User-selected target holding period for this run (e.g., '1-2 weeks', '1-2 months')",
    ]

    sender: Annotated[str, "Agent that sent this message"]

    # research step
    market_report: Annotated[str, "Report from the Market Analyst"]
    sentiment_report: Annotated[str, "Report from the Social Media Analyst"]
    news_report: Annotated[
        str, "Report from the News Researcher of current world affairs"
    ]
    fundamentals_report: Annotated[str, "Report from the Fundamentals Researcher"]
    catalyst_report: Annotated[str, "Report from the Catalyst/Event-Risk Analyst"]
    market_evidence: Annotated[str, "Compact evidence summary from the Market Analyst"]
    sentiment_evidence: Annotated[str, "Compact evidence summary from the Social Media Analyst"]
    news_evidence: Annotated[str, "Compact evidence summary from the News Analyst"]
    fundamentals_evidence: Annotated[str, "Compact evidence summary from the Fundamentals Analyst"]
    catalyst_evidence: Annotated[str, "Compact evidence summary from the Catalyst/Event-Risk Analyst"]
    market_ledger: Annotated[dict, "Structured Analyst Workbench ledger from the Market Analyst"]
    sentiment_ledger: Annotated[dict, "Structured Analyst Workbench ledger from the Social Media Analyst"]
    news_ledger: Annotated[dict, "Structured Analyst Workbench ledger from the News Analyst"]
    fundamentals_ledger: Annotated[dict, "Structured Analyst Workbench ledger from the Fundamentals Analyst"]
    catalyst_ledger: Annotated[dict, "Structured Analyst Workbench ledger from the Catalyst/Event-Risk Analyst"]
    catalyst_event_bundle: Annotated[dict, "Structured CatalystEventBundle input to the Catalyst/Event-Risk Analyst"]
    catalyst_event_report_structured: Annotated[dict, "Validated CatalystEventReport emitted by the Catalyst/Event-Risk Analyst"]
    catalyst_parse_telemetry: Annotated[dict, "Catalyst report parser and fallback telemetry"]
    evidence_source_facts: Annotated[list, "Canonical vendor facts captured from compact bundle tool outputs"]
    evidence_graph: Annotated[dict, "Shared evidence graph built from vendor facts plus analyst inferences"]
    evidence_graph_audit: Annotated[list, "Deterministic audit issues from evidence graph construction"]
    evidence_ledger: Annotated[list, "Stable citeable Evidence Ledger items derived from the evidence graph"]
    admissibility_report: Annotated[dict, "Evidence admissibility report with accepted, downgraded, and rejected evidence IDs"]
    critical_evidence_ids: Annotated[list, "Evidence IDs ranked as critical or high-value for debate"]
    contested_issues: Annotated[list, "Decision-field issues framed from admissible critical evidence"]
    decision_trace: Annotated[dict, "Trace from final decision to thesis, inferences, facts, and sources"]

    # researcher team discussion step
    investment_debate_state: Annotated[
        InvestDebateState, "Current state of the debate on if to invest or not"
    ]
    research_debate_turns: Annotated[list, "Validated structured Bull/Bear debate turns"]
    research_debate_validation: Annotated[dict, "Accepted/rejected structured research debate turn validation report"]
    thesis_ledger: Annotated[dict, "Research Manager machine-readable thesis ledger"]
    thesis_ledger_validation: Annotated[dict, "Thesis Ledger schema and citation validation report"]
    investment_plan: Annotated[str, "Plan generated by the Analyst"]

    trader_investment_plan: Annotated[str, "Plan generated by the Trader"]
    trader_plan_v1: Annotated[dict, "Trader executable proposal with rationale links"]
    trader_plan_validation: Annotated[dict, "Trader plan schema and citation validation report"]
    trader_decision_brief: Annotated[dict, "Structured decision brief consumed by the Trader"]
    trade_setup_diagnosis: Annotated[dict, "Setup classification emitted before Trader action selection"]
    scenario_analysis: Annotated[dict, "Bull/base/bear scenario and reward-risk analysis emitted by the Trader"]
    execution_plan_compiler: Annotated[dict, "Execution compiler guidance used to shape the Trader final proposal"]
    trader_self_audit: Annotated[dict, "Trader pre-Risk-Judge self-audit and deterministic repairs"]
    agent_reasoning_trace: Annotated[dict, "Ordered structured reasoning artifacts exposed for API/UI consumers"]

    # risk management team discussion step
    risk_debate_state: Annotated[
        RiskDebateState, "Current state of the debate on evaluating risk"
    ]
    final_trade_decision: Annotated[str, "Final decision made by the Risk Analysts"]
    final_trade_decision_structured: Annotated[dict, "Canonical validated JSON decision payload"]
    final_trade_decision_validation_error: Annotated[str, "Validation error for canonical decision payload"]
    risk_patches: Annotated[list, "Structured risk debate plan patches extracted from risk analyst turns"]
    risk_patch_validation: Annotated[list, "Validation results for extracted risk plan patches"]
    decision_diff: Annotated[dict, "Difference between trader_plan_v1 and final structured decision"]
    market_snapshot: Annotated[dict, "Canonical market price snapshot used for decision anchoring and validation"]
    decision_guard: Annotated[dict, "Decision validation/repair telemetry and guardrail outcomes"]

    # Tool-loop guardrails and telemetry
    force_no_tools_for: Annotated[str, "Analyst key that must synthesize without tools for this turn"]
    tool_round_counts: Annotated[dict, "Tool rounds per analyst (e.g., market/social/news/fundamentals)"]
    # Backward-compatible alias for tool_round_counts.
    tool_call_counts: Annotated[dict, "Alias of tool_round_counts for legacy readers"]
    tool_call_total: Annotated[int, "Total number of tool rounds across analysts"]
    tool_calls_issued_by_agent: Annotated[dict, "Number of individual tool calls emitted by each analyst"]
    tool_calls_issued_total: Annotated[int, "Total individual tool calls emitted by analysts"]
    analyst_tool_call_links: Annotated[dict, "Workbench tool-call links by analyst and named question"]
    analyst_tool_call_blocked_counts: Annotated[dict, "Workbench fallback tool calls blocked by deterministic gating"]
    analyst_workbench_metrics: Annotated[dict, "Workbench observation, anomaly, question, and hypothesis metrics by analyst"]
    tool_result_cache: Annotated[dict, "Run-scoped cache of tool outputs keyed by normalized tool name and args"]
    tool_cache_metrics: Annotated[dict, "Run-scoped tool cache hit/miss and avoided-call counters"]
    vendor_telemetry: Annotated[list, "Run-scoped vendor routing telemetry emitted by dataflow calls"]

    # Exact run-level LLM telemetry.
    llm_metrics: Annotated[dict, "Exact LLM/API usage counters for this run"]

    # Portfolio awareness (injected at graph init from brokerage API)
    portfolio_context: Annotated[str, "Current portfolio state from brokerage (positions, cash, buying power)"]

    # Market-session context (computed at graph init; baseline ET windows only)
    market_session: Annotated[dict, "Current US market session metadata"]
    market_session_context: Annotated[str, "Human-readable market-session context injected into prompts"]

    # Cross-asset / regime / positioning context bus (computed once at graph init; reused by the
    # news, catalyst, and risk nodes to surface sector/macro pullback risk).
    macro_regime: Annotated[dict, "Compact cross-asset/regime/positioning snapshot for the run"]
    # Per-ticker Pullback Vulnerability Score (extension + crowding + tape fragility + valuation
    # richness) consumed by the risk judge as a pullback-risk override input.
    pullback_vulnerability: Annotated[dict, "Per-ticker pullback vulnerability score and drivers"]
    # Bounded peer-news "sector read-through" block (Tier-2 Phase 2b; gated off by default) — recent
    # guidance/news for the nearest-reporting peers, injected into the news + catalyst prompts.
    sector_read_through: Annotated[dict, "Recent news/guidance for the ticker's nearest-reporting peers"]
    # Tier-4 deterministic positioning-risk gate result emitted by the risk judge (triggered flag,
    # severity, tape factors, and the user-facing warning_text) — surfaced rather than silent.
    positioning_warning: Annotated[dict, "Positioning-risk gate result (triggered/severity/warning_text)"]

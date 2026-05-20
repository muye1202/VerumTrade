import functools
import json

from tradingagents.agents.trader.decision_brief import (
    apply_trader_self_audit,
    build_execution_plan_compiler,
    build_scenario_analysis,
    build_trade_setup_diagnosis,
    build_trader_decision_brief,
)
from tradingagents.agents.utils.agent_runtime.evidence_graph import format_evidence_projection
from tradingagents.agents.utils.agent_runtime.time_horizon import get_time_horizon_spec
from tradingagents.dataflows.config import get_config
from tradingagents.execution.decision_guard import build_market_snapshot
from tradingagents.graph.reasoning_trace import build_agent_reasoning_trace


def create_trader(llm, memory):
    def trader_node(state, name):
        company_name = state["company_of_interest"]
        investment_plan = state["investment_plan"]
        market_research_report = state["market_report"]
        portfolio_context = state.get("portfolio_context", "")
        market_session_context = state.get("market_session_context", "")
        market_snapshot = state.get("market_snapshot", {}) or build_market_snapshot(
            symbol=company_name,
            market_report=market_research_report,
            quote=None,
            structured_decision=None,
            snapshot_source=get_config().get("decision_snapshot_source", "executor_quote_first"),
        )

        evidence_projection = format_evidence_projection(state, "trader")
        decision_brief = build_trader_decision_brief(state)
        setup_diagnosis = build_trade_setup_diagnosis(decision_brief)
        scenario_analysis = build_scenario_analysis(decision_brief, setup_diagnosis)
        execution_plan_compiler = build_execution_plan_compiler(
            decision_brief=decision_brief,
            setup_diagnosis=setup_diagnosis,
            scenario_analysis=scenario_analysis,
            market_snapshot=market_snapshot,
            market_session_context=market_session_context,
            portfolio_context=portfolio_context,
            catalyst_event_report_structured=state.get("catalyst_event_report_structured", {}),
        )
        trader_self_audit, execution_plan_compiler = apply_trader_self_audit(
            decision_brief=decision_brief,
            setup_diagnosis=setup_diagnosis,
            scenario_analysis=scenario_analysis,
            execution_plan_compiler=execution_plan_compiler,
            market_snapshot=market_snapshot,
            market_session_context=market_session_context,
            catalyst_event_report_structured=state.get("catalyst_event_report_structured", {}),
        )

        memory_query = json.dumps(
            {
                "ticker": decision_brief.get("ticker", company_name),
                "primary_setup": setup_diagnosis.get("primary_setup"),
                "entry_status": setup_diagnosis.get("entry_status"),
                "setup_quality": setup_diagnosis.get("setup_quality"),
                "dominant_risk": scenario_analysis.get("dominant_risk"),
                "hard_constraints": decision_brief.get("hard_constraints", []),
                "top_supporting_evidence": [
                    item.get("claim") for item in decision_brief.get("top_supporting_evidence", [])[:3]
                ],
                "top_opposing_evidence": [
                    item.get("claim") for item in decision_brief.get("top_opposing_evidence", [])[:3]
                ],
            },
            ensure_ascii=False,
        )
        past_memories = memory.get_memories(memory_query, n_matches=2)
        past_memory_str = (
            "\n\n".join(str(rec.get("recommendation", "")) for rec in past_memories if rec.get("recommendation"))
            if past_memories
            else "No past memories found."
        )

        spec = get_time_horizon_spec(state.get("time_horizon"))
        holding_text = spec.label
        trading_days_text = f"~{spec.trading_days_range[0]}-{spec.trading_days_range[1]} trading days"

        context = {
            "role": "user",
            "content": f"""TRADER_PIPELINE_INPUTS:

TICKER:
{company_name}

RESEARCH_MANAGER_INVESTMENT_PLAN:
{investment_plan}

EVIDENCE_GRAPH_PROJECTION:
{evidence_projection}

TRADER DECISION BRIEF JSON:
{json.dumps(decision_brief, ensure_ascii=False, indent=2)}

TRADE SETUP DIAGNOSIS JSON:
{json.dumps(setup_diagnosis, ensure_ascii=False, indent=2)}

SCENARIO ANALYSIS JSON:
{json.dumps(scenario_analysis, ensure_ascii=False, indent=2)}

EXECUTION PLAN COMPILER JSON:
{json.dumps(execution_plan_compiler, ensure_ascii=False, indent=2)}

TRADER SELF-AUDIT JSON:
{json.dumps(trader_self_audit, ensure_ascii=False, indent=2)}

MARKET_SESSION_CONTEXT:
{market_session_context or "Not provided."}

MARKET_SNAPSHOT:
{market_snapshot or {}}

PORTFOLIO_CONTEXT:
{portfolio_context or "Not provided."}

MEMORY_LESSONS:
{past_memory_str}""",
        }

        messages = [
            {
                "role": "system",
                "content": f"""You are the portfolio-aware Trader in an agentic stock analysis system.
You do not summarize analyst reports. You convert organized evidence into a trade/no-trade plan.
Your job is to decide whether there is a valid setup, whether it is tradable now, how much risk is justified, and what execution plan should be proposed.

Target holding period for this run: {holding_text} ({trading_days_text}).

Follow this sequence exactly:
1. Diagnose setup type.
2. Separate supporting, opposing, timing, and risk-only evidence.
3. Decide whether the setup is tradable now, conditional, invalid, or no-trade.
4. Build bull/base/bear scenarios.
5. Select action and position size from risk budget.
6. Compile the final transaction proposal.
7. Run the self-audit checklist.

Hard rules:
- Never BUY only because the thesis is bullish; require a valid entry setup.
- Never SELL if no position exists; use HOLD or WAIT_FOR_TRIGGER instead.
- If catalyst action is freeze_new_buys, risk_judge_review, reduce_position, or exit_review, it overrides normal bullish reasoning.
- If the market is closed, do not use MARKET orders.
- All prices must be anchored to market_snapshot.reference_price.
- Stop loss represents thesis invalidation, not an arbitrary percentage distance.
- Take profit must be tied to scenario target, resistance, or reward/risk logic.
- Position size must be reduced when evidence quality is weak, event risk is high, or reward/risk is poor.

Use EXECUTION PLAN COMPILER JSON as the final proposal scaffold. Do not override compiler checks unless the reason is explicit in TRADER_REASONING_SUMMARY.
If recommended_execution_intent is WAIT_FOR_TRIGGER, prefer ACTION: HOLD now.
If recommended_action is HOLD, keep ORDER_TYPE: MARKET, QUANTITY: N/A, LIMIT_PRICE: N/A, and POSITION_SIZE_PCT: N/A.
Keep STOP_LOSS and TAKE_PROFIT anchored to compiler stop_loss/take_profit unless a safer thesis-invalidation level is clearly justified.

Before the final transaction proposal, produce exactly this compact auditable block:

TRADER_REASONING_SUMMARY:
- SETUP_TYPE:
- SETUP_QUALITY:
- PRIMARY_EDGE:
- ENTRY_STATUS:
- EXECUTION_MODE:
- INVALIDATION:
- DOMINANT_RISK:
- BULL_CASE:
- BASE_CASE:
- BEAR_CASE:
- POSITION_SIZE_LOGIC:
- MEMORY_LESSON_USED:
- FINAL_DECISION:

Self-audit checklist before final proposal:
- setup type, setup quality, and entry status are present.
- BUY is supported by A/B setup quality and confirmed entry status.
- SELL is only used when current_position.has_position is true.
- MARKET is not used for BUY/SELL when market session is closed.
- STOP_LOSS and TAKE_PROFIT are numeric and anchored to scenario analysis.
- catalyst overrides are reflected in execution mode, sizing, or action.

The strict transaction proposal remains the final machine-readable section. End with exactly this format:

---
FINAL TRANSACTION PROPOSAL:
- EXECUTION_INTENT: ACT_NOW / WAIT_FOR_TRIGGER
- ACTION: BUY / SELL / HOLD
- TICKER: {company_name}
- QUANTITY: [INTEGER number of shares, or "N/A" for HOLD]
- ORDER_TYPE: MARKET / LIMIT / STOP / STOP_LIMIT / TRAILING_STOP
- TIME_IN_FORCE: DAY / GTC
- LIMIT_PRICE: [required for LIMIT and STOP_LIMIT; otherwise "N/A"]
- STOP_PRICE: [required for STOP and STOP_LIMIT; otherwise "N/A"]
- TRAIL_PERCENT: [for TRAILING_STOP, percent like 3 for 3%; otherwise "N/A"]
- TRAIL_PRICE: [for TRAILING_STOP, dollars like 1.25; otherwise "N/A"]
- STOP_LOSS: [REQUIRED numeric price for BUY/SELL/HOLD]
- TAKE_PROFIT: [REQUIRED numeric price for BUY/SELL/HOLD]
- POSITION_SIZE_PCT: [for BUY only: percent of available capital/effective buying power; otherwise "N/A"]
- TIME_HORIZON: [e.g., "1-3 days", "1-2 weeks", "swing trade"]
- CONFIDENCE: HIGH / MEDIUM / LOW
- RATIONALE: [one-sentence summary]
---""",
            },
            context,
        ]

        result = llm.invoke(messages)

        state_with_trader_outputs = {
            **state,
            "trader_investment_plan": result.content,
            "market_snapshot": market_snapshot,
            "trader_decision_brief": decision_brief,
            "trade_setup_diagnosis": setup_diagnosis,
            "scenario_analysis": scenario_analysis,
            "execution_plan_compiler": execution_plan_compiler,
            "trader_self_audit": trader_self_audit,
        }

        return {
            "messages": [result],
            "trader_investment_plan": result.content,
            "market_snapshot": market_snapshot,
            "trader_decision_brief": decision_brief,
            "trade_setup_diagnosis": setup_diagnosis,
            "scenario_analysis": scenario_analysis,
            "execution_plan_compiler": execution_plan_compiler,
            "trader_self_audit": trader_self_audit,
            "agent_reasoning_trace": build_agent_reasoning_trace(state_with_trader_outputs),
            "sender": name,
        }

    return functools.partial(trader_node, name="Trader")

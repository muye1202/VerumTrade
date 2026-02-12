"""
Portfolio Triage Agent
=====================
Pre-filters portfolio positions to select the N most analysis-worthy stocks
*before* the expensive multi-agent pipeline runs on each one.

Architecture
------------
1.  Receives all current positions with basic brokerage metrics
    (qty, market value, unrealized P&L, weight).
2.  Runs a capped tool-calling loop with the **deep-think LLM** and
    lightweight data tools (news search, stock data) so the agent can
    research recent catalysts or price action.
3.  Returns a structured selection: top-N tickers with priority & rationale,
    plus a skip-list for the remainder.

The agent is intentionally decoupled from LangGraph; it implements its own
simple ReAct-style loop so it can be dropped into PortfolioAnalyzer (or any
other orchestrator) without nesting sub-graphs.

Tool extensibility
------------------
The constructor accepts an arbitrary tool list.  Today we wire in the
existing ``get_news``, ``get_global_news``, and ``get_stock_data`` tools
that route through the vendor abstraction.  If you later add a dedicated
web-search tool (Tavily, SerpAPI, etc.) you only need to append it to the
list - zero changes to the triage logic itself.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from tradingagents.agents.utils.agent_runtime.agent_utils import (
    get_global_news,
    get_news,
    get_stock_data,
)

logger = logging.getLogger("PortfolioTriageAgent")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PortfolioTriageAgent:
    """Select the top-N portfolio positions that warrant full multi-agent analysis.

    Parameters
    ----------
    llm
        The *deep-think* LLM instance (``ChatOpenAI``, ``ChatAnthropic``, …).
        This is the same model used for debate judges / research managers — it
        needs strong reasoning ability to weigh many factors at once.
    tools
        LangChain ``@tool``-decorated callables the agent may invoke during its
        research phase.  Defaults to ``[get_news, get_global_news, get_stock_data]``.
    max_tool_rounds
        Hard cap on tool-call iterations to bound latency.  Each round may
        contain *multiple* parallel tool calls if the LLM supports it.
    config
        Optional dict of TradingAgents config (unused today but reserved for
        future provider-specific tuning).
    """

    DEFAULT_TOOLS = [get_news, get_global_news, get_stock_data]

    def __init__(
        self,
        llm: Any,
        tools: Optional[Sequence] = None,
        max_tool_rounds: int = 6,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.llm = llm
        self.tools = list(tools) if tools is not None else list(self.DEFAULT_TOOLS)
        self.tools_by_name: Dict[str, Any] = {t.name: t for t in self.tools}
        self.max_tool_rounds = max_tool_rounds
        self.config = config or {}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def triage(
        self,
        positions: List[Dict[str, Any]],
        n_select: int,
        portfolio_summary: Dict[str, Any],
        trade_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the triage loop and return a structured selection.

        Parameters
        ----------
        positions
            List of position dicts as returned by
            ``AlpacaExecutor.get_portfolio_summary()["positions"]``.
            Each dict must have at least ``symbol``, ``qty``,
            ``market_value``, ``unrealized_pl``, ``unrealized_plpc``.
        n_select
            Number of stocks the user wants to deep-analyze.
        portfolio_summary
            Top-level summary dict (``account_value``, ``cash``,
            ``buying_power``, ``positions_count``).
        trade_date
            YYYY-MM-DD date for research context.  Defaults to today.

        Returns
        -------
        dict
            ``selected``  – list of ``{ticker, priority, rationale}``
            ``skipped``   – list of ``{ticker, rationale}``
            ``research_notes`` – free-text summary the agent produced
        """
        trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")

        # Fast path: nothing to triage
        if not positions:
            return {"selected": [], "skipped": [], "research_notes": "No positions."}

        if len(positions) <= n_select:
            logger.info(
                "Portfolio has %d positions ≤ N=%d; selecting all.",
                len(positions),
                n_select,
            )
            return {
                "selected": [
                    {
                        "ticker": p["symbol"],
                        "priority": i + 1,
                        "rationale": "All positions selected (portfolio size ≤ N).",
                    }
                    for i, p in enumerate(positions)
                ],
                "skipped": [],
                "research_notes": "Triage skipped — portfolio size ≤ requested N.",
            }

        # ---- Build initial messages ----
        messages = self._build_messages(positions, n_select, portfolio_summary, trade_date)

        # ---- Tool-calling loop ----
        llm_with_tools = self.llm.bind_tools(self.tools)
        final_content = ""

        for round_idx in range(self.max_tool_rounds):
            logger.info("Triage round %d/%d", round_idx + 1, self.max_tool_rounds)

            response = llm_with_tools.invoke(messages)
            messages.append(response)

            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                final_content = _extract_text(response)
                break

            # Execute every tool call in this round (supports parallel calls)
            for tc in tool_calls:
                name = tc.get("name") or tc.get("function", {}).get("name", "")
                args = tc.get("args") or tc.get("function", {}).get("arguments", {})
                tc_id = tc.get("id", f"tc_{round_idx}_{name}")

                logger.info("  ↳ tool %s(%s)", name, _truncate(str(args), 120))
                result = self._safe_invoke_tool(name, args)
                messages.append(ToolMessage(content=str(result), tool_call_id=tc_id))
        else:
            # Exhausted all rounds — force a final answer
            messages.append(
                HumanMessage(
                    content=(
                        "You have reached the maximum number of research rounds. "
                        "Please provide your final stock selection NOW using the "
                        "JSON format described earlier."
                    )
                )
            )
            response = self.llm.invoke(messages)  # no tools bound
            final_content = _extract_text(response)

        # ---- Parse output ----
        return self._parse_selection(final_content, positions, n_select)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        positions: List[Dict[str, Any]],
        n_select: int,
        portfolio_summary: Dict[str, Any],
        trade_date: str,
    ) -> list:
        n_total = len(positions)
        news_start = (
            datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)
        ).strftime("%Y-%m-%d")

        tool_names = ", ".join(t.name for t in self.tools)
        tool_docs = "\n".join(
            f"- **{t.name}**: {(t.description or '').split(chr(10))[0]}"
            for t in self.tools
        )

        system = f"""\
You are a senior portfolio strategist performing TRIAGE on a live portfolio.

## Objective
Select exactly **{n_select}** positions (out of {n_total}) that most urgently \
warrant a full multi-agent deep-dive analysis.  The remaining positions will be \
skipped this cycle to save time and cost (each deep analysis takes ~5-10 min \
with multiple LLM agents).

## Selection Criteria (ranked by importance)
1. **Breaking / Material News** — earnings surprises, M&A, regulatory actions, \
lawsuits, management changes, analyst upgrades/downgrades
2. **Extreme Unrealized P&L** — losses > 10% (cut-loss candidates) or gains > 20% \
(profit-taking candidates)
3. **Concentration Risk** — positions > 15% of portfolio value
4. **Unusual Volatility** — sudden moves not yet reflected in the metrics
5. **Thesis Drift** — any reason the original investment thesis may have changed

## Research Strategy
You have {self.max_tool_rounds} tool-call rounds.  Be efficient:
- Start by scanning the metrics table for outliers (no tools needed).
- Use **get_news** for the 2-4 tickers that look most interesting or concerning.
- Use **get_global_news** once for macro context that could affect the whole book.
- Use **get_stock_data** only when recent intraday/daily price action would \
materially change your ranking.

## Available Tools
{tool_docs}

## Dates
- Current date: {trade_date}
- Suggested news window: {news_start} → {trade_date}"""

        portfolio_table = _format_positions_table(positions, portfolio_summary)

        user = f"""\
{portfolio_table}

---

Select exactly **{n_select}** stocks for deep analysis.

**Step 1** — Examine the table above and decide which tickers deserve research.
**Step 2** — Use tools to investigate those tickers.
**Step 3** — Produce your final selection as a fenced JSON block:

```json
{{
  "selected": [
    {{"ticker": "XXXX", "priority": 1, "rationale": "One-sentence reason"}},
    {{"ticker": "YYYY", "priority": 2, "rationale": "One-sentence reason"}}
  ],
  "skipped": [
    {{"ticker": "ZZZZ", "rationale": "One-sentence reason this can wait"}}
  ],
  "research_notes": "2-3 sentence summary of your research findings"
}}
```

Rules:
- Priority 1 = most urgent.
- Every ticker must appear in exactly one of `selected` or `skipped`.
- `selected` must have exactly {n_select} entries."""

        return [SystemMessage(content=system), HumanMessage(content=user)]

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _safe_invoke_tool(self, name: str, args: Any) -> str:
        tool = self.tools_by_name.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'. Available: {list(self.tools_by_name)}"
        try:
            if isinstance(args, str):
                args = json.loads(args)
            return tool.invoke(args)
        except Exception as exc:
            logger.warning("Tool %s failed: %s", name, exc)
            return f"Error calling {name}: {exc}"

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    def _parse_selection(
        self,
        content: str,
        positions: List[Dict[str, Any]],
        n_select: int,
    ) -> Dict[str, Any]:
        all_tickers = {p["symbol"].upper() for p in positions}

        parsed = _extract_json_block(content)

        if parsed and "selected" in parsed:
            selected = parsed.get("selected", [])
            skipped = parsed.get("skipped", [])
            notes = parsed.get("research_notes", "")

            # Validate: only keep tickers that actually exist in the portfolio
            valid_selected = [
                s for s in selected if s.get("ticker", "").upper() in all_tickers
            ][:n_select]

            # Ensure every portfolio ticker ends up in exactly one bucket
            selected_set = {s["ticker"].upper() for s in valid_selected}
            skipped_set = {s.get("ticker", "").upper() for s in skipped}

            for t in all_tickers - selected_set - skipped_set:
                skipped.append({"ticker": t, "rationale": "Not selected by triage agent."})

            return {
                "selected": valid_selected,
                "skipped": skipped,
                "research_notes": notes,
            }

        # Fallback: heuristic if LLM output couldn't be parsed
        logger.warning("Could not parse triage JSON; using heuristic fallback.")
        return _heuristic_fallback(positions, n_select, content)


# ---------------------------------------------------------------------------
# Pure helper functions (module-private)
# ---------------------------------------------------------------------------

def _extract_text(response: Any) -> str:
    """Pull plain text from a LangChain message / response object."""
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return str(content)


def _extract_json_block(text: str) -> Optional[dict]:
    """Extract a JSON object from fenced code blocks or raw JSON."""
    # Fenced block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Raw braces — find outermost balanced pair
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _format_positions_table(
    positions: List[Dict[str, Any]], summary: Dict[str, Any]
) -> str:
    total = summary.get("account_value", 1) or 1
    cash = summary.get("cash", 0)
    bp = summary.get("buying_power", 0)

    lines = [
        "## Portfolio Snapshot",
        f"- **Account value:** ${total:,.2f}",
        f"- **Cash:** ${cash:,.2f}    |    **Buying power:** ${bp:,.2f}",
        f"- **Open positions:** {len(positions)}",
        "",
        "| # | Ticker | Qty | Mkt Value | Weight % | Unreal P&L | P&L % |",
        "|--:|--------|----:|----------:|---------:|-----------:|------:|",
    ]

    for idx, p in enumerate(positions, 1):
        sym = p.get("symbol", "?")
        qty = float(p.get("qty", 0))
        mv = float(p.get("market_value", 0))
        upl = float(p.get("unrealized_pl", 0))
        uplpc = float(p.get("unrealized_plpc", 0)) * 100
        wt = (mv / total) * 100 if total else 0

        sign = "+" if upl >= 0 else ""
        pct_sign = "+" if uplpc >= 0 else ""

        lines.append(
            f"| {idx} | {sym} | {qty:,.0f} | ${mv:,.2f} | {wt:.1f}% "
            f"| {sign}${upl:,.2f} | {pct_sign}{uplpc:.1f}% |"
        )

    return "\n".join(lines)


def _heuristic_fallback(
    positions: List[Dict[str, Any]], n_select: int, agent_notes: str = ""
) -> Dict[str, Any]:
    """
    Last-resort selection when the LLM output can't be parsed.

    Scores each position by a combination of:
      • absolute unrealized P&L %  (extreme moves → high score)
      • position size               (larger positions → higher score)
      • loss penalty                (big losers get extra priority)
    """
    scored: list[tuple[str, float, dict]] = []
    for p in positions:
        ticker = p.get("symbol", "?")
        mv = abs(float(p.get("market_value", 0)))
        uplpc = float(p.get("unrealized_plpc", 0))

        score = mv * 0.001 + abs(uplpc) * 100
        if uplpc < -0.10:
            score += 50
        if uplpc > 0.20:
            score += 30

        scored.append((ticker, score, p))

    scored.sort(key=lambda x: x[1], reverse=True)

    selected = [
        {
            "ticker": t,
            "priority": i + 1,
            "rationale": "Selected by heuristic fallback (P&L magnitude + position size).",
        }
        for i, (t, _, _) in enumerate(scored[:n_select])
    ]
    skipped = [
        {"ticker": t, "rationale": "Not prioritized by heuristic."}
        for t, _, _ in scored[n_select:]
    ]

    return {
        "selected": selected,
        "skipped": skipped,
        "research_notes": (
            f"⚠ Heuristic fallback used (LLM output could not be parsed). "
            f"Agent raw notes: {_truncate(agent_notes, 500)}"
        ),
    }


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."

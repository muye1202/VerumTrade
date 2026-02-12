"""
Portfolio-level analysis and rebalancing engine.
Analyzes all positions, generates recommendations, and provides strategic insights.

Refactored to include a **triage step**: before running expensive multi-agent
analysis on every position, a PortfolioTriageAgent screens the portfolio and
selects the N most analysis-worthy stocks.  The remaining positions receive a
lightweight "HOLD — not triaged" recommendation.
"""

from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import logging
import time
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.execution import AlpacaExecutor
from tradingagents.execution.portfolio_context import fetch_portfolio_context
from tradingagents.agents.portfolio.triage_agent import PortfolioTriageAgent


class PortfolioAnalyzer:
    """
    Analyzes entire portfolio and generates rebalancing recommendations.
    """

    def __init__(
        self,
        graph: TradingAgentsGraph,
        executor: AlpacaExecutor,
        analysis_date: Optional[str] = None,
        time_horizon: Optional[str] = None,
    ):
        self.graph = graph
        self.executor = executor
        self.analysis_date = analysis_date or datetime.now().strftime("%Y-%m-%d")
        self.time_horizon = time_horizon
        self.logger = logging.getLogger("PortfolioAnalyzer")
        self.config = graph.config if hasattr(graph, 'config') else {}

    def analyze_portfolio(
        self,
        execute_trades: bool = False,
        n_stocks: Optional[int] = None,
        # Progress callbacks for Live GUI integration
        on_triage_start: Optional[Callable[[], None]] = None,
        on_triage_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_stock_start: Optional[Callable[[str, int, int], None]] = None,
        on_stock_chunk: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_stock_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_stock_executed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_execution_start: Optional[Callable[[], None]] = None,
        on_execution_complete: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze all portfolio positions and generate recommendations.

        Args:
            execute_trades: Whether to execute recommended trades
            n_stocks: If set, triage the portfolio down to this many stocks
                      before running full analysis.  ``None`` means analyze all
                      positions (legacy behaviour).
            on_triage_start: Callback when triage phase begins
            on_triage_complete: Callback when triage phase ends (receives triage result)
            on_stock_start: Callback when stock analysis begins (ticker, index, total)
            on_stock_chunk: Callback for each streaming chunk during stock analysis
            on_stock_complete: Callback when stock analysis ends (ticker, analysis)
            on_stock_executed: Callback when a per-stock trade is executed (ticker, result)
            on_execution_start: Callback when trade execution begins
            on_execution_complete: Callback when trade execution ends (receives results)

        Returns:
            Dict with analysis results, recommendations, and insights
        """
        self.logger.info(f"Starting portfolio analysis for {self.analysis_date}")

        # Step 1: Fetch current portfolio
        portfolio = self._fetch_portfolio()
        if not portfolio or portfolio["positions_count"] == 0:
            return {
                "error": "No positions found in portfolio",
                "portfolio": portfolio,
            }

        self.logger.info(f"Analyzing {portfolio['positions_count']} positions")

        # Step 2: Triage — select the top N if requested
        triage_result = None
        positions_to_analyze = portfolio["positions"]

        if n_stocks is not None and n_stocks > 0:
            if on_triage_start:
                on_triage_start()

            triage_result = self._triage_positions(
                portfolio["positions"],
                n_stocks,
                portfolio,
            )
            selected_tickers = {
                s["ticker"].upper() for s in triage_result.get("selected", [])
            }
            positions_to_analyze = [
                p
                for p in portfolio["positions"]
                if p["symbol"].upper() in selected_tickers
            ]
            self.logger.info(
                "Triage complete: %d/%d positions selected for deep analysis.",
                len(positions_to_analyze),
                portfolio["positions_count"],
            )

            if on_triage_complete:
                on_triage_complete(triage_result)

            # Delay after triage to allow API rate limits to recover
            triage_delay = self.config.get("post_triage_delay_s", 10.0)
            if triage_delay > 0:
                self.logger.info(f"Waiting {triage_delay}s after triage before deep analysis...")
                time.sleep(triage_delay)

        # Step 3: Run full multi-agent analysis on selected positions
        #         (with per-stock execution if requested)
        position_analyses = self._analyze_positions(
            positions_to_analyze,
            on_stock_start=on_stock_start,
            on_stock_chunk=on_stock_chunk,
            on_stock_complete=on_stock_complete,
            executor=self.executor if execute_trades else None,
            execute_trades=execute_trades,
            on_stock_executed=on_stock_executed,
        )

        # Step 3b: Add lightweight stub entries for skipped positions
        if triage_result:
            position_analyses = self._merge_skipped_positions(
                position_analyses,
                portfolio["positions"],
                triage_result,
            )

        # Step 4: Perform portfolio-level analysis
        portfolio_metrics = self._calculate_portfolio_metrics(
            portfolio, position_analyses
        )

        # Step 5: Generate recommendations
        recommendations = self._generate_recommendations(
            portfolio, position_analyses, portfolio_metrics
        )

        # Step 6: Execute remaining trades if requested
        #         (per-stock execution already happened in _analyze_positions;
        #          this batch step catches any that weren't executed inline.)
        execution_results = []
        if execute_trades:
            # Collect tickers already executed per-stock
            already_executed = {
                a["ticker"]
                for a in position_analyses
                if a.get("execution_result") and a["execution_result"].get("executed")
            }

            if on_execution_start:
                on_execution_start()

            execution_results = self._execute_recommendations(
                recommendations, position_analyses, already_executed
            )

            if on_execution_complete:
                on_execution_complete(execution_results)

        # Step 7: Generate strategic insights
        strategic_insights = self._generate_strategic_insights(
            portfolio, position_analyses, portfolio_metrics, recommendations
        )

        result = {
            "analysis_date": self.analysis_date,
            "portfolio_summary": portfolio,
            "portfolio_metrics": portfolio_metrics,
            "position_analyses": position_analyses,
            "recommendations": recommendations,
            "execution_results": execution_results,
            "strategic_insights": strategic_insights,
        }

        if triage_result:
            result["triage"] = triage_result

        return result

    # ================================================================
    # Triage
    # ================================================================

    def _triage_positions(
        self,
        positions: List[Dict[str, Any]],
        n_stocks: int,
        portfolio_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Use the deep-think LLM to pre-screen positions.

        Creates a ``PortfolioTriageAgent`` with the same deep-think LLM that
        powers the debate judges, binds it to the lightweight data tools
        (news, stock data), and asks it to pick the N most important positions.
        """
        self.logger.info(
            "Running portfolio triage: selecting %d of %d positions.",
            n_stocks,
            len(positions),
        )

        agent = PortfolioTriageAgent(
            llm=self.graph.deep_thinking_llm,
            # Default tools are already wired (get_news, get_global_news,
            # get_stock_data).  To add a dedicated web-search tool, append it:
            #   tools=[*PortfolioTriageAgent.DEFAULT_TOOLS, my_web_search_tool],
            max_tool_rounds=self.graph.config.get("triage_max_tool_rounds", 6),
            config=self.graph.config,
        )

        return agent.triage(
            positions=positions,
            n_select=n_stocks,
            portfolio_summary=portfolio_summary,
            trade_date=self.analysis_date,
        )

    @staticmethod
    def _merge_skipped_positions(
        deep_analyses: List[Dict[str, Any]],
        all_positions: List[Dict[str, Any]],
        triage_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Merge deep-analyzed positions with lightweight stubs for skipped ones.

        Skipped positions get a default HOLD recommendation with zero
        conviction so they still appear in the final report but don't trigger
        any trades.
        """
        analyzed_tickers = {a["ticker"].upper() for a in deep_analyses}

        # Build a lookup from the skip list for rationale
        skip_rationale: Dict[str, str] = {}
        for s in triage_result.get("skipped", []):
            skip_rationale[s.get("ticker", "").upper()] = s.get("rationale", "")

        for p in all_positions:
            sym = p["symbol"].upper()
            if sym in analyzed_tickers:
                continue

            deep_analyses.append(
                {
                    "ticker": sym,
                    "current_qty": p.get("qty", 0),
                    "current_value": p.get("market_value", 0),
                    "unrealized_pl": p.get("unrealized_pl", 0),
                    "unrealized_plpc": p.get("unrealized_plpc", 0),
                    "decision": "HOLD",
                    "structured_decision": {},
                    "conviction_score": 0,
                    "final_state": None,
                    "analysis_summary": (
                        f"Skipped during triage. "
                        f"Reason: {skip_rationale.get(sym, 'Not prioritised.')}"
                    ),
                    "triaged_out": True,
                }
            )

        return deep_analyses

    # ================================================================
    # Full analysis (unchanged from original)
    # ================================================================

    def _fetch_portfolio(self) -> Dict[str, Any]:
        """Fetch current portfolio state from Alpaca."""
        try:
            return self.executor.get_portfolio_summary()
        except Exception as e:
            self.logger.error(f"Failed to fetch portfolio: {e}")
            return {}

    def _analyze_positions(
        self,
        positions: List[Dict[str, Any]],
        on_stock_start: Optional[Callable[[str, int, int], None]] = None,
        on_stock_chunk: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_stock_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        executor: Optional["AlpacaExecutor"] = None,
        execute_trades: bool = False,
        on_stock_executed: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        """Analyze each position using the trading agents framework.

        If *execute_trades* is True and an *executor* is provided, each
        position's signal is executed **immediately** after analysis completes
        (matching the single-ticker behaviour).

        Args:
            positions: List of position dicts to analyze
            on_stock_start: Callback(ticker, idx, total) when stock analysis begins
            on_stock_chunk: Callback(ticker, chunk) for each streaming chunk
            on_stock_complete: Callback(ticker, analysis) when stock analysis ends
            executor: Optional AlpacaExecutor for per-stock trade execution
            execute_trades: Whether to execute trades immediately per-stock
            on_stock_executed: Callback(ticker, exec_result) after per-stock execution
        """
        analyses = []
        total_positions = len(positions)
        stock_delay = self.config.get("stock_analysis_delay_s", 5.0)

        for idx, position in enumerate(positions):
            ticker = position["symbol"]
            self.logger.info(f"Analyzing position {idx + 1}/{total_positions}: {ticker}")

            if on_stock_start:
                on_stock_start(ticker, idx, total_positions)

            try:
                # Run full analysis with portfolio context and streaming for UI updates
                portfolio_ctx = fetch_portfolio_context(ticker)
                final_state, decision = self._analyze_single_position_with_streaming(
                    ticker, portfolio_ctx, on_stock_chunk
                )

                # Extract structured decision
                structured = self.graph.extract_structured_decision(
                    final_state["final_trade_decision"]
                )

                analysis = {
                    "ticker": ticker,
                    "current_qty": position["qty"],
                    "current_value": position["market_value"],
                    "unrealized_pl": position["unrealized_pl"],
                    "unrealized_plpc": position["unrealized_plpc"],
                    "decision": decision,
                    "structured_decision": structured,
                    "conviction_score": 0,  # placeholder — conviction helpers removed
                    "final_state": final_state,
                    "analysis_summary": self._extract_summary(final_state),
                }
                analyses.append(analysis)

                if on_stock_complete:
                    on_stock_complete(ticker, analysis)

                # ---- Per-stock execution (matches single-ticker behaviour) ----
                if execute_trades and executor is not None:
                    try:
                        exec_result = executor.execute_signal(
                            ticker=ticker,
                            signal=decision,
                            analysis_state=final_state,
                            trade_date=self.analysis_date,
                            agent_quantity=structured.get("quantity"),
                            agent_limit_price=structured.get("limit_price"),
                            agent_order_type=structured.get("order_type"),
                            agent_time_in_force=structured.get("time_in_force"),
                            agent_extended_hours=structured.get("extended_hours"),
                            agent_stop_price=structured.get("stop_price"),
                            agent_trail_percent=structured.get("trail_percent"),
                            agent_trail_price=structured.get("trail_price"),
                            agent_position_size_pct=structured.get("position_size_pct"),
                            agent_stop_loss=structured.get("stop_loss"),
                            agent_take_profit=structured.get("take_profit"),
                        )
                        analysis["execution_result"] = exec_result

                        # Journal capture (non-critical)
                        try:
                            from tradingagents.agents.journal.store import JournalStore
                            from tradingagents.agents.journal.hooks import capture_trade_thesis

                            journal_store = JournalStore()
                            capture_trade_thesis(
                                store=journal_store,
                                final_state=final_state,
                                structured_decision=structured,
                                execution_result=exec_result,
                                trade_date=self.analysis_date,
                                executor=executor,
                            )
                        except Exception:
                            pass

                        if on_stock_executed:
                            on_stock_executed(ticker, exec_result)

                    except Exception as exec_err:
                        self.logger.error(f"Per-stock execution failed for {ticker}: {exec_err}")
                        analysis["execution_result"] = {"executed": False, "error": str(exec_err)}
                        if on_stock_executed:
                            on_stock_executed(ticker, analysis["execution_result"])

            except Exception as e:
                self.logger.exception(f"Error analyzing {ticker}: {e}")
                error_analysis = {
                    "ticker": ticker,
                    "error": str(e),
                    "decision": "HOLD",
                    "conviction_score": 0,
                }
                analyses.append(error_analysis)

                if on_stock_complete:
                    on_stock_complete(ticker, error_analysis)

            # Add delay between stock analyses to respect API rate limits
            if idx < total_positions - 1 and stock_delay > 0:
                self.logger.info(f"Waiting {stock_delay}s before next stock to respect rate limits...")
                time.sleep(stock_delay)

        return analyses

    def _analyze_single_position_with_streaming(
        self,
        ticker: str,
        portfolio_ctx: str,
        on_chunk: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> tuple:
        """Analyze a single position with streaming support for UI updates.

        This method uses graph.stream() to get incremental updates that can be
        passed to the UI callback for real-time progress display.
        """
        self.graph.ticker = ticker

        init_agent_state = self.graph.propagator.create_initial_state(
            ticker,
            self.analysis_date,
            portfolio_context=portfolio_ctx,
            time_horizon=self.time_horizon,
        )
        args = self.graph.propagator.get_graph_args()

        # Stream the graph execution to get incremental updates
        trace = []
        for chunk in self.graph.graph.stream(init_agent_state, **args):
            trace.append(chunk)

            # Call the chunk callback if provided (for UI updates)
            if on_chunk:
                on_chunk(ticker, chunk)

        if not trace:
            raise RuntimeError(f"No chunks received from graph for {ticker}")

        final_state = trace[-1]

        # Store current state for reflection
        self.graph.curr_state = final_state

        # Extract decision
        structured = self.graph.extract_structured_decision(
            final_state.get("final_trade_decision", "")
        )
        decision = structured.get("action") or self.graph.process_signal(
            final_state.get("final_trade_decision", "")
        )

        return final_state, decision

    def _calculate_portfolio_metrics(
        self,
        portfolio: Dict[str, Any],
        analyses: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calculate portfolio-level metrics."""
        total_value = portfolio["account_value"]
        positions = portfolio["positions"]

        # Position concentration
        max_position_pct = (
            max((p["market_value"] / total_value * 100) for p in positions)
            if positions
            else 0
        )

        sector_allocation = self._estimate_sector_allocation(positions)

        winners = sum(1 for p in positions if float(p["unrealized_pl"]) > 0)
        losers = sum(1 for p in positions if float(p["unrealized_pl"]) < 0)

        avg_conviction = (
            sum(a.get("conviction_score", 0) for a in analyses) / len(analyses)
            if analyses
            else 0
        )

        sell_signals = sum(1 for a in analyses if a.get("decision") == "SELL")
        buy_signals = sum(1 for a in analyses if a.get("decision") == "BUY")

        return {
            "total_value": total_value,
            "cash": portfolio["cash"],
            "buying_power": portfolio["buying_power"],
            "position_count": len(positions),
            "max_position_pct": round(max_position_pct, 2),
            "sector_allocation": sector_allocation,
            "win_loss_ratio": f"{winners}/{losers}",
            "avg_conviction": round(avg_conviction, 2),
            "sell_signals": sell_signals,
            "buy_signals": buy_signals,
            "portfolio_health": self._assess_portfolio_health(
                max_position_pct, avg_conviction, winners, losers
            ),
        }

    def _generate_recommendations(
        self,
        portfolio: Dict[str, Any],
        analyses: List[Dict[str, Any]],
        metrics: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate actionable recommendations for portfolio rebalancing."""
        recommendations = []

        for analysis in analyses:
            if "error" in analysis:
                continue

            ticker = analysis["ticker"]
            decision = analysis["decision"]
            conviction = analysis["conviction_score"]
            current_value = analysis.get("current_value", 0)

            position_pct = (current_value / portfolio["account_value"]) * 100

            rec: Dict[str, Any] = {
                "ticker": ticker,
                "action": decision,
                "conviction": conviction,
                "current_position_pct": round(position_pct, 2),
                "rationale": self._build_rationale(analysis, metrics),
                "priority": self._calculate_priority(
                    decision, conviction, position_pct
                ),
                "suggested_action": None,
                "decision_summary": None,
                "triaged_out": analysis.get("triaged_out", False),
            }

            if decision == "SELL":
                qty = analysis.get("current_qty", 0)
                if conviction > 70:
                    rec["suggested_action"] = (
                        f"SELL ALL ({qty} shares)"
                    )
                elif conviction > 50:
                    rec["suggested_action"] = (
                        f"SELL 50% ({int(float(qty) * 0.5)} shares)"
                    )
                else:
                    rec["suggested_action"] = (
                        f"SELL 25% ({int(float(qty) * 0.25)} shares)"
                    )

            elif decision == "BUY":
                if position_pct > 15:
                    rec["suggested_action"] = "HOLD - Position already large"
                elif conviction > 70 and portfolio["cash"] > 1000:
                    add_pct = min(
                        5, portfolio["cash"] / portfolio["account_value"] * 100
                    )
                    rec["suggested_action"] = (
                        f"ADD {add_pct:.1f}% more to position"
                    )
                else:
                    rec["suggested_action"] = "HOLD - Maintain position"

            else:  # HOLD
                rec["suggested_action"] = "HOLD - No action needed"

            rec["decision_summary"] = self._summarize_recommendation(rec)
            recommendations.append(rec)

        recommendations.sort(key=lambda x: x["priority"], reverse=True)
        return recommendations

    def _summarize_recommendation(self, rec: Dict[str, Any]) -> str:
        rationale = (rec.get("rationale") or "").strip()
        suggested = (rec.get("suggested_action") or "").strip()
        parts = []
        if rationale:
            parts.append(rationale.rstrip(".") + ".")
        if suggested:
            parts.append(f"Suggested: {suggested.rstrip('.')}.")
        return self._to_sentence_summary(" ".join(parts).strip(), max_sentences=2)

    def _to_sentence_summary(self, text: str, max_sentences: int = 2) -> str:
        s = (text or "").strip()
        if not s:
            return ""
        out: list[str] = []
        buf: list[str] = []
        for ch in s:
            buf.append(ch)
            if ch in ".!?":
                sentence = "".join(buf).strip()
                if sentence:
                    out.append(sentence)
                buf = []
                if len(out) >= max_sentences:
                    break
        if len(out) < max_sentences and buf:
            tail = "".join(buf).strip()
            if tail:
                out.append(tail)
        return " ".join(out).strip()

    def _execute_recommendations(
        self,
        recommendations: List[Dict[str, Any]],
        analyses: List[Dict[str, Any]],
        already_executed: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """Execute remaining recommendations not already handled per-stock."""
        already_executed = already_executed or set()
        analyses_by_ticker = {a["ticker"]: a for a in analyses if "ticker" in a}

        results = []
        for rec in recommendations:
            if rec["action"] == "HOLD":
                continue
            # Never execute on positions that were triaged out
            if rec.get("triaged_out"):
                continue
            # Skip tickers already executed per-stock
            if rec["ticker"] in already_executed:
                self.logger.info(
                    "Skipping %s — already executed per-stock", rec["ticker"]
                )
                continue

            ticker = rec["ticker"]
            action = rec["action"]
            self.logger.info(
                "Executing %s for %s (batch step)", action, ticker
            )
            try:
                analysis = analyses_by_ticker.get(ticker, {})
                final_state = analysis.get("final_state")
                structured = analysis.get("structured_decision", {})

                result = self.executor.execute_signal(
                    ticker=ticker,
                    signal=action,
                    analysis_state=final_state,
                    trade_date=self.analysis_date,
                    agent_quantity=structured.get("quantity"),
                    agent_limit_price=structured.get("limit_price"),
                    agent_order_type=structured.get("order_type"),
                    agent_time_in_force=structured.get("time_in_force"),
                    agent_extended_hours=structured.get("extended_hours"),
                    agent_stop_price=structured.get("stop_price"),
                    agent_trail_percent=structured.get("trail_percent"),
                    agent_trail_price=structured.get("trail_price"),
                    agent_position_size_pct=structured.get("position_size_pct"),
                    agent_stop_loss=structured.get("stop_loss"),
                    agent_take_profit=structured.get("take_profit"),
                )

                # Journal capture (non-critical)
                try:
                    from tradingagents.agents.journal.store import JournalStore
                    from tradingagents.agents.journal.hooks import capture_trade_thesis

                    journal_store = JournalStore()
                    capture_trade_thesis(
                        store=journal_store,
                        final_state=final_state,
                        structured_decision=structured,
                        execution_result=result,
                        trade_date=self.analysis_date,
                        executor=self.executor,
                    )
                except Exception:
                    pass

                results.append(
                    {
                        "ticker": ticker,
                        "action": action,
                        "conviction": rec["conviction"],
                        "execution_result": result,
                    }
                )
            except Exception as e:
                self.logger.error(f"Execution failed for {ticker}: {e}")
                results.append(
                    {"ticker": ticker, "action": action, "error": str(e)}
                )
        return results

    def _generate_strategic_insights(
        self,
        portfolio: Dict[str, Any],
        analyses: List[Dict[str, Any]],
        metrics: Dict[str, Any],
        recommendations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "portfolio_assessment": self._assess_overall_portfolio(metrics),
            "key_risks": self._identify_key_risks(analyses, metrics),
            "opportunities": self._identify_opportunities(analyses, portfolio),
            "rebalancing_needs": self._assess_rebalancing_needs(
                metrics, recommendations
            ),
            "future_actions": self._suggest_future_actions(
                analyses, metrics, portfolio
            ),
        }

    # ================================================================
    # Helpers
    # ================================================================



    def _extract_summary(self, final_state: Dict[str, Any]) -> str:
        decision = final_state.get("final_trade_decision", "")
        return decision[:200] + "..." if len(decision) > 200 else decision

    def _estimate_sector_allocation(self, positions: List[Dict]) -> Dict[str, float]:
        return {
            "Technology": 40.0,
            "Finance": 25.0,
            "Healthcare": 20.0,
            "Other": 15.0,
        }

    def _assess_portfolio_health(
        self,
        max_position_pct: float,
        avg_conviction: float,
        winners: int,
        losers: int,
    ) -> str:
        if max_position_pct > 20:
            return "ATTENTION: Over-concentrated"
        if avg_conviction < 50:
            return "CAUTION: Low average conviction"
        if winners < losers:
            return "CAUTION: More losers than winners"
        if avg_conviction > 70 and winners > losers:
            return "HEALTHY: Strong positions"
        return "FAIR: Mixed signals"

    def _build_rationale(
        self, analysis: Dict[str, Any], metrics: Dict[str, Any]
    ) -> str:
        decision = analysis["decision"]
        conviction = analysis["conviction_score"]
        pl_pct = analysis.get("unrealized_plpc", 0) * 100
        parts: list[str] = []

        if analysis.get("triaged_out"):
            parts.append(
                f"Skipped during triage (HOLD by default). "
                f"Triage note: {analysis.get('analysis_summary', '')}"
            )
        elif decision == "SELL":
            parts.append(f"Agent recommends SELL with {conviction}% conviction.")
            if pl_pct < -10:
                parts.append(f"Position down {abs(pl_pct):.1f}%, cutting losses.")
            elif pl_pct > 20:
                parts.append(f"Position up {pl_pct:.1f}%, taking profits.")
        elif decision == "BUY":
            parts.append(f"Agent recommends BUY with {conviction}% conviction.")
            if pl_pct > 0:
                parts.append(f"Position up {pl_pct:.1f}%, adding to winner.")
            else:
                parts.append("Opportunity to average up position.")
        else:
            parts.append(f"Agent recommends HOLD ({conviction}% conviction).")

        return " ".join(parts)

    def _calculate_priority(
        self, decision: str, conviction: float, position_pct: float
    ) -> float:
        priority = conviction
        if decision == "SELL" and position_pct > 15:
            priority += 20
        if position_pct < 5:
            priority -= 10
        return max(0, min(100, priority))

    def _assess_overall_portfolio(self, metrics: Dict[str, Any]) -> str:
        health = metrics["portfolio_health"]
        sell_signals = metrics["sell_signals"]
        if "HEALTHY" in health:
            return "Portfolio is in good shape with strong positions."
        if sell_signals > metrics["position_count"] / 2:
            return (
                f"CAUTION: {sell_signals} positions showing SELL signals — "
                "consider rebalancing."
            )
        return f"Portfolio status: {health}. Monitor positions closely."

    def _identify_key_risks(
        self, analyses: List[Dict], metrics: Dict[str, Any]
    ) -> List[str]:
        risks: list[str] = []
        if metrics["max_position_pct"] > 20:
            risks.append(
                f"Over-concentration: Largest position is "
                f"{metrics['max_position_pct']:.1f}% of portfolio (>20% threshold)"
            )
        high_loss = [a for a in analyses if a.get("unrealized_plpc", 0) < -0.15]
        if high_loss:
            tickers = [a["ticker"] for a in high_loss]
            risks.append(
                f"Significant losses in {len(tickers)} positions: {', '.join(tickers)}"
            )
        if metrics["avg_conviction"] < 50:
            risks.append(
                f"Low average conviction ({metrics['avg_conviction']:.1f}) "
                "suggests weak portfolio positioning"
            )
        return risks or ["No major risks identified"]

    def _identify_opportunities(
        self, analyses: List[Dict], portfolio: Dict[str, Any]
    ) -> List[str]:
        opportunities: list[str] = []
        strong_buys = [
            a
            for a in analyses
            if a.get("decision") == "BUY" and a.get("conviction_score", 0) > 70
        ]
        if strong_buys:
            tickers = [a["ticker"] for a in strong_buys]
            opportunities.append(
                f"Strong BUY signals in existing positions: {', '.join(tickers)}"
            )
        turnarounds = [
            a
            for a in analyses
            if (
                a.get("unrealized_plpc", 0) < 0
                and a.get("decision") == "BUY"
                and a.get("conviction_score", 0) > 60
            )
        ]
        if turnarounds:
            tickers = [a["ticker"] for a in turnarounds]
            opportunities.append(
                f"Potential turnaround opportunities: {', '.join(tickers)}"
            )
        cash_pct = (portfolio["cash"] / portfolio["account_value"]) * 100
        if cash_pct > 10:
            opportunities.append(
                f"{cash_pct:.1f}% cash available for deployment in high-conviction ideas"
            )
        return opportunities or ["No immediate opportunities identified"]

    def _assess_rebalancing_needs(
        self, metrics: Dict[str, Any], recommendations: List[Dict]
    ) -> str:
        high_priority = sum(1 for r in recommendations if r["priority"] > 70)
        if high_priority >= 3:
            return f"URGENT: {high_priority} high-priority actions needed"
        if metrics["sell_signals"] > 2:
            return f"MODERATE: {metrics['sell_signals']} positions need attention"
        return "MINIMAL: Portfolio is relatively well-balanced"

    def _suggest_future_actions(
        self,
        analyses: List[Dict],
        metrics: Dict[str, Any],
        portfolio: Dict[str, Any],
    ) -> List[str]:
        suggestions: list[str] = []
        if metrics["position_count"] < 8:
            suggestions.append(
                "Consider adding 2-3 new positions to improve diversification"
            )
        if metrics["max_position_pct"] > 20:
            suggestions.append(
                "Reduce largest position to improve risk distribution"
            )
        cash_pct = (portfolio["cash"] / portfolio["account_value"]) * 100
        if cash_pct < 5:
            suggestions.append(
                "Consider raising cash (currently <5%) for flexibility"
            )
        elif cash_pct > 20:
            suggestions.append(
                f"Deploy excess cash ({cash_pct:.1f}%) into high-conviction opportunities"
            )
        low_conviction = [
            a for a in analyses if a.get("conviction_score", 0) < 40
        ]
        if low_conviction:
            tickers = [a["ticker"] for a in low_conviction]
            suggestions.append(
                f"Monitor low-conviction positions closely: {', '.join(tickers)}"
            )
        suggestions.append(
            "Schedule monthly portfolio review to maintain optimal allocation"
        )
        return suggestions

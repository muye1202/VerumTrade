from typing import List, Dict, Any, Optional
from datetime import datetime
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.execution import AlpacaExecutor
import logging


class BatchAnalyzer:
    """
    Analyzes multiple stocks and ranks them by opportunity quality.
    """
    
    def __init__(
        self,
        graph: TradingAgentsGraph,
        executor: Optional[AlpacaExecutor] = None
    ):
        self.graph = graph
        self.executor = executor
        self.logger = logging.getLogger("BatchAnalyzer")
    
    def analyze_candidates(
        self,
        tickers: List[str],
        trade_date: str,
        max_positions: int = 3,
        time_horizon: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Analyze multiple ticker candidates and rank them.
        
        Args:
            tickers: List of ticker symbols to analyze
            trade_date: Date for analysis
            max_positions: Maximum number of positions to take
            
        Returns:
            List of analysis results, ranked by conviction
        """
        results = []
        
        self.logger.info(f"Analyzing {len(tickers)} candidates: {tickers}")
        
        for ticker in tickers:
            try:
                self.logger.info(f"Analyzing {ticker}...")
                
                # Run full analysis
                final_state, decision = self.graph.propagate(
                    ticker, trade_date, time_horizon=time_horizon
                )
                
                # Calculate conviction score
                conviction_score = self._calculate_conviction(final_state, decision)
                
                results.append({
                    "ticker": ticker,
                    "decision": decision,
                    "conviction_score": conviction_score,
                    "final_state": final_state,
                    "market_report": final_state.get("market_report", ""),
                    "fundamentals_report": final_state.get("fundamentals_report", ""),
                    "news_report": final_state.get("news_report", ""),
                    "final_decision": final_state.get("final_trade_decision", ""),
                })
                
            except Exception as e:
                self.logger.exception(f"Error analyzing {ticker}: {e}")
                continue
        
        # Rank by conviction score
        results.sort(key=lambda x: x["conviction_score"], reverse=True)
        
        # Filter to BUY signals only
        buy_signals = [r for r in results if r["decision"] == "BUY"]
        
        # Take top N positions
        top_picks = buy_signals[:max_positions]
        
        self.logger.info(
            f"Analysis complete. {len(buy_signals)} BUY signals, "
            f"taking top {len(top_picks)} positions"
        )
        
        return top_picks
    
    def _calculate_conviction(
        self,
        final_state: Dict[str, Any],
        decision: str
    ) -> float:
        """
        Calculate a conviction score (0-100) based on analysis quality.
        
        Factors:
        - Signal strength from final decision text
        - Consensus among analysts (bull vs bear debate)
        - Risk assessment alignment
        - Number of positive indicators
        """
        score = 0.0
        
        # Base score by decision
        if decision == "BUY":
            score = 60
        elif decision == "SELL":
            score = 40
        else:  # HOLD
            score = 50
        
        # Analyze final decision text for confidence markers
        final_text = final_state.get("final_trade_decision", "").lower()
        
        # Positive conviction markers
        if any(word in final_text for word in ["strong", "compelling", "excellent", "outstanding"]):
            score += 10
        if any(word in final_text for word in ["high confidence", "strongly recommend", "clear opportunity"]):
            score += 10
        
        # Negative conviction markers
        if any(word in final_text for word in ["uncertain", "mixed", "unclear", "cautious"]):
            score -= 10
        if any(word in final_text for word in ["weak", "concerning", "risky"]):
            score -= 10
        
        # Check debate consensus
        if "investment_debate_state" in final_state:
            debate = final_state["investment_debate_state"]
            judge_decision = debate.get("judge_decision", "").lower()
            
            # Check if judge strongly sided with one view
            if decision == "BUY" and "bull" in judge_decision and "strong" in judge_decision:
                score += 5
            elif decision == "SELL" and "bear" in judge_decision and "strong" in judge_decision:
                score += 5
        
        # Clamp to 0-100
        return max(0, min(100, score))
    
    def execute_portfolio(
        self,
        top_picks: List[Dict[str, Any]],
        trade_date: str
    ) -> List[Dict[str, Any]]:
        """
        Execute trades for top picks.
        
        Args:
            top_picks: Ranked list of stocks to trade
            trade_date: Trading date
            
        Returns:
            List of execution results
        """
        if not self.executor:
            self.logger.warning("No executor configured, skipping execution")
            return []
        
        execution_results = []

        for pick in top_picks:
            try:
                structured = self.graph.extract_structured_decision(
                    pick.get("final_decision")
                    or pick.get("final_state", {}).get("final_trade_decision", "")
                )
                result = self.executor.execute_signal(
                    ticker=pick["ticker"],
                    signal=pick["decision"],
                    analysis_state=pick["final_state"],
                    trade_date=trade_date,
                    agent_quantity=structured.get("quantity"),
                    agent_limit_price=structured.get("limit_price"),
                    agent_position_size_pct=structured.get("position_size_pct"),
                    agent_order_type=structured.get("order_type"),
                    agent_time_in_force=structured.get("time_in_force"),
                    agent_stop_price=structured.get("stop_price"),
                    agent_trail_percent=structured.get("trail_percent"),
                    agent_trail_price=structured.get("trail_price"),
                    agent_stop_loss=structured.get("stop_loss"),
                    agent_take_profit=structured.get("take_profit"),
                )

                execution_results.append({**pick, "execution": result})

            except Exception as e:
                self.logger.error(f"Execution failed for {pick['ticker']}: {e}")
                execution_results.append({**pick, "execution": {"error": str(e)}})
        
        return execution_results

# tradingagents/graph/stock_discovery.py
"""
Stock Discovery Graph: Orchestrates the stock recommendation pipeline
and integrates with BatchAnalyzer for deep analysis of top picks.
"""

import os
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

from langchain_openai import ChatOpenAI

from tradingagents.agents.discovery.stock_recommender import (
    StockRecommenderAgent,
    create_stock_recommender,
)
from tradingagents.default_config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Result from stock discovery process."""
    tickers: List[str]
    report: str
    trade_date: str
    success: bool
    error: Optional[str] = None
    iterations: int = 0


class StockDiscoveryGraph:
    """
    Orchestrates stock discovery using LLM-based recommendation.
    
    This is a lightweight graph that runs the StockRecommenderAgent
    and returns discovered stock candidates for further analysis.
    """
    
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        debug: bool = False,
    ):
        """
        Initialize the discovery graph.

        Args:
            config: Configuration dict (same format as TradingAgentsGraph)
            debug: Enable debug logging
        """
        self.config = config or DEFAULT_CONFIG.copy()
        self.debug = debug
        self.logger = logging.getLogger(self.__class__.__name__)

        if debug:
            logging.basicConfig(level=logging.DEBUG)

        # Initialize the deep-think LLM for discovery
        self.llm = self._create_llm()

        # Create the recommender agent
        self.recommender = create_stock_recommender(
            llm=self.llm,
            enable_web_search=self._supports_web_search(),
        )

    def _create_llm(self) -> ChatOpenAI:
        """Create the LLM instance based on config."""
        provider = self.config.get("llm_provider", "openai").lower()
        model = self.config.get("deep_think_llm", "gpt-4o")
        backend_url = self.config.get("backend_url")

        # Determine API key and base URL based on provider
        api_key = None
        base_url = backend_url

        if provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY")
            base_url = base_url or "https://api.openai.com/v1"
        elif provider == "glm":
            api_key = (
                os.getenv("ZHIPUAI_API_KEY") 
                or os.getenv("GLM_API_KEY")
            )
            base_url = base_url or "https://open.bigmodel.cn/api/paas/v4"
        elif provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY")
            base_url = base_url or "https://api.deepseek.com"
        elif provider == "qwen3-cn":
            api_key = os.getenv("DASHSCOPE_API_KEY")
            base_url = base_url or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        elif provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY")
            base_url = base_url or "https://openrouter.ai/api/v1"
        elif provider == "ollama":
            api_key = "ollama"  # Ollama doesn't require API key
            base_url = base_url or "http://localhost:11434/v1"
        else:
            # Generic OpenAI-compatible
            api_key = os.getenv("OPENAI_API_KEY")

        # Use appropriate LLM class based on provider
        if provider == "glm":
            from tradingagents.graph.trading_graph import GLMFlashSerialChatOpenAI, GLMCompatibleChatOpenAI

            # Use serialized class for glm-4.7-flash to avoid rate limits
            if model == "glm-4.7-flash":
                llm_cls = GLMFlashSerialChatOpenAI
            else:
                llm_cls = GLMCompatibleChatOpenAI

            return llm_cls(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0.7,
            )
        else:
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0.7,
            )

    def _supports_web_search(self) -> bool:
        """Check if the configured LLM provider supports web search."""
        provider = self.config.get("llm_provider", "openai").lower()
        # GLM and OpenAI support web search natively or via tools
        return provider in {"glm", "openai"}

    def run_discovery(
        self,
        trade_date: Optional[str] = None,
    ) -> DiscoveryResult:
        """
        Run the stock discovery process.

        Args:
            trade_date: Target date (defaults to today)

        Returns:
            DiscoveryResult with recommended tickers and report
        """
        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        self.logger.info(f"Starting stock discovery for {trade_date}")

        try:
            result = self.recommender.recommend(
                trade_date=trade_date,
                max_iterations=5,
            )

            self.logger.info(f"Discovery complete. Found {len(result['tickers'])} recommendations")

            return DiscoveryResult(
                tickers=result["tickers"],
                report=result["report"],
                trade_date=trade_date,
                success=True,
                iterations=result.get("iterations", 0),
            )

        except Exception as e:
            self.logger.error(f"Discovery failed: {e}")
            return DiscoveryResult(
                tickers=[],
                report=f"Discovery failed: {str(e)}",
                trade_date=trade_date,
                success=False,
                error=str(e),
            )

    def discover_and_analyze(
        self,
        trade_date: Optional[str] = None,
        max_deep_analysis: int = 3,
        executor=None,
    ) -> Dict[str, Any]:
        """
        Run discovery and then deep analysis on top picks.

        This integrates with BatchAnalyzer to run full TradingAgentsGraph
        analysis on the discovered stocks.

        Args:
            trade_date: Target date
            max_deep_analysis: How many stocks to analyze deeply
            executor: Optional AlpacaExecutor for trade execution

        Returns:
            Dict with discovery results and analysis results
        """
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.graph.batch_analysis import BatchAnalyzer

        # Step 1: Discovery
        discovery_result = self.run_discovery(trade_date)

        if not discovery_result.success or not discovery_result.tickers:
            return {
                "discovery": discovery_result,
                "analysis_results": [],
                "error": discovery_result.error or "No stocks discovered",
            }

        trade_date = discovery_result.trade_date
        tickers_to_analyze = discovery_result.tickers[:max_deep_analysis]

        # Step 2: Deep Analysis using full TradingAgentsGraph
        self.logger.info(f"Running deep analysis on: {tickers_to_analyze}")

        graph = TradingAgentsGraph(
            config=self.config,
            debug=self.debug,
        )

        batch_analyzer = BatchAnalyzer(
            graph=graph,
            executor=executor,
        )

        analysis_results = batch_analyzer.analyze_candidates(
            tickers=tickers_to_analyze,
            trade_date=trade_date,
            max_positions=max_deep_analysis,
        )

        return {
            "discovery": discovery_result,
            "analysis_results": analysis_results,
            "analyzed_tickers": [r["ticker"] for r in analysis_results],
        }

# opentrace/graph/stock_discovery.py
"""
Stock Discovery Graph: Orchestrates the stock recommendation pipeline
and integrates with BatchAnalyzer for deep analysis of top picks.
"""

import logging
import inspect
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime
from opentrace.utils.market_session import now_et

from langchain_openai import ChatOpenAI

from opentrace.agents.discovery.intelligence_integration import (
    IntelligenceDrivenRecommender,
)
from opentrace.default_config import DEFAULT_CONFIG
from opentrace.graph.provider_settings import azure_foundry_reasoning_mode, resolve_llm_endpoint

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
    metadata: Optional[Dict[str, Any]] = None


class StockDiscoveryGraph:
    """
    Orchestrates stock discovery using the prefilter + technical pipeline.

    This is a lightweight graph that runs IntelligenceDrivenRecommender
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
            config: Configuration dict (same format as OpenTraceGraph)
            debug: Enable debug logging
        """
        self.config = config or DEFAULT_CONFIG.copy()
        self.debug = debug
        self.logger = logging.getLogger(self.__class__.__name__)

        if debug:
            logging.basicConfig(level=logging.DEBUG)

        # Initialize discovery LLMs with the same model split as analysis mode:
        # quick_think_llm for Stage 1 scanners, deep_think_llm for Stage 2 synthesis.
        self.deep_llm = self._create_llm(self.config.get("deep_think_llm", "gpt-4o"))
        self.quick_llm = self._create_llm(self.config.get("quick_think_llm", "gpt-4o-mini"))
        # Backward-compatible alias used by older patching code.
        self.llm = self.deep_llm

        # Create the recommender agent using new intelligence architecture
        self.recommender = IntelligenceDrivenRecommender(
            deep_llm=self.deep_llm,
            quick_llm=self.quick_llm,
            config=self.config,
            screening_universe=self.config.get("screening_universe"),
        )

    def _create_llm(self, model: str) -> ChatOpenAI:
        """Create the LLM instance based on config."""
        provider = self.config.get("llm_provider", "openai").lower()
        endpoint = resolve_llm_endpoint(provider, self.config)
        api_key = endpoint.get("api_key")
        base_url = endpoint.get("base_url")

        def _with_extra_params(kwargs: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
            if not extra:
                return kwargs
            # Prefer explicit extra_body whenever supported to avoid model_kwargs warnings.
            if "extra_body" in getattr(ChatOpenAI, "model_fields", {}):
                kwargs["extra_body"] = extra
                return kwargs
            try:
                params = inspect.signature(ChatOpenAI.__init__).parameters
                if "extra_body" in params:
                    kwargs["extra_body"] = extra
                    return kwargs
            except Exception:
                pass
            mk = kwargs.get("model_kwargs") or {}
            mk["extra_body"] = extra
            kwargs["model_kwargs"] = mk
            return kwargs

        def _openrouter_extra_for_model(model_name: str) -> Dict[str, Any]:
            name = (model_name or "").strip().lower()
            if not name:
                return {}
            if name == "openrouter/aurora-alpha":
                return {"reasoning": {"enabled": True}}
            if "thinking" in name:
                return {"reasoning": {"enabled": True}}
            return {}

        def _with_reasoning_effort(kwargs: Dict[str, Any], effort: Optional[str]) -> Dict[str, Any]:
            effort = (effort or "").strip().lower()
            if effort not in {"low", "medium", "high"}:
                return kwargs
            if "reasoning_effort" in getattr(ChatOpenAI, "model_fields", {}):
                kwargs["reasoning_effort"] = effort
                return kwargs
            mk = kwargs.get("model_kwargs") or {}
            mk["reasoning_effort"] = effort
            kwargs["model_kwargs"] = mk
            return kwargs

        azure_foundry_reasoning_effort = None
        if (
            provider == "azure-foundry"
            and self.config.get("azure_foundry_enable_thinking")
            and azure_foundry_reasoning_mode(model) == "effort"
        ):
            azure_foundry_reasoning_effort = self.config.get(
                "azure_foundry_reasoning_effort", "medium"
            )

        # Use appropriate LLM class based on provider
        if provider == "glm":
            from opentrace.graph.opentrace_graph import GLMFlashSerialChatOpenAI, GLMCompatibleChatOpenAI

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
        elif provider == "openrouter":
            from opentrace.graph.opentrace_graph import OpenRouterCompatibleChatOpenAI

            return OpenRouterCompatibleChatOpenAI(
                model=model,
                temperature=0.7,
                **_with_extra_params(
                    {
                        "api_key": api_key,
                        "base_url": base_url,
                    },
                    _openrouter_extra_for_model(model),
                ),
            )
        else:
            llm_kwargs = {
                "api_key": api_key,
                "base_url": base_url,
            }
            if not azure_foundry_reasoning_effort:
                llm_kwargs["temperature"] = 0.7
            return ChatOpenAI(
                model=model,
                **_with_reasoning_effort(
                    llm_kwargs,
                    azure_foundry_reasoning_effort,
                ),
            )

    def _supports_web_search(self) -> bool:
        """Check if the configured LLM provider supports web search."""
        provider = self.config.get("llm_provider", "openai").lower()
        # GLM and OpenAI support web search natively or via tools
        return provider in {"glm", "openai", "azure-foundry"}

    def run_discovery(
        self,
        trade_date: Optional[str] = None,
        exclude_tickers: Optional[List[str]] = None,
        discovery_track: str = "enricher",
    ) -> DiscoveryResult:
        """
        Run the stock discovery process.

        Args:
            trade_date: Target date (defaults to today)
            exclude_tickers: Optional list of symbols to exclude from recommendations
            discovery_track: ``"enricher"`` for Stage 1→2 pipeline,
                ``"anomaly_scan"`` for Track B momentum anomaly scans.

        Returns:
            DiscoveryResult with recommended tickers and report
        """
        if trade_date is None:
            trade_date = now_et().strftime("%Y-%m-%d")

        self.logger.info(f"Starting stock discovery for {trade_date}")

        try:
            result = self.recommender.recommend(
                trade_date=trade_date,
                max_iterations=5,
                excluded_tickers=exclude_tickers,
                discovery_track=discovery_track,
            )

            self.logger.info(f"Discovery complete. Found {len(result['tickers'])} recommendations")

            # Extract theme_candidates from the IntelligenceResult so the API
            # layer can stream them without re-running ThemeScanner.
            _intelligence = result.get("intelligence")
            _theme_cands = []
            if _intelligence is not None and hasattr(_intelligence, "theme_candidates"):
                try:
                    _theme_cands = [c.to_dict() for c in (_intelligence.theme_candidates or [])]
                except Exception:
                    pass

            return DiscoveryResult(
                tickers=result["tickers"],
                report=result["report"],
                trade_date=trade_date,
                success=True,
                iterations=result.get("iterations", 0),
                metadata={
                    "stage0": result.get("stage0", {}),
                    "stage1": result.get("stage1", {}),
                    "stage2": result.get("stage2", {}),
                    "vendor_calls_by_stage": result.get("vendor_calls_by_stage", {}),
                    "data_quality_summary": result.get("data_quality_summary", {}),
                    "filter_relaxations_applied": result.get("filter_relaxations_applied", []),
                    "theme_candidates": _theme_cands,
                    "business_inflection": result.get("business_inflection", {}),
                    "attention_gap": result.get("attention_gap", {}),
                    "evidence_packs": result.get("evidence_packs", {}),
                    "two_layer_scoring": result.get("two_layer_scoring", {}),
                    "thesis_cards": result.get("thesis_cards", {}),
                },
            )

        except Exception as e:
            self.logger.error(f"Discovery failed: {e}")
            return DiscoveryResult(
                tickers=[],
                report=f"Discovery failed: {str(e)}",
                trade_date=trade_date,
                success=False,
                error=str(e),
                metadata=None,
            )

    def discover_and_analyze(
        self,
        trade_date: Optional[str] = None,
        max_deep_analysis: int = 3,
        executor=None,
    ) -> Dict[str, Any]:
        """
        Run discovery and then deep analysis on top picks.

        This integrates with BatchAnalyzer to run full OpenTraceGraph
        analysis on the discovered stocks.

        Args:
            trade_date: Target date
            max_deep_analysis: How many stocks to analyze deeply
            executor: Optional AlpacaExecutor for trade execution

        Returns:
            Dict with discovery results and analysis results
        """
        from opentrace.graph.opentrace_graph import OpenTraceGraph
        from opentrace.graph.batch_analysis import BatchAnalyzer

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

        # Step 2: Deep Analysis using full OpenTraceGraph
        self.logger.info(f"Running deep analysis on: {tickers_to_analyze}")

        graph = OpenTraceGraph(
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

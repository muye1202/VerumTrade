# TradingAgents/graph/trading_graph.py

import os
import inspect
from pathlib import Path
import json
from contextlib import nullcontext
from typing import Dict, Any, Tuple, List, Optional
import asyncio

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import ToolNode

from typing import Optional
from tradingagents.execution import AlpacaExecutor
from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory.memory import FinancialSituationMemory
from tradingagents.execution.portfolio_context import fetch_portfolio_context
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_runtime.agent_utils import (
    get_stock_data,
    get_indicators,
    get_price_action_summary,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_company_news_window,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news
)
from tradingagents.agents.utils.market_data.vwap_tools import (
    get_intraday_vwap_position,
    get_multi_day_vwap_context,
)
from tradingagents.agents.utils.market_data.options_flow_tools import (
    get_unusual_options_activity,
    get_options_sentiment_summary,
)
from tradingagents.agents.utils.market_data.dark_pool_tools import (
    get_dark_pool_short_volume,
    get_off_exchange_volume_context,
)
from tradingagents.agents.utils.market_data.short_interest_tools import (
    get_short_interest_data,
    get_squeeze_candidates_assessment,
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor
from .openai_compat import sanitize_openai_compatible_response_dict
from .glm_compat import sanitize_glm_chat_completion_response_dict
from tradingagents.agents.utils.llm.llm_concurrency import llm_inflight_slot

class StreamCompatibleChatOpenAI(ChatOpenAI):
    """Handle OpenAI SDK Stream responses by aggregating them into a single ChatCompletion-like dict."""

    def _create_chat_result(self, response, generation_info=None):
        # Convert to a plain dict so we can safely normalize provider quirks before LangChain parses it.
        response_dict = None
        if response is not None and response.__class__.__name__ == "Stream":
            response_dict = self._stream_to_response_dict(response)
        elif isinstance(response, dict):
            response_dict = response
        else:
            try:
                response_dict = response.model_dump()
            except Exception:
                response_dict = None

        if isinstance(response_dict, dict):
            sanitize_openai_compatible_response_dict(response_dict)
            return super()._create_chat_result(response_dict, generation_info=generation_info)

        return super()._create_chat_result(response, generation_info=generation_info)

    @staticmethod
    def _stream_to_response_dict(stream):
        content_parts = []
        tool_calls = {}
        model_name = None
        for chunk in stream:
            model_name = getattr(chunk, "model", model_name)
            for choice in getattr(chunk, "choices", []):
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue
                text = getattr(delta, "content", None)
                if text:
                    content_parts.append(text)

                delta_tool_calls = getattr(delta, "tool_calls", None)
                if delta_tool_calls:
                    for tc in delta_tool_calls:
                        idx = getattr(tc, "index", 0) or 0
                        entry = tool_calls.setdefault(
                            idx,
                            {
                                "id": getattr(tc, "id", None),
                                "type": getattr(tc, "type", "function"),
                                "function": {"name": None, "arguments": ""},
                            },
                        )
                        tc_func = getattr(tc, "function", None)
                        if tc_func is not None:
                            name = getattr(tc_func, "name", None)
                            args = getattr(tc_func, "arguments", None)
                            if name:
                                entry["function"]["name"] = name
                            if args:
                                entry["function"]["arguments"] += args

        message = {"role": "assistant", "content": "".join(content_parts)}
        if tool_calls:
            message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]

        response_dict = {"choices": [{"message": message}]}
        if model_name:
            response_dict["model"] = model_name
        return response_dict


class DeepSeekCompatibleChatOpenAI(ChatOpenAI):
    """Sanitize DeepSeek-specific fields (e.g., reasoning_content) for older langchain_openai parsers."""

    def _create_chat_result(self, response, generation_info=None):
        # Convert to a plain dict so we can safely mutate before LangChain parses it.
        response_dict = None
        if isinstance(response, dict):
            response_dict = response
        else:
            try:
                response_dict = response.model_dump()
            except Exception:
                response_dict = None

        if response_dict and isinstance(response_dict, dict):
            sanitize_openai_compatible_response_dict(response_dict)
            return super()._create_chat_result(response_dict, generation_info=generation_info)

        return super()._create_chat_result(response, generation_info=generation_info)

class GLMCompatibleChatOpenAI(ChatOpenAI):
    """Sanitize GLM (ZhipuAI) OpenAI-compatible responses for LangChain parsing."""

    def _create_chat_result(self, response, generation_info=None):
        response_dict = None
        if isinstance(response, dict):
            response_dict = response
        else:
            try:
                response_dict = response.model_dump()
            except Exception:
                response_dict = None

        if response_dict and isinstance(response_dict, dict):
            sanitize_glm_chat_completion_response_dict(response_dict)
            # Apply general OpenAI-compatible sanitizers too (e.g., double-encoded tool args).
            sanitize_openai_compatible_response_dict(response_dict)
            return super()._create_chat_result(response_dict, generation_info=generation_info)

        return super()._create_chat_result(response, generation_info=generation_info)

class GLMFlashSerialChatOpenAI(GLMCompatibleChatOpenAI):
    """Serialize in-flight requests for GLM-4.7-Flash (no parallelism)."""

    def _ta_concurrency_slot(self):
        key = getattr(self, "_ta_llm_concurrency_key", None)
        if not key:
            return nullcontext()
        return llm_inflight_slot(key, 1)

    # Guard the internal generation methods instead of `.invoke()` so that *all*
    # LangChain execution paths (invoke/batch/tool-binding/stream) are serialized.
    def _generate(self, *args, **kwargs):
        with self._ta_concurrency_slot():
            return super()._generate(*args, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        with self._ta_concurrency_slot():
            return await super()._agenerate(*args, **kwargs)

    def _stream(self, *args, **kwargs):
        with self._ta_concurrency_slot():
            yield from super()._stream(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        with self._ta_concurrency_slot():
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk


class TradingAgentsGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config: Dict[str, Any] = None,
    ):
        """Initialize the trading agents graph and components.

        Args:
            selected_analysts: List of analyst types to include
            debug: Whether to run in debug mode
            config: Configuration dictionary. If None, uses default config
        """
        self.debug = debug
        self.config = config or DEFAULT_CONFIG

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs
        if self.config["llm_provider"].lower() in {"openai", "ollama", "openrouter", "qwen3-cn", "deepseek", "glm"}:
            openai_kwargs = {"base_url": self.config["backend_url"]}
            if self.config["llm_provider"].lower() == "qwen3-cn":
                openai_kwargs["api_key"] = os.getenv("DASHSCOPE_API_KEY")
            if self.config["llm_provider"].lower() == "deepseek":
                openai_kwargs["api_key"] = os.getenv("DEEPSEEK_API_KEY")
            if self.config["llm_provider"].lower() == "openrouter":
                openai_kwargs["api_key"] = os.getenv("OPENROUTER_API_KEY")
            if self.config["llm_provider"].lower() == "glm":
                openai_kwargs["api_key"] = (
                    os.getenv("ZHIPUAI_API_KEY")
                    or os.getenv("GLM_API_KEY")
                    or os.getenv("OPENAI_API_KEY")
                )

            # Provider-specific parameters for OpenAI-compatible backends.
            # DashScope supports "enable_thinking" (reasoning mode) for select Qwen models.
            def _with_extra_params(kwargs: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
                if not extra:
                    return kwargs
                try:
                    params = inspect.signature(ChatOpenAI.__init__).parameters
                    if "extra_body" in params:
                        kwargs["extra_body"] = extra
                        return kwargs
                except Exception:
                    # Fall through to safe behavior below.
                    pass

                # Compatibility fallback: when ChatOpenAI.__init__ has no `extra_body`,
                # pass it via model_kwargs so OpenAI SDK receives `extra_body={...}`.
                # Avoid putting provider fields (e.g., enable_thinking) directly under
                # model_kwargs because some versions forward them as top-level kwargs.
                mk = kwargs.get("model_kwargs") or {}
                mk["extra_body"] = extra
                kwargs["model_kwargs"] = mk
                if self.debug:
                    print("INFO: ChatOpenAI doesn't expose extra_body in __init__; using model_kwargs.extra_body:", extra)
                return kwargs

            def _merge_extra_params(*extras: Dict[str, Any]) -> Dict[str, Any]:
                merged: Dict[str, Any] = {}
                for extra in extras:
                    if extra:
                        merged.update(extra)
                return merged

            def _qwen_supports_thinking(model_name: str) -> bool:
                name = (model_name or "").lower()
                # Conservative allowlist: these are the families commonly associated with thinking/reasoning mode.
                if name.startswith("qwen3-max"):
                    return True
                if "thinking" in name:
                    return True
                if name.startswith("qwq"):
                    return True
                return False

            def _qwen_requires_stream(model_name: str) -> bool:
                """Some DashScope models require stream mode regardless of thinking flags."""
                name = (model_name or "").lower()
                # Seen in the wild: qwq-* endpoints can be stream-only.
                if name.startswith("qwq"):
                    return True
                # Thinking variants are commonly streamed (and some vendors enforce it).
                if "thinking" in name:
                    return True
                return False

            def _with_streaming(kwargs: Dict[str, Any], enable: bool) -> Dict[str, Any]:
                """Enable streaming mode when supported by the installed langchain_openai version."""
                if not enable:
                    return kwargs
                try:
                    params = inspect.signature(ChatOpenAI.__init__).parameters
                    if "streaming" in params:
                        kwargs["streaming"] = True
                        return kwargs
                    if "stream" in params:
                        kwargs["stream"] = True
                        return kwargs
                except Exception:
                    pass
                # Fallback: inject stream=True via model_kwargs so it reaches OpenAI SDK.
                mk = kwargs.get("model_kwargs") or {}
                mk["stream"] = True
                kwargs["model_kwargs"] = mk
                return kwargs

            # Some DashScope reasoning-capable models require stream mode when thinking is enabled.
            deep_streaming = bool(
                self.config.get("llm_provider", "").lower() == "qwen3-cn"
                and (
                    (
                        self.config.get("qwen_enable_thinking")
                        and _qwen_supports_thinking(self.config.get("deep_think_llm", ""))
                    )
                    or _qwen_requires_stream(self.config.get("deep_think_llm", ""))
                )
            )
            qwen_extra: Dict[str, Any] = {}
            if self.config["llm_provider"].lower() == "qwen3-cn":
                deep_supports_thinking = _qwen_supports_thinking(self.config.get("deep_think_llm", ""))
                deep_enable_thinking = bool(
                    self.config.get("qwen_enable_thinking")
                    and deep_supports_thinking
                    and deep_streaming
                )
                # DashScope requires enable_thinking=false for non-streaming calls.
                qwen_extra["enable_thinking"] = deep_enable_thinking
                if deep_enable_thinking and self.config.get("qwen_thinking_budget") is not None:
                    qwen_extra["thinking_budget"] = int(self.config["qwen_thinking_budget"])

            openrouter_deep_extra: Dict[str, Any] = {}
            if (
                self.config["llm_provider"].lower() == "openrouter"
                and (self.config.get("deep_think_llm") or "") == "openrouter/aurora-alpha"
            ):
                openrouter_deep_extra["reasoning"] = {"enabled": True}

            provider = self.config["llm_provider"].lower()

            if provider == "qwen3-cn":
                base_llm_cls = StreamCompatibleChatOpenAI
            elif provider == "deepseek":
                base_llm_cls = DeepSeekCompatibleChatOpenAI
            elif provider == "glm":
                base_llm_cls = GLMCompatibleChatOpenAI
            else:
                base_llm_cls = ChatOpenAI

            deep_llm_cls = base_llm_cls
            quick_llm_cls = base_llm_cls

            # GLM-4.7-Flash: enforce no parallelism by serializing in-flight requests.
            if provider == "glm":
                if (self.config.get("deep_think_llm") or "") == "glm-4.7-flash":
                    deep_llm_cls = GLMFlashSerialChatOpenAI
                if (self.config.get("quick_think_llm") or "") == "glm-4.7-flash":
                    quick_llm_cls = GLMFlashSerialChatOpenAI

            self.deep_thinking_llm = deep_llm_cls(
                model=self.config["deep_think_llm"],
                **_with_streaming(
                    _with_extra_params(
                        openai_kwargs.copy(),
                        _merge_extra_params(qwen_extra, openrouter_deep_extra),
                    ),
                    deep_streaming,
                ),
            )
            if provider == "glm" and deep_llm_cls is GLMFlashSerialChatOpenAI:
                self.deep_thinking_llm._ta_llm_concurrency_key = (
                    f"llm:{provider}:{str(self.config.get('backend_url') or '').rstrip('/')}:{self.config.get('deep_think_llm')}".lower()
                )

            quick_streaming = bool(
                self.config.get("llm_provider", "").lower() == "qwen3-cn"
                and (
                    (
                        self.config.get("qwen_enable_thinking_quick")
                        and _qwen_supports_thinking(self.config.get("quick_think_llm", ""))
                    )
                    or _qwen_requires_stream(self.config.get("quick_think_llm", ""))
                )
            )
            qwen_quick_extra: Dict[str, Any] = {}
            if self.config.get("llm_provider", "").lower() == "qwen3-cn":
                quick_supports_thinking = _qwen_supports_thinking(self.config.get("quick_think_llm", ""))
                quick_enable_thinking = bool(
                    self.config.get("qwen_enable_thinking_quick")
                    and quick_supports_thinking
                    and quick_streaming
                )
                qwen_quick_extra["enable_thinking"] = quick_enable_thinking
                if quick_enable_thinking and self.config.get("qwen_thinking_budget") is not None:
                    qwen_quick_extra["thinking_budget"] = int(self.config["qwen_thinking_budget"])

            openrouter_quick_extra: Dict[str, Any] = {}
            if (
                self.config.get("llm_provider", "").lower() == "openrouter"
                and (self.config.get("quick_think_llm") or "") == "openrouter/aurora-alpha"
            ):
                openrouter_quick_extra["reasoning"] = {"enabled": True}

            self.quick_thinking_llm = quick_llm_cls(
                model=self.config["quick_think_llm"],
                **_with_extra_params(
                    _with_streaming(openai_kwargs.copy(), quick_streaming),
                    _merge_extra_params(qwen_quick_extra, openrouter_quick_extra),
                ),
            )
            if provider == "glm" and quick_llm_cls is GLMFlashSerialChatOpenAI:
                self.quick_thinking_llm._ta_llm_concurrency_key = (
                    f"llm:{provider}:{str(self.config.get('backend_url') or '').rstrip('/')}:{self.config.get('quick_think_llm')}".lower()
                )
        elif self.config["llm_provider"].lower() == "anthropic":
            self.deep_thinking_llm = ChatAnthropic(model=self.config["deep_think_llm"], base_url=self.config["backend_url"])
            self.quick_thinking_llm = ChatAnthropic(model=self.config["quick_think_llm"], base_url=self.config["backend_url"])
        elif self.config["llm_provider"].lower() == "google":
            self.deep_thinking_llm = ChatGoogleGenerativeAI(model=self.config["deep_think_llm"])
            self.quick_thinking_llm = ChatGoogleGenerativeAI(model=self.config["quick_think_llm"])
        else:
            raise ValueError(f"Unsupported LLM provider: {self.config['llm_provider']}")

        # Initialize memories
        self.bull_memory = FinancialSituationMemory("bull_memory", self.config)
        self.bear_memory = FinancialSituationMemory("bear_memory", self.config)
        self.trader_memory = FinancialSituationMemory("trader_memory", self.config)
        self.invest_judge_memory = FinancialSituationMemory("invest_judge_memory", self.config)
        self.risk_manager_memory = FinancialSituationMemory("risk_manager_memory", self.config)

        # Create tool nodes
        self.tool_nodes = self._create_tool_nodes()

        # Initialize components
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.config.get("max_debate_rounds", 1),
            max_risk_discuss_rounds=self.config.get("max_risk_discuss_rounds", 1),
            max_tool_calls_per_analyst=self.config.get("max_tool_calls_per_analyst", 8),
            max_tool_calls_total=self.config.get("max_tool_calls_total", 50),
        )
        self.graph_setup = GraphSetup(
            self.quick_thinking_llm,
            self.deep_thinking_llm,
            self.tool_nodes,
            self.bull_memory,
            self.bear_memory,
            self.trader_memory,
            self.invest_judge_memory,
            self.risk_manager_memory,
            self.conditional_logic,
        )

        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100)
        )
        self.reflector = Reflector(self.quick_thinking_llm)
        self.signal_processor = SignalProcessor(self.quick_thinking_llm)

        # State tracking
        self.curr_state = None
        self.ticker = None
        self.log_states_dict = {}  # date to full state dict

        # Set up the graph
        self.graph = self.graph_setup.setup_graph(selected_analysts)

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        return {
            "market": ToolNode(
                [
                    # Core stock data tools
                    get_stock_data,
                    # Technical indicators
                    get_indicators,
                    # Short-term price action/risk metrics
                    get_price_action_summary,
                    # VWAP positioning (Alpaca free)
                    get_intraday_vwap_position,
                    get_multi_day_vwap_context,
                    # Options flow (Yahoo free)
                    get_unusual_options_activity,
                    get_options_sentiment_summary,
                    # Dark pool / off-exchange (FINRA free)
                    get_dark_pool_short_volume,
                    get_off_exchange_volume_context,
                    # Short interest (Yahoo + FINRA free)
                    get_short_interest_data,
                    get_squeeze_candidates_assessment,
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                    get_company_news_window,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
                    get_company_news_window,
                    get_global_news,
                    get_insider_sentiment,
                    get_insider_transactions,
                ]
            ),
            "fundamentals": ToolNode(
                [
                    # Fundamental analysis tools
                    get_fundamentals,
                    get_balance_sheet,
                    get_cashflow,
                    get_income_statement,
                    # Insider activity (fundamental catalyst context)
                    get_insider_sentiment,
                    get_insider_transactions,
                ]
            ),
        }

    def extract_structured_decision(self, full_signal: str) -> dict:
        """Extract structured trading decision from signal text."""
        return self.signal_processor.extract_structured_decision(full_signal)

    def propagate_and_execute(
        self,
        company_name: str,
        trade_date: str,
        executor: Optional[AlpacaExecutor] = None,
        execute: bool = False,
        portfolio_context: str = None,
        time_horizon: Optional[str] = None,
    ):
        # Run normal propagation with portfolio awareness
        # This calls the sync wrapper 'propagate' which runs the loop.
        final_state, decision = self.propagate(
            company_name,
            trade_date,
            portfolio_context=portfolio_context,
            time_horizon=time_horizon,
        )

        execution_result = None

        # Execute if requested
        if execute:
            if not executor:
                raise ValueError("Executor required when execute=True")

            structured = self.extract_structured_decision(
                final_state.get("final_trade_decision", "")
            )

            execution_result = executor.execute_signal(
                ticker=company_name,
                signal=decision,
                analysis_state=final_state,
                trade_date=trade_date,
                agent_quantity=structured.get("quantity"),
                agent_limit_price=structured.get("limit_price"),
                agent_position_size_pct=structured.get("position_size_pct"),
                agent_order_type=structured.get("order_type"),
                agent_time_in_force=structured.get("time_in_force"),
                agent_stop_price=structured.get("stop_price"),
                agent_trail_percent=structured.get("trail_percent"),
                agent_trail_price=structured.get("trail_price"),
            )

        return final_state, decision, execution_result

    async def apropagate_and_execute(
        self,
        company_name: str,
        trade_date: str,
        executor: Optional[AlpacaExecutor] = None,
        execute: bool = False,
        portfolio_context: str = None,
        time_horizon: Optional[str] = None,
    ):
        # Run async propagation
        final_state, decision = await self.apropagate(
            company_name,
            trade_date,
            portfolio_context=portfolio_context,
            time_horizon=time_horizon,
        )

        execution_result = None

        # Execute if requested
        if execute:
            if not executor:
                raise ValueError("Executor required when execute=True")

            structured = self.extract_structured_decision(
                final_state.get("final_trade_decision", "")
            )

            # executor.execute_signal is currently sync.
            # If we want to make it async later, we'd await it.
            # For now, wrap in to_thread to keep main loop responsive?
            # Or just call it if it's fast/IO-bound but sync.
            # Let's wrap it to be safe.
            execution_result = await asyncio.to_thread(
                executor.execute_signal,
                ticker=company_name,
                signal=decision,
                analysis_state=final_state,
                trade_date=trade_date,
                agent_quantity=structured.get("quantity"),
                agent_limit_price=structured.get("limit_price"),
                agent_position_size_pct=structured.get("position_size_pct"),
                agent_order_type=structured.get("order_type"),
                agent_time_in_force=structured.get("time_in_force"),
                agent_stop_price=structured.get("stop_price"),
                agent_trail_percent=structured.get("trail_percent"),
                agent_trail_price=structured.get("trail_price"),
            )

        return final_state, decision, execution_result

    async def apropagate(
        self,
        company_name,
        trade_date,
        portfolio_context: str = None,
        time_horizon: Optional[str] = None,
    ):
        """Run the trading agents graph properly for a company on a specific date (Async)."""
        self.ticker = company_name

        # Fetch portfolio context from brokerage if not provided
        if portfolio_context is None:
            # Note: fetch_portfolio_context is currently sync. 
            # If it becomes async, await it. For now, wrap in thread if blocking?
            # It's likely fast or blocking. Let's assume it's fine or wrap it.
            portfolio_context = await asyncio.to_thread(fetch_portfolio_context, company_name)

        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            portfolio_context=portfolio_context,
            time_horizon=time_horizon,
        )

        args = self.propagator.get_graph_args()

        if self.debug:
            # Debug mode with tracing
            trace = []
            async for chunk in self.graph.astream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)

            final_state = trace[-1]
        else:
            # Standard mode without tracing
            final_state = await self.graph.ainvoke(init_agent_state, **args)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # Prefer structured action if present; otherwise fall back to fast LLM extraction.
        structured = self.extract_structured_decision(
            final_state.get("final_trade_decision", "")
        )
        decision = structured.get("action") or self.process_signal(
            final_state.get("final_trade_decision", "")
        )

        return final_state, decision

    def propagate(
        self,
        company_name,
        trade_date,
        portfolio_context: str = None,
        time_horizon: Optional[str] = None,
    ):
        """Run the trading agents graph for a company on a specific date (Sync Wrapper)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # If we are already in an event loop, we can't use asyncio.run.
            # We must return a coroutine, but this function is defined as sync.
            # This is a critical issue for sync callers in async envs (like Jupyter).
            # Ideally, the caller should use apropagate in async envs.
            # For now, we raise an error or try nest_asyncio if available?
            # Let's assume standard script usage (no loop) or user calls apropagate if in async.
            raise RuntimeError("Event loop is running. Use 'apropagate' instead of 'propagate' in async contexts.")
        
        return asyncio.run(self.apropagate(
            company_name, 
            trade_date, 
            portfolio_context, 
            time_horizon
        ))

    def _log_state(self, trade_date, final_state):
        """Log the final state to a JSON file."""
        self.log_states_dict[str(trade_date)] = {
            "company_of_interest": final_state["company_of_interest"],
            "trade_date": final_state["trade_date"],
            "market_report": final_state["market_report"],
            "sentiment_report": final_state["sentiment_report"],
            "news_report": final_state["news_report"],
            "fundamentals_report": final_state["fundamentals_report"],
            "investment_debate_state": {
                "bull_history": final_state["investment_debate_state"]["bull_history"],
                "bear_history": final_state["investment_debate_state"]["bear_history"],
                "history": final_state["investment_debate_state"]["history"],
                "current_response": final_state["investment_debate_state"][
                    "current_response"
                ],
                "judge_decision": final_state["investment_debate_state"][
                    "judge_decision"
                ],
            },
            "trader_investment_decision": final_state["trader_investment_plan"],
            "risk_debate_state": {
                "risky_history": final_state["risk_debate_state"]["risky_history"],
                "safe_history": final_state["risk_debate_state"]["safe_history"],
                "neutral_history": final_state["risk_debate_state"]["neutral_history"],
                "history": final_state["risk_debate_state"]["history"],
                "judge_decision": final_state["risk_debate_state"]["judge_decision"],
            },
            "investment_plan": final_state["investment_plan"],
            "final_trade_decision": final_state["final_trade_decision"],
        }

        # Save to file
        directory = Path(f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/TradingAgentsStrategy_logs/full_states_log_{trade_date}.json",
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(self.log_states_dict, f, indent=4)

    def reflect_and_remember(self, returns_losses):
        """Reflect on decisions and update memory based on returns."""
        self.reflector.reflect_bull_researcher(
            self.curr_state, returns_losses, self.bull_memory
        )
        self.reflector.reflect_bear_researcher(
            self.curr_state, returns_losses, self.bear_memory
        )
        self.reflector.reflect_trader(
            self.curr_state, returns_losses, self.trader_memory
        )
        self.reflector.reflect_invest_judge(
            self.curr_state, returns_losses, self.invest_judge_memory
        )
        self.reflector.reflect_risk_manager(
            self.curr_state, returns_losses, self.risk_manager_memory
        )

    def process_signal(self, full_signal):
        """Process a signal to extract the core decision."""
        return self.signal_processor.process_signal(full_signal)



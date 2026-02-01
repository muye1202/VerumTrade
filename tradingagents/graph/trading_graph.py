# TradingAgents/graph/trading_graph.py

import os
import inspect
from pathlib import Path
import json
from datetime import date
from typing import Dict, Any, Tuple, List, Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import ToolNode

from typing import Optional
from tradingagents.execution import AlpacaExecutor
from tradingagents.agents import *
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.agents.utils.memory import FinancialSituationMemory
from tradingagents.agents.utils.agent_states import (
    AgentState,
    InvestDebateState,
    RiskDebateState,
)
from tradingagents.dataflows.config import set_config

# Import the new abstract tool methods from agent_utils
from tradingagents.agents.utils.agent_utils import (
    get_stock_data,
    get_indicators,
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    get_news,
    get_insider_sentiment,
    get_insider_transactions,
    get_global_news
)

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor


class StreamCompatibleChatOpenAI(ChatOpenAI):
    """Handle OpenAI SDK Stream responses by aggregating them into a single ChatCompletion-like dict."""

    def _create_chat_result(self, response, generation_info=None):
        if response is not None and response.__class__.__name__ == "Stream":
            response_dict = self._stream_to_response_dict(response)
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
            for choice in response_dict.get("choices", []) or []:
                msg = choice.get("message") or {}
                # DeepSeek can include reasoning_content alongside content.
                if isinstance(msg, dict) and "reasoning_content" in msg:
                    msg.pop("reasoning_content", None)
        return super()._create_chat_result(response_dict or response, generation_info=generation_info)


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
        if self.config["llm_provider"].lower() in {"openai", "ollama", "openrouter", "qwen3-cn", "deepseek"}:
            openai_kwargs = {"base_url": self.config["backend_url"]}
            if self.config["llm_provider"].lower() == "qwen3-cn":
                openai_kwargs["api_key"] = os.getenv("DASHSCOPE_API_KEY")
            if self.config["llm_provider"].lower() == "deepseek":
                openai_kwargs["api_key"] = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")

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

                # Important: do NOT pass DashScope-only flags via model_kwargs here.
                # Many langchain_openai versions forward model_kwargs into the OpenAI SDK
                # as top-level kwargs (e.g., enable_thinking=...), which raises:
                #   TypeError: Completions.create() got an unexpected keyword argument 'enable_thinking'
                #
                # If extra_body isn't supported by this langchain_openai version,
                # we skip these params rather than crashing the whole run.
                if self.debug:
                    print("INFO: ChatOpenAI doesn't support extra_body; skipping provider-specific params:", extra)
                return kwargs

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

            qwen_extra: Dict[str, Any] = {}
            if self.config["llm_provider"].lower() == "qwen3-cn":
                if self.config.get("qwen_enable_thinking") and _qwen_supports_thinking(self.config.get("deep_think_llm", "")):
                    qwen_extra["enable_thinking"] = True
                if (
                    self.config.get("qwen_thinking_budget") is not None
                    and _qwen_supports_thinking(self.config.get("deep_think_llm", ""))
                ):
                    qwen_extra["thinking_budget"] = int(self.config["qwen_thinking_budget"])

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

            if self.config["llm_provider"].lower() == "qwen3-cn":
                ChatLLM = StreamCompatibleChatOpenAI
            elif self.config["llm_provider"].lower() == "deepseek":
                ChatLLM = DeepSeekCompatibleChatOpenAI
            else:
                ChatLLM = ChatOpenAI
            self.deep_thinking_llm = ChatLLM(
                model=self.config["deep_think_llm"],
                **_with_streaming(_with_extra_params(openai_kwargs.copy(), qwen_extra), deep_streaming),
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
            self.quick_thinking_llm = ChatLLM(
                model=self.config["quick_think_llm"],
                **_with_extra_params(
                    _with_streaming(openai_kwargs.copy(), quick_streaming),
                    (
                        {"enable_thinking": True, "thinking_budget": int(self.config["qwen_thinking_budget"])}
                        if (
                            self.config.get("llm_provider", "").lower() == "qwen3-cn"
                            and self.config.get("qwen_enable_thinking_quick")
                            and _qwen_supports_thinking(self.config.get("quick_think_llm", ""))
                            and self.config.get("qwen_thinking_budget") is not None
                        )
                        else (
                            {"enable_thinking": True}
                            if (
                                self.config.get("llm_provider", "").lower() == "qwen3-cn"
                                and self.config.get("qwen_enable_thinking_quick")
                                and _qwen_supports_thinking(self.config.get("quick_think_llm", ""))
                            )
                            else {}
                        )
                    ),
                ),
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
                ]
            ),
            "social": ToolNode(
                [
                    # News tools for social media analysis
                    get_news,
                ]
            ),
            "news": ToolNode(
                [
                    # News and insider information
                    get_news,
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
                ]
            ),
        }

    def propagate_and_execute(
        self,
        company_name: str,
        trade_date: str,
        executor: Optional[AlpacaExecutor] = None,
        execute: bool = False
    ):
        """
        Run the trading agents graph and optionally execute the signal.

        Args:
            company_name: Ticker symbol to analyze
            trade_date: Date for analysis
            executor: AlpacaExecutor instance (required if execute=True)
            execute: Whether to execute the signal via Alpaca

        Returns:
            Tuple of (final_state, decision, execution_result)
        """
        # Run normal propagation
        final_state, decision = self.propagate(company_name, trade_date)

        execution_result = None

        # Execute if requested
        if execute:
            if not executor:
                raise ValueError("Executor required when execute=True")

            execution_result = executor.execute_signal(
                ticker=company_name,
                signal=decision,
                analysis_state=final_state,
                trade_date=trade_date
            )

        return final_state, decision, execution_result

    def propagate(self, company_name, trade_date):
        """Run the trading agents graph for a company on a specific date."""

        self.ticker = company_name

        # Initialize state
        init_agent_state = self.propagator.create_initial_state(
            company_name, trade_date
        )
        args = self.propagator.get_graph_args()

        if self.debug:
            # Debug mode with tracing
            trace = []
            for chunk in self.graph.stream(init_agent_state, **args):
                if len(chunk["messages"]) == 0:
                    pass
                else:
                    chunk["messages"][-1].pretty_print()
                    trace.append(chunk)

            final_state = trace[-1]
        else:
            # Standard mode without tracing
            final_state = self.graph.invoke(init_agent_state, **args)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # Return decision and processed signal
        return final_state, self.process_signal(final_state["final_trade_decision"])

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

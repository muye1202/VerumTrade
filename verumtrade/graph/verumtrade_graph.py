# verumtrade/graph/verumtrade_graph.py

import os
import inspect
import re
from pathlib import Path
import json
from datetime import datetime
from contextlib import nullcontext
from typing import Dict, Any, Tuple, List, Optional
import asyncio

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from langgraph.prebuilt import ToolNode

from typing import Optional
from verumtrade.execution import AlpacaExecutor
from verumtrade.agents import *
from verumtrade.default_config import DEFAULT_CONFIG
from verumtrade.agents.utils.memory.memory import FinancialSituationMemory
from verumtrade.execution.portfolio_context import fetch_portfolio_context
from verumtrade.utils.market_session import now_et
from verumtrade.execution.decision_guard import build_market_snapshot, evaluate_data_quality_fault
from verumtrade.agents.trader.decision_brief import build_trader_plan_v1
from verumtrade.agents.utils.agent_runtime.evidence_graph import build_decision_trace
from verumtrade.graph.debate_schema import DebateWorkflowHardFault
from verumtrade.graph.decision_diff import build_decision_diff
from verumtrade.graph.reasoning_trace import build_agent_reasoning_trace
from verumtrade.dataflows.config import set_config
from verumtrade.graph.provider_settings import azure_foundry_reasoning_mode, resolve_llm_endpoint

# Import the new abstract tool methods from agent_utils
from verumtrade.agents.utils.agent_runtime.agent_utils import (
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
    get_global_news,
    get_news_sentiment,
    get_recent_sec_filings
)
from verumtrade.agents.utils.market_data.vwap_tools import (
    get_intraday_vwap_position,
    get_multi_day_vwap_context,
)
from verumtrade.agents.utils.market_data.options_flow_tools import (
    get_unusual_options_activity,
    get_options_sentiment_summary,
)
from verumtrade.agents.utils.market_data.dark_pool_tools import (
    get_dark_pool_short_volume,
    get_off_exchange_volume_context,
)
from verumtrade.agents.utils.market_data.short_interest_tools import (
    get_short_interest_data,
    get_squeeze_candidates_assessment,
)
from verumtrade.agents.utils.market_data.bundle_tools import (
    get_catalyst_event_bundle,
    get_market_data_bundle,
    get_news_data_bundle,
    get_fundamentals_data_bundle,
    get_sentiment_data_bundle,
)
from verumtrade.agents.utils.agent_runtime.tool_cache import create_cache_aware_tool_node

from .conditional_logic import ConditionalLogic
from .setup import GraphSetup
from .propagation import Propagator
from .reflection import Reflector
from .signal_processing import SignalProcessor
from .decision_schema import validate_final_decision_contract
from .openai_compat import sanitize_openai_compatible_response_dict
from .glm_compat import sanitize_glm_chat_completion_response_dict
from verumtrade.agents.utils.llm.llm_concurrency import llm_inflight_slot
from verumtrade.agents.utils.llm.llm_metrics import (
    attach_llm_metrics_handler,
    snapshot_llm_api_calls,
    diff_llm_api_calls,
)


def _sanitize_outbound_openai_messages_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort repair for malformed tool-call message sequences before API submission.

    Some provider/parser combinations can yield assistant messages with malformed `tool_calls`
    or tool messages missing `tool_call_id`, which OpenAI-compatible endpoints reject with
    strict sequence validation.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    pending_ids: List[str] = []
    consumed_ids: set[str] = set()
    pending_assistant_idx: Optional[int] = None

    def _clear_pending():
        nonlocal pending_ids, consumed_ids, pending_assistant_idx
        pending_ids = []
        consumed_ids = set()
        pending_assistant_idx = None

    def _drop_pending_tool_calls_if_unresolved():
        if not pending_ids or pending_assistant_idx is None:
            _clear_pending()
            return
        unresolved = [tcid for tcid in pending_ids if tcid not in consumed_ids]
        if unresolved:
            assistant_msg = messages[pending_assistant_idx]
            if isinstance(assistant_msg, dict):
                assistant_msg.pop("tool_calls", None)
        _clear_pending()

    def _normalize_tool_calls(raw_tool_calls: Any, msg_idx: int) -> List[Dict[str, Any]]:
        if not isinstance(raw_tool_calls, list):
            return []
        cleaned: List[Dict[str, Any]] = []
        for tc_idx, tc in enumerate(raw_tool_calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                # Accept flattened legacy shapes if present.
                fn = {
                    "name": tc.get("name"),
                    "arguments": tc.get("arguments", ""),
                }
            name = fn.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            arguments = fn.get("arguments", "")
            if isinstance(arguments, (dict, list)):
                try:
                    arguments = json.dumps(arguments, ensure_ascii=False)
                except Exception:
                    arguments = str(arguments)
            elif arguments is None:
                arguments = ""
            elif not isinstance(arguments, str):
                arguments = str(arguments)

            tc_id = tc.get("id")
            if not isinstance(tc_id, str) or not tc_id.strip():
                tc_id = f"call_{msg_idx}_{tc_idx}"

            cleaned.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": name.strip(), "arguments": arguments},
                }
            )
        return cleaned

    for msg_idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            _drop_pending_tool_calls_if_unresolved()
            continue

        if msg.get("content") is None:
            msg["content"] = ""

        role = msg.get("role")
        if role == "assistant":
            _drop_pending_tool_calls_if_unresolved()
            cleaned_tool_calls = _normalize_tool_calls(msg.get("tool_calls"), msg_idx)
            if cleaned_tool_calls:
                msg["tool_calls"] = cleaned_tool_calls
                pending_ids = [tc["id"] for tc in cleaned_tool_calls]
                consumed_ids = set()
                pending_assistant_idx = msg_idx
            else:
                msg.pop("tool_calls", None)
            continue

        if role == "tool":
            if not isinstance(msg.get("content"), str):
                msg["content"] = str(msg.get("content", ""))

            if not pending_ids:
                # Orphan tool message is invalid for OpenAI chat payloads; coerce to assistant text.
                msg["role"] = "assistant"
                msg.pop("tool_call_id", None)
                continue

            unresolved = [tcid for tcid in pending_ids if tcid not in consumed_ids]
            tcid = msg.get("tool_call_id")
            if not isinstance(tcid, str) or not tcid.strip() or tcid not in pending_ids:
                if unresolved:
                    tcid = unresolved[0]
                    msg["tool_call_id"] = tcid
            if isinstance(tcid, str) and tcid in pending_ids:
                consumed_ids.add(tcid)
                if len(consumed_ids) >= len(set(pending_ids)):
                    _clear_pending()
            continue

        _drop_pending_tool_calls_if_unresolved()

    _drop_pending_tool_calls_if_unresolved()
    return payload

class StreamCompatibleChatOpenAI(ChatOpenAI):
    """Handle OpenAI SDK Stream responses by aggregating them into a single ChatCompletion-like dict."""

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        if isinstance(payload, dict):
            _sanitize_outbound_openai_messages_payload(payload)
        return payload

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

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        if isinstance(payload, dict):
            _sanitize_outbound_openai_messages_payload(payload)
        return payload

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

class OpenRouterCompatibleChatOpenAI(ChatOpenAI):
    """Sanitize OpenRouter OpenAI-compatible responses for LangChain parsing."""

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        if isinstance(payload, dict):
            _sanitize_outbound_openai_messages_payload(payload)
        return payload

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
            sanitize_openai_compatible_response_dict(response_dict)
            return super()._create_chat_result(response_dict, generation_info=generation_info)

        return super()._create_chat_result(response, generation_info=generation_info)

class GLMCompatibleChatOpenAI(ChatOpenAI):
    """Sanitize GLM (ZhipuAI) OpenAI-compatible responses for LangChain parsing."""

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        if isinstance(payload, dict):
            _sanitize_outbound_openai_messages_payload(payload)
        return payload

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


class VerumtradeGraph:
    """Main class that orchestrates the trading agents framework."""

    def __init__(
        self,
        selected_analysts=["catalyst", "market", "social", "news", "fundamentals"],
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
        selected_analysts = self.normalize_selected_analysts(selected_analysts)

        # Update the interface's config
        set_config(self.config)

        # Create necessary directories
        os.makedirs(
            os.path.join(self.config["project_dir"], "dataflows/data_cache"),
            exist_ok=True,
        )

        # Initialize LLMs
        if self.config["llm_provider"].lower() in {"openai", "azure-foundry", "ollama", "openrouter", "qwen3-cn", "deepseek", "glm"}:
            provider = self.config["llm_provider"].lower()
            endpoint = resolve_llm_endpoint(provider, self.config)
            openai_kwargs = {}
            if endpoint.get("base_url"):
                openai_kwargs["base_url"] = endpoint["base_url"]
            if endpoint.get("api_key"):
                openai_kwargs["api_key"] = endpoint["api_key"]

            # Provider-specific parameters for OpenAI-compatible backends.
            # DashScope supports "enable_thinking" (reasoning mode) for select Qwen models.
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

            def _openrouter_extra_for_model(model_name: str) -> Dict[str, Any]:
                name = (model_name or "").strip().lower()
                if not name:
                    return {}
                if name == "openrouter/aurora-alpha":
                    return {"reasoning": {"enabled": True}}
                if "thinking" in name:
                    return {"reasoning": {"enabled": True}}
                return {}

            def _qwen_supports_thinking(model_name: str) -> bool:
                name = (model_name or "").lower()
                # Conservative allowlist: these are the families commonly associated with thinking/reasoning mode.
                if name.startswith("qwen3-max"):
                    return True
                if "thinking" in name:
                    return True
                if name.startswith("qwq"):
                    return True
                # Allow override if thinking mode is explicitly enabled via config
                return True

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
            if self.config["llm_provider"].lower() == "openrouter":
                openrouter_deep_extra = _openrouter_extra_for_model(
                    self.config.get("deep_think_llm", "")
                )
            azure_foundry_deep_reasoning_effort = None
            if (
                self.config["llm_provider"].lower() == "azure-foundry"
                and self.config.get("azure_foundry_enable_thinking")
                and azure_foundry_reasoning_mode(self.config.get("deep_think_llm", "")) == "effort"
            ):
                azure_foundry_deep_reasoning_effort = self.config.get(
                    "azure_foundry_reasoning_effort", "medium"
                )
            azure_foundry_quick_reasoning_effort = None
            if (
                self.config["llm_provider"].lower() == "azure-foundry"
                and self.config.get("azure_foundry_enable_thinking")
                and azure_foundry_reasoning_mode(self.config.get("quick_think_llm", "")) == "effort"
            ):
                azure_foundry_quick_reasoning_effort = self.config.get(
                    "azure_foundry_reasoning_effort", "medium"
                )

            if provider == "qwen3-cn":
                base_llm_cls = StreamCompatibleChatOpenAI
            elif provider == "deepseek":
                base_llm_cls = DeepSeekCompatibleChatOpenAI
            elif provider == "openrouter":
                base_llm_cls = OpenRouterCompatibleChatOpenAI
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
                **_with_reasoning_effort(
                    _with_streaming(
                        _with_extra_params(
                            openai_kwargs.copy(),
                            _merge_extra_params(qwen_extra, openrouter_deep_extra),
                        ),
                        deep_streaming,
                    ),
                    azure_foundry_deep_reasoning_effort,
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
            if self.config.get("llm_provider", "").lower() == "openrouter":
                openrouter_quick_extra = _openrouter_extra_for_model(
                    self.config.get("quick_think_llm", "")
                )

            self.quick_thinking_llm = quick_llm_cls(
                model=self.config["quick_think_llm"],
                **_with_reasoning_effort(
                    _with_extra_params(
                        _with_streaming(openai_kwargs.copy(), quick_streaming),
                        _merge_extra_params(qwen_quick_extra, openrouter_quick_extra),
                    ),
                    azure_foundry_quick_reasoning_effort,
                ),
            )
            if provider == "glm" and quick_llm_cls is GLMFlashSerialChatOpenAI:
                self.quick_thinking_llm._ta_llm_concurrency_key = (
                    f"llm:{provider}:{str(self.config.get('backend_url') or '').rstrip('/')}:{self.config.get('quick_think_llm')}".lower()
                )
        elif self.config["llm_provider"].lower() == "anthropic":
            endpoint = resolve_llm_endpoint("anthropic", self.config)
            anthropic_api_key = endpoint.get("api_key")
            anthropic_base_url = endpoint.get("base_url")

            # Configure Thinking Mode
            anthropic_thinking = {}
            if self.config.get("anthropic_enable_thinking"):
                budget = self.config.get("anthropic_thinking_budget")
                if budget:
                    anthropic_thinking = {
                        "thinking": {
                            "type": "enabled",
                            "budget_tokens": int(budget)
                        }
                    }

            # Initialize Clients
            self.deep_thinking_llm = ChatAnthropic(
                model=self.config["deep_think_llm"],
                api_key=anthropic_api_key,
                base_url=anthropic_base_url,
                **anthropic_thinking
            )
            self.quick_thinking_llm = ChatAnthropic(
                model=self.config["quick_think_llm"],
                api_key=anthropic_api_key,
                base_url=anthropic_base_url,
                # Do not enable thinking for quick models to save costs/latency, unless explicitly desired
                # For now, we only apply thinking to the "deep" agent if enabled.
            )
        elif self.config["llm_provider"].lower() == "google":
            endpoint = resolve_llm_endpoint("google", self.config)
            google_kwargs = {}
            if endpoint.get("api_key"):
                google_kwargs["google_api_key"] = endpoint["api_key"]
            self.deep_thinking_llm = ChatGoogleGenerativeAI(
                model=self.config["deep_think_llm"],
                **google_kwargs,
            )
            self.quick_thinking_llm = ChatGoogleGenerativeAI(
                model=self.config["quick_think_llm"],
                **google_kwargs,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {self.config['llm_provider']}")

        # Attach global LLM-call instrumentation handlers.
        attach_llm_metrics_handler(self.deep_thinking_llm)
        attach_llm_metrics_handler(self.quick_thinking_llm)

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
            max_tool_calls_per_analyst=self.config.get(
                "analyst_tool_round_cap",
                self.config.get("max_tool_calls_per_analyst", 4),
            ),
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

    @staticmethod
    def normalize_selected_analysts(selected_analysts) -> List[str]:
        """Deduplicate analyst keys and run catalyst first when selected."""
        raw = (
            ["market", "social", "news", "fundamentals"]
            if selected_analysts is None
            else selected_analysts
        )
        seen = set()
        normalized: List[str] = []
        for item in raw:
            value = getattr(item, "value", item)
            key = str(value or "").strip().lower()
            if not key or key in seen:
                continue
            normalized.append(key)
            seen.add(key)
        if "catalyst" in seen:
            normalized = ["catalyst", *[item for item in normalized if item != "catalyst"]]
        return normalized

    @staticmethod
    def _build_run_metrics(before_snapshot: Dict[str, Any], after_snapshot: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        metrics = diff_llm_api_calls(before_snapshot, after_snapshot)
        rounds = state.get("tool_round_counts") or state.get("tool_call_counts") or {}
        issued = state.get("tool_calls_issued_by_agent") or {}
        workbench_metrics = state.get("analyst_workbench_metrics") or {}
        blocked = state.get("analyst_tool_call_blocked_counts") or {}
        links = state.get("analyst_tool_call_links") or {}
        cache_metrics = state.get("tool_cache_metrics") or {}
        vendor_events = state.get("vendor_telemetry") or []
        metrics.update(
            {
                "analyst_tool_rounds_by_agent": dict(rounds),
                "analyst_tool_rounds_total": int(state.get("tool_call_total", sum(int(v or 0) for v in rounds.values())) or 0),
                "tool_calls_issued_by_agent": dict(issued),
                "tool_calls_issued_total": int(state.get("tool_calls_issued_total", sum(int(v or 0) for v in issued.values())) or 0),
                "analyst_workbench_metrics": dict(workbench_metrics),
                "analyst_tool_call_blocked_counts": dict(blocked),
                "analyst_tool_call_links": dict(links),
                "tool_cache_metrics": dict(cache_metrics),
                "vendor_telemetry_event_count": len(vendor_events) if isinstance(vendor_events, list) else 0,
            }
        )
        return metrics

    def _create_tool_nodes(self) -> Dict[str, ToolNode]:
        """Create tool nodes for different data sources using abstract methods."""
        enable_bundle_tools = bool(self.config.get("enable_bundle_tools", True))

        market_tools = [
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
        social_tools = [
            # News tools for social media analysis
            get_news,
            get_company_news_window,
            get_news_sentiment,
        ]
        news_tools = [
            # News and insider information
            get_news,
            get_company_news_window,
            get_global_news,
            get_news_sentiment,
            get_recent_sec_filings,
        ]
        catalyst_tools = [
            get_catalyst_event_bundle,
            get_company_news_window,
            get_recent_sec_filings,
            get_insider_transactions,
            get_price_action_summary,
        ]
        fundamentals_tools = [
            # Fundamental analysis tools
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
            # Insider activity (fundamental catalyst context)
            get_insider_sentiment,
            get_insider_transactions,
        ]

        if enable_bundle_tools:
            market_tools = [get_market_data_bundle, *market_tools]
            social_tools = [get_sentiment_data_bundle, *social_tools]
            news_tools = [get_news_data_bundle, *news_tools]
            fundamentals_tools = [get_fundamentals_data_bundle, *fundamentals_tools]

        return {
            "catalyst": create_cache_aware_tool_node(catalyst_tools),
            "market": create_cache_aware_tool_node(market_tools),
            "social": create_cache_aware_tool_node(social_tools),
            "news": create_cache_aware_tool_node(news_tools),
            "fundamentals": create_cache_aware_tool_node(fundamentals_tools),
        }

    def extract_structured_decision(self, full_signal: str) -> dict:
        """Extract structured trading decision from signal text."""
        return self.signal_processor.extract_structured_decision(full_signal)

    @staticmethod
    def _merge_repaired_decision_text(existing_text: str, repaired_text: str) -> str:
        """
        Keep narrative text but ensure only one canonical decision block remains.
        If repair output contains a canonical block, replace prior blocks with it.
        """
        if not repaired_text:
            return str(existing_text or "")

        pattern = r"BEGIN_DECISION_JSON\s*\{.*?\}\s*END_DECISION_JSON"
        repaired_matches = list(
            re.finditer(pattern, str(repaired_text), flags=re.DOTALL | re.IGNORECASE)
        )
        if not repaired_matches:
            base = str(existing_text or "").strip()
            extra = str(repaired_text).strip()
            return f"{base}\n\n{extra}".strip()

        latest_block = repaired_matches[-1].group(0).strip()
        base = re.sub(
            pattern,
            "",
            str(existing_text or ""),
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        return f"{base}\n\n{latest_block}".strip() if base else latest_block

    def _attach_canonical_decision(
        self,
        final_state: Dict[str, Any],
        *,
        expected_ticker: Optional[str],
    ) -> Dict[str, Any]:
        market_snapshot = final_state.get("market_snapshot") or build_market_snapshot(
            symbol=expected_ticker or final_state.get("company_of_interest", ""),
            market_report=final_state.get("market_report", ""),
            quote=None,
            structured_decision=None,
            snapshot_source=self.config.get("decision_snapshot_source", "executor_quote_first"),
        )
        final_state["market_snapshot"] = market_snapshot
        structured, err = self.signal_processor.extract_canonical_decision(
            final_state.get("final_trade_decision", ""),
            expected_ticker=expected_ticker,
        )
        if err:
            raise DebateWorkflowHardFault(
                "risk_manager",
                "invalid final BEGIN_DECISION_JSON block",
                details=err,
            )
        contract_violations = validate_final_decision_contract(
            structured if isinstance(structured, dict) else {}
        )
        if contract_violations:
            raise DebateWorkflowHardFault(
                "risk_manager",
                "invalid final decision contract",
                details=contract_violations,
            )
        trader_intent = self._extract_trader_intent_from_state(final_state)
        structured_intent = ""
        if isinstance(structured, dict):
            structured_intent = str(structured.get("execution_intent", "")).strip().lower()
        mode_overridden = bool(
            trader_intent in {"act_now", "wait_for_trigger"}
            and structured_intent in {"act_now", "wait_for_trigger"}
            and trader_intent != structured_intent
        )
        override_reason = (
            str((structured or {}).get("override_reason") or "").strip()
            if isinstance(structured, dict)
            else ""
        )
        final_state["final_trade_decision_structured"] = structured
        final_state["final_trade_decision_validation_error"] = err or ""
        trader_plan = final_state.get("trader_plan_v1")
        if not isinstance(trader_plan, dict) or not trader_plan:
            trader_plan = build_trader_plan_v1(final_state)
            final_state["trader_plan_v1"] = trader_plan
        final_state["decision_diff"] = build_decision_diff(
            trader_plan,
            structured if isinstance(structured, dict) else {},
            accepted_patch_ids=[
                str(item.get("patch_id"))
                for item in final_state.get("risk_patch_validation", []) or []
                if isinstance(item, dict) and item.get("valid") and item.get("patch_id")
            ],
            rejected_patches=[
                {"patch_id": str(item.get("patch_id") or ""), "reason": str(item.get("reason") or "")}
                for item in final_state.get("risk_patch_validation", []) or []
                if isinstance(item, dict) and not item.get("valid")
            ],
        )
        final_state["decision_guard"] = {
            "validation_ok": isinstance(structured, dict) and not bool(err),
            "violations": [] if not err else [err],
            "mode_selected_by": "risk_judge",
            "trader_selected_execution_intent": trader_intent or "",
            "final_execution_intent": structured_intent or "",
            "mode_overridden": mode_overridden,
            "override_reason": override_reason,
            "abort_reason": "",
        }
        final_state["decision_trace"] = build_decision_trace(
            final_state,
            final_state.get("final_trade_decision", ""),
        )
        final_state["agent_reasoning_trace"] = build_agent_reasoning_trace(final_state)
        return final_state

    def _enforce_decision_guard(
        self,
        final_state: Dict[str, Any],
        *,
        expected_ticker: str,
        executor: Optional[AlpacaExecutor] = None,
    ) -> Dict[str, Any]:
        guard = dict(final_state.get("decision_guard") or {})
        structured = final_state.get("final_trade_decision_structured")
        validation_error = final_state.get("final_trade_decision_validation_error", "")

        quote = None
        if (
            executor is not None
            and str(self.config.get("decision_snapshot_source", "executor_quote_first")).strip().lower()
            == "executor_quote_first"
        ):
            try:
                quote = executor._get_latest_quote(expected_ticker)  # noqa: SLF001
            except Exception:
                quote = None

        market_snapshot = build_market_snapshot(
            symbol=expected_ticker,
            market_report=final_state.get("market_report", ""),
            quote=quote,
            structured_decision=structured if isinstance(structured, dict) else None,
            snapshot_source=self.config.get("decision_snapshot_source", "executor_quote_first"),
        )
        final_state["market_snapshot"] = market_snapshot

        if isinstance(structured, dict):
            contract_violations = validate_final_decision_contract(structured)
            if contract_violations:
                validation_error = (
                    f"{validation_error}; final decision contract: {contract_violations}"
                    if validation_error
                    else f"final decision contract: {contract_violations}"
                )
            action = str(structured.get("action", "")).strip().upper()
            if action in {"BUY", "SELL"} and bool(market_snapshot.get("price_anchor_conflict")):
                conflict_msg = str(
                    market_snapshot.get("price_anchor_conflict_reason")
                    or "price anchor conflict detected"
                )
                validation_error = (
                    f"{validation_error}; {conflict_msg}" if validation_error else conflict_msg
                )
            trader_intent = self._extract_trader_intent_from_state(final_state)
            final_intent = str(structured.get("execution_intent", "")).strip().lower()
            override_reason = str(structured.get("override_reason", "") or "").strip()
            if (
                trader_intent in {"act_now", "wait_for_trigger"}
                and final_intent in {"act_now", "wait_for_trigger"}
                and trader_intent != final_intent
                and not override_reason
            ):
                mismatch_msg = "mode override requires non-empty override_reason"
                validation_error = (
                    f"{validation_error}; {mismatch_msg}" if validation_error else mismatch_msg
                )
            hard_fault = evaluate_data_quality_fault({**final_state, "market_snapshot": market_snapshot})
            if hard_fault:
                validation_error = (
                    f"{validation_error}; {hard_fault}" if validation_error else hard_fault
                )

        final_state["final_trade_decision_structured"] = structured
        final_state["final_trade_decision_validation_error"] = validation_error or ""
        trader_plan = final_state.get("trader_plan_v1")
        if not isinstance(trader_plan, dict) or not trader_plan:
            trader_plan = build_trader_plan_v1(final_state)
            final_state["trader_plan_v1"] = trader_plan
        final_state["decision_diff"] = build_decision_diff(
            trader_plan,
            structured if isinstance(structured, dict) else {},
            accepted_patch_ids=[
                str(item.get("patch_id"))
                for item in final_state.get("risk_patch_validation", []) or []
                if isinstance(item, dict) and item.get("valid") and item.get("patch_id")
            ],
            rejected_patches=[
                {"patch_id": str(item.get("patch_id") or ""), "reason": str(item.get("reason") or "")}
                for item in final_state.get("risk_patch_validation", []) or []
                if isinstance(item, dict) and not item.get("valid")
            ],
        )
        guard.update(
            {
                "validation_ok": isinstance(structured, dict) and not bool(validation_error),
                "violations": [],
                "mode_selected_by": "risk_judge",
                "trader_selected_execution_intent": self._extract_trader_intent_from_state(final_state),
                "final_execution_intent": (
                    str((structured or {}).get("execution_intent", "")).strip().lower()
                    if isinstance(structured, dict)
                    else ""
                ),
                "mode_overridden": bool(
                    self._extract_trader_intent_from_state(final_state) in {"act_now", "wait_for_trigger"}
                    and str((structured or {}).get("execution_intent", "")).strip().lower() in {"act_now", "wait_for_trigger"}
                    and self._extract_trader_intent_from_state(final_state)
                    != str((structured or {}).get("execution_intent", "")).strip().lower()
                ),
                "override_reason": (
                    str((structured or {}).get("override_reason") or "").strip()
                    if isinstance(structured, dict)
                    else ""
                ),
                "abort_reason": "",
            }
        )
        final_state["decision_guard"] = guard
        final_state["decision_trace"] = build_decision_trace(
            final_state,
            final_state.get("final_trade_decision", ""),
        )
        final_state["agent_reasoning_trace"] = build_agent_reasoning_trace(final_state)
        return final_state

    @staticmethod
    def _extract_trader_intent_from_state(final_state: Dict[str, Any]) -> str:
        text = str(final_state.get("trader_investment_plan") or "")
        m = re.search(
            r"EXECUTION[_\s-]*INTENT\s*:\s*(ACT_NOW|WAIT_FOR_TRIGGER|ACT NOW|WAIT FOR TRIGGER)",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return ""
        return m.group(1).upper().replace(" ", "_").lower()

    @staticmethod
    def _execution_abort_result(
        ticker: str,
        signal: str,
        trade_date: str,
        validation_error: str,
        *,
        market_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "ticker": ticker,
            "signal": signal,
            "trade_date": trade_date,
            "timestamp": now_et().isoformat(),
            "executed": False,
            "order": None,
            "error": "Structured decision missing or invalid; execution aborted",
            "decision_source": "final_trade_decision_structured",
            "decision_version": None,
            "decision_validation_ok": False,
            "decision_validation_error": validation_error or "structured decision unavailable",
            "market_snapshot_reference_price": (market_snapshot or {}).get("reference_price"),
            "market_snapshot_source": (market_snapshot or {}).get("source"),
        }

    @staticmethod
    def _resolve_immediate_execution_payload(
        structured: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Resolve an immediately executable order payload from canonical decision JSON.
        Returns None for conditional-only v2 plans.
        """
        if not isinstance(structured, dict):
            return None
        version = str(structured.get("decision_version", "v1")).strip().lower() or "v1"
        if version == "v1":
            return structured
        if version != "v2":
            return None

        plan_mode = str(structured.get("plan_mode", "conditional")).strip().lower()
        execution_plan = structured.get("execution_plan") or []
        if not isinstance(execution_plan, list):
            execution_plan = []

        immediate_template = None
        immediate_branch_id = str(structured.get("immediate_branch_id", "")).strip()
        if immediate_branch_id:
            for branch in execution_plan:
                if str((branch or {}).get("branch_id", "")).strip() == immediate_branch_id:
                    immediate_template = (branch or {}).get("action_template")
                    break

        if immediate_template is None and plan_mode == "immediate":
            for branch in execution_plan:
                cond = (branch or {}).get("conditions") or {}
                if not any(
                    [
                        bool(cond.get("price")),
                        bool(cond.get("volume")),
                        bool((cond.get("schedule") or {}).get("valid_from")),
                        bool((cond.get("schedule") or {}).get("valid_to")),
                        str((cond.get("schedule") or {}).get("session_constraint", "ANY")).upper() != "ANY",
                        bool(cond.get("event_conditions")),
                    ]
                ):
                    immediate_template = (branch or {}).get("action_template")
                    break

        if immediate_template is None and plan_mode == "immediate":
            default_action = structured.get("default_action")
            if isinstance(default_action, dict):
                immediate_template = default_action

        if not isinstance(immediate_template, dict):
            return None
        payload = dict(immediate_template)
        payload["decision_version"] = "v2"
        return payload

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

            self._enforce_decision_guard(
                final_state, expected_ticker=company_name, executor=executor
            )
            structured = final_state.get("final_trade_decision_structured")
            validation_error = final_state.get("final_trade_decision_validation_error", "")
            if not isinstance(structured, dict) or validation_error:
                decision = self.process_signal(final_state.get("final_trade_decision", ""))
                final_state["decision_guard"] = {
                    **(final_state.get("decision_guard") or {}),
                    "abort_reason": (
                        "invalid_or_missing_structured_decision"
                        if not isinstance(structured, dict)
                        else "decision_guard_validation_error"
                    ),
                }
                return (
                    final_state,
                    decision,
                    self._execution_abort_result(
                        company_name,
                        decision,
                        trade_date,
                        validation_error or "Structured decision missing.",
                        market_snapshot=final_state.get("market_snapshot") or {},
                    ),
                )
            resolved_execution = self._resolve_immediate_execution_payload(structured)
            if resolved_execution is None:
                decision = "HOLD"
                return (
                    final_state,
                    decision,
                    {
                        "ticker": company_name,
                        "signal": decision,
                        "trade_date": trade_date,
                        "timestamp": now_et().isoformat(),
                        "executed": False,
                        "order": None,
                        "error": None,
                        "message": "Conditional decision plan captured; no immediate execution.",
                        "decision_source": "final_trade_decision_structured",
                        "decision_version": structured.get("decision_version"),
                        "decision_validation_ok": True,
                        "decision_validation_error": "",
                    },
                )
            decision = resolved_execution.get("action") or structured.get("action") or decision

            execution_result = executor.execute_signal(
                ticker=company_name,
                signal=decision,
                analysis_state=final_state,
                trade_date=trade_date,
                agent_quantity=resolved_execution.get("quantity"),
                agent_limit_price=resolved_execution.get("limit_price"),
                agent_position_size_pct=resolved_execution.get("position_size_pct"),
                agent_order_type=resolved_execution.get("order_type"),
                agent_time_in_force=resolved_execution.get("time_in_force"),
                agent_stop_price=resolved_execution.get("stop_price"),
                agent_trail_percent=resolved_execution.get("trail_percent"),
                agent_trail_price=resolved_execution.get("trail_price"),
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

            self._enforce_decision_guard(
                final_state, expected_ticker=company_name, executor=executor
            )
            structured = final_state.get("final_trade_decision_structured")
            validation_error = final_state.get("final_trade_decision_validation_error", "")
            if not isinstance(structured, dict) or validation_error:
                decision = self.process_signal(final_state.get("final_trade_decision", ""))
                final_state["decision_guard"] = {
                    **(final_state.get("decision_guard") or {}),
                    "abort_reason": (
                        "invalid_or_missing_structured_decision"
                        if not isinstance(structured, dict)
                        else "decision_guard_validation_error"
                    ),
                }
                return (
                    final_state,
                    decision,
                    self._execution_abort_result(
                        company_name,
                        decision,
                        trade_date,
                        validation_error or "Structured decision missing.",
                        market_snapshot=final_state.get("market_snapshot") or {},
                    ),
                )
            resolved_execution = self._resolve_immediate_execution_payload(structured)
            if resolved_execution is None:
                decision = "HOLD"
                return (
                    final_state,
                    decision,
                    {
                        "ticker": company_name,
                        "signal": decision,
                        "trade_date": trade_date,
                        "timestamp": now_et().isoformat(),
                        "executed": False,
                        "order": None,
                        "error": None,
                        "message": "Conditional decision plan captured; no immediate execution.",
                        "decision_source": "final_trade_decision_structured",
                        "decision_version": structured.get("decision_version"),
                        "decision_validation_ok": True,
                        "decision_validation_error": "",
                    },
                )
            decision = resolved_execution.get("action") or structured.get("action") or decision

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
                agent_quantity=resolved_execution.get("quantity"),
                agent_limit_price=resolved_execution.get("limit_price"),
                agent_position_size_pct=resolved_execution.get("position_size_pct"),
                agent_order_type=resolved_execution.get("order_type"),
                agent_time_in_force=resolved_execution.get("time_in_force"),
                agent_stop_price=resolved_execution.get("stop_price"),
                agent_trail_percent=resolved_execution.get("trail_percent"),
                agent_trail_price=resolved_execution.get("trail_price"),
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

        # Build the cross-asset/regime/positioning context bus once per run, off-thread so the
        # snapshot's vendor calls do not block the event loop. Degrades to {} when unavailable.
        from verumtrade.agents.utils.market_data.macro_regime import build_macro_regime_context
        from verumtrade.agents.utils.market_data.pullback_vulnerability import (
            build_pullback_vulnerability,
        )
        from verumtrade.agents.utils.market_data.peer_read_through import (
            build_sector_read_through,
        )

        macro_regime = await asyncio.to_thread(build_macro_regime_context, str(trade_date))
        pullback_vulnerability = await asyncio.to_thread(
            build_pullback_vulnerability, company_name, str(trade_date), macro_regime
        )
        # Bounded peer-news read-through (Tier-2 Phase 2b). Gated off by default; returns {} unless
        # peer_read_through.fetch_peer_news is enabled. Off-thread — it issues a few vendor calls.
        sector_read_through = await asyncio.to_thread(
            build_sector_read_through, company_name, str(trade_date)
        )

        init_agent_state = self.propagator.create_initial_state(
            company_name,
            trade_date,
            portfolio_context=portfolio_context,
            time_horizon=time_horizon,
            macro_regime=macro_regime,
            pullback_vulnerability=pullback_vulnerability,
            sector_read_through=sector_read_through,
        )

        args = self.propagator.get_graph_args()
        llm_snapshot_before = snapshot_llm_api_calls()

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

        llm_snapshot_after = snapshot_llm_api_calls()
        final_state["llm_metrics"] = self._build_run_metrics(
            llm_snapshot_before, llm_snapshot_after, final_state
        )
        self._attach_canonical_decision(final_state, expected_ticker=company_name)

        # Store current state for reflection
        self.curr_state = final_state

        # Log state
        self._log_state(trade_date, final_state)

        # Prefer canonical structured action if present; otherwise fall back to fast LLM extraction.
        structured = final_state.get("final_trade_decision_structured")
        decision = (structured or {}).get("action") or self.process_signal(
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
            "catalyst_report": final_state.get("catalyst_report", ""),
            "fundamentals_report": final_state["fundamentals_report"],
            "market_ledger": final_state.get("market_ledger", {}),
            "sentiment_ledger": final_state.get("sentiment_ledger", {}),
            "news_ledger": final_state.get("news_ledger", {}),
            "catalyst_ledger": final_state.get("catalyst_ledger", {}),
            "fundamentals_ledger": final_state.get("fundamentals_ledger", {}),
            "catalyst_event_bundle": final_state.get("catalyst_event_bundle", {}),
            "catalyst_event_report_structured": final_state.get("catalyst_event_report_structured", {}),
            "catalyst_evidence": final_state.get("catalyst_evidence", ""),
            "evidence_source_facts": final_state.get("evidence_source_facts", []),
            "evidence_graph": final_state.get("evidence_graph", {}),
            "evidence_graph_audit": final_state.get("evidence_graph_audit", []),
            "decision_trace": final_state.get("decision_trace", {}),
            "agent_reasoning_trace": final_state.get("agent_reasoning_trace", {}),
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
            "final_trade_decision_structured": final_state.get("final_trade_decision_structured"),
            "final_trade_decision_validation_error": final_state.get("final_trade_decision_validation_error", ""),
            "market_snapshot": final_state.get("market_snapshot", {}),
            "decision_guard": final_state.get("decision_guard", {}),
            "tool_round_counts": final_state.get("tool_round_counts", {}),
            "tool_call_counts": final_state.get("tool_call_counts", {}),
            "tool_call_total": final_state.get("tool_call_total", 0),
            "tool_calls_issued_by_agent": final_state.get("tool_calls_issued_by_agent", {}),
            "tool_calls_issued_total": final_state.get("tool_calls_issued_total", 0),
            "analyst_tool_call_links": final_state.get("analyst_tool_call_links", {}),
            "analyst_tool_call_blocked_counts": final_state.get("analyst_tool_call_blocked_counts", {}),
            "analyst_workbench_metrics": final_state.get("analyst_workbench_metrics", {}),
            "llm_metrics": final_state.get("llm_metrics", {}),
        }

        # Save to file
        directory = Path(f"eval_results/{self.ticker}/VerumtradeStrategy_logs/")
        directory.mkdir(parents=True, exist_ok=True)

        with open(
            f"eval_results/{self.ticker}/VerumtradeStrategy_logs/full_states_log_{trade_date}.json",
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

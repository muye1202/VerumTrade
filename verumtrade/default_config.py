import os


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


DEFAULT_CONFIG = {

    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("VERUMTRADE_RESULTS_DIR", "./results"),

    # Local/offline data root (used by "local" vendors). Override on Windows via VERUMTRADE_DATA_DIR.
    "data_dir": os.getenv(
        "VERUMTRADE_DATA_DIR",
        os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")), "data"),
    ),

    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),

    # LLM settings (providers: openai, azure-foundry, anthropic, google, deepseek, openrouter, ollama, qwen3-cn, glm)
    "llm_provider": os.getenv("VERUMTRADE_LLM_PROVIDER", "openai"),
    "deep_think_llm": "o4-mini",
    "quick_think_llm": "gpt-4o-mini",
    "backend_url": os.getenv("VERUMTRADE_BACKEND_URL", "https://api.openai.com/v1"),

    # GLM note:
    # Some ZhipuAI tiers/models (e.g., glm-4.7-flash) may require no parallel requests.
    # Verumtrade enforces in-process serialization when the selected model is exactly "glm-4.7-flash".

    # Qwen (DashScope) OpenAI-compatible extensions
    # When enabled, we pass DashScope-specific parameters (e.g., enable_thinking) through ChatOpenAI.
    "qwen_enable_thinking": _env_flag("VERUMTRADE_QWEN_ENABLE_THINKING", False),
    "qwen_enable_thinking_quick": _env_flag("VERUMTRADE_QWEN_ENABLE_THINKING_QUICK", False),
    "qwen_thinking_budget": (
        int(os.getenv("VERUMTRADE_QWEN_THINKING_BUDGET"))
        if os.getenv("VERUMTRADE_QWEN_THINKING_BUDGET")
        else None
    ),

    # Anthropic settings
    "anthropic_enable_thinking": _env_flag("VERUMTRADE_ANTHROPIC_ENABLE_THINKING", True),
    "anthropic_thinking_budget": (
        int(os.getenv("VERUMTRADE_ANTHROPIC_THINKING_BUDGET"))
        if os.getenv("VERUMTRADE_ANTHROPIC_THINKING_BUDGET")
        else 1024
    ),
    "azure_foundry_enable_thinking": _env_flag("VERUMTRADE_AZURE_FOUNDRY_ENABLE_THINKING", False),
    "azure_foundry_reasoning_effort": os.getenv("VERUMTRADE_AZURE_FOUNDRY_REASONING_EFFORT", "medium"),

    # Prompt/context budgeting (important for providers with strict context windows).
    "context_budget_mode": os.getenv("VERUMTRADE_CONTEXT_BUDGET_MODE", "adaptive"),
    "prompt_soft_cap_tokens": int(os.getenv("VERUMTRADE_PROMPT_SOFT_CAP_TOKENS", "45000")),
    "char_per_token_estimate": float(os.getenv("VERUMTRADE_CHAR_PER_TOKEN_ESTIMATE", "4.0")),
    "section_max_chars_report": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_REPORT", "2200")),
    "section_max_chars_history": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_HISTORY", "8000")),
    "section_max_chars_response": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_RESPONSE", "1800")),
    "section_max_chars_memory": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_MEMORY", "1200")),
    "section_max_chars_portfolio": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_PORTFOLIO", "2500")),
    "section_max_chars_trader_plan": int(os.getenv("VERUMTRADE_SECTION_MAX_CHARS_TRADER_PLAN", "2000")),
    "tool_response_max_chars": int(os.getenv("VERUMTRADE_TOOL_RESPONSE_MAX_CHARS", "12000")),
    "news_max_items": int(os.getenv("VERUMTRADE_NEWS_MAX_ITEMS", "12")),
    "max_debate_rounds_cap": int(os.getenv("VERUMTRADE_MAX_DEBATE_ROUNDS_CAP", "3")),
    "max_risk_rounds_cap": int(os.getenv("VERUMTRADE_MAX_RISK_ROUNDS_CAP", "3")),

    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": int(os.getenv("VERUMTRADE_MAX_RECUR_LIMIT", "100")),
    # Hard cap for analyst tool rounds (LLM -> tools -> LLM loop control)
    # Set VERUMTRADE_ANALYST_TOOL_ROUND_CAP=0 to disable the per-analyst round cap.
    "analyst_tool_round_cap": int(os.getenv("VERUMTRADE_ANALYST_TOOL_ROUND_CAP", "4")),
    # Guardrails to prevent infinite analyst->tools loops (LangGraph recursion errors)
    "max_tool_calls_per_analyst": int(os.getenv("VERUMTRADE_MAX_TOOL_CALLS_PER_ANALYST", "8")),
    "max_tool_calls_total": int(os.getenv("VERUMTRADE_MAX_TOOL_CALLS_TOTAL", "50")),
    # When true, expose bundled one-shot tools per analyst to reduce extra LLM turns.
    "enable_bundle_tools": _env_flag("VERUMTRADE_ENABLE_BUNDLE_TOOLS", True),
    # When true, build the cross-asset/regime/positioning "macro_regime" context bus once per
    # run and inject it into the news / catalyst / risk nodes (Tier-0 pullback-risk upgrade).
    # Reuses the discovery-lane snapshot builder; degrades to no-op when data is unavailable.
    "enable_macro_regime_context": _env_flag("VERUMTRADE_ENABLE_MACRO_REGIME_CONTEXT", True),
    # When true, compute the per-ticker Pullback Vulnerability Score (extension + crowding + tape
    # fragility + valuation richness) and give the risk judge a dedicated override path on a
    # HIGH/CRITICAL rating (Tier-1 pullback-risk upgrade). Degrades to no-op when data is sparse.
    "enable_pullback_vulnerability": _env_flag("VERUMTRADE_ENABLE_PULLBACK_VULNERABILITY", True),
    # When true, tag soft/second-order/policy/foreign narrative catalysts (policy_trial_balloon,
    # regulatory_narrative, peer_guidance, positioning_unwind) from already-fetched headlines and
    # fold them into macro_events (Tier-3 pullback-risk upgrade). Degrades to no-op on empty data.
    "enable_narrative_catalysts": _env_flag("VERUMTRADE_ENABLE_NARRATIVE_CATALYSTS", True),
    # When true, fetch the foreign-market & FX channel (Korea/Taiwan/Japan/China country ETFs +
    # USD index / USD-JPY) in the regime snapshot and derive foreign-stress events (Tier-3 item 6).
    # A proxy for cross-border flow stress (no free flows feed). Degrades to no-op when disabled.
    "enable_foreign_market_channel": _env_flag("VERUMTRADE_ENABLE_FOREIGN_MARKET_CHANNEL", True),
    # When true, run the deterministic positioning-risk gate in the risk judge: on a HIGH/CRITICAL
    # pullback vulnerability coinciding with a fragile tape (risk-off / rates-up / oil / foreign
    # stress), emit an explicit user-facing positioning_warning and a sharpened override directive
    # (Tier-4 pullback-risk upgrade). Symmetric to the catalyst gate; degrades to no-op otherwise.
    "enable_positioning_gate": _env_flag("VERUMTRADE_ENABLE_POSITIONING_GATE", True),
    # Decision integrity: market snapshot used to provide reference price context to LLM
    "decision_snapshot_source": os.getenv("VERUMTRADE_DECISION_SNAPSHOT_SOURCE", "executor_quote_first"),
    "executor_quote_max_rel_spread": float(os.getenv("VERUMTRADE_EXECUTOR_QUOTE_MAX_REL_SPREAD", "0.01")),

    # LLM burst/rate-limit mitigation for "manager" (deep-think) nodes.
    # These are particularly likely to hit HTTP 429 when the backend enforces RPM/TPM limits.
    "research_manager_min_delay_s": float(os.getenv("VERUMTRADE_RESEARCH_MANAGER_MIN_DELAY_S", "2.0")),
    "research_manager_max_retries": int(os.getenv("VERUMTRADE_RESEARCH_MANAGER_MAX_RETRIES", "6")),
    "research_manager_backoff_base_s": float(os.getenv("VERUMTRADE_RESEARCH_MANAGER_BACKOFF_BASE_S", "1.0")),
    "research_manager_backoff_max_s": float(os.getenv("VERUMTRADE_RESEARCH_MANAGER_BACKOFF_MAX_S", "30.0")),

    "risk_manager_min_delay_s": float(os.getenv("VERUMTRADE_RISK_MANAGER_MIN_DELAY_S", "2.0")),
    "risk_manager_max_retries": int(os.getenv("VERUMTRADE_RISK_MANAGER_MAX_RETRIES", "6")),
    "risk_manager_backoff_base_s": float(os.getenv("VERUMTRADE_RISK_MANAGER_BACKOFF_BASE_S", "1.0")),
    "risk_manager_backoff_max_s": float(os.getenv("VERUMTRADE_RISK_MANAGER_BACKOFF_MAX_S", "30.0")),

    # Structured-output resilience for debate hard gates (researchers, risk debators,
    # research/risk managers). When an LLM emits a malformed/incomplete contract block,
    # the node re-prompts with the exact validation error this many times before the
    # gate fires. 0 disables repair (legacy single-shot behavior).
    "debate_contract_repair_attempts": int(os.getenv("VERUMTRADE_DEBATE_CONTRACT_REPAIR_ATTEMPTS", "2")),

    # Gate policy for INTERMEDIATE debate contracts (researchers, risk debators,
    # research-manager thesis ledger, trader plan, analyst evidence admissibility).
    # When True (default) a contract that is still violated after the repair loop is
    # logged as a degradation and the run CONTINUES with best-effort artifacts, instead
    # of aborting the whole pipeline. The FINAL canonical decision/order gate is always
    # hard regardless of this flag — a malformed executable order is never accepted.
    # Set False to restore legacy hard-fault behavior on the intermediate gates.
    "debate_soft_intermediate_gates": os.getenv("VERUMTRADE_DEBATE_SOFT_INTERMEDIATE_GATES", "1") not in ("0", "false", "False"),

    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        # Prefer Alpaca first; interface.py will automatically fall back to yfinance/local/etc.
        "core_stock_apis": "alpaca",         # Options: alpaca, yfinance, alpha_vantage, twelve_data, local
        "technical_indicators": "alpaca",    # Options: alpaca, yfinance, alpha_vantage, twelve_data, local
        "fundamental_data": "alpha_vantage", # Options: openai, alpha_vantage, local
        "news_data": "alpha_vantage",        # Options: openai, alpha_vantage, google, local
    },

    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
        # Example: "get_news": "openai",               # Override category default
    },

    # Discovery pipeline controls
    "discovery": {
        # off: deterministic-only policy; cached_only: read cache but never invoke LLM;
        # adaptive: call LLM only when regime uncertainty is elevated.
        "policy_mode": os.getenv("VERUMTRADE_DISCOVERY_POLICY_MODE", "off"),
        "min_regime_confidence_for_no_llm": float(
            os.getenv("VERUMTRADE_DISCOVERY_MIN_REGIME_CONFIDENCE", "0.70")
        ),
        "enable_legacy_llm_technical_scoring": _env_flag(
            "VERUMTRADE_DISCOVERY_ENABLE_LEGACY_LLM_TECHNICAL_SCORING",
            False,
        ),
        "feature_matrix": {
            "cache_ttl_hours": int(os.getenv("VERUMTRADE_DISCOVERY_FEATURE_CACHE_TTL_HOURS", "24")),
        },
        "business_inflection": {
            "enabled": _env_flag("VERUMTRADE_DISCOVERY_BUSINESS_INFLECTION_ENABLED", False),
            "max_tickers": int(os.getenv("VERUMTRADE_DISCOVERY_BUSINESS_INFLECTION_MAX_TICKERS", "25")),
        },
    },

    "stage2_scoring": {
        "output": {
            "min_candidates_relaxation": [
                "loosen_rs_floor",
                "disable_sma50_requirement",
                "loosen_gap_down",
                "lower_dollar_volume_floor",
            ],
        },
    },

    # Portfolio analysis rate-limiting delays
    # These delays help prevent 429 errors when analyzing multiple stocks
    "stock_analysis_delay_s": float(os.getenv("VERUMTRADE_STOCK_ANALYSIS_DELAY_S", "5.0")),
    "post_triage_delay_s": float(os.getenv("VERUMTRADE_POST_TRIAGE_DELAY_S", "10.0")),

    # Portfolio triage settings
    "triage_max_tool_rounds": int(os.getenv("VERUMTRADE_TRIAGE_MAX_TOOL_ROUNDS", "6")),

    # Alpaca execution settings
    "alpaca_execution": {
        "enabled": False,
        "paper_trading": True,
        "position_size_pct": 0.10,
        "max_position_size_usd": None,
        # Execution-time risk guardrails (also enforced by prompts, but executor is source of truth)
        "max_concentration_pct": 0.20,
        "skip_if_open_orders_exist": True,
        "order_type": "market",  # or "limit"
        "limit_price_offset_pct": 0.001,
    },

    # Journal event-trigger inference (hybrid manual+inferred confirmation path)
    "journal_event_inference_enabled": _env_flag("JOURNAL_EVENT_INFERENCE_ENABLED", False),
    "journal_event_inference_provider": os.getenv("JOURNAL_EVENT_INFERENCE_PROVIDER", "rules"),
    "journal_event_inference_confidence_min": float(
        os.getenv("JOURNAL_EVENT_INFERENCE_CONFIDENCE_MIN", "0.70")
    ),
}

import os


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


DEFAULT_CONFIG = {

    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", "./results"),

    # Local/offline data root (used by "local" vendors). Override on Windows via TRADINGAGENTS_DATA_DIR.
    "data_dir": os.getenv(
        "TRADINGAGENTS_DATA_DIR",
        os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), ".")), "data"),
    ),

    "data_cache_dir": os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
        "dataflows/data_cache",
    ),

    # LLM settings (providers: openai, anthropic, google, deepseek, openrouter, ollama, qwen3-cn, glm)
    "llm_provider": os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai"),
    "deep_think_llm": "o4-mini",
    "quick_think_llm": "gpt-4o-mini",
    "backend_url": os.getenv("TRADINGAGENTS_BACKEND_URL", "https://api.openai.com/v1"),

    # GLM note:
    # Some ZhipuAI tiers/models (e.g., glm-4.7-flash) may require no parallel requests.
    # TradingAgents enforces in-process serialization when the selected model is exactly "glm-4.7-flash".

    # Qwen (DashScope) OpenAI-compatible extensions
    # When enabled, we pass DashScope-specific parameters (e.g., enable_thinking) through ChatOpenAI.
    "qwen_enable_thinking": _env_flag("TRADINGAGENTS_QWEN_ENABLE_THINKING", False),
    "qwen_enable_thinking_quick": _env_flag("TRADINGAGENTS_QWEN_ENABLE_THINKING_QUICK", False),
    "qwen_thinking_budget": (
        int(os.getenv("TRADINGAGENTS_QWEN_THINKING_BUDGET"))
        if os.getenv("TRADINGAGENTS_QWEN_THINKING_BUDGET")
        else None
    ),

    # Prompt/context budgeting (important for providers with strict context windows).
    "context_budget_mode": os.getenv("TRADINGAGENTS_CONTEXT_BUDGET_MODE", "adaptive"),
    "prompt_soft_cap_tokens": int(os.getenv("TRADINGAGENTS_PROMPT_SOFT_CAP_TOKENS", "45000")),
    "char_per_token_estimate": float(os.getenv("TRADINGAGENTS_CHAR_PER_TOKEN_ESTIMATE", "4.0")),
    "section_max_chars_report": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_REPORT", "2200")),
    "section_max_chars_history": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_HISTORY", "8000")),
    "section_max_chars_response": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_RESPONSE", "1800")),
    "section_max_chars_memory": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_MEMORY", "1200")),
    "section_max_chars_portfolio": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_PORTFOLIO", "2500")),
    "section_max_chars_trader_plan": int(os.getenv("TRADINGAGENTS_SECTION_MAX_CHARS_TRADER_PLAN", "2000")),
    "tool_response_max_chars": int(os.getenv("TRADINGAGENTS_TOOL_RESPONSE_MAX_CHARS", "12000")),
    "news_max_items": int(os.getenv("TRADINGAGENTS_NEWS_MAX_ITEMS", "12")),
    "max_debate_rounds_cap": int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS_CAP", "3")),
    "max_risk_rounds_cap": int(os.getenv("TRADINGAGENTS_MAX_RISK_ROUNDS_CAP", "3")),

    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": int(os.getenv("TRADINGAGENTS_MAX_RECUR_LIMIT", "100")),
    # Guardrails to prevent infinite analyst->tools loops (LangGraph recursion errors)
    "max_tool_calls_per_analyst": int(os.getenv("TRADINGAGENTS_MAX_TOOL_CALLS_PER_ANALYST", "8")),
    "max_tool_calls_total": int(os.getenv("TRADINGAGENTS_MAX_TOOL_CALLS_TOTAL", "50")),

    # LLM burst/rate-limit mitigation for "manager" (deep-think) nodes.
    # These are particularly likely to hit HTTP 429 when the backend enforces RPM/TPM limits.
    "research_manager_min_delay_s": float(os.getenv("TRADINGAGENTS_RESEARCH_MANAGER_MIN_DELAY_S", "2.0")),
    "research_manager_max_retries": int(os.getenv("TRADINGAGENTS_RESEARCH_MANAGER_MAX_RETRIES", "6")),
    "research_manager_backoff_base_s": float(os.getenv("TRADINGAGENTS_RESEARCH_MANAGER_BACKOFF_BASE_S", "1.0")),
    "research_manager_backoff_max_s": float(os.getenv("TRADINGAGENTS_RESEARCH_MANAGER_BACKOFF_MAX_S", "30.0")),

    "risk_manager_min_delay_s": float(os.getenv("TRADINGAGENTS_RISK_MANAGER_MIN_DELAY_S", "2.0")),
    "risk_manager_max_retries": int(os.getenv("TRADINGAGENTS_RISK_MANAGER_MAX_RETRIES", "6")),
    "risk_manager_backoff_base_s": float(os.getenv("TRADINGAGENTS_RISK_MANAGER_BACKOFF_BASE_S", "1.0")),
    "risk_manager_backoff_max_s": float(os.getenv("TRADINGAGENTS_RISK_MANAGER_BACKOFF_MAX_S", "30.0")),

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

    # Portfolio analysis rate-limiting delays
    # These delays help prevent 429 errors when analyzing multiple stocks
    "stock_analysis_delay_s": float(os.getenv("TRADINGAGENTS_STOCK_ANALYSIS_DELAY_S", "5.0")),
    "post_triage_delay_s": float(os.getenv("TRADINGAGENTS_POST_TRIAGE_DELAY_S", "10.0")),

    # Portfolio triage settings
    "triage_max_tool_rounds": int(os.getenv("TRADINGAGENTS_TRIAGE_MAX_TOOL_ROUNDS", "6")),

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
}

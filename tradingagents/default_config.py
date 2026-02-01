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

    # LLM settings (providers: openai, anthropic, google, deepseek, openrouter, ollama, qwen3-cn)
    "llm_provider": os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai"),
    "deep_think_llm": "o4-mini",
    "quick_think_llm": "gpt-4o-mini",
    "backend_url": os.getenv("TRADINGAGENTS_BACKEND_URL", "https://api.openai.com/v1"),

    # Qwen (DashScope) OpenAI-compatible extensions
    # When enabled, we pass DashScope-specific parameters (e.g., enable_thinking) through ChatOpenAI.
    "qwen_enable_thinking": _env_flag("TRADINGAGENTS_QWEN_ENABLE_THINKING", False),
    "qwen_enable_thinking_quick": _env_flag("TRADINGAGENTS_QWEN_ENABLE_THINKING_QUICK", False),
    "qwen_thinking_budget": (
        int(os.getenv("TRADINGAGENTS_QWEN_THINKING_BUDGET"))
        if os.getenv("TRADINGAGENTS_QWEN_THINKING_BUDGET")
        else None
    ),

    # Debate and discussion settings
    "max_debate_rounds": 1,
    "max_risk_discuss_rounds": 1,
    "max_recur_limit": int(os.getenv("TRADINGAGENTS_MAX_RECUR_LIMIT", "100")),
    # Guardrails to prevent infinite analyst->tools loops (LangGraph recursion errors)
    "max_tool_calls_per_analyst": int(os.getenv("TRADINGAGENTS_MAX_TOOL_CALLS_PER_ANALYST", "8")),
    "max_tool_calls_total": int(os.getenv("TRADINGAGENTS_MAX_TOOL_CALLS_TOTAL", "50")),

    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        # Prefer Alpaca first; interface.py will automatically fall back to yfinance/local/etc.
        "core_stock_apis": "alpaca",         # Options: alpaca, yfinance, alpha_vantage, local
        "technical_indicators": "alpaca",    # Options: alpaca, yfinance, alpha_vantage, local
        "fundamental_data": "alpha_vantage", # Options: openai, alpha_vantage, local
        "news_data": "alpha_vantage",        # Options: openai, alpha_vantage, google, local
    },

    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
        # Example: "get_news": "openai",               # Override category default
    },

    # Alpaca execution settings
    "alpaca_execution": {
        "enabled": False,
        "paper_trading": True,
        "position_size_pct": 0.10,
        "max_position_size_usd": None,
        "order_type": "market",  # or "limit"
        "limit_price_offset_pct": 0.001,
    },
}

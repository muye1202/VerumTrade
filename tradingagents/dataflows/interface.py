from typing import Annotated
import json
import logging
import inspect

# Import from vendor-specific modules
from .vendors.local.local import get_YFin_data, get_finnhub_news, get_finnhub_company_insider_sentiment, get_finnhub_company_insider_transactions, get_simfin_balance_sheet, get_simfin_cashflow, get_simfin_income_statements, get_reddit_global_news, get_reddit_company_news
from .vendors.yfinance.y_finance import get_YFin_data_online, get_stock_stats_indicators_window, get_balance_sheet as get_yfinance_balance_sheet, get_cashflow as get_yfinance_cashflow, get_income_statement as get_yfinance_income_statement, get_insider_transactions as get_yfinance_insider_transactions
from .vendors.alpaca.alpaca import get_stock_data_alpaca, AlpacaConnectionError
from .vendors.finnhub.finnhub_vendor import get_company_news_finnhub, get_global_news_finnhub, get_news_sentiment_finnhub, get_earnings_calendar_finnhub
from .vendors.sec_edgar.sec_edgar_vendor import fetch_recent_filings, fetch_company_filings
from .vendors.openai.openai import get_stock_news_openai, get_global_news_openai
from .vendors.alpha_vantage.alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_insider_sentiment as get_alpha_vantage_insider_sentiment,
    get_news as get_alpha_vantage_news
)
from .vendors.alpha_vantage.alpha_vantage_common import AlphaVantageRateLimitError
from .vendors.twelve_data.twelve_data import get_indicator as get_twelve_data_indicator
from .vendors.twelve_data.twelve_data_common import TwelveDataRateLimitError
import re

logger = logging.getLogger(__name__)

# Configuration and routing logic
from .config import get_config

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News (public/insiders, original/processed)",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_sentiment",
            "get_insider_transactions",
            "get_news_sentiment",
            "get_recent_sec_filings",
        ]
    }
}

VENDOR_LIST = [
    "local",
    "alpaca",
    "yfinance",
    "twelve_data",
    "openai",
    "finnhub",
    "sec_edgar"
]


_WARN_MISSING_METHODS = {"get_stock_data", "get_indicators"}


def _clip_middle_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = f"\n...[TRUNCATED {len(text) - max_chars} chars]...\n"
    keep = max_chars - len(marker)
    if keep <= 20:
        return text[:max_chars]
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + text[-tail:]


def _compact_tool_output(method: str, value):
    cfg = get_config()
    mode = str(cfg.get("context_budget_mode", "adaptive")).strip().lower()
    if mode == "off":
        return value

    global_cap = int(cfg.get("tool_response_max_chars", 12000))
    method_cap_map = {
        "get_news": global_cap,
        "get_global_news": global_cap,
        "get_fundamentals": int(global_cap * 1.2),
        "get_balance_sheet": int(global_cap * 1.2),
        "get_cashflow": int(global_cap * 1.2),
        "get_income_statement": int(global_cap * 1.2),
    }
    max_chars = int(method_cap_map.get(method, global_cap))

    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)

    if len(text) <= max_chars:
        return value
    return _clip_middle_text(text, max_chars)


def _missing_value_summary(value) -> str | None:
    """Return a short summary if a tool result likely contains missing values."""
    if value is None:
        return "returned None"

    s = str(value)
    if not s.strip():
        return "returned empty output"

    # Common sentinel strings in this repo (and in pandas-ish outputs).
    matches = re.findall(r"\bN/A\b|\bnan\b|\bNaN\b", s)
    if matches:
        return f"contains {len(matches)} missing markers (e.g. {matches[0]})"

    return None


def _call_vendor_impl(impl_func, *args, **kwargs):
    """Call a vendor function while trimming unsupported args/kwargs.

    Some vendor implementations share a logical method name but expose different
    signatures (e.g. one accepts `ticker`, another accepts `ticker, curr_date`).
    """
    sig = inspect.signature(impl_func)
    params = list(sig.parameters.values())

    accepts_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    if accepts_varargs:
        call_args = args
    else:
        positional_slots = sum(
            1
            for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        call_args = args[:positional_slots]

    if accepts_varkw:
        call_kwargs = kwargs
    else:
        valid_kw = {
            p.name
            for p in params
            if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        call_kwargs = {k: v for k, v in kwargs.items() if k in valid_kw}

    return impl_func(*call_args, **call_kwargs)

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpaca": get_stock_data_alpaca,
        "yfinance": get_YFin_data_online,
        "alpha_vantage": get_alpha_vantage_stock,
        "local": get_YFin_data,
    },
    # technical_indicators
    "get_indicators": {
        "alpaca": get_stock_stats_indicators_window,
        "yfinance": get_stock_stats_indicators_window,
        "twelve_data": get_twelve_data_indicator,
        "alpha_vantage": get_alpha_vantage_indicator,
        "local": get_stock_stats_indicators_window
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        "local": get_simfin_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        "local": get_simfin_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        "local": get_simfin_income_statements,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "finnhub": get_company_news_finnhub,
        "openai": get_stock_news_openai,
        "local": [get_finnhub_news, get_reddit_company_news],
    },
    "get_global_news": {
        "finnhub": get_global_news_finnhub,
        "openai": get_global_news_openai,
        "local": get_reddit_global_news,
    },
    "get_insider_sentiment": {
        "alpha_vantage": get_alpha_vantage_insider_sentiment,
        "local": get_finnhub_company_insider_sentiment
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        "local": get_finnhub_company_insider_transactions,
    },
    "get_news_sentiment": {
        "finnhub": get_news_sentiment_finnhub,
    },
    "get_recent_sec_filings": {
        "sec_edgar": fetch_recent_filings,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support."""
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)

    # Handle comma-separated vendors
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Get all available vendors for this method for fallback
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    
    # Create fallback vendor list: primary vendors first, then remaining vendors as fallbacks
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    # Debug: Print fallback ordering
    primary_str = " → ".join(primary_vendors)
    fallback_str = " → ".join(fallback_vendors)
    logger.debug(
        "%s primary=[%s] fallback_order=[%s]",
        method,
        primary_str,
        fallback_str,
    )

    # Track results and execution state
    results = []
    vendor_attempt_count = 0
    any_primary_vendor_attempted = False
    successful_vendor = None

    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            if vendor in primary_vendors:
                logger.info(
                    "Vendor '%s' not supported for method '%s'; falling back.",
                    vendor,
                    method,
                )
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        is_primary_vendor = vendor in primary_vendors
        vendor_attempt_count += 1

        # Track if we attempted any primary vendor
        if is_primary_vendor:
            any_primary_vendor_attempted = True

        # Debug: Print current attempt
        vendor_type = "PRIMARY" if is_primary_vendor else "FALLBACK"
        logger.debug(
            "Attempting %s vendor '%s' for %s (attempt #%s)",
            vendor_type,
            vendor,
            method,
            vendor_attempt_count,
        )

        # Handle list of methods for a vendor
        if isinstance(vendor_impl, list):
            vendor_methods = [(impl, vendor) for impl in vendor_impl]
            logger.debug(
                "Vendor '%s' has %s implementations for %s",
                vendor,
                len(vendor_methods),
                method,
            )
        else:
            vendor_methods = [(vendor_impl, vendor)]

        # Run methods for this vendor
        vendor_results = []
        for impl_func, vendor_name in vendor_methods:
            try:
                logger.debug("Calling %s from vendor '%s'", impl_func.__name__, vendor_name)
                result = _call_vendor_impl(impl_func, *args, **kwargs)
                vendor_results.append(result)
                logger.debug("%s from vendor '%s' completed successfully", impl_func.__name__, vendor_name)
                    
            except AlpacaConnectionError as e:
                if vendor == "alpaca":
                    logger.debug(
                        "Alpaca market data unavailable (%s); falling back to next vendor.",
                        e,
                    )
                continue
            except AlphaVantageRateLimitError as e:
                if vendor == "alpha_vantage":
                    logger.warning(
                        "Alpha Vantage rate limit exceeded; falling back. details=%s",
                        e,
                    )
                # Continue to next vendor for fallback
                continue
            except TwelveDataRateLimitError as e:
                if vendor == "twelve_data":
                    logger.warning(
                        "Twelve Data rate limit exceeded; falling back. details=%s",
                        e,
                    )
                continue
            except Exception as e:
                # Log error but continue with other implementations
                logger.warning(
                    "%s from vendor '%s' failed: %s",
                    impl_func.__name__,
                    vendor_name,
                    e,
                )
                continue

        # Add this vendor's results
        if vendor_results:
            results.extend(vendor_results)
            successful_vendor = vendor
            result_summary = f"Got {len(vendor_results)} result(s)"
            logger.debug("Vendor '%s' succeeded - %s", vendor, result_summary)

            # Always warn when market-data/indicator tools return N/A/NaN, so users
            # can diagnose bad dates, rate limits, missing data, etc.
            if method in _WARN_MISSING_METHODS:
                for idx, vr in enumerate(vendor_results, start=1):
                    summary = _missing_value_summary(vr)
                    if summary:
                        logger.warning(
                            "%s vendor '%s' result #%s %s. "
                            "This can indicate a non-trading day, missing vendor data, or a failed upstream call.",
                            method,
                            vendor,
                            idx,
                            summary,
                        )
            
            # Stopping logic: Stop after first successful vendor for single-vendor configs
            # Multiple vendor configs (comma-separated) may want to collect from multiple sources
            if len(primary_vendors) == 1:
                logger.debug(
                    "Stopping after successful vendor '%s' (single-vendor config)",
                    vendor,
                )
                break
        else:
            logger.debug("Vendor '%s' produced no results for %s", vendor, method)

    # Final result summary
    if not results:
        logger.error(
            "All %s vendor attempts failed for method '%s'",
            vendor_attempt_count,
            method,
        )
        raise RuntimeError(f"All vendor implementations failed for method '{method}'")
    else:
        logger.debug(
            "Method '%s' completed with %s result(s) from %s vendor attempt(s)",
            method,
            len(results),
            vendor_attempt_count,
        )

    # Return single result if only one, otherwise concatenate as string, then compact.
    if len(results) == 1:
        return _compact_tool_output(method, results[0])
    else:
        # Convert all results to strings and concatenate
        merged = '\n'.join(str(result) for result in results)
        return _compact_tool_output(method, merged)

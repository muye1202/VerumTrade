"""
Lightweight Alpaca portfolio fetcher - works independently of AlpacaExecutor.

Called before graph execution to inject live portfolio state into agent prompts,
so the Trader and Risk Manager nodes can make portfolio-aware decisions.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def fetch_portfolio_capital(
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    paper: bool = True,
) -> Dict[str, Any]:
    """
    Fetch normalized account capital fields from Alpaca.

    Returns:
        dict with equity, cash, buying_power, effective_buying_power, positions_count.
        On failure, returns zeroed fields plus an error string.
    """
    try:
        return _fetch_portfolio_capital_from_alpaca(api_key, secret_key, paper)
    except Exception as e:
        return {
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "effective_buying_power": 0.0,
            "positions_count": 0,
            "error": str(e),
        }


def fetch_portfolio_context(
    ticker: str,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    paper: bool = True,
) -> str:
    """
    Fetch current portfolio state from Alpaca and format as a prompt-ready string.

    Returns a formatted string describing:
    - Account summary (equity, cash, buying power)
    - All current positions with P&L
    - Specific position detail for the target ticker
    - Actionability guidance (can we BUY/SELL this ticker?)

    If Alpaca is unavailable, returns a minimal fallback string so the pipeline
    never crashes - it just operates without portfolio awareness.
    """
    quote_price = None
    try:
        import yfinance as yf
        t_obj = yf.Ticker(ticker.upper())
        info = getattr(t_obj, "fast_info", None)
        if info:
            quote_price = getattr(info, "last_price", getattr(info, "regular_market_price", None))
        if quote_price is None or quote_price == 0:
            hist = t_obj.history(period="1d")
            if not hist.empty:
                quote_price = float(hist["Close"].iloc[-1])
    except Exception:
        pass

    try:
        return _fetch_from_alpaca(ticker, api_key, secret_key, paper, quote_price)
    except Exception as e:
        return _fallback_context(ticker, str(e), quote_price)


def fetch_portfolio_symbols(
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    paper: bool = True,
) -> List[str]:
    """
    Fetch currently open portfolio position symbols from Alpaca.

    Returns:
        Uppercased ticker symbols. Returns an empty list on any failure.
    """
    try:
        client = _build_trading_client(api_key, secret_key, paper)
        positions = client.get_all_positions()
        symbols: List[str] = []
        for p in positions:
            symbol = str(getattr(p, "symbol", "") or "").strip().upper()
            if symbol:
                symbols.append(symbol)
        # Deduplicate while preserving order
        return list(dict.fromkeys(symbols))
    except Exception:
        return []


def _build_trading_client(
    api_key: Optional[str],
    secret_key: Optional[str],
    paper: bool,
):
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        raise RuntimeError("alpaca-py not installed")

    ak = api_key or os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY")
    sk = secret_key or os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_SECRET_KEY")

    if not ak or not sk:
        raise RuntimeError("Alpaca credentials not found in environment")

    base_url = (
        os.getenv("APCA_API_BASE_URL")
        or os.getenv("ALPACA_API_BASE_URL")
        or os.getenv("APCA_ENDPOINT")
        or os.getenv("ALPACA_ENDPOINT")
    )
    if base_url:
        base_url = base_url.strip()
        if base_url.endswith("/v2"):
            base_url = base_url[: -len("/v2")]
        return TradingClient(api_key=ak, secret_key=sk, paper=paper, url_override=base_url)
    return TradingClient(api_key=ak, secret_key=sk, paper=paper)


def _normalize_portfolio_capital(account, positions_count: int) -> Dict[str, Any]:
    equity = _safe_float(getattr(account, "equity", None), default=0.0)
    cash = _safe_float(getattr(account, "cash", None), default=0.0)
    buying_power_raw = _safe_float(getattr(account, "buying_power", None), default=None)
    buying_power = cash if buying_power_raw is None else float(buying_power_raw)
    effective_buying_power = max(float(cash), float(buying_power))
    return {
        "equity": float(equity),
        "cash": float(cash),
        "buying_power": float(buying_power),
        "effective_buying_power": float(effective_buying_power),
        "positions_count": int(positions_count),
    }


def _fetch_portfolio_capital_from_alpaca(
    api_key: Optional[str],
    secret_key: Optional[str],
    paper: bool,
) -> Dict[str, Any]:
    client = _build_trading_client(api_key, secret_key, paper)
    account = client.get_account()
    positions = client.get_all_positions()
    return _normalize_portfolio_capital(account, positions_count=len(positions))


def _fetch_from_alpaca(
    ticker: str,
    api_key: Optional[str],
    secret_key: Optional[str],
    paper: bool,
    quote_price: Optional[float] = None,
) -> str:
    client = _build_trading_client(api_key, secret_key, paper)

    account = client.get_account()
    positions = client.get_all_positions()
    capital = _normalize_portfolio_capital(account, positions_count=len(positions))

    return _format_portfolio_string(ticker, positions, capital, quote_price=quote_price)


def _format_portfolio_string(
    ticker: str,
    positions,
    capital: Dict[str, Any],
    quote_price: Optional[float] = None,
) -> str:
    ticker_upper = ticker.upper()

    equity = float(capital["equity"])
    cash = float(capital["cash"])
    buying_power = float(capital["buying_power"])
    effective_buying_power = float(capital["effective_buying_power"])

    lines = []
    lines.append("## Current Portfolio State (Live from Brokerage)")
    lines.append(f"- Account Equity: ${equity:,.2f}")
    lines.append(f"- Cash Available: ${cash:,.2f}")
    lines.append(f"- Buying Power: ${buying_power:,.2f}")
    lines.append(f"- Effective Buying Power: ${effective_buying_power:,.2f}")
    lines.append(f"- Number of Open Positions: {int(capital['positions_count'])}")
    lines.append("")

    if positions:
        lines.append("### All Current Positions:")
        lines.append("| Symbol | Qty | Avg Cost | Current Value | Unrealized P&L | P&L % |")
        lines.append("|--------|-----|----------|---------------|----------------|-------|")
        for p in positions:
            sym = p.symbol
            qty = float(p.qty)
            mkt_val = float(p.market_value)
            upl = float(p.unrealized_pl)
            uplpc = float(p.unrealized_plpc) * 100
            avg_cost = (
                float(p.avg_entry_price)
                if hasattr(p, "avg_entry_price") and p.avg_entry_price
                else (mkt_val - upl) / qty
                if qty
                else 0
            )
            marker = " **< TARGET**" if sym.upper() == ticker_upper else ""
            lines.append(
                f"| {sym}{marker} | {qty:,.0f} | ${avg_cost:,.2f} | ${mkt_val:,.2f} | "
                f"{'+'if upl >= 0 else ''}${upl:,.2f} | {'+'if uplpc >= 0 else ''}{uplpc:.2f}% |"
            )
        lines.append("")

    target_position = None
    for p in positions:
        if p.symbol.upper() == ticker_upper:
            target_position = p
            break

    lines.append(f"### Position in {ticker_upper} (Analysis Target):")
    if target_position:
        qty = float(target_position.qty)
        mkt_val = float(target_position.market_value)
        upl = float(target_position.unrealized_pl)
        uplpc = float(target_position.unrealized_plpc) * 100
        avg_cost = (
            float(target_position.avg_entry_price)
            if hasattr(target_position, "avg_entry_price") and target_position.avg_entry_price
            else (mkt_val - upl) / qty
            if qty
            else 0
        )
        current_price = mkt_val / qty if qty else 0

        lines.append(f"- **Currently Holding: {qty:,.0f} shares**")
        lines.append(f"- Average Entry Price: ${avg_cost:,.2f}")
        lines.append(f"- Current Price (approx): ${current_price:,.2f}")
        if quote_price is not None and quote_price > 0:
            lines.append(f"- Current Market Reference Price: ${quote_price:,.2f}")
        lines.append(f"- Market Value: ${mkt_val:,.2f}")
        lines.append(
            f"- Unrealized P&L: {'+'if upl >= 0 else ''}${upl:,.2f} "
            f"({'+'if uplpc >= 0 else ''}{uplpc:.2f}%)"
        )
        lines.append("")
        lines.append("**ACTIONABILITY:**")
        lines.append(
            f"- BUY -> Will ADD to existing {qty:,.0f}-share position "
            f"(effective buying power: ${effective_buying_power:,.2f})"
        )
        lines.append(f"- SELL -> Will LIQUIDATE some or all of your {qty:,.0f} shares")
        lines.append(f"- HOLD -> Maintain current {qty:,.0f}-share position")
    else:
        lines.append(f"- **You have ZERO shares of {ticker_upper}.**")
        if quote_price is not None and quote_price > 0:
            lines.append(f"- Current Market Reference Price: ${quote_price:,.2f}")
        lines.append("- No existing position to sell or hold.")
        lines.append("")
        lines.append("**ACTIONABILITY:**")
        lines.append(
            f"- BUY -> Open a NEW position "
            f"(effective buying power: ${effective_buying_power:,.2f})"
        )
        lines.append("- SELL -> **NOT POSSIBLE** - you own zero shares. Do NOT recommend SELL.")
        lines.append(
            "- HOLD -> No position exists; this means PASS on this opportunity."
        )

    lines.append("")
    lines.append(
        f"**Available capital: ${effective_buying_power:,.2f} effective buying power "
        f"(cash ${cash:,.2f}, buying power ${buying_power:,.2f})**"
    )

    return "\n".join(lines)


def _fallback_context(ticker: str, error_reason: str, quote_price: Optional[float] = None) -> str:
    base = (
        f"## Portfolio Context\n"
        f"Portfolio data unavailable ({error_reason}).\n"
        f"Assume NO existing position in {ticker.upper()} unless evidence suggests otherwise.\n"
        f"Do NOT recommend SELL if there is no confirmed existing position.\n"
    )
    if quote_price is not None and quote_price > 0:
        base += f"\n- **Current Market Reference Price:** ${quote_price:,.2f} (use this to anchor your entries/stops)\n"
    return base


def _safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

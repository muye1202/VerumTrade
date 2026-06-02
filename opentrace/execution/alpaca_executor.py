"""
Alpaca execution module.

This file is imported by the CLI and graph, but Alpaca execution is optional.
Keep imports lazy/optional so the analysis pipeline still works without alpaca-py.
"""

_ALPACA_IMPORT_ERROR: Exception | None = None

try:  # Optional dependency: alpaca-py
    from alpaca.trading.client import TradingClient  # type: ignore
    from alpaca.trading.requests import (  # type: ignore
        MarketOrderRequest,
        LimitOrderRequest,
        StopOrderRequest,
        StopLimitOrderRequest,
        TrailingStopOrderRequest,
        TakeProfitRequest,
        StopLossRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderType, OrderClass  # type: ignore
    from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
    from alpaca.data.requests import StockLatestQuoteRequest  # type: ignore
except Exception as e:  # pragma: no cover
    TradingClient = None  # type: ignore[assignment]
    MarketOrderRequest = None  # type: ignore[assignment]
    LimitOrderRequest = None  # type: ignore[assignment]
    StopOrderRequest = None  # type: ignore[assignment]
    StopLimitOrderRequest = None  # type: ignore[assignment]
    TrailingStopOrderRequest = None  # type: ignore[assignment]
    TakeProfitRequest = None  # type: ignore[assignment]
    StopLossRequest = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    TimeInForce = None  # type: ignore[assignment]
    OrderType = None  # type: ignore[assignment]
    OrderClass = None  # type: ignore[assignment]
    StockHistoricalDataClient = None  # type: ignore[assignment]
    StockLatestQuoteRequest = None  # type: ignore[assignment]
    _ALPACA_IMPORT_ERROR = e
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, Any, Optional, Literal
import os
import logging
from pathlib import Path
import json
import re
from opentrace.utils.market_session import now_et
import time


@dataclass(frozen=True)
class OrderSpec:
    order_type: str
    time_in_force: str
    extended_hours: bool = False
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_percent: Optional[float] = None
    trail_price: Optional[float] = None
    requested_limit_price: Optional[float] = None


def _normalize_order_type(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().upper()
    if not v:
        return None
    v = v.replace("-", "_").replace(" ", "_")

    mapping = {
        "MKT": "MARKET",
        "MARKET": "MARKET",
        "MARKET_ORDER": "MARKET",
        "LMT": "LIMIT",
        "LIMIT": "LIMIT",
        "LIMIT_ORDER": "LIMIT",
        "STOP": "STOP",
        "STOP_ORDER": "STOP",
        "STOPLIMIT": "STOP_LIMIT",
        "STOP_LIMIT": "STOP_LIMIT",
        "STOP_LIMIT_ORDER": "STOP_LIMIT",
        "TRAILINGSTOP": "TRAILING_STOP",
        "TRAILING_STOP": "TRAILING_STOP",
        "TRAILING_STOP_ORDER": "TRAILING_STOP",
        "TRAIL_STOP": "TRAILING_STOP",
        "TRAILING": "TRAILING_STOP",
    }
    out = mapping.get(v)
    if out in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"}:
        return out
    return None


def _normalize_time_in_force(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = str(value).strip().upper()
    if not v:
        return None
    v = v.replace("-", "_").replace(" ", "_")
    if v in {"DAY", "GTC"}:
        return v
    return None


def _parse_floatish(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.upper() in {"N/A", "NA", "NONE", "-"}:
        return None
    m = re.search(r"([-+]?\d[\d,]*\.?\d*)", s.replace("%", ""))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def normalize_order_inputs(
    *,
    default_order_type: str,
    default_time_in_force: str,
    side: str,
    current_price: float,
    limit_price_offset_pct: float,
    agent_order_type: Optional[str] = None,
    agent_time_in_force: Optional[str] = None,
    agent_extended_hours: Optional[bool] = None,
    agent_limit_price: Optional[float] = None,
    agent_stop_price: Optional[float] = None,
    agent_trail_percent: Optional[float] = None,
    agent_trail_price: Optional[float] = None,
) -> tuple[Optional[OrderSpec], Optional[str]]:
    """
    Pure validation/normalization helper for AlpacaExecutor.

    Returns (OrderSpec, error). This function does not call the network and does not
    require alpaca-py to be installed.
    """
    agent_ot = _normalize_order_type(agent_order_type) if agent_order_type is not None else None
    if agent_order_type is not None and str(agent_order_type).strip() and agent_ot is None:
        return None, f"Unsupported ORDER_TYPE '{agent_order_type}'."

    agent_tif = _normalize_time_in_force(agent_time_in_force) if agent_time_in_force is not None else None
    if agent_time_in_force is not None and str(agent_time_in_force).strip() and agent_tif is None:
        return None, f"Unsupported TIME_IN_FORCE '{agent_time_in_force}'. Use DAY or GTC."

    order_type = agent_ot or _normalize_order_type(default_order_type) or "MARKET"
    tif = agent_tif or _normalize_time_in_force(default_time_in_force) or "DAY"
    side_u = str(side).strip().upper()

    extended_hours = bool(agent_extended_hours) if agent_extended_hours is not None else False
    if extended_hours:
        if order_type != "LIMIT":
            return None, "EXTENDED_HOURS requires ORDER_TYPE=LIMIT (MARKET/STOP types are not allowed)."
        if tif != "DAY":
            return None, "EXTENDED_HOURS requires TIME_IN_FORCE=DAY."

    limit_price = _parse_floatish(agent_limit_price)
    stop_price = _parse_floatish(agent_stop_price)
    trail_percent = _parse_floatish(agent_trail_percent)
    trail_price = _parse_floatish(agent_trail_price)

    if current_price <= 0:
        return None, "Current price must be positive."

    if order_type == "MARKET":
        return OrderSpec(order_type=order_type, time_in_force=tif, extended_hours=extended_hours), None

    if order_type == "LIMIT":
        requested_limit_price: Optional[float] = None
        if limit_price is None:
            if side_u == "BUY":
                limit_price = current_price * (1 + float(limit_price_offset_pct))
            else:
                limit_price = current_price * (1 - float(limit_price_offset_pct))
            requested_limit_price = round(float(limit_price), 2)
            limit_price = requested_limit_price
        else:
            if limit_price <= 0:
                return None, "LIMIT_PRICE must be positive."
            requested_limit_price = round(float(limit_price), 2)
            limit_price = requested_limit_price
        return (
            OrderSpec(
                order_type=order_type,
                time_in_force=tif,
                extended_hours=extended_hours,
                limit_price=limit_price,
                requested_limit_price=requested_limit_price,
            ),
            None,
        )

    if order_type == "STOP":
        if stop_price is None or stop_price <= 0:
            return None, "STOP_PRICE is required and must be positive for STOP orders."
        return (
            OrderSpec(
                order_type=order_type,
                time_in_force=tif,
                extended_hours=extended_hours,
                stop_price=round(float(stop_price), 2),
            ),
            None,
        )

    if order_type == "STOP_LIMIT":
        if stop_price is None or stop_price <= 0:
            return None, "STOP_PRICE is required and must be positive for STOP_LIMIT orders."
        if limit_price is None or limit_price <= 0:
            return None, "LIMIT_PRICE is required and must be positive for STOP_LIMIT orders."
        return (
            OrderSpec(
                order_type=order_type,
                time_in_force=tif,
                extended_hours=extended_hours,
                stop_price=round(float(stop_price), 2),
                limit_price=round(float(limit_price), 2),
                requested_limit_price=round(float(limit_price), 2),
            ),
            None,
        )

    if order_type == "TRAILING_STOP":
        if (trail_percent is None and trail_price is None) or (trail_percent is not None and trail_price is not None):
            return None, "TRAILING_STOP requires exactly one of TRAIL_PERCENT or TRAIL_PRICE."
        if trail_percent is not None:
            if trail_percent <= 0:
                return None, "TRAIL_PERCENT must be positive."
            return (
                OrderSpec(
                    order_type=order_type,
                    time_in_force=tif,
                    extended_hours=extended_hours,
                    trail_percent=float(trail_percent),
                ),
                None,
            )
        if trail_price is not None:
            if trail_price <= 0:
                return None, "TRAIL_PRICE must be positive."
            return (
                OrderSpec(
                    order_type=order_type,
                    time_in_force=tif,
                    extended_hours=extended_hours,
                    trail_price=round(float(trail_price), 2),
                ),
                None,
            )

    return None, f"Unsupported ORDER_TYPE '{agent_order_type}'."


def _clean_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    # Normalize common "endpoint" vars that include the /v2 suffix.
    if v.endswith("/v2"):
        v = v[: -len("/v2")]
    return v


def _message_for_log(msg: Any) -> Dict[str, Any]:
    """
    Best-effort conversion of LangChain/LangGraph message objects to JSON-safe dicts.

    Avoid importing provider-specific message classes; use duck typing.
    """
    if isinstance(msg, tuple) and len(msg) == 2:
        role, content = msg
        return {"role": str(role), "type": "tuple", "content": str(content)}

    if isinstance(msg, dict):
        role = msg.get("role") or msg.get("type") or "dict"
        content = msg.get("content", msg)
        tool_calls = msg.get("tool_calls")
        out: Dict[str, Any] = {"role": str(role), "type": "dict", "content": str(content)}
        if tool_calls is not None:
            out["tool_calls"] = tool_calls
        return out

    msg_type = getattr(msg, "type", None) or msg.__class__.__name__
    content = getattr(msg, "content", msg)
    out = {"type": str(msg_type), "content": str(content)}

    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        try:
            out["tool_calls"] = tool_calls
        except Exception:
            out["tool_calls"] = str(tool_calls)

    return out


def _analysis_state_for_log(state: Optional[Dict[str, Any]]) -> Any:
    """Convert analysis_state to something JSON-serializable (especially messages)."""
    if state is None:
        return None

    if not isinstance(state, dict):
        return str(state)

    out: Dict[str, Any] = {}
    for k, v in state.items():
        if k == "messages" and isinstance(v, list):
            out[k] = [_message_for_log(m) for m in v]
        else:
            out[k] = v
    return out


class ExtendedHoursSubmissionError(RuntimeError):
    """Raised when an extended-hours order request/submission fails and must not be retried without the flag."""


class AlpacaExecutor:
    """
    Executes OpenTrace trading signals on Alpaca paper trading.

    Handles:
    - Signal translation (BUY/SELL/HOLD -> Alpaca orders)
    - Position management
    - Order execution and tracking
    - Portfolio rebalancing
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
        position_size_pct: float = 0.10,  # Use 10% of portfolio per position
        max_position_size_usd: Optional[float] = None,
        max_concentration_pct: float = 0.20,
        skip_if_open_orders_exist: bool = True,
        order_type: str = "market",
        time_in_force: str = "DAY",
        limit_price_offset_pct: float = 0.001,  # 0.1% offset for limit orders
        log_dir: Optional[str] = None
    ):
        """
        Initialize Alpaca executor.

        Args:
            api_key: Alpaca API key (defaults to ALPACA_API_KEY env var)
            secret_key: Alpaca secret key (defaults to ALPACA_SECRET_KEY env var)
            paper: Use paper trading (True) or live trading (False)
            position_size_pct: Percentage of portfolio to allocate per position
            max_position_size_usd: Maximum dollar amount per position (overrides pct if set)
            max_concentration_pct: Maximum position concentration as % of equity (default 0.20)
            skip_if_open_orders_exist: If True, skip new orders when open orders exist for the ticker
            order_type: Default order type (market/limit/stop/stop_limit/trailing_stop)
            time_in_force: Default time in force (DAY/GTC)
            limit_price_offset_pct: Offset percentage for limit orders
            log_dir: Directory for execution logs
        """

        if TradingClient is None or StockHistoricalDataClient is None:
            raise RuntimeError(
                "Alpaca execution requires the 'alpaca-py' package. Install it to enable paper trading execution."
            ) from _ALPACA_IMPORT_ERROR

        # Get credentials
        self.api_key = (
            api_key
            or os.getenv("APCA_API_KEY_ID")
            or os.getenv("ALPACA_API_KEY")
        )
        self.secret_key = (
            secret_key
            or os.getenv("APCA_API_SECRET_KEY")
            or os.getenv("ALPACA_SECRET_KEY")
        )

        if not self.api_key or not self.secret_key:
            raise ValueError(
                "Alpaca credentials not found. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
                "(or ALPACA_API_KEY and ALPACA_SECRET_KEY) environment variables, or pass them directly."
            )

        # Endpoint overrides (optional; useful for explicitly targeting paper/live environments)
        # Repo root .env currently defines APCA_API_BASE_URL / APCA_ENDPOINT for paper trading.
        self.trading_base_url = _clean_url(
            os.getenv("APCA_API_BASE_URL")
            or os.getenv("ALPACA_API_BASE_URL")
            or os.getenv("APCA_ENDPOINT")
            or os.getenv("ALPACA_ENDPOINT")
        )
        self.data_base_url = _clean_url(
            os.getenv("APCA_API_DATA_URL")
            or os.getenv("ALPACA_DATA_URL")
        )

        # Initialize clients
        if self.trading_base_url:
            self.trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=paper,
                url_override=self.trading_base_url,
            )
        else:
            self.trading_client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=paper,
            )

        if self.data_base_url:
            self.data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                url_override=self.data_base_url,
            )
        else:
            self.data_client = StockHistoricalDataClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )

        # Trading parameters
        self.position_size_pct = position_size_pct
        self.max_position_size_usd = max_position_size_usd
        self.max_concentration_pct = float(max_concentration_pct)
        self.skip_if_open_orders_exist = bool(skip_if_open_orders_exist)
        self.order_type = str(order_type)
        self.time_in_force = str(time_in_force)
        self.limit_price_offset_pct = limit_price_offset_pct

        # Setup logging
        self.log_dir = Path(log_dir) if log_dir else Path("./execution_logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._setup_logger()
        self.logger.info(f"AlpacaExecutor initialized (paper={paper})")
        if self.trading_base_url:
            self.logger.info(f"Alpaca trading base URL override: {self.trading_base_url}")
        if self.data_base_url:
            self.logger.info(f"Alpaca data base URL override: {self.data_base_url}")

    def _setup_logger(self) -> logging.Logger:
        """Setup execution logger."""
        logger = logging.getLogger("AlpacaExecutor")
        logger.setLevel(logging.INFO)

        # File handler
        log_file = self.log_dir / f"execution_{now_et().strftime('%Y%m%d')}.log"
        fh = logging.FileHandler(log_file)
        fh.setLevel(logging.INFO)

        # Console handler
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)

        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        fh.setFormatter(formatter)
        ch.setFormatter(formatter)

        logger.addHandler(fh)
        logger.addHandler(ch)

        return logger

    def _regular_market_is_open(self) -> Optional[bool]:
        """
        Best-effort check for whether the regular market is open.

        Prefers Alpaca clock when available; falls back to a US/Eastern time-window heuristic.
        """
        get_clock = getattr(self.trading_client, "get_clock", None)
        if callable(get_clock):
            try:
                clock = get_clock()
                is_open = getattr(clock, "is_open", None)
                if is_open is not None:
                    return bool(is_open)
            except Exception:
                pass

        try:
            from opentrace.utils.market_session import describe_us_market_session

            return bool(describe_us_market_session().get("is_regular_open"))
        except Exception:
            return None

    def _resolve_extended_hours(self, agent_extended_hours: Optional[bool]) -> bool:
        if agent_extended_hours is not None:
            return bool(agent_extended_hours)
        is_open = self._regular_market_is_open()
        if is_open is None:
            return False
        return not bool(is_open)

    def execute_signal(
        self,
        ticker: str,
        signal: str,
        analysis_state: Optional[Dict[str, Any]] = None,
        trade_date: Optional[str] = None,
        agent_quantity: Optional[int] = None,
        agent_limit_price: Optional[float] = None,
        agent_order_type: Optional[str] = None,
        agent_time_in_force: Optional[str] = None,
        agent_extended_hours: Optional[bool] = None,
        agent_stop_price: Optional[float] = None,
        agent_trail_percent: Optional[float] = None,
        agent_trail_price: Optional[float] = None,
        agent_position_size_pct: Optional[float] = None,
        agent_stop_loss: Optional[float] = None,
        agent_take_profit: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Execute an OpenTrace trading signal.

        Args:
            ticker: Stock ticker symbol
            signal: Trading signal (BUY, SELL, or HOLD)
            analysis_state: Full OpenTrace analysis state (optional, for logging)
            trade_date: Date of analysis (for logging)
            
        Returns:
            Execution result dictionary with order details
        """
        signal = signal.strip().upper()

        self.logger.info(f"Processing signal for {ticker}: {signal}")

        decision_version = None
        decision_validation_ok = False
        decision_validation_error = ""
        if isinstance(analysis_state, dict):
            sd = analysis_state.get("final_trade_decision_structured")
            if isinstance(sd, dict):
                decision_version = sd.get("decision_version")
                decision_validation_ok = bool(sd) and not bool(
                    analysis_state.get("final_trade_decision_validation_error")
                )
            decision_validation_error = str(
                analysis_state.get("final_trade_decision_validation_error", "") or ""
            )

        # Get current position and account info
        current_position = self._get_position(ticker)
        account = self.trading_client.get_account()

        result = {
            "ticker": ticker,
            "signal": signal,
            "trade_date": trade_date or now_et().strftime("%Y-%m-%d"),
            "timestamp": now_et().isoformat(),
            "executed": False,
            "order": None,
            "error": None,
            "decision_source": "final_trade_decision_structured",
            "decision_version": decision_version,
            "decision_validation_ok": decision_validation_ok,
            "decision_validation_error": decision_validation_error,
        }

        try:
              if signal == "BUY":
                  result.update(self._execute_buy(
                      ticker, current_position, account,
                      agent_quantity=agent_quantity,
                      agent_limit_price=agent_limit_price,
                      agent_order_type=agent_order_type,
                      agent_time_in_force=agent_time_in_force,
                      agent_extended_hours=agent_extended_hours,
                      agent_stop_price=agent_stop_price,
                      agent_trail_percent=agent_trail_percent,
                      agent_trail_price=agent_trail_price,
                      agent_position_size_pct=agent_position_size_pct,
                  ))
              elif signal == "SELL":
                  result.update(self._execute_sell(
                      ticker,
                      current_position,
                      account,
                      agent_quantity=agent_quantity,
                      agent_limit_price=agent_limit_price,
                      agent_order_type=agent_order_type,
                      agent_time_in_force=agent_time_in_force,
                      agent_extended_hours=agent_extended_hours,
                      agent_stop_price=agent_stop_price,
                      agent_trail_percent=agent_trail_percent,
                      agent_trail_price=agent_trail_price,
                  ))
              elif signal == "HOLD":
                  # Check if agent provided risk management levels
                  if agent_stop_loss is not None or agent_take_profit is not None:
                      result.update(self._execute_hold_risk_management(
                          ticker,
                          current_position,
                          account,
                          agent_quantity=agent_quantity,
                          agent_stop_loss=agent_stop_loss,
                          agent_take_profit=agent_take_profit,
                          agent_time_in_force=agent_time_in_force,
                      ))
                  else:
                      # Original HOLD behavior: no risk management levels
                      self.logger.info(f"{ticker}: HOLD - No action taken")
                      result["executed"] = False
                      result["message"] = "HOLD signal - no action"
              else:
                self.logger.warning(f"Unknown signal for {ticker}: {signal}")
                result["error"] = f"Unknown signal: {signal}"

        except Exception as e:
            self.logger.error(f"Error executing signal for {ticker}: {e}", exc_info=True)
            result["error"] = str(e)

        # Log execution
        self._log_execution(result, analysis_state)

        return result

    def _execute_buy(
        self,
        ticker: str,
        current_position: Optional[Dict],
        account: Any,
        agent_quantity: Optional[int] = None,
        agent_limit_price: Optional[float] = None,
        agent_order_type: Optional[str] = None,
        agent_time_in_force: Optional[str] = None,
        agent_extended_hours: Optional[bool] = None,
        agent_stop_price: Optional[float] = None,
        agent_trail_percent: Optional[float] = None,
        agent_trail_price: Optional[float] = None,
        agent_position_size_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute BUY signal."""
        # Portfolio-aware sizing: allow adds, but enforce concentration cap.
        capital = self._resolve_account_capital(account)
        cash_available = float(capital["cash"])
        buying_power_available = float(capital["buying_power"])
        effective_buying_power = float(capital["effective_buying_power"])
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        ticker_u = str(ticker or "").strip().upper()

        held_qty = 0.0
        held_value = 0.0
        if current_position is not None:
            try:
                held_qty = float(getattr(current_position, "qty", 0.0) or 0.0)
            except Exception:
                held_qty = 0.0
            try:
                held_value = float(getattr(current_position, "market_value", 0.0) or 0.0)
            except Exception:
                held_value = 0.0

        max_position_value = max(0.0, equity * float(self.max_concentration_pct))
        max_additional_value = max(0.0, max_position_value - held_value) if max_position_value else None

        # Determine target notional from effective buying power, then apply hard caps.
        effective_pct = float(self.position_size_pct)
        if agent_position_size_pct is not None:
            try:
                v = float(agent_position_size_pct)
                # Allow either "10" (meaning 10%) or "0.10" (meaning 10%).
                if v > 1.0:
                    v = v / 100.0
                if 0.0 < v <= 1.0:
                    effective_pct = v
            except Exception:
                pass

        target_value = effective_buying_power * effective_pct
        if self.max_position_size_usd:
            target_value = min(target_value, float(self.max_position_size_usd))
        if max_additional_value is not None:
            target_value = min(target_value, max_additional_value)

        # Get current price (best-effort). Quotes can fail due to transient network issues or Alpaca data access.
        quote = self._get_latest_quote(ticker_u)
        quote_error_hint = None

        current_price = None
        if quote:
            current_price = quote.get("ask_price") or quote.get("bid_price")

        # If quote isn't available, try to proceed using agent-provided prices when possible (e.g., explicit LIMIT/STOP).
        if not current_price:
            fallback_price = (
                _parse_floatish(agent_limit_price)
                or _parse_floatish(agent_stop_price)
                or _parse_floatish(agent_trail_price)
            )
            if fallback_price and fallback_price > 0:
                current_price = float(fallback_price)
                quote_error_hint = "quote_unavailable_used_agent_price"
                if not quote:
                    quote = {
                        "bid_price": None,
                        "ask_price": None,
                        "bid_size": None,
                        "ask_size": None,
                        "timestamp": None,
                        "source": quote_error_hint,
                        "reference_price": current_price,
                    }

        # If concentration cap blocks any add, stop here.
        if max_additional_value is not None and max_additional_value <= 0:
            return {
                "executed": False,
                "message": f"BUY blocked: position already at/above concentration cap ({self.max_concentration_pct:.0%})",
                "held_qty": held_qty,
                "held_value": held_value,
                "equity": equity,
            }

        if not current_price:
            # We can still submit a MARKET order with an explicit quantity, but cannot do cash/concentration sizing.
            normalized_order_type = _normalize_order_type(agent_order_type) or _normalize_order_type(self.order_type) or "MARKET"
            if normalized_order_type == "MARKET" and agent_quantity and agent_quantity > 0:
                current_price = 1.0
                quote_error_hint = "quote_unavailable_market_qty_only"
                self.logger.warning(
                    f"{ticker_u}: Latest quote unavailable; submitting MARKET order with agent quantity only (no sizing caps)."
                )
            else:
                return {
                    "executed": False,
                    "error": "Could not get latest quote",
                    "message": (
                        f"{ticker_u}: Could not fetch a latest quote from Alpaca data. "
                        "This can be due to transient connectivity, a data entitlement issue, or an invalid symbol. "
                        "If your order includes an explicit LIMIT_PRICE/STOP_PRICE, ensure it is provided; "
                        "otherwise set an explicit QUANTITY to allow execution without quote-based sizing."
                    ),
                }

        # Guardrail: skip if open orders exist for this ticker.
        if self.skip_if_open_orders_exist:
            open_orders = self._get_open_orders(ticker_u)
            if open_orders:
                return {
                    "executed": False,
                    "message": f"Skipped BUY: existing open orders for {ticker_u} detected",
                    "open_orders": [self._order_brief(o) for o in open_orders],
                }

        # Use agent-specified quantity if provided, otherwise calculate
        if agent_quantity and agent_quantity > 0:
            qty = int(agent_quantity)
            # If we have a meaningful price reference, enforce capital/concentration caps.
            if quote_error_hint != "quote_unavailable_market_qty_only":
                max_affordable = int(effective_buying_power / float(current_price))
                qty = min(qty, max_affordable)
                if max_additional_value is not None:
                    max_additional_qty = int(max_additional_value / float(current_price))
                    qty = min(qty, max_additional_qty)
            if quote_error_hint == "quote_unavailable_market_qty_only":
                self.logger.info(f"{ticker}: Agent requested {agent_quantity} shares (quote unavailable; no sizing caps applied)")
            else:
                self.logger.info(
                    f"{ticker}: Agent requested {agent_quantity} shares, "
                    f"capped to {qty} by effective buying power ${effective_buying_power:,.2f} "
                    f"(cash=${cash_available:,.2f}, buying_power=${buying_power_available:,.2f})"
                )
        else:
            qty = int(target_value / float(current_price))

        if qty <= 0:
            self.logger.warning(f"{ticker}: Insufficient funds for BUY")
            return {
                "executed": False,
                "error": "Insufficient funds for minimum position"
            }

        # Place order
        try:
            order, requested_limit_price, order_spec = self._place_order(
                ticker=ticker_u,
                qty=qty,
                side=OrderSide.BUY,
                current_price=float(current_price),
                agent_order_type=agent_order_type,
                agent_time_in_force=agent_time_in_force,
                agent_extended_hours=agent_extended_hours,
                agent_limit_price=agent_limit_price,
                agent_stop_price=agent_stop_price,
                agent_trail_percent=agent_trail_percent,
                agent_trail_price=agent_trail_price,
            )
        except Exception as e:
            if isinstance(e, ExtendedHoursSubmissionError):
                return {
                    "executed": False,
                    "error": str(e),
                    "message": "Extended-hours order submission failed; aborting without retry.",
                }
            return {"executed": False, "error": str(e)}

        self.logger.info(
            f"{ticker}: BUY order placed - {qty} shares at ~${current_price:.2f}"
        )

        order_dict = self._order_to_dict(order)
        entry_price_final = _parse_floatish(order_dict.get("filled_avg_price"))
        order_status = str(order_dict.get("status") or "")
        return {
            "executed": True,
            "order": order_dict,
            "qty": qty,
            "price": float(current_price),
            "entry_price_provisional": float(current_price),
            "entry_price_final": entry_price_final,
            "order_status": order_status,
            "order_needs_reconcile": self._order_needs_reconcile(order_dict),
            "side": "BUY",
            "quote": quote,
            "quote_note": quote_error_hint,
            "requested_limit_price": requested_limit_price,
            "order_spec": order_spec.__dict__,
        }

    def _execute_sell(
        self,
        ticker: str,
        current_position: Optional[Dict],
        account: Any,
        agent_quantity: Optional[int] = None,
        agent_limit_price: Optional[float] = None,
        agent_order_type: Optional[str] = None,
        agent_time_in_force: Optional[str] = None,
        agent_extended_hours: Optional[bool] = None,
        agent_stop_price: Optional[float] = None,
        agent_trail_percent: Optional[float] = None,
        agent_trail_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Execute SELL signal."""
        # Check if we have a position to sell
        if not current_position or float(current_position.qty) <= 0:
            self.logger.info(f"{ticker}: No position to sell, skipping SELL")
            return {
                "executed": False,
                "message": "No position to sell"
            }

        held_qty = abs(int(float(current_position.qty)))
        ticker_u = str(ticker or "").strip().upper()

        # Use agent-specified quantity if provided, otherwise default to full position
        if agent_quantity and agent_quantity > 0:
            qty = min(agent_quantity, held_qty)
            self.logger.info(
                f"{ticker}: Agent requested SELL {agent_quantity} shares, "
                f"capped to {qty} by current holdings ({held_qty} shares)"
            )
        else:
            qty = held_qty

        if qty <= 0:
            return {"executed": False, "error": "Invalid SELL quantity"}

        quote = self._get_latest_quote(ticker_u)
        quote_error_hint = None

        current_price = None
        if quote:
            current_price = quote.get("bid_price") or quote.get("ask_price")

        if not current_price:
            fallback_price = (
                _parse_floatish(agent_limit_price)
                or _parse_floatish(agent_stop_price)
                or _parse_floatish(agent_trail_price)
            )
            if fallback_price and fallback_price > 0:
                current_price = float(fallback_price)
                quote_error_hint = "quote_unavailable_used_agent_price"
                if not quote:
                    quote = {
                        "bid_price": None,
                        "ask_price": None,
                        "bid_size": None,
                        "ask_size": None,
                        "timestamp": None,
                        "source": quote_error_hint,
                        "reference_price": current_price,
                    }

        if not current_price:
            normalized_order_type = _normalize_order_type(agent_order_type) or _normalize_order_type(self.order_type) or "MARKET"
            if normalized_order_type == "MARKET":
                current_price = 1.0
                quote_error_hint = "quote_unavailable_market_sell"
                self.logger.warning(
                    f"{ticker_u}: Latest quote unavailable; submitting MARKET SELL without a reference quote."
                )
            else:
                return {
                    "executed": False,
                    "error": "Could not get latest quote",
                    "message": (
                        f"{ticker_u}: Could not fetch a latest quote from Alpaca data. "
                        "Provide an explicit LIMIT_PRICE/STOP_PRICE to execute without a quote, "
                        "or retry later if this is a transient connectivity/data issue."
                    ),
                }

        # Guardrail: skip if open orders exist for this ticker.
        if self.skip_if_open_orders_exist:
            open_orders = self._get_open_orders(ticker_u)
            if open_orders:
                return {
                    "executed": False,
                    "message": f"Skipped SELL: existing open orders for {ticker_u} detected",
                    "open_orders": [self._order_brief(o) for o in open_orders],
                }

        # Place sell order
        try:
            order, requested_limit_price, order_spec = self._place_order(
                ticker=ticker_u,
                qty=qty,
                side=OrderSide.SELL,
                current_price=float(current_price),
                agent_order_type=agent_order_type,
                agent_time_in_force=agent_time_in_force,
                agent_extended_hours=agent_extended_hours,
                agent_limit_price=agent_limit_price,
                agent_stop_price=agent_stop_price,
                agent_trail_percent=agent_trail_percent,
                agent_trail_price=agent_trail_price,
            )
        except Exception as e:
            if isinstance(e, ExtendedHoursSubmissionError):
                return {
                    "executed": False,
                    "error": str(e),
                    "message": "Extended-hours order submission failed; aborting without retry.",
                }
            return {"executed": False, "error": str(e)}

        self.logger.info(
            f"{ticker}: SELL order placed - {qty} shares at ~${current_price:.2f}"
        )

        order_dict = self._order_to_dict(order)
        entry_price_final = _parse_floatish(order_dict.get("filled_avg_price"))
        order_status = str(order_dict.get("status") or "")
        return {
            "executed": True,
            "order": order_dict,
            "qty": qty,
            "price": float(current_price),
            "entry_price_provisional": float(current_price),
            "entry_price_final": entry_price_final,
            "order_status": order_status,
            "order_needs_reconcile": self._order_needs_reconcile(order_dict),
            "side": "SELL",
            "quote": quote,
            "quote_note": quote_error_hint,
            "requested_limit_price": requested_limit_price,
            "order_spec": order_spec.__dict__,
        }

    def _execute_hold_risk_management(
        self,
        ticker: str,
        current_position: Optional[Dict],
        account: Any,
        agent_quantity: Optional[int] = None,
        agent_stop_loss: Optional[float] = None,
        agent_take_profit: Optional[float] = None,
        agent_time_in_force: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute HOLD action with risk management (stop-loss and/or take-profit).

        When agents provide HOLD with stop_loss and/or take_profit levels, this method
        submits OCO orders to protect the existing position.

        Args:
            ticker: Stock ticker
            current_position: Current position from Alpaca (or None)
            account: Alpaca account object
            agent_quantity: Optional quantity to protect (defaults to full position)
            agent_stop_loss: Stop-loss price level
            agent_take_profit: Take-profit price level
            agent_time_in_force: Order duration (defaults to GTC for OCO)

        Returns:
            Execution result dictionary
        """
        ticker_u = str(ticker or "").strip().upper()

        # Check if position exists
        if not current_position or float(current_position.qty) <= 0:
            # No position = "watch mode" - log the levels but don't submit orders
            self.logger.info(
                f"{ticker_u}: HOLD watch mode (no position) - "
                f"stop=${agent_stop_loss}, target=${agent_take_profit}"
            )
            return {
                "executed": False,
                "message": "HOLD watch mode - no position to protect",
                "watch_stop_loss": agent_stop_loss,
                "watch_take_profit": agent_take_profit,
            }

        held_qty = abs(int(float(current_position.qty)))

        # Require at least one risk management level
        if agent_stop_loss is None and agent_take_profit is None:
            self.logger.info(f"{ticker_u}: HOLD - No risk management levels provided")
            return {
                "executed": False,
                "message": "HOLD signal - no stop/target provided"
            }

        # Determine quantity to protect
        if agent_quantity and agent_quantity > 0:
            protect_qty = min(int(agent_quantity), held_qty)
            if protect_qty < held_qty:
                self.logger.info(
                    f"{ticker_u}: Protecting partial position - "
                    f"{protect_qty} of {held_qty} shares"
                )
        else:
            protect_qty = held_qty

        # Get current price for validation
        quote = self._get_latest_quote(ticker_u)
        current_price = None
        if quote:
            current_price = quote.get("ask_price") or quote.get("bid_price")

        # If we have both stop and target, validate price relationship
        if agent_stop_loss and agent_take_profit:
            # For long positions: stop < current < target
            if current_price:
                if agent_stop_loss >= current_price:
                    return {
                        "executed": False,
                        "error": f"Stop-loss (${agent_stop_loss:.2f}) must be below current price (${current_price:.2f})"
                    }
                if agent_take_profit <= current_price:
                    return {
                        "executed": False,
                        "error": f"Take-profit (${agent_take_profit:.2f}) must be above current price (${current_price:.2f})"
                    }

            if agent_stop_loss >= agent_take_profit:
                return {
                    "executed": False,
                    "error": f"Stop-loss (${agent_stop_loss:.2f}) must be below take-profit (${agent_take_profit:.2f})"
                }

        # Check for existing open orders (optional safety check)
        if self.skip_if_open_orders_exist:
            open_orders = self._get_open_orders(ticker_u)
            if open_orders:
                return {
                    "executed": False,
                    "message": f"Skipped HOLD risk mgmt: existing open orders for {ticker_u}",
                    "open_orders": [self._order_brief(o) for o in open_orders],
                }

        # If only one level provided, we can't use OCO - fall back to single order
        if agent_stop_loss is None or agent_take_profit is None:
            self.logger.warning(
                f"{ticker_u}: HOLD risk management requires BOTH stop-loss AND take-profit for OCO orders. "
                f"Only one provided - no orders submitted."
            )
            return {
                "executed": False,
                "error": "OCO orders require both stop-loss and take-profit levels",
                "provided_stop": agent_stop_loss,
                "provided_target": agent_take_profit,
            }

        # Submit OCO order
        time_in_force = agent_time_in_force or "GTC"

        try:
            order = self._submit_oco_order(
                ticker=ticker_u,
                qty=protect_qty,
                side=OrderSide.SELL,  # Assume long positions; TODO: handle shorts
                take_profit_price=float(agent_take_profit),
                stop_loss_price=float(agent_stop_loss),
                time_in_force=time_in_force,
            )

            self.logger.info(
                f"{ticker_u}: OCO order placed - protecting {protect_qty} shares: "
                f"stop=${agent_stop_loss:.2f}, target=${agent_take_profit:.2f}"
            )

            return {
                "executed": True,
                "order_type": "OCO",
                "order": self._order_to_dict(order),
                "protected_qty": protect_qty,
                "held_qty": held_qty,
                "stop_loss": agent_stop_loss,
                "take_profit": agent_take_profit,
                "time_in_force": time_in_force,
            }

        except ValueError as e:
            # Invalid parameters - don't retry
            self.logger.error(f"{ticker_u}: Invalid OCO parameters: {e}")
            return {
                "executed": False,
                "error": str(e),
                "message": "OCO order validation failed"
            }
        except Exception as e:
            # Alpaca API error - log and continue (position remains unprotected)
            self.logger.error(
                f"{ticker_u}: OCO order submission failed: {e}",
                exc_info=True
            )
            return {
                "executed": False,
                "error": str(e),
                "message": "Position remains unprotected - OCO submission failed"
            }

    def _place_order(
        self,
        ticker: str,
        qty: int,
        side: OrderSide,
        current_price: float,
        agent_order_type: Optional[str] = None,
        agent_time_in_force: Optional[str] = None,
        agent_extended_hours: Optional[bool] = None,
        agent_limit_price: Optional[float] = None,
        agent_stop_price: Optional[float] = None,
        agent_trail_percent: Optional[float] = None,
        agent_trail_price: Optional[float] = None,
    ) -> tuple[Any, Optional[float], OrderSpec]:
        """Place order with Alpaca (supports market/limit/stop/stop-limit/trailing-stop)."""
        if MarketOrderRequest is None:
            raise RuntimeError(
                "Alpaca execution requires the 'alpaca-py' package. Install it to enable execution."
            ) from _ALPACA_IMPORT_ERROR

        resolved_extended_hours = self._resolve_extended_hours(agent_extended_hours)
        spec, err = normalize_order_inputs(
            default_order_type=self.order_type,
            default_time_in_force=self.time_in_force,
            side=getattr(side, "value", str(side)),
            current_price=float(current_price),
            limit_price_offset_pct=float(self.limit_price_offset_pct),
            agent_order_type=agent_order_type,
            agent_time_in_force=agent_time_in_force,
            agent_extended_hours=resolved_extended_hours,
            agent_limit_price=agent_limit_price,
            agent_stop_price=agent_stop_price,
            agent_trail_percent=agent_trail_percent,
            agent_trail_price=agent_trail_price,
        )
        if err or spec is None:
            raise ValueError(err or "Invalid order specification.")

        # Map TIF to alpaca enum when available.
        tif = None
        if TimeInForce is not None:
            try:
                tif = getattr(TimeInForce, spec.time_in_force)
            except Exception:
                tif = TimeInForce.DAY
        tif = tif or spec.time_in_force

        # Build appropriate request object.
        if spec.order_type == "MARKET":
            request = MarketOrderRequest(symbol=ticker, qty=qty, side=side, time_in_force=tif)
        elif spec.order_type == "LIMIT":
            try:
                request_kwargs: Dict[str, Any] = {
                    "symbol": ticker,
                    "qty": qty,
                    "side": side,
                    "time_in_force": tif,
                    "limit_price": spec.limit_price,
                }
                if spec.extended_hours:
                    request_kwargs["extended_hours"] = True
                request = LimitOrderRequest(**request_kwargs)
            except TypeError as e:
                if spec.extended_hours:
                    self.logger.error(
                        "Extended-hours LIMIT order request rejected for %s (%s %s): %s",
                        ticker,
                        getattr(side, "value", str(side)),
                        qty,
                        str(e),
                    )
                    raise ExtendedHoursSubmissionError(str(e)) from e
                raise
        elif spec.order_type == "STOP":
            if StopOrderRequest is None:
                raise RuntimeError("Stop orders require alpaca-py StopOrderRequest.")
            request = StopOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=tif,
                stop_price=spec.stop_price,
            )
        elif spec.order_type == "STOP_LIMIT":
            if StopLimitOrderRequest is None:
                raise RuntimeError("Stop-limit orders require alpaca-py StopLimitOrderRequest.")
            request = StopLimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=tif,
                stop_price=spec.stop_price,
                limit_price=spec.limit_price,
            )
        elif spec.order_type == "TRAILING_STOP":
            if TrailingStopOrderRequest is None:
                raise RuntimeError("Trailing stop orders require alpaca-py TrailingStopOrderRequest.")
            request_kwargs: Dict[str, Any] = {
                "symbol": ticker,
                "qty": qty,
                "side": side,
                "time_in_force": tif,
            }
            if spec.trail_percent is not None:
                request_kwargs["trail_percent"] = spec.trail_percent
            if spec.trail_price is not None:
                request_kwargs["trail_price"] = spec.trail_price
            request = TrailingStopOrderRequest(**request_kwargs)
        else:
            raise ValueError(f"Unsupported ORDER_TYPE '{spec.order_type}'.")

        try:
            order = self.trading_client.submit_order(request)
        except Exception as e:
            if getattr(spec, "extended_hours", False):
                self.logger.error(
                    "Extended-hours order submission failed for %s (%s %s): %s",
                    ticker,
                    getattr(side, "value", str(side)),
                    qty,
                    str(e),
                    exc_info=True,
                )
                raise ExtendedHoursSubmissionError(str(e)) from e
            raise
        return order, spec.requested_limit_price, spec

    def _submit_oco_order(
        self,
        ticker: str,
        qty: int,
        side: OrderSide,
        take_profit_price: float,
        stop_loss_price: float,
        time_in_force: str = "GTC",
    ) -> Any:
        """
        Submit an OCO (One-Cancels-Other) order for position risk management.

        OCO orders place two exit orders simultaneously: one take-profit (limit) and
        one stop-loss. When either order fills, the other is automatically canceled.

        Args:
            ticker: Stock symbol
            qty: Number of shares to protect (must not exceed held position)
            side: OrderSide.SELL (for long positions) or OrderSide.BUY (for shorts)
            take_profit_price: Limit price for take-profit order
            stop_loss_price: Stop price for stop-loss order
            time_in_force: Order duration (GTC or DAY)

        Returns:
            Alpaca order response object

        Raises:
            ValueError: If order parameters are invalid
            RuntimeError: If alpaca-py TakeProfitRequest/StopLossRequest not available
        """
        if TakeProfitRequest is None or StopLossRequest is None or OrderClass is None:
            raise RuntimeError(
                "OCO orders require alpaca-py with TakeProfitRequest, StopLossRequest, and OrderClass. "
                "Update alpaca-py to the latest version."
            )

        # Validate price relationship (for SELL orders, stop must be below take-profit)
        if side == OrderSide.SELL:
            if stop_loss_price >= take_profit_price:
                raise ValueError(
                    f"For OCO sell orders, stop-loss (${stop_loss_price:.2f}) must be below "
                    f"take-profit (${take_profit_price:.2f}) by at least $0.01"
                )
        elif side == OrderSide.BUY:
            # For short positions (cover with BUY), stop must be above take-profit
            if stop_loss_price <= take_profit_price:
                raise ValueError(
                    f"For OCO buy orders (short cover), stop-loss (${stop_loss_price:.2f}) must be above "
                    f"take-profit (${take_profit_price:.2f}) by at least $0.01"
                )

        # Map time_in_force to Alpaca enum
        tif = TimeInForce.GTC if time_in_force == "GTC" else TimeInForce.DAY

        # Build OCO request
        request = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=side,
            time_in_force=tif,
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=round(float(take_profit_price), 2)),
            stop_loss=StopLossRequest(stop_price=round(float(stop_loss_price), 2))
        )

        return self.trading_client.submit_order(request)

    def _get_position(self, ticker: str) -> Optional[Any]:
        """Get current position for ticker."""
        try:
            return self.trading_client.get_open_position(ticker)
        except Exception:
            return None

    def _order_brief(self, order: Any) -> Dict[str, Any]:
        """Best-effort summary of an order object (for guardrail messaging)."""
        def _val(x, attr: str):
            v = getattr(x, attr, None)
            if hasattr(v, "value"):
                return v.value
            return v

        return {
            "id": str(getattr(order, "id", "") or ""),
            "symbol": str(getattr(order, "symbol", "") or ""),
            "qty": str(getattr(order, "qty", "") or ""),
            "side": _val(order, "side"),
            "type": _val(order, "type"),
            "status": _val(order, "status"),
        }

    def _get_open_orders(self, ticker: str) -> list[Any]:
        """
        Return a list of open orders for the given ticker.

        Uses best-effort compatibility across alpaca-py versions (request-based vs kwargs APIs).
        If the client cannot list orders, returns an empty list (guardrail becomes no-op).
        """
        ticker_u = ticker.upper()

        # First, try request-object API.
        get_orders = getattr(self.trading_client, "get_orders", None)
        list_orders = getattr(self.trading_client, "list_orders", None)

        orders: Any = None
        if callable(get_orders):
            try:
                from alpaca.trading.requests import GetOrdersRequest  # type: ignore

                try:
                    req = GetOrdersRequest(status="open", symbols=[ticker_u])
                    orders = get_orders(req)
                except Exception:
                    orders = None
            except Exception:
                orders = None

            if orders is None:
                # Try kwargs-based API variants.
                for kwargs in (
                    {"status": "open", "symbols": [ticker_u]},
                    {"status": "open"},
                    {},
                ):
                    try:
                        orders = get_orders(**kwargs)
                        break
                    except TypeError:
                        continue
                    except Exception:
                        continue

        if orders is None and callable(list_orders):
            for kwargs in (
                {"status": "open", "symbols": [ticker_u]},
                {"status": "open"},
                {},
            ):
                try:
                    orders = list_orders(**kwargs)
                    break
                except TypeError:
                    continue
                except Exception:
                    continue

        if orders is None:
            return []

        # Normalize to list.
        if isinstance(orders, dict):
            iterable = list(orders.values())
        else:
            try:
                iterable = list(orders)
            except Exception:
                iterable = []

        out: list[Any] = []
        for o in iterable:
            sym = str(getattr(o, "symbol", "") or "").upper()
            if sym != ticker_u:
                continue
            status = getattr(o, "status", None)
            status_v = status.value if hasattr(status, "value") else str(status or "")
            status_u = status_v.upper()
            # Treat non-terminal statuses as open/pending.
            if status_u and status_u not in {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
                out.append(o)

        return out

    def _get_latest_quote(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get latest quote for ticker (bid/ask).

        Note: Alpaca responses and dict keys can vary by alpaca-py version; this method is defensive.
        """
        ticker_u = str(ticker or "").strip().upper()
        if not ticker_u:
            return None

        def _to_float(v):
            try:
                return float(v) if v is not None else None
            except Exception:
                return None

        def _is_transient_error(exc: Exception) -> bool:
            msg = str(exc).lower()
            return any(
                s in msg
                for s in (
                    "timeout",
                    "timed out",
                    "temporarily unavailable",
                    "connection aborted",
                    "connection reset",
                    "connection error",
                    "service unavailable",
                    "bad gateway",
                    "gateway timeout",
                    "too many requests",
                    "429",
                    "502",
                    "503",
                    "504",
                )
            )

        last_exc: Exception | None = None

        # Try latest quote with a small retry loop (helps with transient network/API blips).
        for attempt in range(1, 4):
            try:
                if StockLatestQuoteRequest is None:
                    return None
                request = StockLatestQuoteRequest(symbol_or_symbols=ticker_u)
                quote = self.data_client.get_stock_latest_quote(request)

                q: Any = None
                if isinstance(quote, dict):
                    q = quote.get(ticker_u) or quote.get(ticker) or quote.get(ticker_u.lower())
                else:
                    q = quote

                if q is None:
                    return None

                bid_price = getattr(q, "bid_price", None)
                ask_price = getattr(q, "ask_price", None)
                bid_size = getattr(q, "bid_size", None)
                ask_size = getattr(q, "ask_size", None)
                ts = getattr(q, "timestamp", None)

                # Best-effort: fetch last trade price alongside the NBBO quote.
                # Used as the reference_price in market snapshots (more reliable than
                # bid/ask mid, especially after hours when NBBO spread is wide).
                last_trade_price: Optional[float] = None
                try:
                    from alpaca.data.requests import StockLatestTradeRequest  # type: ignore
                    _trade_req = StockLatestTradeRequest(symbol_or_symbols=ticker_u)
                    _trade_resp = self.data_client.get_stock_latest_trade(_trade_req)
                    _t: Any = None
                    if isinstance(_trade_resp, dict):
                        _t = _trade_resp.get(ticker_u) or _trade_resp.get(ticker) or _trade_resp.get(ticker_u.lower())
                    else:
                        _t = _trade_resp
                    if _t is not None:
                        last_trade_price = _to_float(getattr(_t, "price", None))
                except Exception:
                    pass  # best-effort; absence of last_trade_price is handled downstream

                # Warn when NBBO spread is unusually wide (e.g. after-hours / pre-market).
                _bid_f = _to_float(bid_price)
                _ask_f = _to_float(ask_price)
                if _bid_f is not None and _ask_f is not None and _bid_f > 0 and _ask_f > 0:
                    _mid = (_bid_f + _ask_f) / 2.0
                    _rel_spread = (_ask_f - _bid_f) / _mid
                    _max_spread = float(
                        (self.config or {}).get("executor_quote_max_rel_spread", 0.01)
                    )
                    if _rel_spread > _max_spread:
                        self.logger.warning(
                            f"Wide NBBO spread for {ticker_u}: bid={_bid_f}, ask={_ask_f}, "
                            f"spread={_rel_spread:.1%}, last_trade={last_trade_price}. "
                            f"Market may be outside regular hours."
                        )

                return {
                    "bid_price": _to_float(bid_price),
                    "ask_price": _to_float(ask_price),
                    "last_trade_price": last_trade_price,
                    "bid_size": _to_float(bid_size),
                    "ask_size": _to_float(ask_size),
                    "timestamp": ts.isoformat()
                    if hasattr(ts, "isoformat")
                    else str(ts)
                    if ts is not None
                    else None,
                    "source": "alpaca_latest_quote",
                }
            except Exception as e:
                last_exc = e
                if attempt < 3 and _is_transient_error(e):
                    time.sleep(0.25 * attempt)
                    continue
                break

        # Fallback: try latest trade and synthesize a quote-like payload.
        try:
            from alpaca.data.requests import StockLatestTradeRequest  # type: ignore

            request = StockLatestTradeRequest(symbol_or_symbols=ticker_u)
            trade = self.data_client.get_stock_latest_trade(request)

            t: Any = None
            if isinstance(trade, dict):
                t = trade.get(ticker_u) or trade.get(ticker) or trade.get(ticker_u.lower())
            else:
                t = trade

            if t is None:
                return None

            price = _to_float(getattr(t, "price", None))
            ts = getattr(t, "timestamp", None)
            if price is None:
                return None

            return {
                "bid_price": price,
                "ask_price": price,
                "bid_size": None,
                "ask_size": None,
                "timestamp": ts.isoformat()
                if hasattr(ts, "isoformat")
                else str(ts)
                if ts is not None
                else None,
                "source": "alpaca_latest_trade",
            }
        except Exception as e:
            # Preserve the most informative exception in logs.
            if last_exc is not None:
                self.logger.error(
                    f"Error getting quote for {ticker_u}: {last_exc} (fallback trade also failed: {e})"
                )
            else:
                self.logger.error(f"Error getting quote for {ticker_u}: {e}")
            return None

    def _order_to_dict(self, order: Any) -> Dict[str, Any]:
        """Convert Alpaca order object to dictionary."""
        def _enumish(v):
            return v.value if hasattr(v, "value") else v

        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": _enumish(getattr(order, "side", None)),
            "type": _enumish(getattr(order, "type", None)),
            "status": _enumish(getattr(order, "status", None)),
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None,
            "filled_qty": str(getattr(order, "filled_qty", "")) if getattr(order, "filled_qty", None) is not None else None,
            "filled_avg_price": str(getattr(order, "filled_avg_price", "")) if getattr(order, "filled_avg_price", None) is not None else None,
            "filled_at": getattr(order, "filled_at", None).isoformat() if getattr(order, "filled_at", None) else None,
            "updated_at": getattr(order, "updated_at", None).isoformat() if getattr(order, "updated_at", None) else None,
        }

    @staticmethod
    def _order_needs_reconcile(order_dict: Dict[str, Any]) -> bool:
        status = str(order_dict.get("status") or "").upper()
        if status in {"FILLED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
            return False
        return True

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Fetch an order by ID and normalize to dict."""
        try:
            order = self.trading_client.get_order_by_id(order_id)
        except Exception as e:
            self.logger.warning(f"Failed to fetch order {order_id}: {e}")
            return None
        return self._order_to_dict(order)

    def _log_execution(
        self,
        result: Dict[str, Any],
        analysis_state: Optional[Dict[str, Any]] = None
    ):
        """Log execution details to file."""
        log_entry = {**result, "analysis_state": _analysis_state_for_log(analysis_state)}

        log_file = self.log_dir / f"executions_{now_et().strftime('%Y%m')}.jsonl"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            # Never break execution due to logging issues.
            try:
                self.logger.warning(f"Failed to write execution log: {e}")
            except Exception:
                pass

    @staticmethod
    def _resolve_account_capital(account: Any) -> Dict[str, float]:
        """Normalize account capital fields with buying-power fallback to cash."""
        cash = _parse_floatish(getattr(account, "cash", None))
        if cash is None:
            cash = 0.0
        buying_power = _parse_floatish(getattr(account, "buying_power", None))
        if buying_power is None:
            buying_power = cash
        return {
            "cash": float(cash),
            "buying_power": float(buying_power),
            "effective_buying_power": float(max(cash, buying_power)),
        }

    def get_buying_power_info(self) -> Dict[str, float]:
        """Return normalized buying-power information for account-level sizing."""
        account = self.trading_client.get_account()
        return self._resolve_account_capital(account)

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get current portfolio summary."""
        account = self.trading_client.get_account()
        positions = self.trading_client.get_all_positions()
        capital = self._resolve_account_capital(account)

        return {
            "account_value": float(account.equity),
            "cash": float(capital["cash"]),
            "buying_power": float(capital["buying_power"]),
            "effective_buying_power": float(capital["effective_buying_power"]),
            "positions_count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "avg_entry_price": float(getattr(p, "avg_entry_price", 0.0) or 0.0),
                    "cost_basis": float(getattr(p, "cost_basis", 0.0) or 0.0),
                    "side": str(getattr(getattr(p, "side", None), "value", getattr(p, "side", "")) or ""),
                }
                for p in positions
            ]
        }

"""
Alpaca execution module.

This file is imported by the CLI and graph, but Alpaca execution is optional.
Keep imports lazy/optional so the analysis pipeline still works without alpaca-py.
"""

_ALPACA_IMPORT_ERROR: Exception | None = None

try:  # Optional dependency: alpaca-py
    from alpaca.trading.client import TradingClient  # type: ignore
    from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest  # type: ignore
    from alpaca.trading.enums import OrderSide, TimeInForce, OrderType  # type: ignore
    from alpaca.data.historical import StockHistoricalDataClient  # type: ignore
    from alpaca.data.requests import StockLatestQuoteRequest  # type: ignore
except Exception as e:  # pragma: no cover
    TradingClient = None  # type: ignore[assignment]
    MarketOrderRequest = None  # type: ignore[assignment]
    LimitOrderRequest = None  # type: ignore[assignment]
    OrderSide = None  # type: ignore[assignment]
    TimeInForce = None  # type: ignore[assignment]
    OrderType = None  # type: ignore[assignment]
    StockHistoricalDataClient = None  # type: ignore[assignment]
    StockLatestQuoteRequest = None  # type: ignore[assignment]
    _ALPACA_IMPORT_ERROR = e
from datetime import datetime
from typing import Dict, Any, Optional, Literal
import os
import logging
from pathlib import Path
import json


class AlpacaExecutor:
    """
    Executes trading signals from TradingAgents framework on Alpaca paper trading.

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
        order_type: Literal["market", "limit"] = "market",
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
            order_type: Order type - "market" or "limit"
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

        # Initialize clients
        self.trading_client = TradingClient(
            api_key=self.api_key,
            secret_key=self.secret_key,
            paper=paper
        )

        self.data_client = StockHistoricalDataClient(
            api_key=self.api_key,
            secret_key=self.secret_key
        )

        # Trading parameters
        self.position_size_pct = position_size_pct
        self.max_position_size_usd = max_position_size_usd
        self.order_type = order_type
        self.limit_price_offset_pct = limit_price_offset_pct

        # Setup logging
        self.log_dir = Path(log_dir) if log_dir else Path("./execution_logs")
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.logger = self._setup_logger()
        self.logger.info(f"AlpacaExecutor initialized (paper={paper})")

    def _setup_logger(self) -> logging.Logger:
        """Setup execution logger."""
        logger = logging.getLogger("AlpacaExecutor")
        logger.setLevel(logging.INFO)

        # File handler
        log_file = self.log_dir / f"execution_{datetime.now().strftime('%Y%m%d')}.log"
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

    def execute_signal(
        self,
        ticker: str,
        signal: str,
        analysis_state: Optional[Dict[str, Any]] = None,
        trade_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Execute a trading signal from TradingAgents.

        Args:
            ticker: Stock ticker symbol
            signal: Trading signal (BUY, SELL, or HOLD)
            analysis_state: Full state from TradingAgents (optional, for logging)
            trade_date: Date of analysis (for logging)
            
        Returns:
            Execution result dictionary with order details
        """
        signal = signal.strip().upper()

        self.logger.info(f"Processing signal for {ticker}: {signal}")

        # Get current position and account info
        current_position = self._get_position(ticker)
        account = self.trading_client.get_account()

        result = {
            "ticker": ticker,
            "signal": signal,
            "trade_date": trade_date or datetime.now().strftime("%Y-%m-%d"),
            "timestamp": datetime.now().isoformat(),
            "executed": False,
            "order": None,
            "error": None
        }

        try:
            if signal == "BUY":
                result.update(self._execute_buy(ticker, current_position, account))
            elif signal == "SELL":
                result.update(self._execute_sell(ticker, current_position, account))
            elif signal == "HOLD":
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
        account: Any
    ) -> Dict[str, Any]:
        """Execute BUY signal."""
        # Check if we already have a position
        if current_position and float(current_position.qty) > 0:
            self.logger.info(
                f"{ticker}: Already holding {current_position.qty} shares, "
                "skipping BUY"
            )
            return {
                "executed": False,
                "message": f"Already holding {current_position.qty} shares"
            }

        # Calculate position size
        cash_available = float(account.cash)
        target_value = cash_available * self.position_size_pct

        if self.max_position_size_usd:
            target_value = min(target_value, self.max_position_size_usd)

        # Get current price
        current_price = self._get_current_price(ticker)

        if not current_price:
            return {"executed": False, "error": "Could not get current price"}

        # Calculate shares to buy
        qty = int(target_value / current_price)

        if qty <= 0:
            self.logger.warning(f"{ticker}: Insufficient funds for BUY")
            return {
                "executed": False,
                "error": "Insufficient funds for minimum position"
            }

        # Place order
        order = self._place_order(ticker, qty, OrderSide.BUY, current_price)

        self.logger.info(
            f"{ticker}: BUY order placed - {qty} shares at ~${current_price:.2f}"
        )

        return {
            "executed": True,
            "order": self._order_to_dict(order),
            "qty": qty,
            "price": current_price,
            "side": "BUY"
        }

    def _execute_sell(
        self,
        ticker: str,
        current_position: Optional[Dict],
        account: Any
    ) -> Dict[str, Any]:
        """Execute SELL signal."""
        # Check if we have a position to sell
        if not current_position or float(current_position.qty) <= 0:
            self.logger.info(f"{ticker}: No position to sell, skipping SELL")
            return {
                "executed": False,
                "message": "No position to sell"
            }

        qty = abs(int(float(current_position.qty)))
        current_price = self._get_current_price(ticker)

        if not current_price:
            return {"executed": False, "error": "Could not get current price"}

        # Place sell order
        order = self._place_order(ticker, qty, OrderSide.SELL, current_price)

        self.logger.info(
            f"{ticker}: SELL order placed - {qty} shares at ~${current_price:.2f}"
        )

        return {
            "executed": True,
            "order": self._order_to_dict(order),
            "qty": qty,
            "price": current_price,
            "side": "SELL"
        }

    def _place_order(
        self,
        ticker: str,
        qty: int,
        side: OrderSide,
        current_price: float
    ) -> Any:
        """Place order with Alpaca."""
        if self.order_type == "market":
            request = MarketOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY
            )
        else:  # limit order
            # Calculate limit price with offset
            if side == OrderSide.BUY:
                limit_price = current_price * (1 + self.limit_price_offset_pct)
            else:
                limit_price = current_price * (1 - self.limit_price_offset_pct)

            request = LimitOrderRequest(
                symbol=ticker,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=round(limit_price, 2)
            )

        return self.trading_client.submit_order(request)

    def _get_position(self, ticker: str) -> Optional[Any]:
        """Get current position for ticker."""
        try:
            return self.trading_client.get_open_position(ticker)
        except Exception:
            return None

    def _get_current_price(self, ticker: str) -> Optional[float]:
        """Get current market price for ticker."""
        try:
            request = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            quote = self.data_client.get_stock_latest_quote(request)

            if ticker in quote:
                return float(quote[ticker].ask_price)
            return None
        except Exception as e:
            self.logger.error(f"Error getting price for {ticker}: {e}")
            return None

    def _order_to_dict(self, order: Any) -> Dict[str, Any]:
        """Convert Alpaca order object to dictionary."""
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": str(order.qty),
            "side": order.side.value,
            "type": order.type.value,
            "status": order.status.value,
            "submitted_at": order.submitted_at.isoformat() if order.submitted_at else None
        }

    def _log_execution(
        self,
        result: Dict[str, Any],
        analysis_state: Optional[Dict[str, Any]] = None
    ):
        """Log execution details to file."""
        log_entry = {
            **result,
            "analysis_state": analysis_state
        }

        log_file = self.log_dir / f"executions_{datetime.now().strftime('%Y%m')}.jsonl"

        with open(log_file, 'a') as f:
            f.write(json.dumps(log_entry) + '\n')

    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get current portfolio summary."""
        account = self.trading_client.get_account()
        positions = self.trading_client.get_all_positions()

        return {
            "account_value": float(account.equity),
            "cash": float(account.cash),
            "buying_power": float(account.buying_power),
            "positions_count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc)
                }
                for p in positions
            ]
        }

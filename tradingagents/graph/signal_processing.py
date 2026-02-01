# TradingAgents/graph/signal_processing.py

import re
from typing import Dict, Any, Optional
from langchain_openai import ChatOpenAI


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: ChatOpenAI):
        """Initialize with an LLM for processing."""
        self.quick_thinking_llm = quick_thinking_llm

    def process_signal(self, full_signal: str) -> str:
        """
        Process a full trading signal to extract the core decision.

        Args:
            full_signal: Complete trading signal text

        Returns:
            Extracted decision (BUY, SELL, or HOLD)
        """
        messages = [
            (
                "system",
                "You are an efficient assistant designed to analyze paragraphs or financial reports provided by a group of analysts. Your task is to extract the investment decision: SELL, BUY, or HOLD. Provide only the extracted decision (SELL, BUY, or HOLD) as your output, without adding any additional text or information.",
            ),
            ("human", full_signal),
        ]

        return self.quick_thinking_llm.invoke(messages).content

    def extract_structured_decision(self, full_signal: str) -> Dict[str, Any]:
        """
        Extract a structured trading decision from the full signal text.

        Parses the FINAL TRADING DECISION / FINAL TRANSACTION PROPOSAL block
        if present, otherwise falls back to LLM extraction for the action only.

        Returns:
            Dict with keys: action, ticker, quantity, order_type, limit_price,
            stop_loss, take_profit, position_size_pct, time_horizon, confidence, rationale
        """
        result = {
            "action": "HOLD",
            "ticker": None,
            "quantity": None,
            "order_type": "MARKET",
            "limit_price": None,
            "stop_loss": None,
            "take_profit": None,
            "position_size_pct": None,
            "time_horizon": None,
            "confidence": None,
            "rationale": None,
        }

        if not full_signal:
            return result

        # Try to parse structured block first
        parsed = self._parse_structured_block(full_signal)
        if parsed:
            result.update(parsed)
        else:
            # Fallback: extract just the action via LLM
            result["action"] = self.process_signal(full_signal)

        return result

    def _parse_structured_block(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse the FINAL TRADING DECISION / FINAL TRANSACTION PROPOSAL block."""
        patterns = [
            r"FINAL TRADING DECISION:(.*?)(?:---|$)",
            r"FINAL TRANSACTION PROPOSAL:(.*?)(?:---|$)",
        ]

        block = None
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if match:
                block = match.group(1)
                break

        if not block:
            return None

        result = {}
        field_map = {
            "ACTION": "action",
            "TICKER": "ticker",
            "QUANTITY": "quantity",
            "ORDER_TYPE": "order_type",
            "LIMIT_PRICE": "limit_price",
            "STOP_LOSS": "stop_loss",
            "TAKE_PROFIT": "take_profit",
            "POSITION_SIZE_PCT": "position_size_pct",
            "TIME_HORIZON": "time_horizon",
            "CONFIDENCE": "confidence",
            "RATIONALE": "rationale",
        }

        for field_key, result_key in field_map.items():
            pattern = rf"-\s*{field_key}\s*:\s*(.+?)(?:\n|$)"
            match = re.search(pattern, block, re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip("*").strip()

                if value.upper() in ("N/A", "NA", "NONE", "-"):
                    value = None

                # Parse numeric values for specific fields
                if result_key == "quantity" and value:
                    num_match = re.search(r"(\d+)", value)
                    value = int(num_match.group(1)) if num_match else None

                if result_key in ("limit_price", "stop_loss", "take_profit") and value:
                    price_match = re.search(r"\$?([\d,.]+)", value)
                    value = float(price_match.group(1).replace(",", "")) if price_match else None

                if result_key == "position_size_pct" and value:
                    pct_match = re.search(r"([\d.]+)", value)
                    value = float(pct_match.group(1)) if pct_match else None

                if result_key == "action" and value:
                    value = value.upper()
                    if "BUY" in value:
                        value = "BUY"
                    elif "SELL" in value:
                        value = "SELL"
                    else:
                        value = "HOLD"

                result[result_key] = value

        if "action" not in result or not result["action"]:
            return None

        return result

# Verumtrade/graph/signal_processing.py

import re
from typing import Dict, Any, Optional, TYPE_CHECKING, Tuple

from .decision_schema import extract_decision_json_block, validate_structured_decision

if TYPE_CHECKING:  # pragma: no cover
    from langchain_openai import ChatOpenAI


class SignalProcessor:
    """Processes trading signals to extract actionable decisions."""

    def __init__(self, quick_thinking_llm: "ChatOpenAI"):
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
        Backward-compatible parser for non-execution call sites.
        Prefer `extract_canonical_decision` for execution.
        """
        parsed, _ = self.extract_canonical_decision(full_signal)
        if parsed:
            return parsed
        return {"action": self.process_signal(full_signal)}

    def extract_canonical_decision(
        self, full_signal: str, expected_ticker: Optional[str] = None
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Extract and validate the canonical JSON decision block.
        Returns (normalized_decision, validation_error).
        """
        raw, raw_err = extract_decision_json_block(full_signal)
        if raw_err:
            return None, raw_err
        return validate_structured_decision(raw or {}, expected_ticker=expected_ticker)

    @staticmethod
    def _normalize_order_type(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        v = str(value).strip().upper()
        v = v.replace("-", "_").replace(" ", "_")
        allowed = {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "TRAILING_STOP"}
        if v in allowed:
            return v
        # Common aliases
        alias = {
            "STOPLIMIT": "STOP_LIMIT",
            "STOP_LIMIT_ORDER": "STOP_LIMIT",
            "TRAILINGSTOP": "TRAILING_STOP",
            "TRAIL_STOP": "TRAILING_STOP",
            "TRAILING": "TRAILING_STOP",
        }
        return alias.get(v)

    @staticmethod
    def _normalize_time_in_force(value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        v = str(value).strip().upper()
        v = v.replace("-", "_").replace(" ", "_")
        if v in {"DAY", "GTC"}:
            return v
        return None

    @staticmethod
    def _parse_float(value: Optional[str]) -> Optional[float]:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        if s.upper() in ("N/A", "NA", "NONE", "-"):
            return None
        m = re.search(r"([-+]?\d[\d,]*\.?\d*)", s.replace("%", ""))
        if not m:
            return None
        try:
            return float(m.group(1).replace(",", ""))
        except Exception:
            return None

    def _parse_structured_block(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse the FINAL TRADING DECISION / FINAL TRANSACTION PROPOSAL block."""
        patterns = [
            r"(?:^|\n)\s*(?:#+\s*)?FINAL TRADING DECISION\s*:?\s*\n(.*?)(?:(?:\n\s*---\s*\n)|(?:\n\s*#{1,6}\s+)|\Z)",
            r"(?:^|\n)\s*(?:#+\s*)?FINAL TRANSACTION PROPOSAL\s*:?\s*\n(.*?)(?:(?:\n\s*---\s*\n)|(?:\n\s*#{1,6}\s+)|\Z)",
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
            "TIME_IN_FORCE": "time_in_force",
            "EXTENDED_HOURS": "extended_hours",
            "LIMIT_PRICE": "limit_price",
            "STOP_PRICE": "stop_price",
            "TRAIL_PERCENT": "trail_percent",
            "TRAIL_PRICE": "trail_price",
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
                    # Prefer the last integer in the field to avoid mis-parsing
                    # strings like "10% of portfolio (~37 shares)".
                    nums = re.findall(r"(\d+)", value)
                    value = int(nums[-1]) if nums else None

                if result_key in (
                    "limit_price",
                    "stop_price",
                    "stop_loss",
                    "take_profit",
                    "trail_price",
                    "trail_percent",
                ):
                    value = self._parse_float(value)

                if result_key == "position_size_pct" and value:
                    value = self._parse_float(value)

                if result_key == "action" and value:
                    value = value.upper()
                    if "BUY" in value:
                        value = "BUY"
                    elif "SELL" in value:
                        value = "SELL"
                    else:
                        value = "HOLD"

                if result_key == "order_type":
                    if value is None:
                        result[result_key] = None
                        continue
                    normalized = self._normalize_order_type(value)
                    value = normalized if normalized is not None else str(value).strip().upper()

                if result_key == "time_in_force":
                    if value is None:
                        result[result_key] = None
                        continue
                    normalized = self._normalize_time_in_force(value)
                    value = normalized if normalized is not None else str(value).strip().upper()

                if result_key == "extended_hours":
                    if value is None:
                        value = None
                    else:
                        v = str(value).strip().lower()
                        if v in ("true", "t", "yes", "y", "1"):
                            value = True
                        elif v in ("false", "f", "no", "n", "0"):
                            value = False
                        else:
                            value = None

                result[result_key] = value

        if "action" not in result or not result["action"]:
            return None

        # Light validation: keep missing params as None so executor can error clearly.
        order_type = result.get("order_type")
        if order_type == "LIMIT" and not result.get("limit_price"):
            result["limit_price"] = None
        if order_type == "STOP" and not result.get("stop_price"):
            result["stop_price"] = None
        if order_type == "STOP_LIMIT":
            if not result.get("stop_price"):
                result["stop_price"] = None
            if not result.get("limit_price"):
                result["limit_price"] = None
        if order_type == "TRAILING_STOP":
            tp = result.get("trail_percent")
            tr = result.get("trail_price")
            # Require exactly one; ambiguous inputs become None so executor rejects.
            if (tp is None and tr is None) or (tp is not None and tr is not None):
                result["trail_percent"] = None
                result["trail_price"] = None

        return result

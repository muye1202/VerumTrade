# tradingagents/agents/discovery/intelligence_sub_agents.py
"""
Stage 1 Intelligence Sub-Agents for Stock Discovery Pipeline.

Three focused, parallel sub-agents that each gather one dimension of market
intelligence and return structured data — NOT free-text. This avoids the
context-window saturation problem of monolithic recommendation prompts.

Architecture:
    ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
    │ MacroSector      │  │ CatalystNews     │  │ TechnicalMomentum│
    │ Scanner          │  │ Scanner          │  │ Scanner          │
    └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
             │                     │                     │
             └─────────────┬───────┘─────────────────────┘
                           ▼
                   Structured merge → Stage 2 Synthesis

Each sub-agent:
  - Has a SHORT, focused system prompt (no multi-page instruction manuals)
  - Returns structured dataclass results (not markdown)
  - Calls only the tools it needs (minimal tool surface)
  - Can run concurrently with the others (no shared state)

Usage:
    scanner = IntelligenceScanner(llm=llm, config=config)
    results = scanner.scan_all(trade_date="2025-06-15")
    # results.sector_signals, results.catalyst_signals, results.technical_signals
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from typing import Annotated

logger = logging.getLogger(__name__)


# =============================================================================
# Structured output dataclasses
# =============================================================================
# These are the "contracts" between Stage 1 and Stage 2.  The LLM is asked to
# produce JSON that we parse into these.  If parsing fails we fall back to
# heuristic extraction so the pipeline never hard-fails.

@dataclass
class SectorSignal:
    """One sector's momentum reading."""
    sector: str
    etf: str
    return_30d: float = 0.0
    return_10d: float = 0.0          # Short-window acceleration
    relative_to_spy: float = 0.0     # Excess return vs SPY
    momentum_rank: int = 0           # 1 = strongest
    narrative: str = ""              # LLM's 1-sentence read on WHY


@dataclass
class CatalystSignal:
    """A news-driven catalyst attached to a ticker or sector."""
    ticker: str = ""
    sector: str = ""
    catalyst_type: str = ""          # e.g. "earnings_beat", "fda_approval", "analyst_upgrade"
    headline: str = ""
    sentiment_score: float = 0.0     # -1 (bearish) to +1 (bullish)
    recency_days: int = 0            # Days old relative to trade_date
    actionability: str = "medium"    # "high", "medium", "low"


@dataclass
class TechnicalSignal:
    """Technical momentum reading for one ticker."""
    ticker: str
    price: float = 0.0
    vs_sma50_pct: float = 0.0       # % above/below 50-day SMA
    vs_sma200_pct: float = 0.0      # % above/below 200-day SMA
    momentum_20d: float = 0.0       # 20-day return %
    adx: float = 0.0                # Trend strength (0-100)
    obv_trend: str = "neutral"      # "accumulation", "distribution", "neutral"
    relative_strength_vs_spy: float = 0.0  # RS > 1 = outperforming
    volume_ratio: float = 0.0       # Recent vol / 20d avg vol
    composite_score: float = 0.0    # Weighted composite (calculated post-LLM)


@dataclass
class IntelligenceResult:
    """Aggregated output of all three sub-agents."""
    sector_signals: List[SectorSignal] = field(default_factory=list)
    catalyst_signals: List[CatalystSignal] = field(default_factory=list)
    technical_signals: List[TechnicalSignal] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    scan_date: str = ""
    scan_duration_secs: float = 0.0

    @property
    def hot_sectors(self) -> List[SectorSignal]:
        """Top 3 sectors by momentum rank."""
        return sorted(self.sector_signals, key=lambda s: s.momentum_rank)[:3]

    @property
    def high_conviction_catalysts(self) -> List[CatalystSignal]:
        """Catalysts with positive sentiment and high actionability."""
        return [
            c for c in self.catalyst_signals
            if c.sentiment_score > 0.3 and c.actionability == "high"
        ]

    @property
    def breakout_candidates(self) -> List[TechnicalSignal]:
        """Tickers passing breakout filter: above MAs, ADX > 20, positive momentum."""
        return [
            t for t in self.technical_signals
            if t.vs_sma50_pct > 0
            and t.vs_sma200_pct > 0
            and t.adx > 20
            and t.momentum_20d > 3
        ]

    def tickers_with_multi_signal_alignment(self) -> List[str]:
        """
        Tickers that appear in BOTH catalyst signals AND technical breakout candidates.
        Multi-signal alignment is the strongest discovery signal.
        """
        catalyst_tickers = {c.ticker for c in self.high_conviction_catalysts if c.ticker}
        breakout_tickers = {t.ticker for t in self.breakout_candidates}
        return sorted(catalyst_tickers & breakout_tickers)


# =============================================================================
# Sub-Agent 1: Macro / Sector Scanner
# =============================================================================

SECTOR_SCANNER_SYSTEM_PROMPT = """You are a macro/sector rotation analyst. Your ONLY job is to rank sector momentum.

You will receive sector ETF performance data. Analyze it and return a JSON object.

Rules:
- Rank all sectors by momentum (1 = strongest)
- Calculate each sector's return relative to SPY (excess return)
- Note which sectors are ACCELERATING (10d return > 30d return implies acceleration)
- Write a 1-sentence narrative per sector explaining the driver
- Be concise and data-driven. No preamble, no markdown.

Return ONLY this JSON structure (no markdown fencing, no extra text):
{
  "sectors": [
    {
      "sector": "Technology",
      "etf": "XLK",
      "return_30d": 5.2,
      "return_10d": 3.1,
      "relative_to_spy": 2.1,
      "momentum_rank": 1,
      "narrative": "AI capex cycle driving semis and cloud names"
    }
  ],
  "market_regime": "risk-on | risk-off | mixed",
  "key_theme": "One sentence on the dominant macro theme"
}"""


class MacroSectorScanner:
    """
    Sub-agent that scans sector ETFs to identify rotation and momentum.

    Data flow:
      1. Fetch 30-day and 10-day returns for all 11 GICS sector ETFs + SPY
      2. Pass structured performance table to a focused LLM prompt
      3. LLM ranks sectors, identifies acceleration, provides narrative
      4. Output: List[SectorSignal]

    This agent does NOT call tools via LLM tool-binding. It pre-fetches all
    data deterministically, then uses the LLM purely for ranking/interpretation.
    This is intentional: sector data is finite and known, so letting the LLM
    decide what to fetch wastes tokens and introduces non-determinism.
    """

    SECTOR_ETFS = {
        "XLK": "Technology",
        "XLF": "Financials",
        "XLE": "Energy",
        "XLV": "Healthcare",
        "XLY": "Consumer Discretionary",
        "XLP": "Consumer Staples",
        "XLI": "Industrials",
        "XLB": "Materials",
        "XLRE": "Real Estate",
        "XLU": "Utilities",
        "XLC": "Communication Services",
    }

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _fetch_sector_returns(self, trade_date: str) -> List[Dict[str, Any]]:
        """
        Pre-fetch sector ETF returns for 30-day and 10-day windows.
        Returns raw performance data without LLM involvement.
        """
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_30d = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")  # Extra buffer for holidays

        # Fetch SPY as benchmark
        all_tickers = list(self.SECTOR_ETFS.keys()) + ["SPY"]
        results = []

        for ticker in all_tickers:
            try:
                raw_csv = route_to_vendor(
                    "get_stock_data",
                    ticker,
                    start_30d,
                    trade_date,
                )

                # Parse CSV to extract close prices
                lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
                if len(lines) < 3:
                    continue

                header = lines[0].split(",")
                close_idx = header.index("Close")
                date_idx = header.index("Date") if "Date" in header else 0

                # Build (date, close) list
                prices = []
                for line in lines[1:]:
                    parts = line.split(",")
                    try:
                        prices.append({
                            "date": parts[date_idx].strip(),
                            "close": float(parts[close_idx]),
                        })
                    except (ValueError, IndexError):
                        continue

                if len(prices) < 5:
                    continue

                latest_close = prices[-1]["close"]

                # 30-day return (use first available price as proxy)
                first_close = prices[0]["close"]
                return_30d = ((latest_close - first_close) / first_close) * 100

                # 10-day return (from ~10 trading days back)
                idx_10d = max(0, len(prices) - 10)
                close_10d = prices[idx_10d]["close"]
                return_10d = ((latest_close - close_10d) / close_10d) * 100

                results.append({
                    "ticker": ticker,
                    "sector": self.SECTOR_ETFS.get(ticker, "Benchmark"),
                    "return_30d": round(return_30d, 2),
                    "return_10d": round(return_10d, 2),
                    "latest_close": round(latest_close, 2),
                })

            except Exception as e:
                self.logger.warning(f"Failed to fetch {ticker}: {e}")
                continue

        return results

    def scan(self, trade_date: str) -> List[SectorSignal]:
        """
        Run the sector scan.

        Returns ranked SectorSignal list. If LLM parsing fails,
        falls back to pure quantitative ranking (no LLM needed).
        """
        raw_data = self._fetch_sector_returns(trade_date)
        if not raw_data:
            self.logger.error("No sector data fetched")
            return []

        # Separate SPY benchmark
        spy_data = next((d for d in raw_data if d["ticker"] == "SPY"), None)
        sector_data = [d for d in raw_data if d["ticker"] != "SPY"]

        # Pre-calculate relative-to-SPY returns
        spy_30d = spy_data["return_30d"] if spy_data else 0.0
        spy_10d = spy_data["return_10d"] if spy_data else 0.0
        for s in sector_data:
            s["relative_to_spy_30d"] = round(s["return_30d"] - spy_30d, 2)

        # Sort by 30d return for quantitative ranking (used as fallback)
        sector_data.sort(key=lambda x: x["return_30d"], reverse=True)
        for i, s in enumerate(sector_data):
            s["quant_rank"] = i + 1

        # Build data table for LLM
        table = "Sector ETF Performance:\n"
        table += f"SPY (benchmark): 30d={spy_30d:+.2f}%, 10d={spy_10d:+.2f}%\n\n"
        table += "| Sector | ETF | 30d Return | 10d Return | vs SPY |\n"
        table += "|--------|-----|-----------|-----------|--------|\n"
        for s in sector_data:
            table += (
                f"| {s['sector']} | {s['ticker']} | "
                f"{s['return_30d']:+.2f}% | {s['return_10d']:+.2f}% | "
                f"{s['relative_to_spy_30d']:+.2f}% |\n"
            )

        # LLM call — pure interpretation, no tool calls
        prompt = ChatPromptTemplate.from_messages([
            ("system", SECTOR_SCANNER_SYSTEM_PROMPT),
            ("human", f"Date: {trade_date}\n\n{table}"),
        ])

        try:
            result = (prompt | self.llm).invoke({})
            content = result.content if hasattr(result, "content") else str(result)
            signals = self._parse_sector_response(content, sector_data)
            if signals:
                return signals
        except Exception as e:
            self.logger.warning(f"LLM sector analysis failed, using quant fallback: {e}")

        # Fallback: pure quantitative ranking (no LLM needed)
        return self._quant_fallback(sector_data, spy_30d)

    def _parse_sector_response(
        self, response_text: str, raw_data: List[Dict]
    ) -> Optional[List[SectorSignal]]:
        """Parse LLM JSON response into SectorSignal list."""
        # Strip markdown code fences if present
        text = response_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract JSON from within the response
            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return None
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return None

        sectors = data.get("sectors", [])
        if not sectors:
            return None

        signals = []
        for s in sectors:
            signals.append(SectorSignal(
                sector=s.get("sector", ""),
                etf=s.get("etf", ""),
                return_30d=float(s.get("return_30d", 0)),
                return_10d=float(s.get("return_10d", 0)),
                relative_to_spy=float(s.get("relative_to_spy", 0)),
                momentum_rank=int(s.get("momentum_rank", 0)),
                narrative=s.get("narrative", ""),
            ))

        return sorted(signals, key=lambda x: x.momentum_rank) if signals else None

    def _quant_fallback(
        self, sector_data: List[Dict], spy_30d: float
    ) -> List[SectorSignal]:
        """Pure quantitative ranking when LLM fails."""
        signals = []
        for s in sector_data:
            signals.append(SectorSignal(
                sector=s["sector"],
                etf=s["ticker"],
                return_30d=s["return_30d"],
                return_10d=s["return_10d"],
                relative_to_spy=s.get("relative_to_spy_30d", s["return_30d"] - spy_30d),
                momentum_rank=s["quant_rank"],
                narrative="(quantitative ranking — LLM analysis unavailable)",
            ))
        return signals


# =============================================================================
# Sub-Agent 2: Catalyst / News Scanner
# =============================================================================

CATALYST_SCANNER_SYSTEM_PROMPT = """You are a financial news catalyst analyst. Your ONLY job is to extract actionable trading catalysts from news.

You will receive recent financial news. For each news item that contains a SPECIFIC, ACTIONABLE catalyst:
- Identify the ticker symbol affected (if mentioned or clearly implied)
- Classify the catalyst type
- Score sentiment from -1.0 (very bearish) to +1.0 (very bullish)
- Rate actionability: "high" (trade within days), "medium" (watch list), "low" (background)

Catalyst types: earnings_beat, earnings_miss, analyst_upgrade, analyst_downgrade,
fda_approval, fda_rejection, merger_acquisition, product_launch, guidance_raise,
guidance_cut, regulatory_action, insider_buying, buyback_announced, sector_rotation,
macro_policy, geopolitical, other

Rules:
- ONLY include catalysts with clear stock-level implications
- Skip vague market commentary and opinion pieces
- Prioritize catalysts that are RECENT (within 3 days) and SPECIFIC
- If no clear ticker, include sector if identifiable
- Return ONLY JSON, no markdown

Return this JSON structure:
{
  "catalysts": [
    {
      "ticker": "NVDA",
      "sector": "Technology",
      "catalyst_type": "earnings_beat",
      "headline": "NVIDIA Q3 revenue tops estimates by 22%, raises guidance",
      "sentiment_score": 0.9,
      "recency_days": 1,
      "actionability": "high"
    }
  ],
  "dominant_narrative": "One sentence on the biggest theme in current news"
}"""


class CatalystNewsScanner:
    """
    Sub-agent that scans recent news for specific, actionable trading catalysts.

    Data flow:
      1. Fetch recent global news via existing dataflow
      2. Optionally fetch company-specific news for tickers in the screening universe
      3. Pass news to a focused LLM prompt for catalyst extraction
      4. Output: List[CatalystSignal]

    Design decision: This agent uses the LLM for interpretation (not tool-calling)
    because the news data is pre-fetched and finite. The LLM's job is purely
    classification and sentiment scoring — tasks where it excels.
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _fetch_news(self, trade_date: str, look_back_days: int = 3) -> str:
        """Fetch global + market news using existing dataflows."""
        from tradingagents.dataflows.interface import route_to_vendor

        # Global news
        try:
            global_news = route_to_vendor(
                "get_global_news",
                trade_date,
                look_back_days,
                limit=20,
            )
        except Exception as e:
            self.logger.warning(f"Global news fetch failed: {e}")
            global_news = ""

        return global_news

    def _fetch_company_news_batch(
        self, tickers: List[str], trade_date: str, look_back_days: int = 3
    ) -> str:
        """Fetch company-specific news for a batch of tickers."""
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=look_back_days)
        start_str = start_dt.strftime("%Y-%m-%d")

        all_news = []
        for ticker in tickers[:10]:  # Cap to avoid excessive API calls
            try:
                news = route_to_vendor(
                    "get_news",
                    ticker,
                    start_str,
                    trade_date,
                )
                if news:
                    all_news.append(f"--- {ticker} ---\n{news}")
            except Exception as e:
                self.logger.debug(f"Company news for {ticker} failed: {e}")
                continue

        return "\n\n".join(all_news)

    def scan(
        self,
        trade_date: str,
        focus_tickers: Optional[List[str]] = None,
        look_back_days: int = 3,
    ) -> List[CatalystSignal]:
        """
        Run the catalyst/news scan.

        Args:
            trade_date: Target date
            focus_tickers: Optional list of tickers to fetch company-specific news for.
                           If None, only global news is scanned.
            look_back_days: How far back to look for news

        Returns:
            List of CatalystSignal, sorted by sentiment_score descending
        """
        # Gather news data
        news_text = self._fetch_news(trade_date, look_back_days)

        if focus_tickers:
            company_news = self._fetch_company_news_batch(
                focus_tickers, trade_date, look_back_days
            )
            if company_news:
                news_text += f"\n\n## Company-Specific News:\n{company_news}"

        if not news_text.strip():
            self.logger.warning("No news data available")
            return []

        # Truncate if news is excessively long (protect context window)
        MAX_NEWS_CHARS = 8000
        if len(news_text) > MAX_NEWS_CHARS:
            news_text = news_text[:MAX_NEWS_CHARS] + "\n\n[... truncated for brevity ...]"

        # LLM call — classification and sentiment, no tool calls
        prompt = ChatPromptTemplate.from_messages([
            ("system", CATALYST_SCANNER_SYSTEM_PROMPT),
            ("human", f"Date: {trade_date}\nLookback: {look_back_days} days\n\n{news_text}"),
        ])

        try:
            result = (prompt | self.llm).invoke({})
            content = result.content if hasattr(result, "content") else str(result)
            signals = self._parse_catalyst_response(content)
            if signals:
                return sorted(signals, key=lambda c: c.sentiment_score, reverse=True)
        except Exception as e:
            self.logger.warning(f"LLM catalyst extraction failed: {e}")

        # Fallback: return empty (no heuristic fallback for news — LLM is essential here)
        return []

    def _parse_catalyst_response(self, response_text: str) -> Optional[List[CatalystSignal]]:
        """Parse LLM JSON response into CatalystSignal list."""
        text = response_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return None
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return None

        catalysts = data.get("catalysts", [])
        if not catalysts:
            return None

        signals = []
        for c in catalysts:
            signals.append(CatalystSignal(
                ticker=c.get("ticker", ""),
                sector=c.get("sector", ""),
                catalyst_type=c.get("catalyst_type", "other"),
                headline=c.get("headline", ""),
                sentiment_score=float(c.get("sentiment_score", 0)),
                recency_days=int(c.get("recency_days", 0)),
                actionability=c.get("actionability", "medium"),
            ))

        return signals if signals else None


# =============================================================================
# Sub-Agent 3: Technical Momentum Scanner
# =============================================================================

TECHNICAL_SCANNER_SYSTEM_PROMPT = """You are a technical analysis screener. Your ONLY job is to evaluate pre-computed technical data and score each stock's setup quality.

You will receive a table of stocks with their technical indicators. For each stock, score the setup quality and identify the volume/trend regime.

Scoring guide:
- composite_score: 0-100 weighted score based on:
  * Price above 50 SMA AND 200 SMA: +25 points
  * ADX > 25 (trending): +20 points
  * ADX > 40 (strong trend): +10 bonus points
  * 20-day momentum > 5%: +15 points
  * 20-day momentum > 10%: +5 bonus points
  * Relative strength vs SPY > 1.0: +15 points
  * Volume ratio > 1.2 (above-average volume): +10 points
  * OBV accumulation pattern: +10 points (detect from data if possible)

- obv_trend: Based on the price/volume relationship:
  * "accumulation" if price up AND volume expanding
  * "distribution" if price up BUT volume declining (bearish divergence)
  * "neutral" if unclear

Return ONLY this JSON structure:
{
  "signals": [
    {
      "ticker": "NVDA",
      "price": 950.00,
      "vs_sma50_pct": 8.5,
      "vs_sma200_pct": 45.2,
      "momentum_20d": 12.3,
      "adx": 35.0,
      "obv_trend": "accumulation",
      "relative_strength_vs_spy": 1.85,
      "volume_ratio": 1.4,
      "composite_score": 85
    }
  ]
}"""


class TechnicalMomentumScanner:
    """
    Sub-agent that screens a stock universe for technical breakout setups.

    Data flow:
      1. For each ticker in the universe, pre-fetch:
         - Current price + 30-day price history
         - 50-day and 200-day SMA
         - ADX (via advanced_indicators if available, else estimated)
         - Volume data for volume ratio
      2. Calculate relative strength vs SPY
      3. Pass pre-computed data table to LLM for scoring and OBV interpretation
      4. Output: List[TechnicalSignal]

    Design decisions:
      - Pre-fetch all data programmatically (no LLM tool calls for data gathering)
      - LLM is used ONLY for qualitative scoring and OBV divergence detection
      - Quantitative fallback scoring if LLM fails (pipeline never breaks)
      - Parallelized data fetching for performance
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}
        self.logger = logging.getLogger(self.__class__.__name__)

    def _fetch_ticker_technicals(
        self, ticker: str, trade_date: str
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch all technical data for a single ticker.
        Returns None if data is unavailable (ticker skipped silently).
        """
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start_30d = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")

        try:
            # --- Price data (30 days) ---
            raw_csv = route_to_vendor(
                "get_stock_data", ticker, start_30d, trade_date
            )
            lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
            if len(lines) < 15:
                return None

            header = lines[0].split(",")
            close_idx = header.index("Close")
            vol_idx = header.index("Volume") if "Volume" in header else None

            prices = []
            volumes = []
            for line in lines[1:]:
                parts = line.split(",")
                try:
                    prices.append(float(parts[close_idx]))
                    if vol_idx is not None:
                        volumes.append(float(parts[vol_idx]))
                except (ValueError, IndexError):
                    continue

            if len(prices) < 10:
                return None

            current_price = prices[-1]

            # 20-day momentum
            idx_20d = max(0, len(prices) - 20)
            price_20d_ago = prices[idx_20d]
            momentum_20d = ((current_price - price_20d_ago) / price_20d_ago) * 100

            # Volume ratio (recent 5-day avg vs 20-day avg)
            volume_ratio = 0.0
            if volumes and len(volumes) >= 20:
                avg_vol_20d = sum(volumes[-20:]) / 20
                avg_vol_5d = sum(volumes[-5:]) / 5
                if avg_vol_20d > 0:
                    volume_ratio = avg_vol_5d / avg_vol_20d

            # --- SMA 50 ---
            sma50_val = None
            try:
                sma50_raw = route_to_vendor(
                    "get_indicators", ticker, "close_50_sma", trade_date, 3
                )
                sma50_val = self._extract_indicator_value(sma50_raw)
            except Exception:
                pass

            # --- SMA 200 ---
            sma200_val = None
            try:
                sma200_raw = route_to_vendor(
                    "get_indicators", ticker, "close_200_sma", trade_date, 3
                )
                sma200_val = self._extract_indicator_value(sma200_raw)
            except Exception:
                pass

            # --- ADX (try advanced indicator, fall back to 0) ---
            adx_val = 0.0
            try:
                # Try the advanced indicator tool if available
                adx_raw = route_to_vendor(
                    "get_indicators", ticker, "adx", trade_date, 3
                )
                adx_val = self._extract_indicator_value(adx_raw) or 0.0
            except Exception:
                # ADX not available via vendor — we'll estimate later or leave at 0
                pass

            # Calculate % vs SMAs
            vs_sma50 = 0.0
            vs_sma200 = 0.0
            if sma50_val and sma50_val > 0:
                vs_sma50 = ((current_price - sma50_val) / sma50_val) * 100
            if sma200_val and sma200_val > 0:
                vs_sma200 = ((current_price - sma200_val) / sma200_val) * 100

            return {
                "ticker": ticker,
                "price": round(current_price, 2),
                "vs_sma50_pct": round(vs_sma50, 2),
                "vs_sma200_pct": round(vs_sma200, 2),
                "momentum_20d": round(momentum_20d, 2),
                "adx": round(adx_val, 1),
                "volume_ratio": round(volume_ratio, 2),
                "prices_20d": prices[-20:],  # For LLM OBV analysis
                "volumes_20d": volumes[-20:] if len(volumes) >= 20 else volumes,
            }

        except Exception as e:
            self.logger.debug(f"Technical data fetch failed for {ticker}: {e}")
            return None

    def _extract_indicator_value(self, raw_text: str) -> Optional[float]:
        """
        Extract the latest numeric value from indicator tool output.
        Handles multiple common formats returned by route_to_vendor get_indicators.
        """
        if not raw_text:
            return None

        # Pattern: "value: 123.45" or "123.45" on last non-empty line
        lines = [l.strip() for l in raw_text.strip().split("\n") if l.strip()]
        for line in reversed(lines):
            # Try "key: value" format
            if ":" in line:
                val_str = line.split(":")[-1].strip()
                try:
                    return float(val_str)
                except ValueError:
                    pass
            # Try bare float
            try:
                return float(line)
            except ValueError:
                continue

        # Try regex for any float
        match = re.search(r"[-+]?\d+\.?\d*", raw_text)
        if match:
            try:
                return float(match.group())
            except ValueError:
                pass

        return None

    def _fetch_spy_returns(self, trade_date: str) -> Tuple[float, float]:
        """Fetch SPY 20-day return for relative strength calculation."""
        from tradingagents.dataflows.interface import route_to_vendor

        end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
        start = (end_dt - timedelta(days=35)).strftime("%Y-%m-%d")

        try:
            raw_csv = route_to_vendor("get_stock_data", "SPY", start, trade_date)
            lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
            if len(lines) < 15:
                return 0.0, 0.0

            header = lines[0].split(",")
            close_idx = header.index("Close")

            prices = []
            for line in lines[1:]:
                try:
                    prices.append(float(line.split(",")[close_idx]))
                except (ValueError, IndexError):
                    continue

            if len(prices) < 10:
                return 0.0, 0.0

            idx_20d = max(0, len(prices) - 20)
            spy_current = prices[-1]
            spy_20d_ago = prices[idx_20d]
            spy_return_20d = ((spy_current - spy_20d_ago) / spy_20d_ago) * 100

            return spy_current, spy_return_20d

        except Exception:
            return 0.0, 0.0

    def scan(
        self,
        universe: List[str],
        trade_date: str,
        max_workers: int = 4,
    ) -> List[TechnicalSignal]:
        """
        Screen the universe for technical breakout setups.

        Args:
            universe: List of ticker symbols to screen
            trade_date: Target date
            max_workers: Concurrent data fetch threads

        Returns:
            List of TechnicalSignal, sorted by composite_score descending
        """
        if not universe:
            return []

        # Fetch SPY benchmark
        spy_price, spy_return_20d = self._fetch_spy_returns(trade_date)

        # Parallel data fetching
        raw_technicals = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._fetch_ticker_technicals, ticker, trade_date): ticker
                for ticker in universe
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    result = future.result()
                    if result is not None:
                        # Calculate relative strength vs SPY
                        if spy_return_20d != 0:
                            # RS ratio: stock's 20d return / SPY's 20d return
                            # Normalized so RS > 1 = outperforming
                            stock_ret = result["momentum_20d"]
                            if spy_return_20d > 0:
                                result["relative_strength_vs_spy"] = round(
                                    stock_ret / spy_return_20d, 2
                                )
                            else:
                                # SPY negative: stock less negative = outperforming
                                result["relative_strength_vs_spy"] = round(
                                    1.0 + (stock_ret - spy_return_20d) / 100, 2
                                )
                        else:
                            result["relative_strength_vs_spy"] = 1.0

                        raw_technicals.append(result)
                except Exception as e:
                    self.logger.debug(f"Data fetch failed for {ticker}: {e}")

        if not raw_technicals:
            self.logger.warning("No technical data fetched for any ticker")
            return []

        # Build data table for LLM scoring
        table = f"Technical Screening Data ({trade_date}):\n"
        table += f"SPY 20d Return: {spy_return_20d:+.2f}%\n\n"
        table += "| Ticker | Price | vs 50SMA | vs 200SMA | 20d Mom | ADX | Vol Ratio | RS vs SPY |\n"
        table += "|--------|-------|---------|----------|---------|-----|-----------|----------|\n"

        for t in raw_technicals:
            table += (
                f"| {t['ticker']} | ${t['price']:.2f} | "
                f"{t['vs_sma50_pct']:+.1f}% | {t['vs_sma200_pct']:+.1f}% | "
                f"{t['momentum_20d']:+.1f}% | {t['adx']:.0f} | "
                f"{t['volume_ratio']:.2f} | {t['relative_strength_vs_spy']:.2f} |\n"
            )

        # Add price/volume arrays for OBV analysis (compact format)
        table += "\n\nRecent Price/Volume for OBV analysis:\n"
        for t in raw_technicals[:15]:  # Limit to top 15 to protect context window
            p_str = ",".join(f"{p:.1f}" for p in t.get("prices_20d", [])[-10:])
            v_str = ",".join(f"{int(v)}" for v in t.get("volumes_20d", [])[-10:])
            table += f"{t['ticker']}: prices=[{p_str}] volumes=[{v_str}]\n"

        # LLM call for scoring and OBV interpretation
        prompt = ChatPromptTemplate.from_messages([
            ("system", TECHNICAL_SCANNER_SYSTEM_PROMPT),
            ("human", table),
        ])

        signals = None
        try:
            result = (prompt | self.llm).invoke({})
            content = result.content if hasattr(result, "content") else str(result)
            signals = self._parse_technical_response(content)
        except Exception as e:
            self.logger.warning(f"LLM technical scoring failed, using quant fallback: {e}")

        if not signals:
            # Quantitative fallback scoring
            signals = self._quant_fallback_scoring(raw_technicals)

        return sorted(signals, key=lambda s: s.composite_score, reverse=True)

    def _parse_technical_response(
        self, response_text: str
    ) -> Optional[List[TechnicalSignal]]:
        """Parse LLM JSON response into TechnicalSignal list."""
        text = response_text.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            json_match = re.search(r"\{[\s\S]*\}", text)
            if not json_match:
                return None
            try:
                data = json.loads(json_match.group())
            except json.JSONDecodeError:
                return None

        signals_data = data.get("signals", [])
        if not signals_data:
            return None

        signals = []
        for s in signals_data:
            signals.append(TechnicalSignal(
                ticker=s.get("ticker", ""),
                price=float(s.get("price", 0)),
                vs_sma50_pct=float(s.get("vs_sma50_pct", 0)),
                vs_sma200_pct=float(s.get("vs_sma200_pct", 0)),
                momentum_20d=float(s.get("momentum_20d", 0)),
                adx=float(s.get("adx", 0)),
                obv_trend=s.get("obv_trend", "neutral"),
                relative_strength_vs_spy=float(s.get("relative_strength_vs_spy", 0)),
                volume_ratio=float(s.get("volume_ratio", 0)),
                composite_score=float(s.get("composite_score", 0)),
            ))

        return signals if signals else None

    def _quant_fallback_scoring(
        self, raw_technicals: List[Dict]
    ) -> List[TechnicalSignal]:
        """
        Pure quantitative composite scoring when LLM fails.
        Uses the same scoring rubric as the LLM prompt so results are comparable.
        """
        signals = []
        for t in raw_technicals:
            score = 0.0

            # Price above both SMAs: +25
            if t["vs_sma50_pct"] > 0 and t["vs_sma200_pct"] > 0:
                score += 25

            # ADX scoring
            if t["adx"] > 40:
                score += 30  # 20 + 10 bonus
            elif t["adx"] > 25:
                score += 20

            # Momentum scoring
            if t["momentum_20d"] > 10:
                score += 20  # 15 + 5 bonus
            elif t["momentum_20d"] > 5:
                score += 15

            # Relative strength
            rs = t.get("relative_strength_vs_spy", 1.0)
            if rs > 1.0:
                score += 15

            # Volume
            if t.get("volume_ratio", 0) > 1.2:
                score += 10

            # OBV: can't compute without LLM, leave at 0 for fallback

            signals.append(TechnicalSignal(
                ticker=t["ticker"],
                price=t["price"],
                vs_sma50_pct=t["vs_sma50_pct"],
                vs_sma200_pct=t["vs_sma200_pct"],
                momentum_20d=t["momentum_20d"],
                adx=t["adx"],
                obv_trend="neutral",  # Unknown without LLM
                relative_strength_vs_spy=t.get("relative_strength_vs_spy", 1.0),
                volume_ratio=t.get("volume_ratio", 0),
                composite_score=score,
            ))

        return signals


# =============================================================================
# Orchestrator: Parallel execution of all three sub-agents
# =============================================================================

class IntelligenceScanner:
    """
    Top-level orchestrator that runs all three sub-agents in parallel
    and merges their outputs into a single IntelligenceResult.

    Usage:
        from tradingagents.agents.discovery.intelligence_sub_agents import IntelligenceScanner

        scanner = IntelligenceScanner(llm=my_llm, config=my_config)
        result = scanner.scan_all(
            trade_date="2025-06-15",
            universe=["AAPL", "NVDA", "MSFT", ...],
        )

        # Access structured results
        print(result.hot_sectors)
        print(result.breakout_candidates)
        print(result.tickers_with_multi_signal_alignment())
    """

    def __init__(self, llm, config: Optional[Dict[str, Any]] = None):
        self.llm = llm
        self.config = config or {}

        self.sector_scanner = MacroSectorScanner(llm=llm, config=config)
        self.catalyst_scanner = CatalystNewsScanner(llm=llm, config=config)
        self.technical_scanner = TechnicalMomentumScanner(llm=llm, config=config)

        self.logger = logging.getLogger(self.__class__.__name__)

    def scan_all(
        self,
        trade_date: str,
        universe: Optional[List[str]] = None,
        focus_tickers: Optional[List[str]] = None,
        max_workers: int = 3,
    ) -> IntelligenceResult:
        """
        Run all three sub-agents in parallel and merge results.

        Args:
            trade_date: Target date (YYYY-MM-DD)
            universe: Ticker universe for technical scanning.
                      If None, uses DEFAULT_SCREENING_UNIVERSE.
            focus_tickers: Optional tickers for company-specific news.
                           If None, uses top tickers from sector scan.
            max_workers: Thread pool size for parallel sub-agent execution.
                         Default 3 = one thread per sub-agent.

        Returns:
            IntelligenceResult with all signals merged
        """
        import time
        from tradingagents.agents.discovery.stock_recommender import DEFAULT_SCREENING_UNIVERSE

        start_time = time.time()
        universe = universe or DEFAULT_SCREENING_UNIVERSE

        result = IntelligenceResult(scan_date=trade_date)

        # Run sub-agents in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # Submit all three scans
            sector_future = pool.submit(self.sector_scanner.scan, trade_date)
            catalyst_future = pool.submit(
                self.catalyst_scanner.scan,
                trade_date,
                focus_tickers,
            )
            technical_future = pool.submit(
                self.technical_scanner.scan,
                universe,
                trade_date,
            )

            # Collect results (each has built-in error handling)
            try:
                result.sector_signals = sector_future.result(timeout=120)
                self.logger.info(f"Sector scan: {len(result.sector_signals)} sectors ranked")
            except Exception as e:
                result.errors.append(f"Sector scan failed: {e}")
                self.logger.error(f"Sector scan failed: {e}")

            try:
                result.catalyst_signals = catalyst_future.result(timeout=120)
                self.logger.info(f"Catalyst scan: {len(result.catalyst_signals)} catalysts found")
            except Exception as e:
                result.errors.append(f"Catalyst scan failed: {e}")
                self.logger.error(f"Catalyst scan failed: {e}")

            try:
                result.technical_signals = technical_future.result(timeout=180)
                self.logger.info(f"Technical scan: {len(result.technical_signals)} tickers screened")
            except Exception as e:
                result.errors.append(f"Technical scan failed: {e}")
                self.logger.error(f"Technical scan failed: {e}")

        result.scan_duration_secs = round(time.time() - start_time, 1)

        # Log summary
        aligned = result.tickers_with_multi_signal_alignment()
        self.logger.info(
            f"Intelligence scan complete in {result.scan_duration_secs}s. "
            f"Hot sectors: {[s.etf for s in result.hot_sectors]}, "
            f"Breakout candidates: {[t.ticker for t in result.breakout_candidates]}, "
            f"Multi-signal aligned: {aligned}"
        )

        return result

    def scan_with_dynamic_universe(
        self,
        trade_date: str,
        base_universe: Optional[List[str]] = None,
    ) -> IntelligenceResult:
        """
        Two-phase scan: first gather sector + catalyst intelligence,
        then dynamically expand the technical screening universe based
        on what sectors are hot and which tickers appear in news.

        This is the RECOMMENDED entry point for the discovery pipeline.
        It addresses the static-universe problem by adapting the screening
        universe to current market conditions.
        """
        from tradingagents.agents.discovery.stock_recommender import DEFAULT_SCREENING_UNIVERSE

        base_universe = base_universe or DEFAULT_SCREENING_UNIVERSE

        # Phase 1: Sector + Catalyst scans (parallel)
        with ThreadPoolExecutor(max_workers=2) as pool:
            sector_future = pool.submit(self.sector_scanner.scan, trade_date)
            catalyst_future = pool.submit(
                self.catalyst_scanner.scan, trade_date, None, 3
            )

            sector_signals = []
            catalyst_signals = []

            try:
                sector_signals = sector_future.result(timeout=120)
            except Exception as e:
                self.logger.error(f"Phase 1 sector scan failed: {e}")

            try:
                catalyst_signals = catalyst_future.result(timeout=120)
            except Exception as e:
                self.logger.error(f"Phase 1 catalyst scan failed: {e}")

        # Phase 2: Expand universe based on Phase 1 intelligence
        expanded_universe = set(base_universe)

        # Add tickers from catalyst signals
        for c in catalyst_signals:
            if c.ticker and len(c.ticker) <= 5 and c.ticker.isalpha():
                expanded_universe.add(c.ticker.upper())

        self.logger.info(
            f"Universe expanded from {len(base_universe)} to {len(expanded_universe)} "
            f"tickers based on Phase 1 intelligence"
        )

        # Phase 3: Technical scan on expanded universe
        try:
            technical_signals = self.technical_scanner.scan(
                list(expanded_universe), trade_date
            )
        except Exception as e:
            self.logger.error(f"Phase 2 technical scan failed: {e}")
            technical_signals = []

        # Assemble result
        result = IntelligenceResult(
            sector_signals=sector_signals,
            catalyst_signals=catalyst_signals,
            technical_signals=technical_signals,
            scan_date=trade_date,
        )

        aligned = result.tickers_with_multi_signal_alignment()
        self.logger.info(
            f"Dynamic scan complete. Multi-signal aligned tickers: {aligned}"
        )

        return result

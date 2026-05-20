from __future__ import annotations
"""
Theme Engine — Core Data Models

Dataclasses shared by the taxonomy loader, evidence collector,
exposure scorer, and the Stage -1 theme scanner.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Value-chain graph primitives
# ---------------------------------------------------------------------------

@dataclass
class ThemeNode:
    """
    One node in a theme's value-chain graph.

    node_type choices:
      "theme_root"   — the demand driver that activates the chain
      "bottleneck"   — a constrained layer (mark is_bottleneck=True here too)
      "supply_layer" — an intermediate supply-chain tier
      "end_market"   — downstream consumption or deployment
      "enabler"      — a horizontal enabler (software platform, testing, etc.)
    """
    node_id: str
    label: str
    node_type: str
    is_bottleneck: bool = False
    description: str = ""


@dataclass
class TickerExposure:
    """
    Maps a public ticker to a specific value-chain node within a theme.

    exposure_type choices:
      "direct"       — the company's core business IS this node
      "indirect"     — the company's products primarily serve this node
      "second_order" — benefits as the theme grows, but not as the primary driver

    source choices:
      "seed"         — manually seeded in taxonomy.yaml
      "llm_inferred" — inferred by the exposure scorer LLM
      "filing"       — extracted from SEC filing or earnings text
      "news"         — extracted from news / press release
    """
    ticker: str
    node_id: str
    exposure_type: str
    exposure_confidence: float  # 0.0–1.0
    evidence: List[str] = field(default_factory=list)
    source: str = "seed"


@dataclass
class ThemeChain:
    """
    A complete theme: its value-chain DAG and all known ticker exposures.

    nodes + edges describe the supply-chain graph.
    ticker_exposures map public tickers to specific nodes in that graph.
    """
    theme_id: str
    theme_label: str
    description: str
    nodes: List[ThemeNode] = field(default_factory=list)
    edges: List[Tuple[str, str]] = field(default_factory=list)
    ticker_exposures: List[TickerExposure] = field(default_factory=list)
    freshness_date: str = ""
    acceleration_signal: float = 0.0  # 0–1; populated at scan time

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def bottleneck_nodes(self) -> List[ThemeNode]:
        return [n for n in self.nodes if n.is_bottleneck]

    @property
    def bottleneck_node_ids(self) -> set:
        return {n.node_id for n in self.bottleneck_nodes}

    @property
    def bottleneck_tickers(self) -> List[TickerExposure]:
        ids = self.bottleneck_node_ids
        return [e for e in self.ticker_exposures if e.node_id in ids]

    def node_by_id(self, node_id: str) -> Optional[ThemeNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def exposures_for_ticker(self, ticker: str) -> List[TickerExposure]:
        return [e for e in self.ticker_exposures if e.ticker == ticker.upper()]

    @property
    def all_tickers(self) -> List[str]:
        seen: List[str] = []
        for e in self.ticker_exposures:
            if e.ticker not in seen:
                seen.append(e.ticker)
        return seen


# ---------------------------------------------------------------------------
# Pipeline output schema
# ---------------------------------------------------------------------------

@dataclass
class ThemeExposureCandidate:
    """
    Output schema for one ticker × theme pair produced by Stage -1.

    This is what flows into Stage 1C, the two-layer scorer, and the
    story validator.  The shape intentionally matches the JSON schema
    in the spec so downstream consumers can ser/deser without extra work.
    """
    theme: str
    bottleneck: str
    ticker: str
    exposure_type: str           # "direct" | "indirect" | "second_order"
    exposure_confidence: float   # 0.0–1.0
    evidence: List[str] = field(default_factory=list)
    why_it_matters: str = ""
    theme_acceleration: float = 0.0
    freshness_date: str = ""
    # Back-references — useful when merging with Stage 1A/1B results
    theme_id: str = ""
    node_id: str = ""

    def to_dict(self) -> dict:
        return {
            "theme": self.theme,
            "bottleneck": self.bottleneck,
            "ticker": self.ticker,
            "exposure_type": self.exposure_type,
            "exposure_confidence": round(self.exposure_confidence, 4),
            "evidence": self.evidence,
            "why_it_matters": self.why_it_matters,
            "theme_acceleration": round(self.theme_acceleration, 4),
            "freshness_date": self.freshness_date,
            "theme_id": self.theme_id,
            "node_id": self.node_id,
        }

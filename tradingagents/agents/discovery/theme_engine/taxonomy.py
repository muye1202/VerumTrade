from __future__ import annotations
"""
Theme Engine — Taxonomy Loader

Reads data/themes/taxonomy.yaml (and an optional overrides.yaml),
validates the DAG structure, and returns a list of ThemeChain objects
ready for use by the exposure scorer and theme scanner.

Usage:
    loader = ThemeTaxonomyLoader()
    chains = loader.load()

    # Or with a config dict:
    loader = ThemeTaxonomyLoader(config={"theme_engine": {"taxonomy_path": "/path/to/custom.yaml"}})

The loader is safe to instantiate multiple times — the parsed taxonomy
is cached per-file-path so repeated calls within the same process are free.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import ThemeChain, ThemeExposureCandidate, ThemeNode, TickerExposure

logger = logging.getLogger(__name__)

# Module-level parse cache: path → List[ThemeChain]
_TAXONOMY_CACHE: Dict[str, List[ThemeChain]] = {}

# Default data directory: <repo_root>/data/themes/
_REPO_ROOT = Path(__file__).parents[4]
_DEFAULT_TAXONOMY_PATH = _REPO_ROOT / "data" / "themes" / "taxonomy.yaml"
_DEFAULT_OVERRIDES_PATH = _REPO_ROOT / "data" / "themes" / "overrides.yaml"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class ThemeTaxonomyLoader:
    """
    Loads, validates, and caches the theme taxonomy.

    config keys (all optional, under config["theme_engine"]):
      taxonomy_path   — absolute path to the taxonomy YAML file
      overrides_path  — absolute path to the overrides YAML file
      force_reload    — bool, bypass cache and re-read from disk
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        taxonomy_path: Optional[str] = None,
        overrides_path: Optional[str] = None,
    ):
        cfg = dict((config or {}).get("theme_engine") or {})
        self._taxonomy_path = Path(
            taxonomy_path
            or cfg.get("taxonomy_path")
            or _DEFAULT_TAXONOMY_PATH
        )
        self._overrides_path = Path(
            overrides_path
            or cfg.get("overrides_path")
            or _DEFAULT_OVERRIDES_PATH
        )
        self._force_reload = bool(cfg.get("force_reload", False))

    # ------------------------------------------------------------------

    def load(self) -> List[ThemeChain]:
        """Return the full list of ThemeChain objects, using cache where possible."""
        cache_key = str(self._taxonomy_path)
        if not self._force_reload and cache_key in _TAXONOMY_CACHE:
            return _TAXONOMY_CACHE[cache_key]

        raw = self._load_yaml(self._taxonomy_path)
        chains = self._parse_all(raw)

        if self._overrides_path.exists():
            override_raw = self._load_yaml(self._overrides_path)
            chains = self._apply_overrides(chains, override_raw)

        errors = self._validate(chains)
        for e in errors:
            logger.warning("taxonomy validation: %s", e)

        _TAXONOMY_CACHE[cache_key] = chains
        logger.info(
            "Taxonomy loaded: %d themes, %d total seed exposures",
            len(chains),
            sum(len(c.ticker_exposures) for c in chains),
        )
        return chains

    def load_by_id(self, theme_id: str) -> Optional[ThemeChain]:
        for chain in self.load():
            if chain.theme_id == theme_id:
                return chain
        return None

    def all_tickers(self) -> List[str]:
        """Return deduplicated list of all seeded tickers across all themes."""
        seen: List[str] = []
        for chain in self.load():
            for ticker in chain.all_tickers:
                if ticker not in seen:
                    seen.append(ticker)
        return seen

    def candidates_for_ticker(self, ticker: str) -> List[ThemeExposureCandidate]:
        """Return seed-level ThemeExposureCandidates for a specific ticker."""
        ticker = ticker.upper()
        results: List[ThemeExposureCandidate] = []
        for chain in self.load():
            for exp in chain.exposures_for_ticker(ticker):
                node = chain.node_by_id(exp.node_id)
                bottleneck_label = next(
                    (n.label for n in chain.bottleneck_nodes), chain.theme_label
                )
                results.append(
                    ThemeExposureCandidate(
                        theme=chain.theme_label,
                        bottleneck=bottleneck_label,
                        ticker=ticker,
                        exposure_type=exp.exposure_type,
                        exposure_confidence=exp.exposure_confidence,
                        evidence=list(exp.evidence),
                        why_it_matters=_why_it_matters(chain, node, exp),
                        theme_id=chain.theme_id,
                        node_id=exp.node_id,
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_yaml(path: Path) -> Dict[str, Any]:
        try:
            import yaml  # pyyaml
        except ImportError as exc:
            raise ImportError(
                "PyYAML is required for the theme taxonomy. "
                "Install it with: pip install pyyaml"
            ) from exc

        if not path.exists():
            raise FileNotFoundError(
                f"Taxonomy file not found: {path}\n"
                "Expected at: data/themes/taxonomy.yaml (repo root)"
            )
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"taxonomy YAML must be a mapping, got {type(data)}: {path}")
        return data

    @staticmethod
    def _parse_node(raw: Dict[str, Any]) -> ThemeNode:
        return ThemeNode(
            node_id=str(raw["id"]),
            label=str(raw["label"]),
            node_type=str(raw.get("type", "supply_layer")),
            is_bottleneck=bool(raw.get("is_bottleneck", False)),
            description=str(raw.get("description", "")),
        )

    @staticmethod
    def _parse_exposure(raw: Dict[str, Any]) -> TickerExposure:
        evidence = raw.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        return TickerExposure(
            ticker=str(raw["ticker"]).upper().strip(),
            node_id=str(raw["node"]),
            exposure_type=str(raw.get("type", "indirect")),
            exposure_confidence=float(raw.get("confidence", 0.5)),
            evidence=list(evidence),
            source="seed",
        )

    def _parse_chain(self, raw: Dict[str, Any]) -> ThemeChain:
        nodes = [self._parse_node(n) for n in (raw.get("chain") or [])]
        edges: List[Tuple[str, str]] = []
        for edge in (raw.get("edges") or []):
            if isinstance(edge, (list, tuple)) and len(edge) == 2:
                edges.append((str(edge[0]), str(edge[1])))
        exposures = [
            self._parse_exposure(e) for e in (raw.get("seed_exposures") or [])
        ]
        return ThemeChain(
            theme_id=str(raw["id"]),
            theme_label=str(raw["label"]),
            description=str(raw.get("description", "")).strip(),
            nodes=nodes,
            edges=edges,
            ticker_exposures=exposures,
        )

    def _parse_all(self, raw: Dict[str, Any]) -> List[ThemeChain]:
        themes_raw = raw.get("themes") or []
        if not isinstance(themes_raw, list):
            raise ValueError("taxonomy.yaml must have a top-level 'themes' list")
        chains = []
        for item in themes_raw:
            try:
                chains.append(self._parse_chain(item))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed theme entry: %s — %s", item.get("id", "?"), exc)
        return chains

    @staticmethod
    def _validate(chains: List[ThemeChain]) -> List[str]:
        """Return a list of validation warning strings (empty = clean)."""
        errors: List[str] = []
        ids = {c.theme_id for c in chains}
        seen_ids: set = set()
        for chain in chains:
            # Duplicate theme IDs
            if chain.theme_id in seen_ids:
                errors.append(f"Duplicate theme_id: '{chain.theme_id}'")
            seen_ids.add(chain.theme_id)

            node_ids = {n.node_id for n in chain.nodes}

            # Edge references unknown nodes
            for src, dst in chain.edges:
                if src not in node_ids:
                    errors.append(f"[{chain.theme_id}] edge src '{src}' not in nodes")
                if dst not in node_ids:
                    errors.append(f"[{chain.theme_id}] edge dst '{dst}' not in nodes")

            # Exposure references unknown nodes
            for exp in chain.ticker_exposures:
                if exp.node_id not in node_ids:
                    errors.append(
                        f"[{chain.theme_id}] ticker {exp.ticker} references unknown node '{exp.node_id}'"
                    )
                if not (0.0 <= exp.exposure_confidence <= 1.0):
                    errors.append(
                        f"[{chain.theme_id}] {exp.ticker} confidence {exp.exposure_confidence} out of [0,1]"
                    )
        return errors

    @staticmethod
    def _apply_overrides(
        chains: List[ThemeChain], overrides_raw: Dict[str, Any]
    ) -> List[ThemeChain]:
        """
        Merge overrides into existing chains.

        Supports:
          - Adding new themes (new id → appended)
          - Extending seed_exposures of an existing theme
          - Replacing description/label of an existing theme
        """
        override_themes = overrides_raw.get("themes") or []
        chain_by_id = {c.theme_id: c for c in chains}
        for item in override_themes:
            tid = str(item.get("id", ""))
            if not tid:
                continue
            if tid not in chain_by_id:
                # New theme — parse and append
                loader = ThemeTaxonomyLoader.__new__(ThemeTaxonomyLoader)
                try:
                    chains.append(loader._parse_chain(item))
                except Exception as exc:
                    logger.warning("Override: failed to parse new theme '%s': %s", tid, exc)
                continue

            existing = chain_by_id[tid]
            if "label" in item:
                existing.theme_label = str(item["label"])
            if "description" in item:
                existing.description = str(item["description"]).strip()
            # Merge additional seed exposures (no dedup by design — caller controls)
            for raw_exp in (item.get("seed_exposures") or []):
                try:
                    existing.ticker_exposures.append(
                        ThemeTaxonomyLoader._parse_exposure(raw_exp)
                    )
                except Exception as exc:
                    logger.warning("Override: bad exposure in '%s': %s", tid, exc)
        return chains


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _why_it_matters(
    chain: ThemeChain,
    node: Optional[ThemeNode],
    exp: TickerExposure,
) -> str:
    node_label = node.label if node else exp.node_id
    bottleneck_labels = [n.label for n in chain.bottleneck_nodes]
    if node and node.is_bottleneck:
        return (
            f"The company operates directly within '{node_label}', "
            f"a constrained layer of the {chain.theme_label} supply chain."
        )
    if bottleneck_labels:
        return (
            f"The company has {exp.exposure_type} exposure to '{node_label}' "
            f"in the {chain.theme_label} value chain, which feeds the "
            f"'{bottleneck_labels[0]}' bottleneck."
        )
    return (
        f"The company has {exp.exposure_type} exposure to the "
        f"{chain.theme_label} theme via '{node_label}'."
    )


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def load_taxonomy(
    config: Optional[Dict[str, Any]] = None,
    force_reload: bool = False,
) -> List[ThemeChain]:
    """Load the theme taxonomy with default settings. Cached after first call."""
    cfg: Dict[str, Any] = dict(config or {})
    if force_reload:
        te_cfg = dict(cfg.get("theme_engine") or {})
        te_cfg["force_reload"] = True
        cfg["theme_engine"] = te_cfg
    return ThemeTaxonomyLoader(config=cfg).load()

import json
import asyncio
import copy
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from verumtrade.default_config import DEFAULT_CONFIG
from verumtrade.graph.stock_discovery import StockDiscoveryGraph
from verumtrade.graph.provider_settings import serialize_provider_settings
from verumtrade.agents.discovery.theme_engine import ThemeScanner
from api.schemas import DiscoveryRequest

router = APIRouter()
logger = logging.getLogger(__name__)

_TRACK_LABELS = {
    "enricher": "Enricher",
    "anomaly_scan": "Anomaly Scan",
    "dual_track": "Dual-Track",
}


# ---------------------------------------------------------------------------
# REST endpoint — standalone theme scan
# ---------------------------------------------------------------------------

@router.get("/discovery/themes")
async def get_themes(
    date: str = Query(default=None, description="Analysis date YYYY-MM-DD"),
    scan_mode: str = Query(default="seed_only", description="seed_only | with_evidence"),
):
    """
    Run ThemeScanner standalone and return all ThemeExposureCandidate records.
    seed_only mode is instant (no network); with_evidence fetches live headlines.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    config = {"theme_engine": {"scan_mode": scan_mode}}
    scanner = ThemeScanner(config=config)
    candidates = scanner.scan(date)
    return {
        "candidates": [c.to_dict() for c in candidates],
        "count": len(candidates),
        "date": date,
        "scan_mode": scan_mode,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_config(req: DiscoveryRequest) -> dict:
    """Return a deep-copied DEFAULT_CONFIG merged with request settings."""
    config = copy.deepcopy(DEFAULT_CONFIG)

    config["quick_think_llm"] = req.shallow_thinker
    config["deep_think_llm"] = req.deep_thinker
    config["llm_provider"] = req.llm_provider.lower()
    config["backend_url"] = req.backend_url or ""
    config["provider_settings"] = serialize_provider_settings(req.provider_settings)
    if req.azure_foundry_enable_thinking is not None:
        config["azure_foundry_enable_thinking"] = req.azure_foundry_enable_thinking
    if req.azure_foundry_reasoning_effort is not None:
        config["azure_foundry_reasoning_effort"] = req.azure_foundry_reasoning_effort

    config.setdefault("theme_engine", {})
    config["theme_engine"]["scan_mode"] = req.scan_mode

    config.setdefault("discovery", {})
    config["discovery"]["policy_mode"] = req.policy_mode
    config["discovery"].setdefault("business_inflection", {})
    config["discovery"]["business_inflection"]["enabled"] = req.business_inflection_enabled

    config.setdefault("numeric_filter", {})
    config["numeric_filter"].setdefault("catalyst_prefilter", {})
    config["numeric_filter"]["catalyst_prefilter"]["mode"] = req.discovery_catalyst_mode

    return config


def _discovery_failure_events(result) -> list[dict]:
    error = str(getattr(result, "error", "") or "Unknown discovery pipeline error.")
    return [
        {
            "event": "system",
            "content": f"Pipeline error: {error}",
        },
        {
            "event": "error",
            "content": f"Discovery aborted: {error}",
        },
    ]


# ---------------------------------------------------------------------------
# WebSocket endpoint — full discovery pipeline with streaming
# ---------------------------------------------------------------------------

@router.websocket("/ws/discovery")
async def discovery_ws(websocket: WebSocket):
    """
    Real-time stock discovery stream.

    Client sends a JSON payload matching DiscoveryRequest immediately after
    connection; the server streams a sequence of typed events:

        {"event": "system",           "content": "..."}
        {"event": "stage",            "stage": -1|0|1|2|3,
                                      "label": "...", "status": "started|completed"}
        {"event": "theme_candidates", "candidates": [...], "count": N}
        {"event": "business_inflection", "payload": {...}, "count": N}
        {"event": "attention_gap",    "payload": {...}, "count": N}
        {"event": "evidence_packs",   "payload": {...}, "count": N}
        {"event": "two_layer_scoring","payload": {...}, "count": N}
        {"event": "thesis_cards",     "payload": {...}, "count": N}
        {"event": "report",           "key": "discovery_report", "content": "..."}
        {"event": "completed",        "tickers": [...],
                                      "candidate_count": N, "success": bool}
        {"event": "error",            "content": "..."}
    """
    await websocket.accept()

    candidates_json: list = []

    try:
        raw = await websocket.receive_text()
        req = DiscoveryRequest(**json.loads(raw))

        track_label = _TRACK_LABELS.get(req.discovery_track, req.discovery_track)
        await websocket.send_json({
            "event": "system",
            "content": f"Starting {track_label} discovery for {req.analysis_date}…",
        })

        config = _build_config(req)

        # ── Stage -1: Theme Engine ──────────────────────────────────────────
        # ThemeScanner is fast (seed_only = pure YAML, no I/O), so we run it
        # inline before handing off to the blocking executor.  This lets theme
        # signals reach the UI while the heavier pipeline is still initialising.
        await websocket.send_json({
            "event": "stage", "stage": -1,
            "label": "Theme Engine", "status": "started",
        })

        if req.mock:
            # Mock mode: return a small synthetic response
            await websocket.send_json({"event": "stage", "stage": -1, "label": "Theme Engine", "status": "completed"})
            await websocket.send_json({"event": "theme_candidates", "candidates": [], "count": 0})
            await asyncio.sleep(0.4)
            await websocket.send_json({"event": "stage", "stage": 0, "label": "Universe Screening", "status": "started"})
            await asyncio.sleep(0.4)
            await websocket.send_json({"event": "stage", "stage": 0, "label": "Universe Screening", "status": "completed"})
            await websocket.send_json({"event": "stage", "stage": 1, "label": "Enrichment & Scoring", "status": "completed"})
            await websocket.send_json({"event": "stage", "stage": 2, "label": "Business Inflection", "status": "started"})
            await asyncio.sleep(0.2)
            mock_inflection = {
                "enabled": req.business_inflection_enabled,
                "signals": [
                    {
                        "ticker": "NVDA",
                        "inflection_type": "margin_expansion",
                        "confidence": 0.82,
                        "metrics": ["gross_margin", "revenue_growth"],
                        "evidence": ["Mock margin expansion and revenue acceleration signal."],
                    }
                ] if req.business_inflection_enabled else [],
            }
            await websocket.send_json({"event": "business_inflection", "payload": mock_inflection, "count": len(mock_inflection["signals"])})
            await websocket.send_json({"event": "stage", "stage": 2, "label": "Business Inflection", "status": "completed"})
            await websocket.send_json({"event": "stage", "stage": 3, "label": "Attention Gap", "status": "started"})
            await asyncio.sleep(0.2)
            mock_attention = {
                "signals": [
                    {
                        "ticker": "NVDA",
                        "theme": "AI Infrastructure",
                        "attention_gap_score": 0.77,
                        "inflection_score": 0.82,
                        "theme_score": 0.74,
                        "accumulation_score": 0.61,
                        "under_attention_score": 0.72,
                    }
                ] if req.business_inflection_enabled else [],
            }
            await websocket.send_json({"event": "attention_gap", "payload": mock_attention, "count": len(mock_attention["signals"])})
            await websocket.send_json({"event": "stage", "stage": 3, "label": "Attention Gap", "status": "completed"})
            mock_packs = {
                "packs": [
                    {
                        "ticker": "NVDA",
                        "evidence_score": 64.0,
                        "theme_score": 78.0,
                        "business_inflection_score": 82.0,
                        "attention_gap_score": 77.0,
                        "momentum_confirmation_score": 68.0,
                        "risk_penalty": 7.0,
                        "primary_theme": "AI Infrastructure",
                        "primary_bottleneck": "accelerated compute",
                        "evidence_bullets": ["Mock theme and inflection evidence."],
                    }
                ] if req.business_inflection_enabled else [],
            }
            mock_two_layer = {
                "candidates": [
                    {
                        "ticker": "NVDA",
                        "discovery_score": 73.0,
                        "evidence_score": 64.0,
                        "thesis_score": 80.0,
                        "momentum_confirmation_score": 68.0,
                        "attention_gap_score": 77.0,
                        "tier": "actionable",
                        "action": "actionable",
                        "tier_reasons": ["Strong thesis quality", "Momentum confirmation present"],
                    }
                ] if req.business_inflection_enabled else [],
            }
            mock_cards = {
                "cards": [
                    {
                        "ticker": "NVDA",
                        "status": "actionable",
                        "bull_thesis": "NVDA has direct exposure to AI infrastructure demand.",
                        "evidence": ["Mock theme and inflection evidence."],
                        "risks": ["Valuation and crowding risk."],
                        "kill_conditions": ["Revenue or margin inflection reverses."],
                        "confidence": 0.73,
                    }
                ] if req.business_inflection_enabled else [],
            }
            await websocket.send_json({"event": "evidence_packs", "payload": mock_packs, "count": len(mock_packs["packs"])})
            await websocket.send_json({"event": "two_layer_scoring", "payload": mock_two_layer, "count": len(mock_two_layer["candidates"])})
            await websocket.send_json({"event": "thesis_cards", "payload": mock_cards, "count": len(mock_cards["cards"])})
            await websocket.send_json({"event": "report", "key": "discovery_report", "content": "# Mock Discovery\nThis is a mock run."})
            await websocket.send_json({"event": "completed", "tickers": ["AAPL", "NVDA"], "candidate_count": 0, "success": True})
            return

        try:
            theme_scanner = ThemeScanner(config=config)
            theme_candidates = theme_scanner.scan(req.analysis_date)
            candidates_json = [c.to_dict() for c in theme_candidates]
            theme_count = len(candidates_json)
            unique_themes = len({c["theme"] for c in candidates_json})

            await websocket.send_json({
                "event": "stage", "stage": -1,
                "label": "Theme Engine", "status": "completed",
            })
            await websocket.send_json({
                "event": "theme_candidates",
                "candidates": candidates_json,
                "count": theme_count,
            })
            await websocket.send_json({
                "event": "system",
                "content": (
                    f"Theme engine: {theme_count} signal(s) across "
                    f"{unique_themes} theme(s) detected."
                ),
            })
        except Exception as te:
            logger.warning(f"Theme scan failed (non-fatal): {te}")
            await websocket.send_json({
                "event": "stage", "stage": -1,
                "label": "Theme Engine", "status": "completed",
            })

        # ── Stage 0–3: Full discovery pipeline ─────────────────────────────
        await websocket.send_json({
            "event": "stage", "stage": 0,
            "label": "Universe Screening", "status": "started",
        })
        await websocket.send_json({
            "event": "system",
            "content": "Running universe screener and multi-factor pipeline…",
        })

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: StockDiscoveryGraph(config=config).run_discovery(
                trade_date=req.analysis_date,
                exclude_tickers=[],
                discovery_track=req.discovery_track,
            ),
        )

        if not result.success:
            for event in _discovery_failure_events(result):
                await websocket.send_json(event)
            return

        await websocket.send_json({
            "event": "stage", "stage": 0,
            "label": "Universe Screening", "status": "completed",
        })
        await websocket.send_json({
            "event": "stage", "stage": 1,
            "label": "Enrichment & Scoring", "status": "completed",
        })

        # If the pipeline's own theme scan produced a richer list (e.g. after
        # LLM re-scoring), prefer it over the pre-scan we already streamed.
        pipeline_theme_cands = (result.metadata or {}).get("theme_candidates", [])
        if pipeline_theme_cands:
            await websocket.send_json({
                "event": "theme_candidates",
                "candidates": pipeline_theme_cands,
                "count": len(pipeline_theme_cands),
            })
            final_count = len(pipeline_theme_cands)
        else:
            final_count = len(candidates_json)

        metadata = result.metadata or {}
        inflection_payload = metadata.get("business_inflection") or {"enabled": req.business_inflection_enabled, "signals": []}
        attention_payload = metadata.get("attention_gap") or {"signals": []}
        evidence_payload = metadata.get("evidence_packs") or {"packs": []}
        two_layer_payload = metadata.get("two_layer_scoring") or {"candidates": []}
        thesis_payload = metadata.get("thesis_cards") or {"cards": []}

        await websocket.send_json({
            "event": "stage", "stage": 2,
            "label": "Business Inflection", "status": "started",
        })
        await websocket.send_json({
            "event": "business_inflection",
            "payload": inflection_payload,
            "count": len(inflection_payload.get("signals", [])),
        })
        await websocket.send_json({
            "event": "stage", "stage": 2,
            "label": "Business Inflection", "status": "completed",
        })
        await websocket.send_json({
            "event": "stage", "stage": 3,
            "label": "Attention Gap", "status": "started",
        })
        await websocket.send_json({
            "event": "attention_gap",
            "payload": attention_payload,
            "count": len(attention_payload.get("signals", [])),
        })
        await websocket.send_json({
            "event": "stage", "stage": 3,
            "label": "Attention Gap", "status": "completed",
        })
        await websocket.send_json({
            "event": "evidence_packs",
            "payload": evidence_payload,
            "count": len(evidence_payload.get("packs", [])),
        })
        await websocket.send_json({
            "event": "two_layer_scoring",
            "payload": two_layer_payload,
            "count": len(two_layer_payload.get("candidates", [])),
        })
        await websocket.send_json({
            "event": "thesis_cards",
            "payload": thesis_payload,
            "count": len(thesis_payload.get("cards", [])),
        })

        if result.report:
            await websocket.send_json({
                "event": "report",
                "key": "discovery_report",
                "content": result.report,
            })

        await websocket.send_json({
            "event": "completed",
            "tickers": result.tickers or [],
            "candidate_count": final_count,
            "success": result.success,
        })

    except WebSocketDisconnect:
        logger.info("Discovery WebSocket client disconnected")
    except Exception as exc:
        logger.error(f"Discovery WebSocket error: {exc}", exc_info=True)
        try:
            await websocket.send_json({"event": "error", "content": str(exc)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

import json
import asyncio
import copy
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from opentrace.default_config import DEFAULT_CONFIG
from opentrace.graph.stock_discovery import StockDiscoveryGraph
from opentrace.graph.provider_settings import serialize_provider_settings
from opentrace.agents.discovery.theme_engine import ThemeScanner
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

    config.setdefault("numeric_filter", {})
    config["numeric_filter"].setdefault("catalyst_prefilter", {})
    config["numeric_filter"]["catalyst_prefilter"]["mode"] = req.discovery_catalyst_mode

    return config


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
        {"event": "stage",            "stage": -1|0|1|2,
                                      "label": "...", "status": "started|completed"}
        {"event": "theme_candidates", "candidates": [...], "count": N}
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

        # ── Stage 0–2: Full discovery pipeline ─────────────────────────────
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

        if not result.success and result.error:
            await websocket.send_json({
                "event": "system",
                "content": f"Pipeline warning: {result.error}",
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

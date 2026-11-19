import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from api.schemas import AnalysisRequest, AnalysisResponse
from api.utils import build_analysis_reports_payload, stream_analysis_ws, run_analysis_sync
from api.replay import (
    DEFAULT_REPLAY_ID,
    demo_mode_enabled,
    list_replays,
    load_replay,
    replay_final_reports,
    stream_replay,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _requested_replay_id(req: AnalysisRequest) -> str | None:
    """Replay id to serve for this request, or None for a live run."""
    if req.replay_id:
        return req.replay_id
    return DEFAULT_REPLAY_ID if demo_mode_enabled() else None


@router.get("/replays")
async def get_replays():
    """List recorded runs that can be played back without API keys."""
    return {"demo_mode": demo_mode_enabled(), "replays": list_replays()}


@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_sync(req: AnalysisRequest):
    """
    Synchronous endpoint for single ticker analysis. 
    Waits for the entire graph to complete and returns the final report.
    """
    replay_id = _requested_replay_id(req)
    if replay_id:
        try:
            replay = load_replay(replay_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        reports = replay_final_reports(replay)
        return AnalysisResponse(
            status="completed",
            ticker=replay.get("ticker", req.ticker),
            date=replay.get("analysis_date", req.analysis_date),
            decision=reports.get("final_trade_decision"),
            reports=reports,
        )
    try:
        final_state = await run_analysis_sync(req)
        if not final_state:
            raise HTTPException(status_code=500, detail="Analysis failed to produce a final state.")
            
        reports = build_analysis_reports_payload(final_state)
        
        return AnalysisResponse(
            status="completed",
            ticker=req.ticker,
            date=req.analysis_date,
            decision=final_state.get("final_trade_decision"),
            reports=reports
        )
    except Exception as e:
        logger.error(f"Error during synchronous analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/analyze")
async def analyze_ws(websocket: WebSocket):
    """
    WebSocket endpoint for real-time streamed single ticker analysis.
    The client should connect, then send a JSON payload matching AnalysisRequest.
    The server will stream updates until completion.
    """
    await websocket.accept()
    
    try:
        # Wait for the initial configuration payload
        data = await websocket.receive_text()
        req_data = json.loads(data)
        req = AnalysisRequest(**req_data)

        replay_id = _requested_replay_id(req)
        if replay_id:
            replay = load_replay(replay_id)
            await stream_replay(replay, websocket)
            await websocket.send_json({"event": "completed", "ticker": replay.get("ticker", req.ticker)})
            return

        await websocket.send_json({"event": "system", "content": f"Configuration received. Starting analysis for {req.ticker}..."})
        
        # Run the streamed analysis
        final_state = await stream_analysis_ws(req, websocket)
        
        # Send a completion event
        await websocket.send_json({"event": "completed", "ticker": req.ticker})
        
    except WebSocketDisconnect:
        logger.info("Client disconnected from WebSocket")
    except Exception as e:
        logger.error(f"Error during websocket analysis: {str(e)}")
        await websocket.send_json({"event": "error", "content": str(e)})
    finally:
        try:
            await websocket.close()
        except Exception:
            pass

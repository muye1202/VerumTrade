import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from api.schemas import AnalysisRequest, AnalysisResponse
from api.utils import stream_analysis_ws, run_analysis_sync

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/analyze", response_model=AnalysisResponse)
async def analyze_sync(req: AnalysisRequest):
    """
    Synchronous endpoint for single ticker analysis. 
    Waits for the entire graph to complete and returns the final report.
    """
    try:
        final_state = await run_analysis_sync(req)
        if not final_state:
            raise HTTPException(status_code=500, detail="Analysis failed to produce a final state.")
            
        reports = {
            "market_report": final_state.get("market_report"),
            "sentiment_report": final_state.get("sentiment_report"),
            "news_report": final_state.get("news_report"),
            "fundamentals_report": final_state.get("fundamentals_report"),
            "investment_debate_state": final_state.get("investment_debate_state"),
            "trader_investment_plan": final_state.get("trader_investment_plan"),
            "risk_debate_state": final_state.get("risk_debate_state"),
            "final_trade_decision": final_state.get("final_trade_decision"),
        }
        
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

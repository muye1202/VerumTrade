from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from pydantic import BaseModel

from api.database import get_db
from api.models import AnalysisSession

router = APIRouter()

class HistoryListItem(BaseModel):
    id: int
    ticker: str
    analysis_date: str
    time_horizon: str
    created_at: str

@router.get("/history", response_model=List[HistoryListItem])
def get_history(db: Session = Depends(get_db)):
    """Retrieve all past analysis sessions (lightweight)."""
    sessions = db.query(AnalysisSession).order_by(AnalysisSession.created_at.desc()).all()
    
    return [
        HistoryListItem(
            id=s.id,
            ticker=s.ticker,
            analysis_date=s.analysis_date,
            time_horizon=s.time_horizon,
            created_at=s.created_at.isoformat()
        )
        for s in sessions
    ]

@router.get("/history/{session_id}")
def get_history_detail(session_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Retrieve the full logs and reports for a specific session."""
    session = db.query(AnalysisSession).filter(AnalysisSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    return {
        "id": session.id,
        "ticker": session.ticker,
        "analysis_date": session.analysis_date,
        "time_horizon": session.time_horizon,
        "logs": session.logs,
        "reports": session.reports,
        "created_at": session.created_at.isoformat()
    }

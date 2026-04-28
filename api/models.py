from sqlalchemy import Column, Integer, String, DateTime, JSON
from datetime import datetime
from api.database import Base

class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)
    analysis_date = Column(String)
    time_horizon = Column(String)
    
    # Store the entire run's output so it can be replayed/viewed on the frontend
    logs = Column(JSON, default=list)
    reports = Column(JSON, default=dict)
    
    created_at = Column(DateTime, default=datetime.utcnow)

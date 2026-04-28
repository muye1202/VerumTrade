from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import analysis, history
from api.database import engine, Base
import api.models

# Create DB tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="TradingAgents API",
    description="FastAPI backend for TradingAgents multi-agent framework",
    version="1.0.0",
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router, prefix="/api", tags=["analysis"])
app.include_router(history.router, prefix="/api", tags=["history"])

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "TradingAgents API is running"}

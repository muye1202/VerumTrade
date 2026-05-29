from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import analysis, history, discovery
from api.database import engine, Base, run_schema_migrations
import api.models

# Create DB tables (new installs) then apply incremental column migrations (upgrades)
Base.metadata.create_all(bind=engine)
run_schema_migrations()

app = FastAPI(
    title="OpenTrace API",
    description="FastAPI backend for OpenTrace agentic market research workflows",
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
app.include_router(discovery.router, prefix="/api", tags=["discovery"])

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "OpenTrace API is running"}

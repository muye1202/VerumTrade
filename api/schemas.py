from pydantic import BaseModel, Field
from typing import List, Optional

class ExecutionSettings(BaseModel):
    enabled: bool = False
    provider: str = "alpaca"
    paper: bool = True
    position_size_pct: float = 0.10

class AnalysisRequest(BaseModel):
    ticker: str = Field(..., description="The stock ticker symbol (e.g. AAPL)")
    analysis_date: str = Field(..., description="Date of analysis in YYYY-MM-DD format")
    analysts: List[str] = Field(
        default=["market", "social", "news", "fundamentals"],
        description="List of analysts to include"
    )
    research_depth: int = Field(default=1, description="Depth of research (debate rounds)")
    llm_provider: str = Field(default="openai", description="LLM provider (e.g. openai, anthropic)")
    backend_url: Optional[str] = Field(default=None, description="Backend URL for local/custom LLMs")
    shallow_thinker: str = Field(default="gpt-4o-mini", description="Model used for shallow tasks")
    deep_thinker: str = Field(default="gpt-4o", description="Model used for deep reasoning")
    time_horizon: str = Field(default="1-2 weeks", description="Trading time horizon")
    skip_completed_analysts: bool = Field(default=False, description="Whether to skip already completed analysts")
    execution: Optional[ExecutionSettings] = None
    mock: bool = Field(default=False, description="If true, bypass LLM and return mock stream")

class AnalysisResponse(BaseModel):
    status: str
    ticker: str
    date: str
    decision: Optional[str] = None
    reports: dict = {}
    error: Optional[str] = None

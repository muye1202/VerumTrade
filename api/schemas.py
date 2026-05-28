from pydantic import BaseModel, Field
from typing import Dict, List, Optional

class ExecutionSettings(BaseModel):
    enabled: bool = False
    provider: str = "alpaca"
    paper: bool = True
    position_size_pct: float = 0.10

class ProviderSettings(BaseModel):
    api_key: Optional[str] = Field(default=None, description="Request-scoped API key")
    base_url: Optional[str] = Field(default=None, description="Request-scoped provider base URL")

class AnalysisRequest(BaseModel):
    ticker: str = Field(..., description="The stock ticker symbol (e.g. AAPL)")
    analysis_date: str = Field(..., description="Date of analysis in YYYY-MM-DD format")
    analysts: List[str] = Field(
        default=["catalyst", "market", "social", "news", "fundamentals"],
        description="List of analysts to include"
    )
    research_depth: int = Field(default=1, description="Depth of research (debate rounds)")
    llm_provider: str = Field(default="openai", description="LLM provider (e.g. openai, anthropic)")
    backend_url: Optional[str] = Field(default=None, description="Backend URL for local/custom LLMs")
    provider_settings: Optional[Dict[str, ProviderSettings]] = Field(
        default=None,
        description="Optional request-scoped provider settings keyed by provider id",
    )
    shallow_thinker: str = Field(default="gpt-4o-mini", description="Model used for shallow tasks")
    deep_thinker: str = Field(default="gpt-4o", description="Model used for deep reasoning")
    time_horizon: str = Field(default="1-2 weeks", description="Trading time horizon")
    skip_completed_analysts: bool = Field(default=False, description="Whether to skip already completed analysts")
    execution: Optional[ExecutionSettings] = None
    mock: bool = Field(default=False, description="If true, bypass LLM and return mock stream")
    qwen_enable_thinking: Optional[bool] = Field(default=None, description="Whether Qwen thinking mode is enabled")
    qwen_thinking_budget: Optional[int] = Field(default=None, description="Qwen thinking budget")

class DiscoveryRequest(BaseModel):
    analysis_date: str = Field(..., description="Analysis date in YYYY-MM-DD format")
    discovery_track: str = Field(default="enricher", description="enricher | anomaly_scan | dual_track")
    discovery_catalyst_mode: str = Field(default="daily_calendar", description="daily_calendar | per_ticker_calendar")
    scan_mode: str = Field(default="seed_only", description="seed_only | with_evidence")
    policy_mode: str = Field(default="off", description="off | adaptive (LLM re-scoring)")
    llm_provider: str = Field(default="openai", description="LLM provider")
    backend_url: Optional[str] = Field(default=None, description="Backend URL for local/custom LLMs")
    provider_settings: Optional[Dict[str, ProviderSettings]] = Field(
        default=None,
        description="Optional request-scoped provider settings keyed by provider id",
    )
    shallow_thinker: str = Field(default="gpt-4o-mini", description="Model for shallow tasks")
    deep_thinker: str = Field(default="gpt-4o", description="Model for deep reasoning")
    mock: bool = Field(default=False, description="If true, return mock stream without running pipeline")


class AnalysisResponse(BaseModel):
    status: str
    ticker: str
    date: str
    decision: Optional[str] = None
    reports: dict = {}
    error: Optional[str] = None

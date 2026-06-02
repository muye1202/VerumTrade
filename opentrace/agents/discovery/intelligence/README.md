# Discovery Intelligence Pipeline

This folder (`discovery/intelligence/`) contains the core orchestration and implementation of the multi-stage stock discovery pipeline. The pipeline acts as a quantitative and qualitative funnel. It begins by interpreting broad market conditions to apply top-down risk policies, subsequently filters and enriches thousands of tickers, and finally scores them to yield a concise list of high-conviction trade candidates.

## General Workflow

### Pre-Stage 0: Market Context & Policy Generation
Before individual stocks are evaluated, the pipeline builds an understanding of the prevailing market environment.
*   **Intelligence Snapshot (`market_context_snapshot.py`)**: Fetches macro-level indicators, volatility indices (e.g., VIX), broader market trend data (e.g., SPY behavior), and sector performance to establish a "market regime" snapshot.
*   **LLM Policy Engine (`market_policy_llm.py`)**: An LLM ingests the intelligence snapshot and dynamically generates an operational policy. This includes defining risk posture, adjusting sector weights (e.g., overweighting defensive sectors in bearish regimes), and tuning pipeline thresholds.
*   **Universe Pre-Filtering (`universe_prefilters.py`)**: Applies simple quantitative filters based on the policy (e.g., minimum average daily dollar volume) to prune the initial universe of thousands of tickers down to a manageable list.

### Stage 1: Parallel Evaluation Tracks
The orchestrator (`pipeline_orchestrator.py`) passes the pre-filtered universe into Stage 1, which supports parallel strategies (tracks) to discover promising tickers. 

*   **Track A - Deep Enrichment (`track_a_enrichment.py`)**: The primary track focusing on rich, multi-dimensional analysis. It aggregates technical indicators (including multi-timeframe momentum alignment, accumulation/distribution ratios, and breakout persistence), fundamental data (including beat magnitude trends), analyst consensus (EPS/revenue estimate revision momentum via nightly SQLite snapshots), sentiment metrics (insider trading, news), and institutional positioning (short interest, options activity).
*   **Track B - Anomaly Scans (`track_b_anomaly_scans.py`)**: A purely technical scanner designed to flag specific quantitative phenomena based on the calculated momentum metrics (`technical_momentum_metrics.py`). Examples include short-term momentum acceleration, stealth accumulation, relative strength divergence, and volatility breakouts.

*Note: The orchestrator can run `enricher` (Track A), `anomaly_scan` (Track B), or merge them via a `dual_track` configuration.*

### Stage 2: Selection & Scoring
Candidates that survive Stage 1 are passed directly to the scoring engine.
*   **Scoring & Filtration (`candidate_scoring.py`)**: The `Stage2Scorer` computes a final composite score for each ticker based on 8 weighted factors (Estimate Revision, Breakout Persistence, Accumulation/Distribution, Earnings Surprise, Technical Momentum, Options Flow, Sector Momentum, and Short Squeeze). It enforces strict structural exclusions (e.g., "Must be above 50 SMA") as defined by the LLM policy, applies quality penalties for missing data, and penalizes whipsaw noise. The highest-ranked tickers are returned by the pipeline for deep-dive thesis generation.

## Shared Components
*   **`pipeline_orchestrator.py`**: The `IntelligenceScanner` acts as the conductor orchestrating Pre-Stage 0, Stage 1 (Tracks A & B), and Stage 2 into a single unified flow.
*   **`pipeline_models.py` / `feature_matrix.py`**: Defining the system's data contract (`FeatureMatrix`, `IntelligenceResult`, etc.).
*   **`pipeline_cache.py` / `pipeline_utils.py`**: Caching and general technical utilities that minimize repetitive API calls across continuous pipeline runs.

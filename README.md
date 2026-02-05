<div align="center">
  <img src="assets/bull_line_art.svg" alt="Neural Bull — AIStockTrader" width="200">

  <!-- <h1>AI Stock Trader</h1> -->
  <p><em>A multi-agent AI system where teams of specialised LLM agents research, debate, and decide on stock trades — end to end.</em></p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
    <img src="https://img.shields.io/badge/Python-≥3.10-green.svg" alt="Python">
    <img src="https://img.shields.io/badge/Framework-LangGraph-7c3aed.svg" alt="LangGraph">
    <img src="https://img.shields.io/badge/Trading-Alpaca-f5a623.svg" alt="Alpaca">
  </p>
</div>

---

| | |
|:--|:--|
| [🚀 Getting Started](#-getting-started) | Install, configure API keys, and run your first analysis |
| [📖 User Guide](#-user-guide) | Everything you need to use TradingAgents day-to-day |
| [🏗️ Architecture](#-architecture) | How the agents, data, and execution layers fit together |

---

## 🚀 Getting Started

### 📦 Install

> [!NOTE]
> Requires **Python 3.10 or newer**. An API key for at least one LLM provider is the only hard requirement to begin.

```bash
# Clone the repo
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents

# Option A — pip (editable install)
pip install -e .

# Option B — uv (faster)
uv sync
```

### 🔑 Set up your API keys

Create a `.env` file in the repo root (it is git-ignored). Add whichever keys you need:

```env
# ─── LLM Providers (pick at least one) ───
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=anthropic-...
GOOGLE_API_KEY=AIza...
DEEPSEEK_API_KEY=sk-...
DASHSCOPE_API_KEY=sk-...          # Qwen / DashScope
ZHIPUAI_API_KEY=...               # GLM / ZhipuAI

# ─── Market Data (optional — extends available data sources) ───
ALPHA_VANTAGE_API_KEY=...

# ─── Alpaca Brokerage (optional — required for paper / live trading) ───
APCA_API_KEY_ID=...
APCA_API_SECRET_KEY=...
APCA_API_BASE_URL=https://paper-api.alpaca.markets/v2
```

> [!TIP]
> Yahoo Finance works out of the box with **no key at all**, so you can start analysing stocks right away even without a data-vendor key.

---

## 📖 User Guide

### 💻 Running a single-stock analysis (CLI)

The interactive CLI walks you through every choice step by step — this is the recommended way to start:

```bash
python -m cli.main analyze
```

You'll be prompted to pick:

| Prompt | What it does |
|:--|:--|
| **Ticker** | The stock symbol to analyse (e.g. `NVDA`) |
| **Date** | The analysis date in `YYYY-MM-DD` format |
| **Analysts** | Which specialist agents to include — Market, Social, News, Fundamentals. Pick any combination |
| **Research depth** | How many debate rounds the agents run: **Shallow** (fast) · **Medium** · **Deep** (most thorough) |
| **LLM Provider** | Which AI backend to use: Google, Qwen, DeepSeek, GLM, and more |
| **Models** | One quick-thinking model (used by most agents) and one deep-thinking model (used by the judges) |
| **Execution** | Analysis only, or also place a paper trade via Alpaca |

A live terminal dashboard streams agent progress, tool calls, and the growing report in real time. All outputs are saved automatically to `results/{ticker}/{date}/` when the run finishes.

### 📊 Running a portfolio analysis

```bash
python -m cli.main analyze-portfolio
```

This mode pulls your current Alpaca positions, runs a **triage step** to decide which stocks most need attention right now, then performs full multi-agent analysis on the ones that matter. Stocks that are skipped get a lightweight "HOLD" entry. You can optionally let the system execute trades on your behalf (paper trading by default).

### 🐍 Running from Python (programmatic)

For scripting or integration, skip the CLI entirely:

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"]      = "openai"
config["deep_think_llm"]    = "gpt-4o-mini"
config["quick_think_llm"]   = "gpt-4o-mini"

ta = TradingAgentsGraph(config=config)

# Returns the full state and a structured trade decision
state, decision = ta.propagate("NVDA", "2024-05-10")
print(decision)
```

### ⚙️ Configuration cheat-sheet

All defaults live in `tradingagents/default_config.py`. Here are the knobs people change most often:

| Key | What it controls | Example values |
|:--|:--|:--|
| `llm_provider` | Which LLM backend to use | `openai` · `anthropic` · `google` · `deepseek` · `qwen3-cn` · `glm` |
| `deep_think_llm` / `quick_think_llm` | Model names for judges vs. analysts | `"o4-mini"` · `"gpt-4o-mini"` |
| `max_debate_rounds` | How many Bull ↔ Bear rounds | `1` (fast) … `5` (thorough) |
| `data_vendors` | Where market data comes from | See table below |
| `alpaca_execution.enabled` | Turn trading on / off | `true` / `false` |
| `alpaca_execution.paper_trading` | Paper vs. live | `true` (safe default) |

**Data vendor options** — configured per category:

| Category | Available sources |
|:--|:--|
| `core_stock_apis` | `alpaca` · `yfinance` · `alpha_vantage` · `local` |
| `technical_indicators` | `alpaca` · `yfinance` · `alpha_vantage` · `local` |
| `fundamental_data` | `alpha_vantage` · `openai` · `local` |
| `news_data` | `alpha_vantage` · `openai` · `google` · `local` |

> [!TIP]
> If a vendor is unavailable at runtime the system automatically falls back to the next option in the list — nothing crashes.

### 📈 Supported order types

When execution is enabled, the agents can recommend any of these:

| Order type | Description |
|:--|:--|
| `MARKET` | Execute immediately at the current market price |
| `LIMIT` | Execute only at a specified price or better |
| `STOP` | Triggers a market order once the stock hits a stop price |
| `STOP_LIMIT` | Triggers a limit order once the stock hits a stop price |
| `TRAILING_STOP` | Stop that moves with the stock price, locking in gains |

---

## 🏗️ Architecture

TradingAgents is built on **LangGraph** — each agent is a node in a directed workflow graph, and the edges between them define the order of operations. Here is the full pipeline:

```
┌─────────────────────────────────────────────────────────────────────┐
│                       📊 Market Data Layer                          │
│   Alpaca  ·  Yahoo Finance  ·  Alpha Vantage  ·  Google  ·  Local   │
│                  (automatic fallback between sources)               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  price · news · fundamentals · sentiment
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    🔍 1. Analyst Team                                │
│                                                                     │
│   ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌───────────────┐  │
│   │  Market    │→ │  Social    │→ │  News    │→ │ Fundamentals  │  │
│   │  Analyst   │  │  Analyst   │  │  Analyst │  │   Analyst     │  │
│   └────────────┘  └────────────┘  └──────────┘  └───────────────┘  │
│        Each analyst calls data tools in a loop, then writes a       │
│        focused report.  Uses the quick-thinking LLM.                │
└───────────────────────────────┬─────────────────────────────────────┘
                                │  four analyst reports
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 💬 2. Research Team (Debate)                         │
│                                                                     │
│          ┌──────────────┐  ◄──►  ┌──────────────┐                   │
│          │ Bull         │        │ Bear         │                   │
│          │ Researcher   │        │ Researcher   │                   │
│          └──────┬───────┘        └──────┬───────┘                   │
│                 │                       │                            │
│                 ▼───────────────────────▼                            │
│          ┌────────────────────────────────┐                         │
│          │      Research Manager          │  ← deep-thinking LLM   │
│          │  Judges the debate, writes     │                         │
│          │  the investment plan           │                         │
│          └────────────────┬───────────────┘                         │
└───────────────────────────┼─────────────────────────────────────────┘
                            │  investment plan
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     📝 3. Trading Team                               │
│                                                                     │
│          ┌────────────────────────────────┐                         │
│          │            Trader              │                         │
│          │  Combines all reports into a   │                         │
│          │  BUY / SELL / HOLD decision    │                         │
│          │  with order type & quantity    │                         │
│          └────────────────┬───────────────┘                         │
└───────────────────────────┼─────────────────────────────────────────┘
                            │  proposed trade
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│               ⚖️  4. Risk Management Team (Debate)                  │
│                                                                     │
│     ┌───────────┐   ┌─────────┐   ┌───────────┐                    │
│     │  Risky    │◄──│ Neutral │──►│  Safe     │                    │
│     │  Analyst  │   │ Analyst │   │  Analyst  │                    │
│     └───────────┘   └─────────┘   └───────────┘                    │
│                         │                                           │
│                         ▼                                           │
│          ┌────────────────────────────────┐                         │
│          │        Risk Judge              │  ← deep-thinking LLM   │
│          │  Final decision with position  │                         │
│          │  size, guardrails, and order   │                         │
│          └────────────────┬───────────────┘                         │
└───────────────────────────┼─────────────────────────────────────────┘
                            │  final structured decision
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  💰 5. Execution Layer                               │
│                                                                     │
│          ┌────────────────────────────────┐                         │
│          │       Alpaca Executor          │                         │
│          │  Places paper or live orders   │                         │
│          │  Enforces concentration caps   │                         │
│          │  and position-size guardrails  │                         │
│          └────────────────────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

### 🧠 Two tiers of LLM

Every agent in the system uses one of two model slots:

| Tier | Used by | Why |
|:--|:--|:--|
| **Quick-thinking** | Market / Social / News / Fundamentals Analysts, Bull & Bear Researchers, Trader, Risk Debaters | Speed and cost — these agents run many times and don't need heavy reasoning |
| **Deep-thinking** | Research Manager, Risk Judge, Portfolio Triage Agent | These are the key decision points where accuracy matters most; a stronger model pays off here |

You set both in one place (`deep_think_llm` and `quick_think_llm`) and the system routes them automatically.

### 🗄️ Data layer

All market-data tool calls go through a single routing layer (`dataflows/interface.py`). You pick a preferred vendor per category in your config; if that vendor is unavailable the system silently tries the next one. Supported sources include Alpaca, Yahoo Finance, Alpha Vantage, Google News, and local cached files.

### 💾 Memory and learning

Each agent team has its own **vector-store memory** (backed by ChromaDB). After a trade plays out, you can call `reflect_and_remember()` to record what happened. The next time a similar situation comes up the agents pull those lessons into their reasoning — so the system genuinely learns from experience over time.

### 📁 Portfolio mode extras

When you run portfolio analysis, an additional **Triage Agent** runs first. It scans all your positions and picks the ones that need the most attention right now — based on breaking news, unusual price moves, concentration risk, and more. Only those stocks go through the full multi-agent pipeline; everything else gets a quick "HOLD" recommendation. This keeps costs and run-time in check even with large portfolios.

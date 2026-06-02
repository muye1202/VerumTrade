<div align="center">
  <img src="assets/logo.svg" alt="OpenTrace" width="300">

  <p><em>An open-source multi-agent AI trading framework where each agentic analyst follows a structured reasoning graph, with visible reasoning traces and decision traces from evidence to final trade proposal.</em></p>

  <p>
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
    <img src="https://img.shields.io/badge/Python-â‰¥3.10-green.svg" alt="Python">
    <img src="https://img.shields.io/badge/Node.js-â‰¥18-green.svg" alt="Node.js">
    <img src="https://img.shields.io/badge/Framework-LangGraph-7c3aed.svg" alt="LangGraph">
    <img src="https://img.shields.io/badge/Trading-Alpaca-f5a623.svg" alt="Alpaca">
  </p>

  <img src="demos/SNDK_demo_preview.gif" alt="OpenTrace SNDK demo preview" width="100%">
</div>

---

## ðŸ”­ What is OpenTrace?

OpenTrace turns a single ticker (or your whole portfolio) into a **transparent, auditable trade
recommendation** produced by a team of specialised LLM agents. Instead of a black-box "buy/sell"
signal, every conclusion is backed by a **trace** you can read: which evidence each analyst found,
how the bull and bear sides argued, what the risk team flagged, and why the final decision was
made.

It is built for two audiences:

- **Researchers** studying multi-agent LLM systems, interpretability, and agentic decision-making.
- **Practitioners** who want a serious, inspectable second opinion before they trade (paper or live).

### âœ¨ Highlights

- ðŸ§© **Structured agent pipeline** â€” specialised analysts â†’ bull/bear research debate â†’ trader plan
  â†’ risk-management review â†’ execution, wired as a [LangGraph](https://github.com/langchain-ai/langgraph) workflow.
- ðŸ”Ž **Visible reasoning & decision traces** â€” the namesake feature: every step from raw evidence to
  the final proposal is captured and rendered in the web UI.
- ðŸ”— **Evidence graph** â€” analyst findings are distilled into a structured fact graph that grounds
  every downstream agent, reducing hand-wavy reasoning.
- ðŸ—žï¸ **Catalyst & event-risk awareness** â€” a dedicated analyst surfaces earnings, FDA, and macro
  catalysts that move prices.
- ðŸ§  **Closed-loop learning** â€” a journal subsystem tracks each thesis to its real outcome and feeds
  the lessons back into agent memory.
- ðŸ›°ï¸ **AI stock discovery** â€” a multi-stage screener finds promising tickers, then runs the full
  pipeline on the best candidates.
- ðŸ”Œ **Bring your own model & data** â€” 8 LLM providers (incl. local Ollama) and 6+ market-data
  vendors with automatic fallback. Start free with Yahoo Finance and one LLM key.
- ðŸ’¸ **Paper or live execution** â€” optional Alpaca integration with position-size and concentration
  guardrails.

---

## ðŸ†• What's new vs. the original TradingAgents

OpenTrace builds on [Tauric Research's TradingAgents](https://github.com/tauricresearch/tradingagents)
(see [Credits](#-credits--acknowledgments)) and extends it substantially:

| Area | Addition in OpenTrace |
|:--|:--|
| **Transparency** | Reasoning & decision **traces** plus a React UI to inspect them ([details](#-reasoning--decision-traces)) |
| **Grounding** | **Evidence Graph** synthesis layer between analysts and researchers |
| **New analyst** | **Catalyst / Event-Risk Analyst** (earnings, FDA, macro catalysts) |
| **Learning** | **Journal** subsystem: thesis state machine, outcome monitoring, reflection â†’ lesson memory |
| **Discovery** | Multi-stage **stock-discovery** pipeline + a macro **Theme Engine** |
| **Decision rigor** | Structured **Decision Schema** + pre-execution **Decision Guard** validation |
| **Execution** | Live/paper **Alpaca** execution with concentration & position-size guardrails and 5 order types |
| **Reach** | 8 LLM providers (OpenAI, Anthropic, Google, DeepSeek, Qwen, GLM, OpenRouter, Ollama) and a multi-vendor data layer with fallback |
| **Engineering** | A **context-budget** manager for token control, plus full FastAPI + React + Typer/Rich apps |

---

| | |
|:--|:--|
| [ðŸš€ Quick Start â€” Web App](#-quick-start--web-app) | Launch the browser-based UI in minutes |
| [ðŸ’» Quick Start â€” CLI](#-quick-start--cli) | Run analyses from the terminal |
| [ðŸ Python API](#-python-api-programmatic) | Use OpenTrace in your own scripts |
| [ðŸ”¬ Reasoning & Decision Traces](#-reasoning--decision-traces) | What makes OpenTrace transparent |
| [ðŸ—ï¸ Architecture](#%EF%B8%8F-architecture) | How the autonomous agent teams collaborate |
| [âš™ï¸ Configuration](#%EF%B8%8F-configuration) | LLM providers, data vendors, and tuning knobs |
| [ðŸ§° Troubleshooting](#-troubleshooting--faq) | Fixes for common first-run issues |

---

## ðŸš€ Quick Start â€” Web App

The web interface is the easiest way to get started. It launches a **React + Vite** frontend and a **FastAPI** backend.

### Prerequisites

| Tool | Version | Check |
|:--|:--|:--|
| Python | â‰¥ 3.10 | `python --version` |
| Node.js | â‰¥ 18 | `node --version` |
| npm | â‰¥ 9 | `npm --version` |

### Step 1 â€” Clone & install

```bash
git clone https://github.com/muye1202/OpenTrace.git
cd OpenTrace

# Option A â€” pip (editable install)
pip install -e .

# Option B â€” uv (faster, if you have uv installed)
uv sync
```

### Step 2 â€” Configure API keys

```bash
# Copy the template
cp .env.example .env        # Linux / macOS
copy .env.example .env       # Windows
```

Open `.env` and paste in at least one LLM provider key (OpenAI, Anthropic, Google, DeepSeek, etc.). That's all you need â€” market data from Yahoo Finance works with no key at all.

> [!TIP]
> See [`.env.example`](.env.example) for every supported key and what it does.

### Step 3 â€” Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### Step 4 â€” Launch

Open **two terminals** from the project root:

**Terminal 1 â€” Backend (FastAPI)**
```bash
uvicorn api.main:app --reload
```

**Terminal 2 â€” Frontend (Vite)**
```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser. The frontend talks to the
backend on `http://localhost:8000`, and completed analyses are persisted so you can revisit them
from the **History** view.

> [!NOTE]
> Alternatively, you can use `python run.py` which starts both the backend and frontend in a single terminal. However, separate terminals give you better visibility into logs.

### ðŸ’¡ What to expect (cost & time)

OpenTrace runs many LLM calls per analysis, so each run takes time and incurs API cost:

| Setting | Rough time | Rough LLM cost* |
|:--|:--|:--|
| **Shallow** depth, small models (e.g. `gpt-4o-mini`, `gemini-2.0-flash`) | ~1â€“3 min | a few cents |
| **Deep** depth, larger reasoning models | several minutes | ~$0.50â€“$2+ |
| **Local** models via Ollama | depends on hardware | free (your compute) |

<sub>* Approximate, provider-dependent â€” not a quote. Watch your provider dashboard for actual usage.</sub>

**First run?** Start with **Shallow** depth and small quick/deep models to confirm everything works
before spending on deeper analyses.

---

## ðŸ’» Quick Start â€” CLI

The interactive CLI walks you through every setting step by step â€” no config files to edit.

### Prerequisites

Same as above ([Python â‰¥ 3.10](#prerequisites)), plus the editable install:

```bash
pip install -e .
```

### Single-stock analysis

```bash
python -m cli.main analyze
# or, after editable install:
opentrace analyze
```

`analyze` first asks whether you want **single-ticker analysis**, **portfolio analysis**, or
**AI stock discovery**, then walks you through:

| Prompt | What it does |
|:--|:--|
| **Ticker** | Stock symbol(s) to analyse (e.g. `NVDA`, `AAPL`) |
| **Date** | Analysis date in `YYYY-MM-DD` format |
| **Analysts** | Which specialists to include â€” Catalyst/Event-Risk, Market, Social, News, Fundamentals |
| **Research depth** | How many debate rounds: **Shallow** (fast) Â· **Medium** Â· **Deep** (thorough) |
| **Time horizon** | Target holding period (1â€“2 weeks up to 2â€“3 months) |
| **LLM Provider** | OpenAI, Google, Anthropic, DeepSeek, Qwen, GLM, OpenRouter, or Ollama |
| **Models** | Quick-thinking model (analysts) and deep-thinking model (judges) |
| **Execution** | Analysis only, or also place a paper trade via Alpaca |

A live terminal dashboard streams agent progress, tool calls, and the growing report in real time. Results are saved to `results/stocks/{date}/{ticker}/`.

### Portfolio analysis

```bash
python -m cli.main analyze-portfolio
# or
opentrace analyze-portfolio
```

Pulls your Alpaca positions, runs a **triage step** to identify which stocks most need attention, then performs full multi-agent analysis on those. Remaining stocks get a lightweight "HOLD" entry.

### Stock discovery

Stock discovery runs from the main `analyze` command â€” choose **"Stock Discovery (AI finds
promising stocks)"** at the first prompt. The system screens for promising tickers using
multi-factor scoring, then runs deep multi-agent analysis on the top candidates. You can launch a
fresh discovery run or resume deep analysis from a previously saved candidate list. See
[Stock Discovery mode](#-stock-discovery-mode) for the pipeline details.

### Journal

```bash
opentrace journal
```

Track trade outcomes and build agent memory. See [`journal_cli/README.md`](journal_cli/README.md) for details.

> [!TIP]
> Run `opentrace --help` (or `python -m cli.main --help`) to see every command and option.

---

## ðŸ Python API (programmatic)

For scripting or integration, skip the UI entirely:

```python
from opentrace.graph.opentrace_graph import OpenTraceGraph
from opentrace.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

config = DEFAULT_CONFIG.copy()
config["llm_provider"]      = "google"          # or "openai", "anthropic", "deepseek", etc.
config["deep_think_llm"]    = "gemini-2.5-flash"
config["quick_think_llm"]   = "gemini-2.0-flash"

ta = OpenTraceGraph(config=config)

# Returns the full state and a structured trade decision
state, decision = ta.propagate("NVDA", "2024-05-10")
print(decision)
```

---

## ðŸ”¬ Reasoning & Decision Traces

Transparency is OpenTrace's reason for existing. Every analysis emits two kinds of trace, both
viewable in the web UI:

- **Reasoning trace** â€” for each agent, *what it looked at and how it concluded*. Built by
  [`graph/reasoning_trace.py`](opentrace/graph/reasoning_trace.py) and surfaced in the
  **Trader Reasoning** and **Evidence Graph** panels.
- **Decision trace** â€” the chain from evidence â†’ research debate â†’ trader plan â†’ risk review â†’
  final structured decision, rendered in the **Decision Trace** panel. The final decision itself is
  a structured object validated against [`graph/decision_schema.py`](opentrace/graph/decision_schema.py)
  and extracted by [`graph/signal_processing.py`](opentrace/graph/signal_processing.py).

Conceptually, a trace lets you answer "*why this trade?*" at every level:

```text
Evidence Graph        â†’  "Q3 revenue +18% YoY; RSI 71 (overbought); insider selling last week"
   â†“
Research debate       â†’  Bull: durable demand Â· Bear: valuation stretched â†’ Manager: cautious BUY
   â†“
Trader plan           â†’  BUY, LIMIT @ $X, size 10% of buying power
   â†“
Risk review           â†’  Conservative trims size; Risk Judge approves with concentration cap
   â†“
Final decision        â†’  { action: BUY, order_type: LIMIT, qty: ..., rationale: ... }
```

<sub>Illustrative â€” the exact fields come from the decision schema and evidence-graph code above.</sub>

The web UI renders these via dedicated React panels
(`DecisionTracePanel`, `TraderReasoningPanel`, `EvidenceGraphPanel` under `frontend/src/`), so you
can expand any agent's contribution instead of trusting a single opaque verdict.

---

## ðŸ—ï¸ Architecture

OpenTrace is built on **LangGraph** â€” each agent is a node in a directed workflow graph. Here is the full pipeline:

```
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                          ðŸ“Š Market Data Layer                            â”‚
â”‚  Alpaca Â· Yahoo Finance Â· Alpha Vantage Â· Twelve Data Â· Finnhub Â· Local  â”‚
â”‚                   (automatic fallback between sources)                   â”‚
â”‚                                                                          â”‚
â”‚  Tools: stock data Â· indicators Â· VWAP Â· options flow Â· dark pool Â·      â”‚
â”‚         short interest Â· news Â· SEC filings Â· insider transactions        â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                 â”‚  price Â· news Â· fundamentals Â· sentiment
                                 â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                       ðŸ” I. Analyst Team                                 â”‚
â”‚                                                                          â”‚
â”‚  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â”‚
â”‚  â”‚ Catalyst  â”‚â†’ â”‚  Market  â”‚â†’ â”‚  Social  â”‚â†’ â”‚  News  â”‚â†’ â”‚Fundamentalsâ”‚  â”‚
â”‚  â”‚  Event    â”‚  â”‚  Analyst â”‚  â”‚  Analyst â”‚  â”‚ Analystâ”‚  â”‚  Analyst   â”‚  â”‚
â”‚  â”‚  Analyst  â”‚  â”‚          â”‚  â”‚          â”‚  â”‚        â”‚  â”‚            â”‚  â”‚
â”‚  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜  â”‚
â”‚       Each specialist calls data tools in a loop, then writes            â”‚
â”‚       a focused report. Uses the quick-thinking LLM.                     â”‚
â”‚       (Any combination of analysts can be selected.)                     â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                 â”‚  analyst reports
                                 â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                   ðŸ”— Evidence Graph Synthesis                             â”‚
â”‚       Extracts key facts from all analyst reports and builds             â”‚
â”‚       a structured evidence graph for downstream agents.                 â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                 â”‚  evidence graph + reports
                                 â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                    ðŸ’¬ II. Research Team Decision                          â”‚
â”‚                                                                          â”‚
â”‚           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”  â—„â”€â”€â–º  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                       â”‚
â”‚           â”‚    Bull      â”‚        â”‚    Bear      â”‚                       â”‚
â”‚           â”‚  Researcher  â”‚        â”‚  Researcher  â”‚                       â”‚
â”‚           â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜        â””â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”˜                       â”‚
â”‚                  â”‚   (multi-round debate) â”‚                              â”‚
â”‚                  â–¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â–¼                               â”‚
â”‚           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                             â”‚
â”‚           â”‚      Research Manager          â”‚  â† deep-thinking LLM        â”‚
â”‚           â”‚  Judges the debate, writes     â”‚                             â”‚
â”‚           â”‚  the investment decision       â”‚                             â”‚
â”‚           â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                             â”‚  investment decision
                             â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                      ðŸ“ III. Trading Team Plan                           â”‚
â”‚                                                                          â”‚
â”‚           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                             â”‚
â”‚           â”‚            Trader              â”‚                             â”‚
â”‚           â”‚  Synthesizes research into a   â”‚                             â”‚
â”‚           â”‚  concrete investment plan with â”‚                             â”‚
â”‚           â”‚  order type & quantity details â”‚                             â”‚
â”‚           â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                             â”‚  proposed plan
                             â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                âš–ï¸  IV. Risk Management Team Decision                      â”‚
â”‚                                                                          â”‚
â”‚      â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”   â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                        â”‚
â”‚      â”‚Aggressive â”‚â”€â”€â–ºâ”‚ Neutral â”‚â—„â”€â”€â”‚Conservativeâ”‚                       â”‚
â”‚      â”‚  Analyst  â”‚   â”‚ Analyst â”‚   â”‚  Analyst   â”‚                       â”‚
â”‚      â””â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”˜   â””â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”˜                        â”‚
â”‚            â””â”€â”€â”€â”€ (multi-round cycle) â”€â”€â”€â”€â”€â”˜                              â”‚
â”‚                        â”‚                                                 â”‚
â”‚                        â–¼                                                 â”‚
â”‚           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                             â”‚
â”‚           â”‚         Risk Judge             â”‚  â† deep-thinking LLM        â”‚
â”‚           â”‚  Final risk-aware decision     â”‚                             â”‚
â”‚           â”‚  with position-sizing & limits â”‚                             â”‚
â”‚           â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¼â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                             â”‚  final structured decision
                             â–¼
â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
â”‚                       ðŸ’° V. Execution Layer                              â”‚
â”‚                                                                          â”‚
â”‚           â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”                             â”‚
â”‚           â”‚       Alpaca Executor          â”‚                             â”‚
â”‚           â”‚  Places paper or live orders   â”‚                             â”‚
â”‚           â”‚  Enforces concentration caps   â”‚                             â”‚
â”‚           â”‚  and position-size guardrails  â”‚                             â”‚
â”‚           â”‚  Decision guard validation     â”‚                             â”‚
â”‚           â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜                             â”‚
â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

### ðŸ§  Two tiers of LLM

Every agent in the system uses one of two model slots:

| Tier | Used by | Why |
|:--|:--|:--|
| **Quick-thinking** | Catalyst / Market / Social / News / Fundamentals Analysts, Bull & Bear Researchers, Trader, Risk Debaters | Speed and cost â€” these agents run many times and don't need heavy reasoning |
| **Deep-thinking** | Research Manager, Risk Judge, Portfolio Triage Agent | These are the key decision points where accuracy matters most; a stronger model pays off here |

You set both in one place (`deep_think_llm` and `quick_think_llm`) and the system routes them automatically.

### ðŸ”— Evidence graph

After the analysts finish, OpenTrace doesn't just concatenate their reports. It distills them into a
**structured evidence graph** ([`agents/utils/agent_runtime/evidence_graph.py`](opentrace/agents/utils/agent_runtime/evidence_graph.py))
â€” a compact set of typed facts (catalysts, metrics, risks, sentiment) that every downstream agent
references. This keeps the bull/bear debate and the trader anchored to concrete evidence instead of
free-floating prose, and it's what powers the Evidence Graph panel in the UI.

### ðŸ—„ï¸ Data layer

All market-data tool calls go through a single routing layer ([`dataflows/interface.py`](opentrace/dataflows/interface.py)). You pick a preferred vendor per category in your config; if that vendor is unavailable the system silently tries the next one.

Each analyst has access to a curated set of data tools:

| Analyst | Key tools |
|:--|:--|
| **Catalyst Event** | Catalyst event bundle, company news window, SEC filings, insider transactions, price action |
| **Market** | Stock data, indicators, VWAP, options flow, dark pool volume, short interest |
| **Social** | News, company news window, news sentiment |
| **News** | News, company news window, global news, news sentiment, SEC filings |
| **Fundamentals** | Fundamentals, balance sheet, cash flow, income statement, insider sentiment & transactions |

> [!TIP]
> When `enable_bundle_tools` is on (default), each analyst also gets a one-shot "bundle" tool that fetches all key data in a single call, reducing LLM turns and latency.

### ðŸ’¾ Memory & closed-loop learning

Each agent team has its own **vector-store memory** (backed by ChromaDB). On top of that, the
**Journal** subsystem ([`opentrace/agents/journal/`](opentrace/agents/journal/)) closes the
loop between a decision and its real-world outcome:

- **Thesis extraction & state machine** â€” each trade's thesis is captured and tracked through its
  lifecycle (open â†’ playing out â†’ invalidated/realized).
- **Condition & outcome monitoring** â€” a scheduler watches positions, infers triggering events, and
  records what actually happened.
- **Reflection â†’ lesson memory** â€” `reflect_and_remember()` turns outcomes into lessons that are
  retrieved the next time a similar setup appears, so the system improves with experience.

See [`opentrace/agents/journal/USAGE.md`](opentrace/agents/journal/USAGE.md) and
[`journal_cli/README.md`](journal_cli/README.md).

### ðŸ“ Portfolio mode extras

When you run portfolio analysis, an additional **Triage Agent** runs first. It scans all your positions and picks the ones that need the most attention right now â€” based on breaking news, unusual price moves, concentration risk, and more. Only those stocks go through the full multi-agent pipeline; everything else gets a quick "HOLD" recommendation.

### ðŸ”Ž Stock Discovery mode

The discovery pipeline runs independently of the main analysis graph:

1. **Stage 0 â€” Catalyst prefilter**: Screens for upcoming earnings, FDA events, and macro catalysts
2. **Stage 1 â€” Multi-factor enrichment**: Technical momentum metrics, relative strength, volume analysis across the screening universe
3. **Stage 2 â€” Candidate scoring**: Composite ranking with configurable relaxation rules
4. **Deep analysis**: Top candidates are fed into the full OpenTraceGraph for multi-agent analysis

Supports three tracks: **Enricher** (swing trade), **Anomaly Scan** (intraday/next-day), and **Dual-Track** (merged).

A complementary **Theme Engine** ([`agents/discovery/theme_engine/`](opentrace/agents/discovery/theme_engine/))
scans for active macro themes and scores how exposed each candidate is to them, so discovery can be
steered by what's actually driving the market.

---

## âš™ï¸ Configuration

All defaults live in [`opentrace/default_config.py`](opentrace/default_config.py). Here are the knobs you'll use most:

### LLM settings

| Key | What it controls | Example values |
|:--|:--|:--|
| `llm_provider` | Which LLM backend to use | `openai` Â· `anthropic` Â· `google` Â· `deepseek` Â· `openrouter` Â· `qwen3-cn` Â· `glm` Â· `ollama` |
| `deep_think_llm` | Model for judges & managers | `"o4-mini"` Â· `"gemini-2.5-flash"` Â· `"claude-sonnet-4-20250514"` |
| `quick_think_llm` | Model for analysts & researchers | `"gpt-4o-mini"` Â· `"gemini-2.0-flash"` |
| `max_debate_rounds` | Bull â†” Bear debate rounds | `1` (default), capped at `3` |

### Data vendor options

Configured per category in the `data_vendors` dict:

| Category | Available sources | Default |
|:--|:--|:--|
| `core_stock_apis` | `alpaca` Â· `yfinance` Â· `alpha_vantage` Â· `twelve_data` Â· `local` | `alpaca` |
| `technical_indicators` | `alpaca` Â· `yfinance` Â· `alpha_vantage` Â· `twelve_data` Â· `local` | `alpaca` |
| `fundamental_data` | `alpha_vantage` Â· `openai` Â· `local` | `alpha_vantage` |
| `news_data` | `alpha_vantage` Â· `openai` Â· `google` Â· `local` | `alpha_vantage` |

> [!TIP]
> If a vendor is unavailable at runtime the system automatically falls back to the next option â€” nothing crashes. (Finnhub and SEC EDGAR back specific tools such as insider/filing data rather than the four switchable categories above.)

### Trade execution

| Key | What it controls | Default |
|:--|:--|:--|
| `alpaca_execution.enabled` | Turn trading on / off | `false` |
| `alpaca_execution.paper_trading` | Paper vs. live | `true` |
| `alpaca_execution.position_size_pct` | Default position size | `0.10` (10%) |
| `alpaca_execution.max_concentration_pct` | Max single-stock concentration | `0.20` (20%) |

### Supported order types

| Order type | Description |
|:--|:--|
| `MARKET` | Execute immediately at the current market price |
| `LIMIT` | Execute only at a specified price or better |
| `STOP` | Triggers a market order once the stock hits a stop price |
| `STOP_LIMIT` | Triggers a limit order once the stock hits a stop price |
| `TRAILING_STOP` | Stop that moves with the stock price, locking in gains |

### Context budget mode

Controls how prompts are compressed to fit within model context windows:

| Mode | Behaviour |
|:--|:--|
| `adaptive` (default) | Cap prompt sections and apply a soft token budget |
| `compact` | Stronger compression for tighter context windows |
| `off` | No limiting â€” âš ï¸ may cause 400 errors on models with strict limits |

Set via `.env`:
```env
OPENTRACE_CONTEXT_BUDGET_MODE=adaptive
```

---

## ðŸ§° Troubleshooting & FAQ

| Symptom | Likely cause & fix |
|:--|:--|
| `No API key` / auth errors | A provider key is missing or wrong in `.env`. You need **one** LLM key; market data works with no key via Yahoo Finance. |
| **HTTP 400 â€” context length exceeded** | The model's context window is too small for the prompt. Keep `OPENTRACE_CONTEXT_BUDGET_MODE=adaptive` (default) or set it to `compact`; avoid `off` on strict models. |
| **HTTP 429 â€” rate limited** | Your provider is throttling. Use a smaller/faster model, lower research depth, or raise the manager delay knobs (`OPENTRACE_RESEARCH_MANAGER_MIN_DELAY_S`, `OPENTRACE_RISK_MANAGER_MIN_DELAY_S`). |
| Frontend loads but calls fail | The backend isn't running or is on a different port. Start `uvicorn api.main:app --reload` (default `http://localhost:8000`). |
| `npm run dev` fails | Check Node â‰¥ 18 and run `npm install` inside `frontend/`. |
| ChromaDB / native build errors on install | Ensure you're on Python â‰¥ 3.10 in a clean virtualenv; upgrade `pip` before `pip install -e .`. |
| Analysis is slow / expensive | Use **Shallow** depth and small quick/deep models (see [What to expect](#-what-to-expect-cost--time)), or run local models with Ollama. |

Run `opentrace --help` to discover every command and flag.

---

## ðŸ¤ Contributing

Contributions â€” research ideas and engineering fixes alike â€” are welcome. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, project layout, and the PR process, and
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) for community guidelines.

---

## ðŸ“š Citation

If you use OpenTrace in academic work, please cite this repository (see
[`CITATION.cff`](CITATION.cff)) **and** the upstream TradingAgents framework it builds on:

```bibtex
@software{opentrace,
  title  = {OpenTrace: A multi-agent AI trading framework with visible reasoning and decision traces},
  author = {Jia, Muye},
  year   = {2026},
  url    = {https://github.com/muye1202/OpenTrace}
}

@misc{tradingagents,
  title        = {TradingAgents: Multi-Agents LLM Financial Trading Framework},
  author       = {Tauric Research},
  howpublished = {\url{https://github.com/tauricresearch/tradingagents}}
}
```

---

## ðŸ¤ Credits & Acknowledgments

This project is built upon the open-source [TradingAgents](https://github.com/tauricresearch/tradingagents) framework developed by Tauric Research. We are grateful to the original authors for their pioneering work on multi-agent LLM systems for financial analysis and trading.

---

## âš ï¸ Disclaimer

OpenTrace is a **research and educational tool**. It is not financial advice. Always paper-trade first and understand the risks before using real money. The authors are not responsible for any financial losses incurred through the use of this software.

---

## ðŸ“„ License

[Apache License 2.0](LICENSE) â€” see the [LICENSE](LICENSE) file for details.

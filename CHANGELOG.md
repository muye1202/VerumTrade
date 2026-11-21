# Changelog

All notable changes to VerumTrade are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Live demo in the browser** at <https://muye1202.github.io/VerumTrade/demo/>: the web app built
  with `VITE_STATIC_DEMO=1` replays recorded runs from static JSON, with no server and no keys. A
  Pages workflow publishes it with the landing page and example report.

## [0.1.0] — 2026-11-19

The first tagged release of VerumTrade: an open-source, multi-agent LLM framework for stock
research where every decision comes with visible reasoning and a decision trace, from raw evidence
to the final trade proposal.

> [!IMPORTANT]
> VerumTrade is a research and decision-support tool, not an autonomous trader and not financial
> advice. Paper-trade first and make any real-money decision yourself.

### ▶ Try it in 60 seconds, no API keys

```bash
git clone https://github.com/muye1202/VerumTrade.git && cd VerumTrade
docker compose up --build
```

Open <http://localhost:5173> and click **▶ Replay MU (2026-06-23)** to watch a real recorded run
stream through the full web app. Add one LLM key to `.env` when you want to analyze your own
tickers.

### Highlights

- **Keyless demo replay.** A recorded MU analysis plays back through the same WebSocket stream as a
  live run, so the transcript, report panels, evidence graph, debates, and decision trace all render
  as they would live. `VERUMTRADE_DEMO_MODE=1` turns the backend into a replay-only server for
  hosted demos.
- **One-command Docker setup.** `docker compose up --build` starts the FastAPI backend and the web
  app (served by nginx, which proxies the API and WebSockets). History persists in a Docker volume.
  A CI workflow builds the stack and replays the demo on every push to `main` and every pull request.
- **Public test suite and CI.** 227 offline pytest tests (no API keys needed) plus frontend lint,
  checks, and build now run on every push to `main` and every pull request.
- **Visible reasoning and decision traces.** Each step from analyst evidence to final decision is
  captured and rendered in the web UI.
- **Evidence graph.** Analyst findings are distilled into a structured, citable fact graph that
  grounds the downstream debate and decisions.
- **Structured agent pipeline (LangGraph).** Catalyst, market, social, news, and fundamentals
  analysts → research controller → bull/bear debate → thesis compiler → trader plan → risk debate →
  risk judge.
- **Inspectable research harness.** Analysts run an assess-then-act research loop; a research
  controller opens follow-up tasks that must be resolved with evidence gathered in-task; point-in-time
  SEC EDGAR filings and XBRL financial facts are fetched, content-hashed, and verified before they
  can be cited.
- **Swing-thesis scenarios.** A thesis compiler produces bull/base/bear scenarios with
  operand-traced calculations; theses, forecasts, and watch contracts are stored append-only so later
  runs can show what changed and score earlier forecasts.
- **Crowding and macro-pullback awareness.** A macro regime context bus, a per-ticker Pullback
  Vulnerability Score, and peer/sector read-through warn when a crowded, extended name is fragile.
- **Stock discovery mode.** A multi-stage screener (theme engine, universe screen, enrichment,
  business inflection, attention gap, two-layer scoring) ranks candidates and writes thesis cards.
- **Bring your own model and data.** OpenAI, Azure Foundry, Anthropic, Google, DeepSeek, Qwen,
  GLM, OpenRouter, and local Ollama; Yahoo Finance works with no key, with optional Alpaca, Alpha
  Vantage, Twelve Data, Finnhub, and SEC EDGAR sources and automatic fallback.
- **Optional paper or live execution** through Alpaca, with position-size and concentration
  guardrails and a pre-execution decision guard.
- **Web app, CLI, and Python API.** React + Vite UI, Typer/Rich CLI, and a FastAPI backend.
- **English and 简体中文** documentation and example report.

### Fixed

- The decision trace could report the wrong action (for example SELL on a HOLD decision that
  mentioned "SELL is invalid"), because it was built before the structured decision existed and
  fell back to the first action word in the text. It now reads the risk judge's embedded decision
  JSON first.

### Upgrade notes

- The project was renamed from **OpenTrace** to **VerumTrade**. Python imports are now
  `verumtrade.*`, and environment variables use the `VERUMTRADE_` prefix (previously
  `OPENTRACE_`). Update any existing `.env` file.
- The web app needs Node.js 20.19 or newer (required by Vite 8). Docker users don't need Node.

### Credits

VerumTrade builds on [TradingAgents](https://github.com/tauricresearch/tradingagents) by Tauric
Research.

[0.1.0]: https://github.com/muye1202/VerumTrade/releases/tag/v0.1.0

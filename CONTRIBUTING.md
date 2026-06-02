# Contributing to OpenTrace

Thanks for your interest in improving OpenTrace! This project is both an academic
exploration of multi-agent LLM trading systems and a practical stock-analysis aid,
and contributions of either flavour â€” research ideas or engineering fixes â€” are
welcome.

> âš ï¸ **Reminder:** OpenTrace is a research and educational tool, not financial
> advice. See the [Disclaimer](README.md#-disclaimer).

## Ways to contribute

- **Bug reports** â€” open an [issue](https://github.com/muye1202/OpenTrace/issues)
  with steps to reproduce, your OS/Python/Node versions, the LLM provider you used,
  and the relevant log output.
- **Features & agents** â€” new analysts, data vendors, or pipeline stages. Please
  open an issue to discuss the design before a large PR.
- **Docs** â€” clarifications to the README, setup notes, or examples.

## Development setup

```bash
# 1. Clone
git clone https://github.com/muye1202/OpenTrace.git
cd OpenTrace

# 2. Python (editable install â€” pulls deps from pyproject.toml)
pip install -e .          # or: uv sync

# 3. Frontend
cd frontend && npm install && cd ..

# 4. Configure keys
cp .env.example .env      # then add at least one LLM provider key
```

Run the app two ways:

```bash
# Web (two terminals)
uvicorn api.main:app --reload     # backend â†’ http://localhost:8000
cd frontend && npm run dev        # frontend â†’ http://localhost:5173

# CLI
opentrace analyze
```

## Project layout

The published repository tracks the runnable application only:

- `opentrace/` â€” core multi-agent framework (agents, dataflows, graph, execution)
- `api/` â€” FastAPI backend
- `cli/` â€” Typer + Rich terminal interface
- `frontend/` â€” React + Vite web client

Local-only working directories (`tests/`, `docs/`, `scripts/`, `results/`,
`data_cache/`, `*.db`) are intentionally git-ignored â€” see [`.gitignore`](.gitignore).
If you add tests or design docs you want to share, mention it in your PR so we can
decide how to include them.

## Coding conventions

- Match the style of the surrounding code; keep agent prompts and node wiring
  consistent with existing patterns in `opentrace/agents/` and
  `opentrace/graph/`.
- Dependencies live in `pyproject.toml`. `requirements.txt` is a mirror â€” update
  **both** if you change a dependency.
- Keep secrets out of commits. Never commit a real `.env`, API key, or brokerage
  credential.

## Pull request process

1. Fork and create a feature branch (`git checkout -b feature/my-change`).
2. Make focused commits with clear messages.
3. Verify the app still launches (web + CLI) and that any code you touched runs.
4. Open a PR describing **what** changed and **why**, and link any related issue.

## Code of conduct

By participating you agree to abide by our
[Code of Conduct](CODE_OF_CONDUCT.md).

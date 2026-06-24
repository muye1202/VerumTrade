# Project Memory

## Stock Discovery Upgrade Progress

- Current working branch for this effort: `web/discovery_mode`.
- Latest `origin/main` was pulled into `web/discovery_mode` with a fast-forward merge before the stock discovery upgrade work continued.
- Backend discovery now includes these deterministic intelligence modules:
  - `verumtrade/agents/discovery/intelligence/business_inflection.py`
  - `verumtrade/agents/discovery/intelligence/attention_gap.py`
  - `verumtrade/agents/discovery/intelligence/discovery_evidence_pack.py`
  - `verumtrade/agents/discovery/intelligence/two_layer_discovery_scoring.py`
  - `verumtrade/agents/discovery/intelligence/thesis_card_validator.py`
- These modules are wired through:
  - `verumtrade/agents/discovery/intelligence/pipeline_models.py`
  - `verumtrade/agents/discovery/intelligence/pipeline_orchestrator.py`
  - `verumtrade/agents/discovery/intelligence_integration.py`
  - `verumtrade/graph/stock_discovery.py`
  - `verumtrade/default_config.py`
- Business inflection extraction is disabled by default and can be enabled with:
  - config key: `discovery.business_inflection.enabled`
  - env flag: `VERUMTRADE_DISCOVERY_BUSINESS_INFLECTION_ENABLED`
- Discovery result metadata now carries:
  - `business_inflection`
  - `attention_gap`
  - `evidence_packs`
  - `two_layer_scoring`
  - `thesis_cards`
- The recommender now prefers `two_layer_candidates` for final ranking when available, so thesis-rich candidates can outrank technical-only names.
- Discovery reports now include:
  - `Two-Layer Discovery Scores`
  - `Thesis Cards`

## Discovery Web App Progress

- Discovery WebSocket request schema now accepts `business_inflection_enabled`.
- `api/routes/discovery.py` maps that request field into `config["discovery"]["business_inflection"]["enabled"]`.
- Discovery WebSocket now streams structured events:
  - `theme_candidates`
  - `business_inflection`
  - `attention_gap`
- Frontend discovery mode now has an `Inflection Scan` toggle in the config strip.
- Frontend discovery pipeline stage UI now reflects the current workflow:
  - Theme Engine
  - Universe Screen
  - Enrichment
  - Inflection
  - Attention Gap
- New frontend files:
  - `frontend/src/DiscoverySignalsPanel.jsx`
  - `frontend/src/DiscoveryDecisionPanel.jsx`
  - `frontend/src/discoverySignalsViewModel.js`
  - `frontend/src/discoverySignalsViewModel.check.mjs`
- `frontend/src/App.jsx` renders structured Business Inflection and Attention Gap panels in both live analysis and completed report views.
- `frontend/src/App.jsx` also renders Candidate Decisions from `two_layer_scoring_json`, `evidence_packs_json`, and `thesis_cards_json` in both live analysis and completed report views.
- `api/routes/discovery.py` streams `evidence_packs`, `two_layer_scoring`, and `thesis_cards` WebSocket events before the markdown report.

## Verification Status

Fresh verification after the UI/API wiring:

- `node frontend\src\discoverySignalsViewModel.check.mjs` passed.
- `npm.cmd run build` passed.
- `npm.cmd run lint` passed.
- `python -m pytest -p no:cacheprovider tests\discovery -q` passed with `22 passed, 1 warning`.

Fresh verification after adding evidence packs, two-layer scoring, and thesis cards:

- `python -m pytest -p no:cacheprovider tests\discovery -q` passed with `27 passed, 1 warning`.

Fresh verification after updating the discovery UI for candidate tiers and thesis cards:

- `node frontend\src\discoverySignalsViewModel.check.mjs` passed.
- `npm.cmd run build` passed.
- `npm.cmd run lint` passed.
- `python -m pytest -p no:cacheprovider tests\discovery -q` passed with `27 passed, 1 warning`.
- Vite dev server returned HTTP `200` at `http://127.0.0.1:5173`.

Known validation gap:

- Browser plugin was not available in the Codex session and Playwright was not installed in `frontend/node_modules`, so rendered screenshot QA was not performed.
- Vite dev server was started and `http://127.0.0.1:5173` returned HTTP `200`.

## Notes For Future Threads

- Do not revert unrelated local changes; this workspace may have ongoing edits from prior discovery work.
- `rg` may be blocked by Windows permissions in this workspace; use PowerShell `Select-String` as fallback.
- `npm` may fail in PowerShell due execution policy blocking `npm.ps1`; use `npm.cmd` instead.
- Git status may warn that your global excludes file (e.g. `~/.config/git/ignore` or `%USERPROFILE%\.config\git\ignore`) is inaccessible. This warning has been harmless.

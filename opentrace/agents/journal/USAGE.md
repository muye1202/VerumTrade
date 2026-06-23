# Journal Internals

The journal package is retained as an internal subsystem for thesis capture,
outcome tracking, reflection, and lesson memory. It is not exposed as a
standalone end-user CLI workflow.

The public `python -m cli.main journal ...` command group has been removed for
now to keep OpenTrace's safety posture clear: the main product surface is a
research and decision-support pipeline, not an autonomous trade-monitoring or
execution daemon.

## Current Boundary

- Single-ticker and portfolio analysis may still use journal internals for
  non-critical thesis capture and future learning hooks.
- Graph-local role memory and journal lesson-memory modules remain available to
  internal code.
- Standalone journal commands, daemon startup through the main CLI, and
  user-facing journal command documentation are intentionally not part of the
  root CLI surface.

## Relevant Modules

| Area | Module |
|:--|:--|
| SQLite store and models | `opentrace.agents.journal.core` |
| Report/thesis ingestion helpers | `opentrace.agents.journal.ingestion` |
| Outcome monitoring internals | `opentrace.agents.journal.monitoring` |
| Reflection and lesson memory | `opentrace.agents.journal.learning` |
| Portfolio sync helpers | `opentrace.agents.journal.portfolio` |

## Safety Notes

Keep journal behavior opt-in inside higher-level workflows. Do not reintroduce a
standalone command group without also adding clear execution guardrails, dry-run
defaults, prominent risk documentation, and tests proving the public CLI surface
matches the intended safety boundary.

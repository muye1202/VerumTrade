from pathlib import Path

from export_example_report_html import render_example_report_html


def test_render_example_report_html_builds_interactive_static_page():
    markdown = """# Example Verumtrade Report: MU (2026-06-23)
> Historical example.
## Run Snapshot
| Field | Value |
|:--|:--|
| Ticker | MU |
## Analyst Reports
<details open>
<summary><strong>Catalyst</strong></summary>

## Catalyst / Event-Risk Report
- Event risk rating: **MEDIUM**

</details>
## Final Verdict
<details open>
<summary><strong>Final Trade Decision</strong></summary>

Narrative and decision rationale

</details>
"""

    html = render_example_report_html(markdown, source_path=Path("MU-2026-06-23.md"))

    assert "<!doctype html>" in html
    assert "Verumtrade example report" in html
    assert 'data-section-id="run-snapshot"' in html
    assert 'data-section-id="analyst-reports"' in html
    assert 'data-panel-title="Catalyst"' in html
    assert 'class="report-layout"' in html
    assert "<nav" in html
    assert "<button" in html
    assert 'data-lang-option="zh-CN"' in html
    assert "简体中文视图" in html
    assert "Event risk rating" in html
    assert "Narrative and decision rationale" in html
    assert "<script>" in html
    assert 'aria-expanded="true"' not in html
    assert 'class="report-panel is-open"' not in html
    assert html.count('class="panel-body" hidden') == 2


def test_render_example_report_html_uses_scrolling_report_header_and_stat_readouts():
    markdown = """# Example Verumtrade Report: MU (2026-06-23)
> Historical example.
## Run Snapshot
| Field | Value |
|:--|:--|
| Ticker | MU |
| Recommended action | HOLD |
| Setup quality | C |
| Execution intent | WAIT_FOR_TRIGGER |
## Scenario Snapshot
| Scenario | Probability | Target / Risk | Path |
|:--|:--|:--|:--|
| Bull | 0.45 | 114.0 | Price works toward the next reward zone. |
| Base | 0.09 |  | Setup remains unresolved. |
| Bear | 0.46 |  | Catalyst risk dominates before confirmation. |
"""

    html = render_example_report_html(markdown, source_path=Path("MU-2026-06-23.md"))

    assert "position: sticky" not in html
    assert "position: fixed" not in html
    assert 'class="snapshot-grid"' in html
    assert 'class="snapshot-card snapshot-card--primary"' in html
    assert 'class="scenario-readout"' in html
    assert 'class="scenario-card scenario-card--bull"' in html


def test_render_example_report_html_omits_raw_json_payloads_and_places_key_terms_last():
    markdown = """# Example Verumtrade Report: MU (2026-06-23)
> Historical example with ATR.
## Run Snapshot
| Field | Value |
|:--|:--|
| Ticker | MU |
## Additional Diagnostics
<details>
<summary><strong>Tool Cache Metrics</strong></summary>

```json
{"cache": {"large": ["raw", "payload"]}}
```

</details>
## Final Verdict
<details open>
<summary><strong>Final Trade Decision</strong></summary>

Narrative decision.

BEGIN_DECISION_JSON
{"decision": "raw payload"}
END_DECISION_JSON

</details>
"""

    html = render_example_report_html(markdown, source_path=Path("MU-2026-06-23.md"))

    assert "Tool Cache Metrics" not in html
    assert "BEGIN_DECISION_JSON" not in html
    assert "raw payload" not in html
    assert "Narrative decision." in html
    assert html.index('id="final-verdict"') < html.index('id="key-terms"')
    assert html.rindex('data-nav-target="final-verdict"') < html.rindex('data-nav-target="key-terms"')

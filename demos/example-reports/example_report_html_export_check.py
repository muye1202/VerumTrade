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

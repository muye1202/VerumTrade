from verumtrade.agents.analysts.workbench import (
    extract_ledger_and_memo,
    finalize_analyst_workbench_output,
)


def test_extract_ledger_accepts_markdown_wrapped_fenced_json():
    content = """
**BEGIN_ANALYST_LEDGER_JSON**
```json
{
  "analyst_domain": "news",
  "observations": [
    {
      "id": "obs_1",
      "text": "MRVL earnings headlines are mixed with valuation concerns.",
      "source_fact_ids": ["fact_news_001"],
      "surprise_score": 0.6,
      "status": "explained"
    },
    {
      "id": "obs_2",
      "description": "Bank price-target increases support the AI narrative.",
      "source_fact_ids": ["fact_news_002"],
      "surprise_score": 0.4,
      "status": "explained"
    },
    {
      "id": "obs_3",
      "claim": "A downgrade creates near-term narrative conflict.",
      "source_fact_ids": ["fact_news_003"],
      "surprise_score": 0.7,
      "status": "contradictory"
    }
  ],
  "active_hypotheses": [
    {
      "id": "h_1",
      "claim": "News flow is supportive but crowded.",
      "origin": "anomaly_generated",
      "support": ["obs_1", "obs_2"],
      "against": ["obs_3"],
      "confidence": 0.55,
      "falsifier": "Fresh company-specific negative guidance dominates the tape.",
      "unresolved_questions": []
    }
  ]
}
```
**END_ANALYST_LEDGER_JSON**

## Domain Inference
News flow is not one-sided.
"""

    ledger, memo = extract_ledger_and_memo("news", content)

    assert [obs["claim"] for obs in ledger["observations"]] == [
        "MRVL earnings headlines are mixed with valuation concerns.",
        "Bank price-target increases support the AI narrative.",
        "A downgrade creates near-term narrative conflict.",
    ]
    assert ledger["active_hypotheses"][0]["claim"] == "News flow is supportive but crowded."
    assert "BEGIN_ANALYST_LEDGER_JSON" not in memo


def test_recovery_ignores_textual_deepseek_pseudo_tool_calls():
    content = """
Let me fetch the remaining indicators and institutional flow data that was missing from the bundle:

<function_calls>
<invoke name="get_indicators">
<parameter name="symbol" string="true">MRVL</parameter>
<parameter name="indicator" string="true">rsi</parameter>
</invoke>
</function_calls>
"""

    finalized = finalize_analyst_workbench_output("market", content)
    claims = [obs["claim"] for obs in finalized["ledger"]["observations"]]

    assert not any("<invoke" in claim or "<parameter" in claim for claim in claims)
    assert finalized["ledger"]["critic_flags"]
    assert "structured ledger contract" in finalized["report"]

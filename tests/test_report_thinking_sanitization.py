from __future__ import annotations

from verumtrade.utils.report_sanitization import strip_thinking_blocks


def test_strip_thinking_blocks_removes_hidden_reasoning():
    text = "before\n<thinking>\nsecret\n</thinking>\nafter"
    cleaned = strip_thinking_blocks(text)
    assert "<thinking>" not in cleaned.lower()
    assert "secret" not in cleaned
    assert "before" in cleaned
    assert "after" in cleaned

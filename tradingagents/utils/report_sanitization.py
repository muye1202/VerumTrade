from __future__ import annotations

import re
from typing import Optional


_THINKING_BLOCK_RE = re.compile(r"<thinking>[\s\S]*?</thinking>", flags=re.IGNORECASE)


def strip_thinking_blocks(text: Optional[str]) -> str:
    content = str(text or "")
    return _THINKING_BLOCK_RE.sub("", content).strip()

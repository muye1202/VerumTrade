"""Passage-level access to stored documents.

Passages keep exact character offsets into the stored document text, so any claim
built from a passage can cite ``(document_id, start, end)`` and be re-checked
against the source later.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TypedDict


class Passage(TypedDict, total=False):
    passage_id: str
    start: int
    end: int
    section: str
    text: str
    score: float


_SECTION_HEADING = re.compile(
    r"^\s*(item\s+\d+[a-z]?(?:\.\d+)?\b.{0,120}|part\s+[ivx]+\b.{0,80})$",
    re.IGNORECASE,
)
_STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "have", "has",
    "had", "its", "our", "their", "does", "did", "what", "which", "who", "how", "why", "when",
    "any", "all", "not", "but", "into", "than", "then", "there", "been", "being", "about",
    "latest", "company", "issuer", "disclosure", "filing", "describe", "whether", "actually",
}


def _terms(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]", text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    ]


def split_passages(text: str, max_chars: int = 1200) -> list[Passage]:
    """Group consecutive lines into passages of at most ``max_chars``, tracking section headings."""
    passages: list[Passage] = []
    section = ""
    start: int | None = None
    end = 0
    pos = 0
    for line in str(text or "").splitlines(keepends=True):
        line_start, pos = pos, pos + len(line)
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = bool(_SECTION_HEADING.match(stripped)) and len(stripped) <= 160
        if start is not None and (is_heading or pos - start > max_chars):
            passages.append(_passage(text, len(passages), start, end, section))
            start = None
        if is_heading:
            section = stripped[:160]
        if start is None:
            start = line_start
        end = line_start + len(line.rstrip("\r\n"))
    if start is not None:
        passages.append(_passage(text, len(passages), start, end, section))
    return passages


def _passage(text: str, idx: int, start: int, end: int, section: str) -> Passage:
    return {
        "passage_id": f"p{idx + 1:04d}",
        "start": start,
        "end": end,
        "section": section,
        "text": text[start:end],
    }


def search_passages(
    text: str,
    query: str,
    *,
    top_k: int = 4,
    section: str | None = None,
    max_chars: int = 1200,
) -> list[Passage]:
    """Rank passages by lexical relevance to ``query`` (TF-IDF style).

    This is retrieval, not interpretation: it narrows a long filing to the spans a
    reader should look at. Whether a span supports a claim is decided downstream.
    """
    passages = split_passages(text, max_chars=max_chars)
    if section:
        wanted = section.lower()
        filtered = [p for p in passages if wanted in p["section"].lower()]
        passages = filtered or passages
    query_terms = set(_terms(query))
    if not passages or not query_terms:
        return passages[:top_k] if not query_terms else []

    tokenized = [Counter(_terms(p["text"])) for p in passages]
    doc_freq = Counter(term for counts in tokenized for term in set(counts) if term in query_terms)
    total = len(passages)
    scored: list[Passage] = []
    for passage, counts in zip(passages, tokenized):
        score = 0.0
        for term in query_terms:
            tf = counts.get(term, 0)
            if tf:
                idf = math.log(1 + total / (1 + doc_freq[term]))
                score += (1 + math.log(tf)) * idf
        if section and section.lower() in passage["section"].lower():
            score *= 1.2
        if score > 0:
            scored.append({**passage, "score": round(score, 4)})
    scored.sort(key=lambda p: (-p["score"], p["start"]))
    return scored[:top_k]

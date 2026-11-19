"""Durable store of source documents, kept separate from the claims made about them.

A ``SourceDocument`` records identity (issuer, publisher, source-native ID), timing
(publication, availability, retrieval), and the full text that was read. Claims
reference a document by ``document_id`` plus a character span, so a reviewer can
re-open exactly what the model saw.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, TypedDict


class SourceDocument(TypedDict, total=False):
    document_id: str
    canonical_url: str
    issuer_cik: str
    issuer_name: str
    ticker: str
    publisher: str
    document_type: str
    source_native_id: str
    content_hash: str
    published_at: str
    available_at: str
    period_of_report: str
    retrieved_at: str
    source_version: str
    text: str
    metadata: dict[str, Any]


_COLUMNS = [
    "document_id",
    "canonical_url",
    "issuer_cik",
    "issuer_name",
    "ticker",
    "publisher",
    "document_type",
    "source_native_id",
    "content_hash",
    "published_at",
    "available_at",
    "period_of_report",
    "retrieved_at",
    "source_version",
    "text",
    "metadata",
]


def content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def make_document(**fields: Any) -> SourceDocument:
    """Build a document, deriving ``content_hash``, ``document_id`` and ``retrieved_at``."""
    text = str(fields.get("text") or "")
    digest = content_hash(text)
    doc: SourceDocument = {key: fields[key] for key in _COLUMNS if key in fields}  # type: ignore[assignment]
    doc["text"] = text
    doc["content_hash"] = digest
    doc["document_id"] = f"doc_{digest[:16]}"
    doc.setdefault("retrieved_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    doc.setdefault("metadata", {})
    return doc


def default_store_path() -> str:
    from verumtrade.dataflows.config import get_config

    config = get_config()
    explicit = config.get("research_source_store_path")
    if explicit:
        return str(explicit)
    return os.path.join(str(config.get("data_cache_dir") or "."), "source_store.db")


class SourceStore:
    """SQLite-backed document store. Identical content is stored once."""

    _lock = threading.Lock()

    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_store_path()
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False) if self.path == ":memory:" else None
        with self._connect() as conn:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS source_documents (
                    {", ".join(f"{col} TEXT" for col in _COLUMNS)},
                    PRIMARY KEY (document_id)
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_source_native ON source_documents (source_native_id)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._memory_conn is not None:
            with self._memory_conn:
                yield self._memory_conn
            return
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def put(self, doc: SourceDocument) -> SourceDocument:
        row = {col: doc.get(col) for col in _COLUMNS}  # type: ignore[misc]
        row["metadata"] = json.dumps(doc.get("metadata") or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"INSERT OR IGNORE INTO source_documents ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                [row[col] for col in _COLUMNS],
            )
        return doc

    def get(self, document_id: str) -> SourceDocument | None:
        return next(self._query("document_id = ?", [document_id]), None)

    def find_by_native_id(self, source_native_id: str) -> SourceDocument | None:
        return next(
            self._query("source_native_id = ? ORDER BY retrieved_at DESC", [source_native_id]),
            None,
        )

    def _query(self, where: str, params: list[Any]) -> Iterator[SourceDocument]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM source_documents WHERE {where}", params
            )
            rows = cursor.fetchall()
        for row in rows:
            doc = dict(zip(_COLUMNS, row))
            doc["metadata"] = json.loads(doc.get("metadata") or "{}")
            yield doc  # type: ignore[misc]

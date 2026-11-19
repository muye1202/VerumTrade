"""Programmatic verification of source-linked facts.

This is the first of three verification levels: it checks source identity,
issuer, availability time, and that a quoted span still matches the stored
document byte for byte. It does not judge whether the passage *supports* a
proposition (semantic verification) or whether the evidence supports an
inference (mechanism review); those need a model or a reviewer and their own
evaluation against labeled cases.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from verumtrade.research.source_store import SourceStore, content_hash

VerificationStatus = Literal["verified", "failed", "unverifiable"]


class VerificationCheck(TypedDict):
    check: str
    passed: bool
    detail: str


class VerificationResult(TypedDict):
    status: VerificationStatus
    checks: list[VerificationCheck]


def _check(name: str, passed: bool, detail: str = "") -> VerificationCheck:
    return {"check": name, "passed": bool(passed), "detail": detail}


def _result(checks: list[VerificationCheck]) -> VerificationResult:
    return {"status": "verified" if all(c["passed"] for c in checks) else "failed", "checks": checks}


def _date_on_or_before(stamp: Any, as_of: str | None) -> bool:
    if not as_of:
        return True
    value = str(stamp or "")[:10]
    return bool(value) and value <= str(as_of)[:10]


def verify_document_span(
    provenance: dict[str, Any],
    *,
    store: SourceStore,
    expected_cik: str | None = None,
    as_of: str | None = None,
) -> VerificationResult:
    doc = store.get(str(provenance.get("document_id") or ""))
    if doc is None:
        return {
            "status": "unverifiable",
            "checks": [_check("document_stored", False, "source document is not in the source store")],
        }
    text = str(doc.get("text") or "")
    start, end = int(provenance.get("start", -1)), int(provenance.get("end", -1))
    quote = str(provenance.get("quote") or "")
    checks = [
        _check(
            "content_hash",
            content_hash(text) == doc.get("content_hash") == provenance.get("content_hash"),
            "stored text hash matches the hash recorded with the claim",
        ),
        _check(
            "span_matches_quote",
            0 <= start < end <= len(text) and text[start:end] == quote,
            f"document[{start}:{end}] equals the quoted passage",
        ),
        _check(
            "availability",
            _date_on_or_before(doc.get("available_at") or doc.get("published_at"), as_of),
            f"available {doc.get('available_at') or doc.get('published_at')} vs as-of {as_of}",
        ),
    ]
    if expected_cik:
        checks.append(
            _check(
                "issuer",
                str(doc.get("issuer_cik") or "") == str(expected_cik),
                f"document issuer CIK {doc.get('issuer_cik')} vs expected {expected_cik}",
            )
        )
    return _result(checks)


def verify_xbrl_fact(
    provenance: dict[str, Any],
    *,
    expected_cik: str | None = None,
    as_of: str | None = None,
) -> VerificationResult:
    checks = [
        _check("unit_present", bool(provenance.get("unit")), f"unit {provenance.get('unit')}"),
        _check("period_present", bool(provenance.get("period_end")), f"period end {provenance.get('period_end')}"),
        _check(
            "availability",
            _date_on_or_before(provenance.get("first_filed"), as_of),
            f"first filed {provenance.get('first_filed')} vs as-of {as_of}",
        ),
        _check("accession_present", bool(provenance.get("accession")), str(provenance.get("accession") or "")),
    ]
    if provenance.get("derived"):
        checks.append(
            _check(
                "derivation_inputs",
                bool(provenance.get("formula")) and len(provenance.get("input_accessions") or []) >= 2,
                f"{provenance.get('formula')} from {provenance.get('input_accessions')}",
            )
        )
    if expected_cik:
        checks.append(
            _check(
                "issuer",
                str(provenance.get("issuer_cik") or "") == str(expected_cik),
                f"fact issuer CIK {provenance.get('issuer_cik')} vs expected {expected_cik}",
            )
        )
    return _result(checks)


def verify_provenance(
    provenance: dict[str, Any] | None,
    *,
    store: SourceStore | None = None,
    expected_cik: str | None = None,
    as_of: str | None = None,
) -> VerificationResult:
    kind = str((provenance or {}).get("kind") or "")
    if kind == "document_span" and store is not None:
        return verify_document_span(provenance or {}, store=store, expected_cik=expected_cik, as_of=as_of)
    if kind == "xbrl_fact":
        return verify_xbrl_fact(provenance or {}, expected_cik=expected_cik, as_of=as_of)
    if kind == "filing_record":
        return _result(
            [
                _check(
                    "availability",
                    _date_on_or_before(provenance.get("available_at"), as_of),
                    f"accepted {provenance.get('available_at')} vs as-of {as_of}",
                ),
                _check(
                    "issuer",
                    not expected_cik or str(provenance.get("issuer_cik") or "") == str(expected_cik),
                    f"filing issuer CIK {provenance.get('issuer_cik')} vs expected {expected_cik}",
                ),
            ]
        )
    return {"status": "unverifiable", "checks": [_check("provenance_kind", False, f"unsupported kind '{kind}'")]}

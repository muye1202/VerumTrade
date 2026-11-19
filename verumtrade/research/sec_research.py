"""SEC research operations that return source-linked evidence packets.

Every packet has the shape the evidence graph already ingests (``facts`` with
``id``/``claim``), plus a capability ``envelope`` and, on each fact, a
``provenance`` block recording exactly where the fact came from and the result of
programmatic verification.
"""

from __future__ import annotations

from typing import Any, Iterable

from verumtrade.research.document_reader import search_passages
from verumtrade.research.envelope import build_envelope
from verumtrade.research.sec_client import FilingRecord, SecClient, SecClientError
from verumtrade.research.source_store import SourceStore, make_document
from verumtrade.research.verification import verify_provenance
from verumtrade.research.xbrl import DEFAULT_METRICS, describe_fact, facts_as_of

DEFAULT_FORMS = ("10-K", "10-Q", "8-K", "10-K/A", "10-Q/A")
MAX_EXHIBITS = 3


# Each fact's ``as_of`` is the run's observation date (what was knowable then); when the
# underlying document was published or accepted is kept separately in ``provenance``.


def _error_packet(bundle: str, ticker: str, as_of: str, issue: str) -> dict[str, Any]:
    return {
        "bundle": bundle,
        "symbol": ticker,
        "date": as_of,
        "facts": [],
        "missing_data": [{"section": bundle, "issue": issue}],
    }


def _attach_verification(fact: dict[str, Any], store: SourceStore | None, cik: str, as_of: str) -> dict[str, Any]:
    result = verify_provenance(fact["provenance"], store=store, expected_cik=cik, as_of=as_of)
    fact["provenance"]["verification"] = result
    if result["status"] == "failed":
        fact["quality"] = "contradictory"
        fact["confidence"] = min(float(fact.get("confidence", 0.5)), 0.3)
    return fact


def list_filings_packet(
    ticker: str,
    as_of: str,
    *,
    client: SecClient,
    form_types: Iterable[str] | None = None,
    limit: int = 8,
    domain: str = "fundamentals",
) -> dict[str, Any]:
    bundle = "SecFilingList"
    try:
        filings = client.list_filings(ticker, as_of=as_of, form_types=form_types or DEFAULT_FORMS, limit=limit)
    except SecClientError as exc:
        return _error_packet(bundle, ticker, as_of, str(exc))
    facts = []
    for filing in filings:
        acc = filing["accession_number"].replace("-", "")
        items = f" items {filing['items']}" if filing.get("items") else ""
        claim = (
            f"{filing['company_name']} filed {filing['form_type']}{items} on {filing['filing_date']}"
            + (f" for period {filing['report_date']}" if filing.get("report_date") else "")
            + f" (accession {filing['accession_number']})."
        )
        fact = {
            "id": f"fact_sec_filing_{acc}",
            "domain": domain,
            "claim": claim,
            "text": claim,
            "source": "list_sec_filings",
            "section": filing["form_type"],
            "as_of": as_of,
            "confidence": 0.95,
            "quality": "normal",
            "source_type": "structured_filing_data",
            "provenance": {
                "kind": "filing_record",
                "issuer_cik": filing["cik"],
                "accession": filing["accession_number"],
                "canonical_url": filing.get("primary_document_url", ""),
                "available_at": filing.get("accepted_at") or filing.get("filing_date"),
            },
        }
        facts.append(_attach_verification(fact, None, filing["cik"], as_of))
    identity = client.resolve_cik(ticker) or {}
    return {
        "bundle": bundle,
        "symbol": ticker,
        "date": as_of,
        "issuer": {"cik": identity.get("cik", ""), "name": identity.get("name", "")},
        "envelope": build_envelope(
            "sec_edgar_submissions",
            "structured_filing_data",
            "point_in_time",
            as_of=as_of,
            available_fields=["form", "filing_date", "accepted_at", "report_date", "items", "accession"],
            notes=["Issuer resolved by CIK; only filings accepted on or before the as-of date are listed."],
        ),
        "filings": filings,
        "facts": facts,
        "missing_data": [] if filings else [{"section": bundle, "issue": "no matching filings before as-of date"}],
        "instruction": "Use read_sec_filing with an accession_number to read the filing text.",
    }


def _find_filing(client: SecClient, ticker: str, as_of: str, accession: str) -> FilingRecord | None:
    target = accession.replace("-", "").strip()
    for filing in client.list_filings(ticker, as_of=as_of, limit=400):
        if filing["accession_number"].replace("-", "") == target:
            return filing
    return None


def _stored_document(
    client: SecClient,
    store: SourceStore,
    filing: FilingRecord,
    filename: str,
) -> dict[str, Any]:
    native_id = f"{filing['accession_number']}/{filename}"
    existing = store.find_by_native_id(native_id)
    if existing is not None:
        return existing
    url, text = client.fetch_document_text(filing, filename)
    doc = make_document(
        canonical_url=url,
        issuer_cik=filing["cik"],
        issuer_name=filing.get("company_name", ""),
        ticker=filing.get("ticker", ""),
        publisher="SEC EDGAR",
        document_type=filing["form_type"],
        source_native_id=native_id,
        published_at=filing.get("filing_date", ""),
        available_at=filing.get("accepted_at") or filing.get("filing_date", ""),
        period_of_report=filing.get("report_date", ""),
        source_version="sec_edgar_archives_v1",
        text=text,
        metadata={"filename": filename, "items": filing.get("items", "")},
    )
    return store.put(doc)


def read_filing_packet(
    ticker: str,
    as_of: str,
    accession: str,
    question: str,
    *,
    client: SecClient,
    store: SourceStore,
    section: str = "",
    document: str = "",
    max_passages: int = 4,
    domain: str = "fundamentals",
) -> dict[str, Any]:
    bundle = "SecFilingPassages"
    try:
        filing = _find_filing(client, ticker, as_of, accession)
    except SecClientError as exc:
        return _error_packet(bundle, ticker, as_of, str(exc))
    if filing is None:
        return _error_packet(
            bundle,
            ticker,
            as_of,
            f"accession {accession} is not a {ticker} filing available on or before {as_of}; "
            "refusing to read another issuer's or a later document",
        )

    filenames = [document] if document else [filing.get("primary_document", "")]
    if not document and filing["form_type"].startswith("8-K"):
        filenames.extend(client.filing_exhibits(filing)[:MAX_EXHIBITS])
    documents, missing = [], []
    for name in [n for n in filenames if n]:
        try:
            documents.append(_stored_document(client, store, filing, name))
        except Exception as exc:  # network or parse failure for one exhibit
            missing.append({"section": name, "issue": f"document fetch failed: {exc}"})

    ranked = []
    for doc in documents:
        for passage in search_passages(doc["text"], question, top_k=max_passages, section=section or None):
            ranked.append((passage["score"], doc, passage))
    ranked.sort(key=lambda item: -item[0])

    facts = []
    for _score, doc, passage in ranked[:max_passages]:
        acc = filing["accession_number"].replace("-", "")
        quote = doc["text"][passage["start"] : passage["end"]]
        fact = {
            "id": f"fact_sec_{acc}_{doc['document_id'][4:12]}_{passage['passage_id']}",
            "domain": domain,
            "claim": " ".join(quote.split())[:320],
            "text": quote,
            "source": "read_sec_filing",
            "section": passage.get("section") or filing["form_type"],
            "as_of": as_of,
            "confidence": 0.9,
            "quality": "normal",
            "source_type": "primary_document",
            "provenance": {
                "kind": "document_span",
                "document_id": doc["document_id"],
                "content_hash": doc["content_hash"],
                "canonical_url": doc["canonical_url"],
                "issuer_cik": doc["issuer_cik"],
                "accession": filing["accession_number"],
                "start": passage["start"],
                "end": passage["end"],
                "quote": quote,
                "section": passage.get("section", ""),
                "retrieval_score": passage["score"],
            },
        }
        facts.append(_attach_verification(fact, store, filing["cik"], as_of))

    if documents and not facts:
        missing.append({"section": bundle, "issue": f"no passage in the filing matches: {question}"})
    return {
        "bundle": bundle,
        "symbol": ticker,
        "date": as_of,
        "filing": filing,
        "question": question,
        "envelope": build_envelope(
            "sec_edgar_archives",
            "primary_document",
            "point_in_time",
            as_of=as_of,
            available_fields=["passage_text", "character_span", "section"],
            notes=[
                "Passages are ranked by lexical relevance; relevance is not support. "
                "Cite a passage only for what it actually states.",
            ],
        ),
        "documents": [
            {
                "document_id": doc["document_id"],
                "canonical_url": doc["canonical_url"],
                "content_hash": doc["content_hash"],
                "chars": len(doc["text"]),
            }
            for doc in documents
        ],
        "facts": facts,
        "missing_data": missing,
    }


def financial_facts_packet(
    ticker: str,
    as_of: str,
    *,
    client: SecClient,
    metrics: Iterable[str] | None = None,
    periods: int = 4,
    domain: str = "fundamentals",
) -> dict[str, Any]:
    bundle = "SecXbrlFacts"
    identity = None
    try:
        identity = client.resolve_cik(ticker)
        if not identity:
            raise SecClientError(f"No SEC CIK mapping for ticker '{ticker}'")
        companyfacts = client.company_facts(identity["cik"])
    except SecClientError as exc:
        return _error_packet(bundle, ticker, as_of, str(exc))
    requested = list(metrics or DEFAULT_METRICS)
    xbrl_facts = facts_as_of(companyfacts, as_of, metrics=requested, periods=periods)
    facts = []
    for item in xbrl_facts:
        fact_id = (
            f"fact_xbrl_{item['concept']}_{item['period_type']}_{item['period_end']}_{item['unit']}"
            .replace("/", "_")
        )
        fact = {
            "id": fact_id,
            "domain": domain,
            "claim": describe_fact(item),
            "text": describe_fact(item),
            "source": "get_sec_financial_facts",
            "section": item["metric"],
            "as_of": as_of,
            "confidence": 0.95,
            "quality": "normal",
            "source_type": "structured_filing_data",
            "provenance": {"kind": "xbrl_fact", "issuer_cik": identity["cik"], **item},
        }
        facts.append(_attach_verification(fact, None, identity["cik"], as_of))
    found = {item["metric"] for item in xbrl_facts}
    missing = [
        {"section": metric, "issue": "no standard XBRL fact filed on or before as-of date"}
        for metric in requested
        if metric not in found
    ]
    return {
        "bundle": bundle,
        "symbol": ticker,
        "date": as_of,
        "issuer": {"cik": identity["cik"], "name": identity.get("name", "")},
        "envelope": build_envelope(
            "sec_xbrl_companyfacts",
            "structured_filing_data",
            "point_in_time",
            as_of=as_of,
            available_fields=["value", "unit", "period_start", "period_end", "period_type", "form", "filed"],
            missing_fields=[m["section"] for m in missing],
            notes=[
                "Only facts filed on or before the as-of date are used; restated periods keep the original value.",
                "Standard US-GAAP concepts only: segments and custom metrics require read_sec_filing.",
            ],
        ),
        "facts": facts,
        "missing_data": missing,
    }

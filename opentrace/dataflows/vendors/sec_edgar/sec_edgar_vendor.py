"""
SEC EDGAR real-time filing monitor.

Free, no API key. Only requirement: set User-Agent header to
identify yourself (SEC policy, not authentication).
Headers: {"User-Agent": "YourApp/1.0 (your-email@example.com)"}
Rate limit: 10 requests/second (generous).
"""

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

SEC_HEADERS = {
    "User-Agent": "opentrace/1.0 (admin@opentrace.com)",
    "Accept": "application/json",
}

# --- Filing types that generate tradeable catalysts ---
CATALYST_FORM_TYPES = {
    "8-K": "material_event",     # Earnings, M&A, guidance, exec changes
    "4": "insider_trade",         # Insider buys/sells (within 2 business days)
    "SC 13D": "activist_position", # >5% stake with activist intent
    "SC 13G": "passive_stake",    # >5% passive stake
    "S-3": "shelf_offering",      # Potential dilution signal
}

# --- 8-K item codes that map to specific catalyst types ---
ITEM_CATALYST_MAP = {
    "2.02": "earnings_release",         # Results of Operations
    "1.01": "material_agreement",       # Entry into Material Agreement (M&A, partnerships)
    "1.02": "contract_termination",     # Termination of Material Agreement
    "2.01": "acquisition_disposition",  # Completion of Acquisition or Disposition
    "2.05": "restructuring",            # Costs from Exit Activities
    "2.06": "asset_impairment",         # Material Impairments
    "3.01": "delisting_notice",         # Notice of Delisting
    "5.02": "executive_change",         # Departure/Appointment of Officers
    "7.01": "regulation_fd",            # Reg FD Disclosure (guidance, presentations)
    "8.01": "other_material_event",     # Other Events (catch-all for important items)
}


def fetch_recent_filings(
    ticker: Optional[str] = None,
    curr_date: Optional[str] = None,
    form_types: Optional[List[str] | str] = None,
    lookback_days: int = 45,
    lookback_hours: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Fetch recent SEC filings via EDGAR full-text search.

    Returns structured filing metadata with ticker mapping.
    """
    if form_types is None:
        form_types = ["10-Q", "10-K", "8-K"]
    elif isinstance(form_types, str):
        form_types = [form_types]

    if curr_date:
        try:
            end_dt = datetime.strptime(str(curr_date)[:10], "%Y-%m-%d")
        except Exception:
            end_dt = datetime.utcnow()
    else:
        end_dt = datetime.utcnow()
    if lookback_hours is not None:
        start_dt = end_dt - timedelta(hours=int(lookback_hours))
    else:
        start_dt = end_dt - timedelta(days=int(lookback_days))

    results = []
    for form_type in form_types:
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?forms={form_type}"
            f"&dateRange=custom"
            f"&startdt={start_dt.strftime('%Y-%m-%d')}"
            f"&enddt={end_dt.strftime('%Y-%m-%d')}"
        )
        if ticker:
            url += f"&q={ticker}"
        try:
            resp = requests.get(url, headers=SEC_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            for hit in data.get("hits", {}).get("hits", [])[:limit]:
                source = hit.get("_source", {})
                results.append({
                    "form_type": form_type,
                    "catalyst_category": CATALYST_FORM_TYPES.get(form_type, "unknown"),
                    "company_name": source.get("display_names", [""])[0] if source.get("display_names") else "",
                    "ticker": _resolve_ticker_from_cik(source.get("entity_id")),
                    "filed_at": source.get("file_date"),
                    "description": source.get("display_description", ""),
                    "url": f"https://www.sec.gov/Archives/edgar/data/{source.get('entity_id')}/{source.get('adsh', '').replace('-', '')}/" if source.get('entity_id') and source.get('adsh') else "",
                })
        except Exception as e:
            continue

    return results


def fetch_company_filings(
    cik: str,
    form_types: Optional[List[str]] = None,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Fetch recent filings for a specific company by CIK."""
    cik_padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        descriptions = recent.get("primaryDocDescription", [])
        accessions = recent.get("accessionNumber", [])

        results = []
        for i, form in enumerate(forms[:50]):
            if form_types and form not in form_types:
                continue
            results.append({
                "form_type": form,
                "filed_at": dates[i] if i < len(dates) else "",
                "description": descriptions[i] if i < len(descriptions) else "",
                "accession": accessions[i] if i < len(accessions) else "",
                "ticker": data.get("tickers", [""])[0] if data.get("tickers") else "",
                "company_name": data.get("name", ""),
            })
            if len(results) >= limit:
                break

        return results
    except Exception:
        return []


def _resolve_ticker_from_cik(cik: str) -> str:
    """Look up ticker from CIK using SEC company tickers JSON."""
    # SEC provides a complete CIK-to-ticker mapping, cached daily:
    # https://www.sec.gov/files/company_tickers.json
    # In production, cache this file and look up from the cache.
    # For now, this is a placeholder
    return ""

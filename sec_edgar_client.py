from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from functools import lru_cache


SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKER_LIST_URL = "https://www.sec.gov/files/company_tickers.json"
FILING_DOCUMENT_URL = "https://www.sec.gov/Archives/edgar/data/{cik_no_zeros}/{accession_no_dashes}/{primary_document}"
DEFAULT_USER_AGENT = "SEC Financial Screening MVP contact@example.com"
REQUEST_TIMEOUT_SECONDS = 75
REQUEST_RETRIES = 3


@dataclass
class CompanyMatch:
    cik: str
    ticker: str
    company_name: str
    sic: str | None
    sic_description: str | None


def get_user_agent() -> str:
    return os.getenv("SEC_USER_AGENT", DEFAULT_USER_AGENT)


def req_get_json(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> dict:
    return json.loads(req_get_text(url, timeout=timeout))


def req_get_text(url: str, timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    last_error = None
    for attempt in range(REQUEST_RETRIES):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": get_user_agent(),
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_error = exc
            if attempt < REQUEST_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Request failed after {REQUEST_RETRIES} attempts: {url} ({last_error})") from last_error


def normalize_name(name: str) -> str:
    return "".join(ch for ch in (name or "").upper() if ch.isalnum())


@lru_cache(maxsize=1)
def fetch_ticker_universe() -> list[dict]:
    payload = req_get_json(TICKER_LIST_URL)
    rows = list(payload.values()) if isinstance(payload, dict) else payload
    universe = []
    for row in rows:
        cik = str(row.get("cik_str") or "").strip()
        if not cik:
            continue
        universe.append(
            {
                "cik": cik.zfill(10),
                "ticker": str(row.get("ticker") or "").upper(),
                "title": str(row.get("title") or "").strip(),
                "norm_title": normalize_name(str(row.get("title") or "")),
            }
        )
    return universe


def search_companies(query: str, limit: int = 10) -> list[CompanyMatch]:
    query = (query or "").strip()
    if not query:
        return []

    universe = fetch_ticker_universe()
    norm_query = normalize_name(query)
    upper_query = query.upper()

    exact_ticker = [row for row in universe if row["ticker"] == upper_query]
    exact_name = [row for row in universe if row["norm_title"] == norm_query and row not in exact_ticker]
    contains = [
        row for row in universe
        if (upper_query in row["ticker"] or norm_query in row["norm_title"])
        and row not in exact_ticker
        and row not in exact_name
    ]
    ranked = exact_ticker + exact_name + contains

    results: list[CompanyMatch] = []
    for row in ranked[:limit]:
        meta = fetch_company_submissions(row["cik"])
        results.append(
            CompanyMatch(
                cik=row["cik"],
                ticker=row["ticker"],
                company_name=meta.get("name") or row["title"],
                sic=meta.get("sic"),
                sic_description=meta.get("sicDescription"),
            )
        )
        time.sleep(0.12)
    return results


@lru_cache(maxsize=256)
def fetch_company_submissions(cik: str) -> dict:
    return req_get_json(SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))


@lru_cache(maxsize=256)
def fetch_company_facts(cik: str) -> dict:
    return req_get_json(COMPANYFACTS_URL.format(cik=str(cik).zfill(10)))


@lru_cache(maxsize=512)
def fetch_filing_document_html(cik: str, accession_number: str) -> str:
    normalized_accession = str(accession_number or "").strip()
    if not normalized_accession:
        raise ValueError("Missing SEC accession number.")

    submissions = fetch_company_submissions(str(cik).zfill(10))
    recent = ((submissions.get("filings") or {}).get("recent") or {})
    accessions = recent.get("accessionNumber") or []
    primary_documents = recent.get("primaryDocument") or []

    primary_document = None
    for index, accession in enumerate(accessions):
        if accession == normalized_accession and index < len(primary_documents):
            primary_document = primary_documents[index]
            break
    if not primary_document:
        raise ValueError(f"Primary document not found for accession {normalized_accession}.")

    url = FILING_DOCUMENT_URL.format(
        cik_no_zeros=str(int(str(cik))),
        accession_no_dashes=normalized_accession.replace("-", ""),
        primary_document=primary_document,
    )
    return req_get_text(url)

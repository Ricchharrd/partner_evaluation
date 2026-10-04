from __future__ import annotations

from .legacy_sec.sec_edgar_client import CompanyMatch, search_companies
from .legacy_sec.sec_screening import screen_companies
from .schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument


SEC_ITEM_MAP = {
    "revenue": "revenue",
    "operating_income": "operating_income",
    "net_income": "net_income",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "total_equity": "total_equity",
    "current_assets": "current_assets",
    "current_liabilities": "current_liabilities",
    "cash": "cash",
    "accounts_receivable": "accounts_receivable",
    "financial_debt": "financial_debt",
    "interest_expense_abs": "interest_expense",
    "operating_cash_flow": "operating_cash_flow",
    "retained_earnings": "retained_earnings",
}


def candidate_rows(query: str, limit: int = 8) -> tuple[list[CompanyMatch], list[dict]]:
    matches = search_companies(query, limit=limit)
    return matches, [
        {
            "법인명": item.company_name,
            "Ticker": item.ticker,
            "CIK": item.cik,
            "SIC": item.sic or "-",
            "업종": item.sic_description or "-",
        }
        for item in matches
    ]


def collect_sec_project(match: CompanyMatch, start_year: int, end_year: int) -> tuple[AnalysisProject, list[str]]:
    results, errors = screen_companies([match], start_year, end_year)
    warnings = [error.error_message for error in errors]
    if not results:
        raise ValueError("선택한 기간에 사용할 수 있는 SEC 연차 재무값이 없습니다. " + " ".join(warnings))

    entity = EntityProfile(
        legal_name=match.company_name,
        country="United States",
        identifiers={"ticker": match.ticker, "cik": match.cik},
        industry=match.sic_description or "",
        entity_type="금융회사" if match.sic and str(match.sic).startswith("6") else "일반기업",
        reporting_scope="연결",
        accounting_standard="US GAAP",
    )
    project = AnalysisProject(title=f"{match.company_name} 공개 재무분석", entity=entity)
    source = SourceDocument(
        name=f"SEC companyfacts CIK {match.cik}",
        source_type="SEC API",
        url=f"https://data.sec.gov/api/xbrl/companyfacts/CIK{match.cik}.json",
        mime_type="application/json",
        status="자동수집",
        note="SEC EDGAR companyfacts 및 필요한 경우 원문 10-K 보조 파싱",
    )
    project.sources.append(source)

    for result in results:
        for sec_key, item_key in SEC_ITEM_MAP.items():
            value = result.metrics.get(sec_key)
            project.facts.append(
                FinancialFact(
                    entity_id=entity.entity_id,
                    fiscal_year=result.fiscal_year,
                    standard_item=item_key,
                    original_label=sec_key,
                    original_value=value,
                    normalized_value=value,
                    currency="USD",
                    period_start=result.period_start or "",
                    period_end=result.period_end or "",
                    reporting_scope="연결",
                    accounting_standard="US GAAP",
                    source_id=source.source_id,
                    source_locator=(result.notes or {}).get(sec_key, "SEC companyfacts") + f" | {result.form} filed {result.filed}",
                    published_at=result.filed,
                    report_version=result.form,
                    extraction_method="SEC XBRL 구조 파싱",
                    validation_status="검토 필요",
                )
            )

    if entity.entity_type == "금융회사":
        warnings.append("금융회사 SIC로 식별되었습니다. 재무비율의 해석에는 별도 기준이 필요합니다.")
    return project, warnings

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import json
import urllib.parse
import urllib.request
import zipfile

from lxml import etree

from .ingest import normalize_item, parse_number
from .schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument


DART_BASE = "https://opendart.fss.or.kr/api"
ACCOUNT_ID_MAP = {
    "ifrs-full_Revenue": "revenue",
    "ifrs-full_OperatingProfitLoss": "operating_income",
    "ifrs-full_ProfitLoss": "net_income",
    "ifrs-full_Assets": "total_assets",
    "ifrs-full_Liabilities": "total_liabilities",
    "ifrs-full_Equity": "total_equity",
    "ifrs-full_CurrentAssets": "current_assets",
    "ifrs-full_CurrentLiabilities": "current_liabilities",
    "ifrs-full_CashAndCashEquivalents": "cash",
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cash_flow",
    "dart_OperatingIncomeLoss": "operating_income",
}


def _get(url: str, params: dict, timeout: int = 30) -> bytes:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "Partner Financial Workbench/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


@lru_cache(maxsize=4)
def _corp_codes(api_key: str) -> list[dict]:
    payload = _get(f"{DART_BASE}/corpCode.xml", {"crtfc_key": api_key})
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        xml_name = next(name for name in archive.namelist() if name.lower().endswith(".xml"))
        root = etree.fromstring(archive.read(xml_name))
    return [
        {
            "corp_code": node.findtext("corp_code") or "",
            "corp_name": node.findtext("corp_name") or "",
            "stock_code": node.findtext("stock_code") or "",
            "modify_date": node.findtext("modify_date") or "",
        }
        for node in root.findall("list")
    ]


def _normalize_name(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def search_dart_companies(api_key: str, query: str, limit: int = 10) -> tuple[list[dict], list[dict]]:
    if not api_key:
        raise ValueError("DART_API_KEY가 필요합니다.")
    normalized = _normalize_name(query)
    ranked = sorted(
        [row for row in _corp_codes(api_key) if normalized in _normalize_name(row["corp_name"]) or query.strip() == row["stock_code"]],
        key=lambda row: (0 if _normalize_name(row["corp_name"]) == normalized else 1, 0 if row["stock_code"] else 1, row["corp_name"]),
    )[:limit]
    return ranked, [
        {"법인명": row["corp_name"], "종목코드": row["stock_code"] or "비상장", "고유번호": row["corp_code"], "최종변경": row["modify_date"]}
        for row in ranked
    ]


@lru_cache(maxsize=128)
def _fetch_financial_rows(api_key: str, corp_code: str, year: int, scope: str) -> list[dict]:
    payload = json.loads(
        _get(
            f"{DART_BASE}/fnlttSinglAcntAll.json",
            {"crtfc_key": api_key, "corp_code": corp_code, "bsns_year": year, "reprt_code": "11011", "fs_div": scope},
        ).decode("utf-8")
    )
    if payload.get("status") == "013":
        return []
    if payload.get("status") != "000":
        raise RuntimeError(f"OpenDART 오류 {payload.get('status')}: {payload.get('message')}")
    return payload.get("list") or []


def collect_dart_project(api_key: str, company: dict, start_year: int, end_year: int, scope: str = "CFS") -> tuple[AnalysisProject, list[str]]:
    entity = EntityProfile(
        legal_name=company["corp_name"],
        country="Korea",
        identifiers={"corp_code": company["corp_code"], "stock_code": company.get("stock_code") or ""},
        reporting_scope="연결" if scope == "CFS" else "별도",
        accounting_standard="K-IFRS",
    )
    project = AnalysisProject(title=f"{entity.legal_name} 공개 재무분석", entity=entity)
    source = SourceDocument(
        name=f"OpenDART 단일회사 전체 재무제표 {company['corp_code']}",
        source_type="OpenDART API",
        url="https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
        mime_type="application/json",
        status="자동수집",
        note="API 키는 URL과 저장자료에 기록하지 않습니다.",
    )
    project.sources.append(source)
    warnings = []

    for year in range(start_year, end_year + 1):
        rows = _fetch_financial_rows(api_key, company["corp_code"], year, scope)
        if not rows:
            warnings.append(f"FY{year} {scope} 연차 재무제표가 없습니다.")
            continue
        picked = set()
        for row in rows:
            item = ACCOUNT_ID_MAP.get(row.get("account_id") or "") or normalize_item(row.get("account_nm") or "")
            if not item or item in picked:
                continue
            value = parse_number(row.get("thstrm_amount"))
            if value is None:
                continue
            picked.add(item)
            project.facts.append(
                FinancialFact(
                    entity_id=entity.entity_id,
                    fiscal_year=year,
                    standard_item=item,
                    original_label=row.get("account_nm") or item,
                    original_value=value,
                    normalized_value=value,
                    currency="KRW",
                    period_start=row.get("thstrm_dt") or "",
                    period_end=row.get("thstrm_dt") or "",
                    reporting_scope=entity.reporting_scope,
                    accounting_standard="K-IFRS",
                    source_id=source.source_id,
                    source_locator=f"account_id={row.get('account_id')}; sj_div={row.get('sj_div')}; ord={row.get('ord')}; rcept_no={row.get('rcept_no')}",
                    report_version=row.get("rcept_no") or "",
                    extraction_method="OpenDART 구조 파싱",
                    validation_status="검토 필요",
                )
            )
    if not project.facts:
        raise ValueError("선택한 기간에 OpenDART 재무값을 찾지 못했습니다.")
    return project, warnings

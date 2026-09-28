"""Discover configured sources without requiring users to choose an API."""
from datetime import date
import re

from .sec_adapter import candidate_rows, collect_sec_project
from .dart_adapter import search_dart_companies, collect_dart_project, _fetch_financial_rows
from .legacy_sec.sec_edgar_client import fetch_company_facts
from .legacy_sec.sec_screening import _is_target_annual_row


def find_candidates(query, dart_key=""):
    query = query.strip()
    if not query:
        raise ValueError("기업명을 입력하십시오.")
    candidates, notices = [], []
    korean = bool(re.search(r"[가-힣]", query)) or bool(re.fullmatch(r"\d{6}", query))
    if dart_key:
        try:
            matches, _ = search_dart_companies(dart_key, query, limit=5)
            candidates.extend({"source": "DART", "match": m, "label": f"{m['corp_name']} · {m.get('stock_code') or '비상장'} · DART"} for m in matches)
        except Exception:
            notices.append("DART 검색 연결에 실패했습니다. 자료가 없다는 뜻은 아닙니다.")
    elif korean:
        notices.append("DART 연결이 설정되지 않아 국내 공시를 검색하지 못했습니다.")
    if not korean:
        try:
            matches, _ = candidate_rows(query, limit=5)
            candidates.extend({"source": "SEC", "match": m, "label": f"{m.company_name} · {m.ticker} · SEC"} for m in matches)
        except Exception:
            notices.append("SEC 검색 연결에 실패했습니다. 자료가 없다는 뜻은 아닙니다.")
    return candidates, notices


def latest_sec_year(facts):
    years = set()
    for taxonomy in facts.get("facts", {}).values():
        for concept in taxonomy.values():
            for rows in concept.get("units", {}).values():
                for row in rows:
                    year = row.get("fy")
                    if isinstance(year, int) and 2000 <= year <= date.today().year and _is_target_annual_row(row, year) and row.get("start"):
                        try:
                            duration = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
                        except (ValueError, KeyError):
                            continue
                        if 330 <= duration <= 380:
                            years.add(year)
    if not years:
        raise ValueError("연간 재무자료의 회계연도를 확인하지 못했습니다. 보고서 파일을 이용하십시오.")
    return max(years)


def collect_latest(candidate, dart_key=""):
    if candidate["source"] == "SEC":
        year = latest_sec_year(fetch_company_facts(candidate["match"].cik))
        project, warnings = collect_sec_project(candidate["match"], year - 2, year)
    else:
        company = candidate["match"]
        year, scope = None, None
        for target in range(date.today().year, date.today().year - 5, -1):
            for basis in ("CFS", "OFS"):
                if _fetch_financial_rows(dart_key, company["corp_code"], target, basis):
                    year, scope = target, basis
                    break
            if year is not None:
                break
        if year is None:
            raise ValueError("최근 5개 회계연도에 수집 가능한 연차 재무자료를 찾지 못했습니다.")
        project, warnings = collect_dart_project(dart_key, company, year - 2, year, scope)
        if scope == "OFS":
            warnings.append("최신 연도 연결자료가 없어 별도재무제표 기준으로 수집했습니다. 연결자료와 혼합하지 않습니다.")
    project.narrative["automatic_period"] = {"start": year - 2, "end": year}
    return project, warnings

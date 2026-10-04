from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json

from .analysis import build_basic_narrative, calculate_ratios, context_blocks
from .schema import utc_now, ValidationIssue
from .validation import validate_facts
from .legacy_sec.sec_fx import HARDCODED_USD_KRW_RATES
from .account_guards import normalize_scope

WORKFLOW_VERSION = 3


def filter_interim_comparatives(existing, incoming):
    """Keep an existing full year intact when a later interim filing repeats prior-year columns."""
    if not incoming:
        return incoming, []

    def duration(fact):
        if not fact.period_start or not fact.period_end or fact.period_start == fact.period_end:
            return None
        try:
            return (date.fromisoformat(fact.period_end) - date.fromisoformat(fact.period_start)).days
        except ValueError:
            return None

    flow_items = {"revenue", "operating_income", "net_income", "operating_cash_flow"}
    latest_year = max(fact.fiscal_year for fact in incoming)
    is_interim = any(fact.fiscal_year == latest_year and fact.standard_item in flow_items
                     and (days := duration(fact)) is not None and 0 < days < 330 for fact in incoming)
    if not is_interim:
        return incoming, []
    annual_contexts = {(fact.fiscal_year, fact.reporting_scope, fact.currency) for fact in existing
                       if fact.standard_item in flow_items and (days := duration(fact)) is not None
                       and 330 <= days <= 380}
    kept = [fact for fact in incoming if not (fact.fiscal_year < latest_year and
            (fact.fiscal_year, fact.reporting_scope, fact.currency) in annual_contexts)]
    omitted = len(incoming) - len(kept)
    warning = [f"기존 연간 실적과 혼합하지 않도록 반기 보고서의 전년 비교값 {omitted}건을 저장에서 제외했습니다."] if omitted else []
    return kept, warning


def input_digest(project):
    payload = {"entity": project.entity.__dict__, "facts": [f.__dict__ for f in project.facts],
               "sources": [s.__dict__ for s in project.sources], "versions": project.versions, "guard_version": 2}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def log_action(project, action, detail):
    project.narrative.setdefault("audit_log", []).append({"at": utc_now(), "action": action, "detail": detail})
    project.touch()


def invalidate(project):
    for key in ("final", "ai_cache", "basic", "policy_evaluation", "legacy_company_rating", "calculated_digest", "review"):
        project.narrative.pop(key, None)
    project.ratios = []
    project.validations = []
    project.status = "재검토 필요"


def recalculate(project):
    invalidate(project)
    for fact in project.facts:
        fact.reporting_scope = normalize_scope(fact.reporting_scope)
    project.validations = validate_facts(project.facts)
    for year, reason in context_blocks(project.facts).items():
        reasons = set(reason.split(", "))
        if reasons == {"연간 회계기간 아님"}:
            project.validations.append(ValidationIssue(
                "INTERIM_PERIOD", "정보", year,
                "중간 재무제표입니다. 수치는 보존하되 연간 재무비율 계산과 연간 실적 비교는 보류합니다."))
        else:
            project.validations.append(ValidationIssue("CONTEXT_BLOCK", "오류", year, reason))
    project.ratios = calculate_ratios(project.facts)
    project.narrative["basic"] = build_basic_narrative(project.facts, project.ratios)
    fx_rows = []
    for year in sorted({f.fiscal_year for f in project.facts}):
        currencies = {f.currency for f in project.facts if f.fiscal_year == year}
        if currencies == {"USD"} and year in HARDCODED_USD_KRW_RATES:
            rate = HARDCODED_USD_KRW_RATES[year]
            fx_rows.append({"연도": year, "평균 USD/KRW": rate["average"], "기말 USD/KRW": rate["closing"],
                            "기말 관측일": rate["closing_date"], "기준": "기존 FRED 역년 환율표. 개별 회계기간 환율이 아닌 잠정 대용치"})
    project.narrative["fx_display"] = fx_rows
    error_years = {issue.fiscal_year for issue in project.validations if issue.severity == "오류"}
    for ratio in project.ratios:
        if ratio.fiscal_year in error_years:
            ratio.value = None
            ratio.status = "검증 오류로 보류"
    project.narrative["basic"] = build_basic_narrative(project.facts, project.ratios)
    project.versions.pop("rating_policy", None)
    project.narrative["calculated_digest"] = input_digest(project)
    project.narrative["calculated_at"] = utc_now()
    project.status = "검토 중"
    log_action(project, "재계산", "현재 입력 기준으로 계산 및 이전 AI 설명 무효화")


def is_current(project):
    return project.narrative.get("calculated_digest") == input_digest(project)


def finalize(project, reviewer, note):
    from .hitl import current_review
    if not reviewer.strip() or not note.strip():
        raise ValueError("검토자와 검토의견을 입력하십시오.")
    if not is_current(project) or not project.facts:
        raise ValueError("최신 입력으로 검증·계산을 먼저 실행하십시오.")
    if any(issue.severity == "오류" for issue in project.validations):
        raise ValueError("오류를 해소하기 전에는 검토를 완료할 수 없습니다.")
    if not current_review(project):
        raise ValueError("사람의 검토에서 대상·수치·예외·사업정보 확인을 먼저 기록하십시오. 자료 변경 후에는 재확인이 필요합니다.")
    review = {"at": utc_now(), "reviewer": reviewer.strip(), "note": note.strip(), "input_digest": input_digest(project)}
    project.narrative["review"] = review
    project.narrative.setdefault("assessment_history", []).append({
        **review, "entity": deepcopy(project.entity.__dict__), "facts": deepcopy([f.__dict__ for f in project.facts]),
        "sources": deepcopy([s.__dict__ for s in project.sources]), "versions": deepcopy(project.versions),
        "ratios": deepcopy([r.__dict__ for r in project.ratios]),
        "business": deepcopy(project.narrative.get("business_evidence", [])),
        "updates": deepcopy(project.narrative.get("partner_updates", [])),
        "research_briefs": deepcopy(project.narrative.get("research_briefs", [])),
        "report_narrative": deepcopy(project.narrative.get("final") or project.narrative.get("basic", {})),
    })
    project.status = "검토 완료"
    log_action(project, "검토 완료", f"{reviewer}: {note}")

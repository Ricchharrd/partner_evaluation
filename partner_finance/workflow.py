from __future__ import annotations

from copy import deepcopy
import hashlib
import json

from .analysis import build_basic_narrative, calculate_ratios, context_blocks
from .policy import evaluate_company_policy
from .schema import utc_now, ValidationIssue
from .validation import validate_facts
from .legacy_sec.sec_fx import HARDCODED_USD_KRW_RATES


def input_digest(project):
    payload = {"entity": project.entity.__dict__, "facts": [f.__dict__ for f in project.facts],
               "sources": [s.__dict__ for s in project.sources], "versions": project.versions}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def log_action(project, action, detail):
    project.narrative.setdefault("audit_log", []).append({"at": utc_now(), "action": action, "detail": detail})
    project.touch()


def invalidate(project):
    for key in ("final", "ai_cache", "basic", "policy_evaluation", "calculated_digest", "review"):
        project.narrative.pop(key, None)
    project.ratios = []
    project.validations = []
    project.status = "재검토 필요"


def recalculate(project):
    invalidate(project)
    project.validations = validate_facts(project.facts)
    for year, reason in context_blocks(project.facts).items():
        project.validations.append(ValidationIssue("CONTEXT_BLOCK", "오류", year, reason))
    project.ratios = calculate_ratios(project.facts)
    project.narrative["basic"] = build_basic_narrative(project.facts, project.ratios)
    rates = {}
    fx_rows = []
    for year in sorted({f.fiscal_year for f in project.facts}):
        currencies = {f.currency for f in project.facts if f.fiscal_year == year}
        if currencies == {"USD"} and year in HARDCODED_USD_KRW_RATES:
            rate = HARDCODED_USD_KRW_RATES[year]
            rates[year] = rate["average"]
            fx_rows.append({"연도": year, "평균 USD/KRW": rate["average"], "기말 USD/KRW": rate["closing"],
                            "기말 관측일": rate["closing_date"], "기준": "기존 FRED 역년 환율표. 개별 회계기간 환율이 아닌 잠정 대용치"})
    project.narrative["fx_display"] = fx_rows
    project.narrative["policy_evaluation"] = evaluate_company_policy(project, rates)
    error_years = {issue.fiscal_year for issue in project.validations if issue.severity == "오류"}
    for ratio in project.ratios:
        if ratio.fiscal_year in error_years:
            ratio.value = None
            ratio.status = "검증 오류로 보류"
    project.narrative["basic"] = build_basic_narrative(project.facts, project.ratios)
    for row in project.narrative["policy_evaluation"]:
        if row.get("fiscal_year") in error_years:
            row.update(score=None, grade=None, status="보류", reason="재무 검증 오류를 먼저 해소하십시오.", components=[])
        if row.get("fiscal_year") in rates:
            row["currency_note"] = "매출·영업이익만 역년 평균환율로 원화 비교. 비율은 원통화 산정. K-IFRS 완전 환산 아님."
        row["provisional"] = True
    project.narrative["calculated_digest"] = input_digest(project)
    project.narrative["calculated_at"] = utc_now()
    project.status = "검토 중"
    log_action(project, "재계산", "현재 입력 기준으로 계산 및 이전 AI 설명 무효화")


def is_current(project):
    return project.narrative.get("calculated_digest") == input_digest(project)


def finalize(project, reviewer, note):
    if not reviewer.strip() or not note.strip():
        raise ValueError("검토자와 검토의견을 입력하십시오.")
    if not is_current(project) or not project.facts:
        raise ValueError("최신 입력으로 검증·계산을 먼저 실행하십시오.")
    if any(issue.severity == "오류" for issue in project.validations):
        raise ValueError("오류를 해소하기 전에는 검토를 완료할 수 없습니다.")
    review = {"at": utc_now(), "reviewer": reviewer.strip(), "note": note.strip(), "input_digest": input_digest(project)}
    project.narrative["review"] = review
    project.narrative.setdefault("assessment_history", []).append({
        **review, "entity": deepcopy(project.entity.__dict__), "facts": deepcopy([f.__dict__ for f in project.facts]),
        "sources": deepcopy([s.__dict__ for s in project.sources]), "versions": deepcopy(project.versions),
        "evaluations": deepcopy(project.narrative.get("policy_evaluation", [])),
        "ratios": deepcopy([r.__dict__ for r in project.ratios]),
        "business": deepcopy(project.narrative.get("business_evidence", [])),
        "updates": deepcopy(project.narrative.get("partner_updates", [])),
        "research_briefs": deepcopy(project.narrative.get("research_briefs", [])),
        "report_narrative": deepcopy(project.narrative.get("final") or project.narrative.get("basic", {})),
    })
    project.status = "검토 완료"
    log_action(project, "검토 완료", f"{reviewer}: {note}")


def assessment_rows(project):
    evaluations = project.narrative.get("policy_evaluation", [])[-3:]
    rows = {}
    for evaluation in evaluations:
        year = f"FY{evaluation.get('fiscal_year', '-')}"
        for component in evaluation.get("components", []):
            label = f"{component['label']} ({component['weight']:g}%)"
            rows.setdefault(label, {"평가항목": label})[year] = component.get("grade") or "미확인"
        rows.setdefault("점수", {"평가항목": "잠정 점수"})[year] = evaluation.get("score")
        rows.setdefault("등급", {"평가항목": "잠정 등급"})[year] = evaluation.get("grade") or "보류"
        rows.setdefault("감점", {"평가항목": "감점 합계"})[year] = sum(a["points"] for a in evaluation.get("adjustments", []))
    return list(rows.values())

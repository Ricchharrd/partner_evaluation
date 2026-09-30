from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isfinite

from .schema import FinancialFact, RatioResult


def safe_ratio(numerator: float | None, denominator: float | None, *, positive_denominator: bool = False):
    if numerator is None or denominator is None:
        return None, "입력값 누락"
    if not isfinite(numerator) or not isfinite(denominator):
        return None, "유효하지 않은 수치"
    if denominator == 0:
        return None, "분모가 0"
    if positive_denominator and denominator < 0:
        return None, "분모가 음수여서 비교 불가"
    return numerator / denominator, "계산됨"


def context_blocks(facts):
    """Reject ambiguous contexts before selecting any values for calculations."""
    blocks = {}
    for year in {f.fiscal_year for f in facts}:
        rows = [f for f in facts if f.fiscal_year == year]
        reasons = []
        for field in ("entity_id", "currency", "reporting_scope", "accounting_standard"):
            if len({getattr(f, field) for f in rows}) > 1:
                reasons.append(f"{field} 혼합")
        if any(f.currency in ("", "미확인") or f.reporting_scope in ("", "미확인") for f in rows):
            reasons.append("통화 또는 재무범위 미확인")
        if len({f.period_end for f in rows if f.period_end}) > 1:
            reasons.append("기말일 불일치")
        durations = {f.period_start for f in rows if f.period_start and f.period_start != f.period_end}
        if len(durations) > 1:
            reasons.append("기간 시작일 불일치")
        for f in rows:
            if f.effective_value is not None and not isfinite(f.effective_value):
                reasons.append("비유한 수치")
            if f.period_start and f.period_end and f.period_start != f.period_end:
                try:
                    days = (date.fromisoformat(f.period_end) - date.fromisoformat(f.period_start)).days
                    if not 330 <= days <= 380:
                        reasons.append("연간 회계기간 아님")
                except ValueError:
                    reasons.append("날짜 형식 오류")
        for item in {f.standard_item for f in rows}:
            values = {f.effective_value for f in rows if f.standard_item == item and f.effective_value is not None}
            if len(values) > 1:
                reasons.append(f"{item} 출처값 충돌")
        if reasons:
            blocks[year] = ", ".join(sorted(set(reasons)))
    return blocks


def effective_fact_map(facts: list[FinancialFact]) -> dict[tuple[int, str], FinancialFact]:
    selected: dict[tuple[int, str], FinancialFact] = {}
    for fact in facts:
        key = (fact.fiscal_year, fact.standard_item)
        current = selected.get(key)
        score = (bool(fact.user_value is not None), bool(fact.restated), fact.collected_at, fact.fact_id)
        current_score = (
            bool(current.user_value is not None),
            bool(current.restated),
            current.collected_at,
            current.fact_id,
        ) if current else None
        if current is None or score > current_score:
            selected[key] = fact
    return selected


def _add(results, year, key, label, numerator, denominator, formula, *, positive_denominator=False, note=""):
    value, status = safe_ratio(numerator, denominator, positive_denominator=positive_denominator)
    results.append(
        RatioResult(
            fiscal_year=year,
            metric_key=key,
            label=label,
            value=value,
            formula=formula,
            inputs={"분자": numerator, "분모": denominator},
            status=status,
            note=note,
        )
    )


def calculate_ratios(facts: list[FinancialFact]) -> list[RatioResult]:
    selected = effective_fact_map(facts)
    blocked = context_blocks(facts)
    years = sorted({year for year, _ in selected})
    results: list[RatioResult] = []
    previous_revenue = None
    previous_assets = None

    for year in years:
        previous_revenue = None
        previous_assets = None
        current_context = next(f for f in facts if f.fiscal_year == year)
        for item in ("revenue", "total_assets"):
            prior = selected.get((year - 1, item))
            if prior and year - 1 not in blocked and all(getattr(prior, k) == getattr(current_context, k) for k in ("currency", "reporting_scope", "entity_id", "accounting_standard")):
                if item == "revenue":
                    previous_revenue = prior.effective_value
                else:
                    previous_assets = prior.effective_value
        value = lambda key: selected.get((year, key)).effective_value if year not in blocked and selected.get((year, key)) else None
        revenue = value("revenue")
        operating_income = value("operating_income")
        net_income = value("net_income")
        assets = value("total_assets")
        liabilities = value("total_liabilities")
        equity = value("total_equity")
        current_assets = value("current_assets")
        current_liabilities = value("current_liabilities")
        financial_debt = value("financial_debt")
        if financial_debt is None:
            debt_parts = [value("short_term_debt"), value("long_term_debt")]
            if all(part is not None for part in debt_parts):
                financial_debt = sum(debt_parts)
        interest_expense = value("interest_expense")
        operating_cash_flow = value("operating_cash_flow")
        capex = value("capex")

        _add(results, year, "operating_margin", "영업이익률", operating_income, revenue, "영업이익 / 매출액")
        _add(results, year, "net_margin", "순이익률", net_income, revenue, "당기순이익 / 매출액")
        average_assets = assets if previous_assets is None or assets is None else (previous_assets + assets) / 2
        _add(results, year, "roa", "총자산이익률(ROA)", net_income, average_assets, "당기순이익 / 평균자산", positive_denominator=True)
        _add(results, year, "current_ratio", "유동비율", current_assets, current_liabilities, "유동자산 / 유동부채", positive_denominator=True)
        _add(results, year, "debt_ratio", "부채비율", liabilities, equity, "부채총계 / 자본총계", positive_denominator=True)
        _add(results, year, "interest_coverage", "이자보상배율", operating_income, abs(interest_expense) if interest_expense is not None else None, "영업이익 / |이자비용|", positive_denominator=True)
        _add(results, year, "debt_to_operating_income", "금융부채/영업이익", financial_debt, operating_income, "금융부채 / 영업이익", positive_denominator=True)
        _add(results, year, "ocf_to_debt", "영업CF/금융부채", operating_cash_flow, financial_debt, "영업활동현금흐름 / 금융부채", positive_denominator=True)
        _add(results, year, "ocf_margin", "영업현금흐름률", operating_cash_flow, revenue, "영업활동현금흐름 / 매출액")

        fcf = None if operating_cash_flow is None or capex is None else operating_cash_flow - abs(capex)
        results.append(
            RatioResult(year, "free_cash_flow", "잉여현금흐름", fcf, "영업활동현금흐름 - |CAPEX|", {"영업CF": operating_cash_flow, "CAPEX": capex}, "계산됨" if fcf is not None else "입력값 누락")
        )
        growth, status = safe_ratio(None if revenue is None or previous_revenue is None else revenue - previous_revenue, previous_revenue, positive_denominator=True)
        results.append(
            RatioResult(year, "revenue_growth", "매출성장률", growth, "(당기 매출 - 전기 매출) / 전기 매출", {"당기매출": revenue, "전기매출": previous_revenue}, status)
        )
        previous_revenue = revenue
        previous_assets = assets
    for result in results:
        if result.fiscal_year in blocked:
            result.value = None
            result.status = "계산 보류"
            result.note = blocked[result.fiscal_year]
    return results


def ratio_rows(ratios: list[RatioResult]) -> list[dict]:
    return [
        {
            "연도": row.fiscal_year,
            "지표": row.label,
            "값": row.value,
            "산식": row.formula,
            "상태": row.status,
            "비고": row.note,
        }
        for row in ratios
    ]


def build_basic_narrative(facts: list[FinancialFact], ratios: list[RatioResult]) -> dict:
    by_metric = defaultdict(list)
    for ratio in ratios:
        if ratio.value is not None:
            by_metric[ratio.metric_key].append(ratio)
    observations = []
    for key, label, percent in [
        ("revenue_growth", "매출", True),
        ("operating_margin", "영업이익률", True),
        ("current_ratio", "유동비율", False),
        ("debt_ratio", "부채비율", True),
        ("ocf_to_debt", "영업CF/금융부채", True),
    ]:
        rows = sorted(by_metric.get(key, []), key=lambda row: row.fiscal_year)
        if not rows:
            continue
        latest = rows[-1]
        rendered = f"{latest.value * 100:,.1f}%" if percent else f"{latest.value:,.2f}배"
        observations.append(f"FY{latest.fiscal_year} {label}은(는) {rendered}입니다.")

    return {
        "company_overview": "기업개요는 공식 홈페이지·연차보고서 근거를 확인한 뒤 작성해야 합니다.",
        "observed_facts": observations or ["확정된 재무값이 부족하여 수치 기반 관찰사항을 생성하지 못했습니다."],
        "interpretation": ["변동 원인은 주석 또는 경영진 설명 근거가 확보되기 전까지 미확인입니다."],
        "review_points": ["누락값과 검증 경고를 먼저 해소한 뒤 최종 판단하십시오."],
        "generation_mode": "규칙 기반 기본 문구",
    }

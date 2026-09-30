from __future__ import annotations

from .analysis import effective_fact_map, context_blocks
from .schema import AnalysisProject


GRADE_POINTS = {"AAA": 100, "AA": 95, "A": 90, "BB": 80, "B": 70, "CC": 60, "C": 50, "D": 40}
WEIGHTS = {
    "revenue": 15.0, "operating_income": 10.0, "interest_coverage": 7.5,
    "debt_to_operating_income": 7.5, "ocf_to_debt": 15.0, "debt_ratio": 15.0,
    "receivable_days": 15.0, "current_ratio": 15.0,
}


def higher(value, thresholds):
    if value is None:
        return None
    return next((grade for grade, threshold in thresholds if value >= threshold), "D")


def lower(value, thresholds):
    if value is None:
        return None
    return next((grade for grade, threshold in thresholds if value <= threshold), "D")


def _final_grade(score):
    if score is None:
        return None
    return "A" if score >= 90 else "B1" if score >= 80 else "B2" if score >= 70 else "C" if score >= 50 else "D"


def _altman(values):
    assets = values.get("total_assets")
    liabilities = values.get("total_liabilities")
    equity = values.get("total_equity")
    required = [assets, liabilities, equity, values.get("retained_earnings"), values.get("operating_income"), values.get("revenue")]
    if any(value is None for value in required) or assets == 0 or liabilities == 0:
        return None
    working_capital = None
    if values.get("current_assets") is not None and values.get("current_liabilities") is not None:
        working_capital = values["current_assets"] - values["current_liabilities"]
    if working_capital is None:
        return None
    return 1.2 * working_capital / assets + 1.4 * values["retained_earnings"] / assets + 3.3 * values["operating_income"] / assets + 0.6 * equity / liabilities + 0.99 * values["revenue"] / assets


def evaluate_company_policy(project: AnalysisProject, fx_rates: dict[int, float] | None = None) -> list[dict]:
    if project.entity.entity_type in {"금융회사", "프로젝트 SPV"}:
        return [{"status": "별도 기준 필요", "reason": f"{project.entity.entity_type}에는 일반기업 평가표를 적용하지 않습니다."}]
    selected = effective_fact_map(project.facts)
    blocked = context_blocks(project.facts)
    years = sorted({year for year, _ in selected})
    ratio_lookup = {(ratio.fiscal_year, ratio.metric_key): ratio.value for ratio in project.ratios}
    output = []
    prior_values = None
    prior_year = None
    for year in years:
        if year in blocked:
            output.append({"fiscal_year": year, "status": "보류", "reason": blocked[year], "score": None, "grade": None, "components": []})
            prior_values = None
            continue
        values = {item: fact.effective_value for (fact_year, item), fact in selected.items() if fact_year == year}
        currencies = {fact.currency for (fact_year, _), fact in selected.items() if fact_year == year and fact.currency}
        rate = 1.0 if currencies == {"KRW"} else (fx_rates or {}).get(year)
        absolute_available = rate is not None and len(currencies) == 1
        revenue_million_krw = values.get("revenue") * rate / 1_000_000 if absolute_available and values.get("revenue") is not None else None
        op_million_krw = values.get("operating_income") * rate / 1_000_000 if absolute_available and values.get("operating_income") is not None else None
        revenue = values.get("revenue")
        receivable = values.get("accounts_receivable")
        receivable_days = receivable / revenue * 365 if receivable is not None and revenue not in (None, 0) else None
        components = [
            ("revenue", "매출액", revenue_million_krw, higher(revenue_million_krw, [("AAA", 40000), ("AA", 15000), ("A", 12000), ("BB", 7000), ("B", 3500), ("CC", 1000), ("C", 250)])),
            ("operating_income", "영업이익", op_million_krw, higher(op_million_krw, [("AAA", 4000), ("AA", 2000), ("A", 1500), ("BB", 750), ("B", 250), ("CC", 125), ("C", 60)])),
            ("interest_coverage", "이자보상배율", ratio_lookup.get((year, "interest_coverage")), higher(ratio_lookup.get((year, "interest_coverage")), [("AAA", 20), ("AA", 15), ("A", 10), ("BB", 5), ("B", 2.25), ("CC", 1), ("C", 0.5)])),
            ("debt_to_operating_income", "금융부채/영업이익", ratio_lookup.get((year, "debt_to_operating_income")), lower(ratio_lookup.get((year, "debt_to_operating_income")), [("AAA", .25), ("AA", .75), ("A", 1.5), ("BB", 2.75), ("B", 4.5), ("CC", 6.5), ("C", 9)])),
            ("ocf_to_debt", "영업CF/금융부채", ratio_lookup.get((year, "ocf_to_debt")), higher(ratio_lookup.get((year, "ocf_to_debt")), [("AAA", 1), ("AA", .8), ("A", .55), ("BB", .35), ("B", .2), ("CC", .1), ("C", .05)])),
            ("debt_ratio", "부채비율", ratio_lookup.get((year, "debt_ratio")), lower(ratio_lookup.get((year, "debt_ratio")), [("AAA", 1), ("AA", 2), ("BB", 3), ("CC", 4)])),
            ("receivable_days", "매출채권회전기일", receivable_days, lower(receivable_days, [("AAA", 30), ("AA", 40), ("A", 50), ("BB", 60), ("B", 70), ("CC", 80), ("C", 90)])),
            ("current_ratio", "유동비율", ratio_lookup.get((year, "current_ratio")), higher(ratio_lookup.get((year, "current_ratio")), [("AA", 1), ("BB", .9), ("CC", .8)])),
        ]
        component_rows = [
            {"metric_key": key, "label": label, "value": value, "grade": grade, "weight": WEIGHTS[key], "weighted_points": None if grade is None else GRADE_POINTS[grade] * WEIGHTS[key] / 100}
            for key, label, value, grade in components
        ]
        missing = [row["label"] for row in component_rows if row["grade"] is None]
        base_score = None if missing else sum(row["weighted_points"] for row in component_rows)
        altman = _altman(values)
        adjustments = []
        if altman is not None and altman < 1:
            adjustments.append({"label": "Altman Z-score < 1", "points": -2})
        if prior_values and prior_year == year - 1:
            consecutive = any(
                values.get(key) is not None and prior_values.get(key) is not None and values[key] < 0 and prior_values[key] < 0
                for key in ["operating_cash_flow", "net_income"]
            )
            if consecutive:
                adjustments.append({"label": "2개년 연속 영업CF 또는 순이익 적자", "points": -2})
        score = None if base_score is None else max(0, min(100, base_score + sum(row["points"] for row in adjustments)))
        output.append(
            {
                "fiscal_year": year, "status": "보류" if missing else "산정",
                "reason": "평가 입력 미확인: " + ", ".join(missing) if missing else "",
                "components": component_rows, "base_score": base_score, "adjustments": adjustments,
                "score": score, "grade": _final_grade(score), "altman_z": altman,
                "altman_basis": "장부자본·영업이익 대용치 사용. 원형 Altman(시가총액·EBIT)과 다르며 잠정 점수입니다.",
                "currency_note": "금액항목은 KRW 기준" if absolute_available else "환율 미입력으로 금액항목 평가 보류",
            }
        )
        prior_values = values
        prior_year = year
    return output

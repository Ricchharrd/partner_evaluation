from __future__ import annotations

from collections import defaultdict

from .analysis import effective_fact_map
from .schema import FinancialFact, ValidationIssue
from .account_guards import mapping_problem, normalize_scope


REQUIRED_ITEMS = ["revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity"]


def _tolerance(values: list[float], multipliers: list[float]) -> float:
    magnitude = max([abs(value) for value in values if value is not None] or [0])
    unit = max(multipliers or [1.0])
    return max(unit * 2, magnitude * 0.002)


def validate_facts(facts: list[FinancialFact]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for fact in facts:
        problem = mapping_problem(fact.standard_item, fact.original_label, fact.source_locator)
        if problem:
            issues.append(ValidationIssue("ACCOUNT_MAPPING", "오류", fact.fiscal_year, problem, [fact.standard_item], "오류"))
    selected = effective_fact_map(facts)
    years = sorted({fact.fiscal_year for fact in facts})

    for year in years:
        year_facts = [fact for fact in facts if fact.fiscal_year == year]
        by_item = defaultdict(list)
        for fact in year_facts:
            by_item[fact.standard_item].append(fact)

        missing = [key for key in REQUIRED_ITEMS if selected.get((year, key)) is None or selected[(year, key)].effective_value is None]
        if missing:
            issues.append(ValidationIssue("REQUIRED_MISSING", "경고", year, "필수 항목 미확인: " + ", ".join(missing), missing, "보류"))

        for key, rows in by_item.items():
            non_null = [row.effective_value for row in rows if row.effective_value is not None]
            if len(rows) > 1 and len({round(value, 6) for value in non_null}) > 1:
                issues.append(ValidationIssue("SOURCE_CONFLICT", "경고", year, f"{key} 값이 출처 또는 버전별로 충돌합니다.", [key]))

        currencies = {fact.currency for fact in year_facts if fact.currency}
        scopes = {normalize_scope(fact.reporting_scope) for fact in year_facts if fact.reporting_scope}
        ends = {fact.period_end for fact in year_facts if fact.period_end}
        starts = {fact.period_start for fact in year_facts if fact.period_start and fact.period_start != fact.period_end}
        if len(currencies) > 1:
            issues.append(ValidationIssue("MIXED_CURRENCY", "오류", year, "동일 연도에 여러 통화가 혼합되어 금액 비교를 보류합니다.", status="오류"))
        if len(scopes) > 1:
            issues.append(ValidationIssue("MIXED_SCOPE", "경고", year, "연결·별도 범위가 혼합되어 있습니다."))
        if len(ends) > 1 or len(starts) > 1:
            issues.append(ValidationIssue("MIXED_PERIOD", "경고", year, "동일 연도 값의 회계기간이 서로 다릅니다."))

        def fact_value(key):
            fact = selected.get((year, key))
            return fact.effective_value if fact else None

        assets = fact_value("total_assets")
        liabilities = fact_value("total_liabilities")
        equity = fact_value("total_equity")
        if None not in (assets, liabilities, equity):
            tolerance = _tolerance([assets, liabilities, equity], [fact.unit_multiplier for fact in year_facts])
            difference = assets - liabilities - equity
            if abs(difference) > tolerance:
                issues.append(ValidationIssue("BALANCE_MISMATCH", "오류", year, f"자산과 부채+자본이 {difference:,.0f} 차이 납니다(허용오차 {tolerance:,.0f}).", ["total_assets", "total_liabilities", "total_equity"], "오류"))
        else:
            issues.append(ValidationIssue("BALANCE_UNVERIFIABLE", "정보", year, "자산·부채·자본 중 누락값이 있어 대차 검증을 수행하지 못했습니다.", status="검증 불가"))

        cash_keys = ["beginning_cash", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "fx_effect_on_cash", "ending_cash"]
        cash = {key: fact_value(key) for key in cash_keys}
        if all(cash[key] is not None for key in cash_keys):
            expected = cash["beginning_cash"] + cash["operating_cash_flow"] + cash["investing_cash_flow"] + cash["financing_cash_flow"] + cash["fx_effect_on_cash"]
            tolerance = _tolerance(list(cash.values()), [fact.unit_multiplier for fact in year_facts])
            if abs(expected - cash["ending_cash"]) > tolerance:
                issues.append(ValidationIssue("CASHFLOW_MISMATCH", "오류", year, "기초현금과 현금흐름·환율효과를 반영한 기말현금이 일치하지 않습니다.", cash_keys, "오류"))
        else:
            issues.append(ValidationIssue("CASHFLOW_UNVERIFIABLE", "정보", year, "현금흐름 연결 항목이 부족하여 기초·기말 현금을 검증하지 못했습니다.", cash_keys, "검증 불가"))

    for key in REQUIRED_ITEMS + ["operating_cash_flow", "financial_debt"]:
        ordered = []
        for year in years:
            fact = selected.get((year, key))
            if fact and fact.effective_value is not None:
                ordered.append((year, fact.effective_value))
        for (prior_year, prior), (year, current) in zip(ordered, ordered[1:]):
            if prior != 0 and abs((current - prior) / abs(prior)) >= 0.75:
                issues.append(ValidationIssue("LARGE_YOY_CHANGE", "경고", year, f"{key}가 FY{prior_year} 대비 75% 이상 변동했습니다.", [key]))

    if not facts:
        issues.append(ValidationIssue("NO_FACTS", "오류", None, "추출 또는 입력된 재무값이 없습니다.", status="오류"))
    return issues


def validation_rows(issues: list[ValidationIssue]) -> list[dict]:
    return [
        {"연도": row.fiscal_year, "심각도": row.severity, "코드": row.code, "내용": row.message, "관련항목": ", ".join(row.item_keys), "상태": row.status}
        for row in issues
    ]

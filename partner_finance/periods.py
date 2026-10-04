from datetime import date


def is_annual_flow(fact):
    """Only a dated, roughly twelve-month flow is safe for annual comparison."""
    if not fact.period_start or not fact.period_end or fact.period_start == fact.period_end:
        return False
    try:
        days = (date.fromisoformat(fact.period_end) - date.fromisoformat(fact.period_start)).days
    except ValueError:
        return False
    return 330 <= days <= 380


def period_label(facts, year):
    flows = [fact for fact in facts if fact.fiscal_year == year and fact.period_start
             and fact.period_end and fact.period_start != fact.period_end]
    if not flows:
        return f"{year}년 (기간 미확인)"
    periods = {(fact.period_start, fact.period_end) for fact in flows}
    if len(periods) != 1:
        return f"{year}년 (기간 혼합, 확인 필요)"
    start, end = next(iter(periods))
    if all(is_annual_flow(fact) for fact in flows):
        return f"FY{year} (연간)"
    return f"{year}년 중간 ({start}~{end})"

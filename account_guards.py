"""Conservative semantic guards shared by extraction and legacy-data validation."""
import re


def normalize_scope(value):
    key = str(value or "").strip().lower()
    return {"consolidated": "연결", "consolidated financial statements": "연결",
            "연결재무제표": "연결", "separate": "별도", "standalone": "별도",
            "individual": "별도", "별도재무제표": "별도"}.get(key, key or "미확인")


def mapping_problem(item, label, evidence="", document=""):
    label = re.sub(r"\s+", " ", label.lower()).strip()
    if item == "total_liabilities" and ("equity" in label or "부채와 자본" in label or "부채 및 자본" in label):
        return "부채·자본 합계를 부채총계로 사용할 수 없습니다."
    if item == "net_income" and any(t in label for t in ("attributable", "지배", "parent")):
        return "귀속 순이익을 연결 전체 순이익으로 사용할 수 없습니다."
    if item == "interest_expense" and any(t in label for t in ("net financial", "financial result", "순금융")):
        return "순금융손익은 이자비용이 아닙니다."
    if item == "financial_debt" and any(t in label for t in ("net financial debt", "net debt", "순차입", "순금융")):
        return "순차입금은 총금융부채가 아닙니다."
    if item in {"operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex"}:
        context = (label + " " + evidence).lower()
        definitions = re.sub(r"\s+", " ", document.lower())
        apm_definition = "this apm represents" in definitions and "operating cash flow" in definitions
        if apm_definition or any(t in context for t in ("apm", "net investment cashflow", "financing/others", "ordinary capex")):
            return "회사 정의 APM일 가능성이 있어 정식 현금흐름 항목으로 사용하지 않습니다."
    return ""

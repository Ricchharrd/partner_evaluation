from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


STANDARD_ITEMS = {
    "revenue": "매출액",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "total_assets": "자산총계",
    "total_liabilities": "부채총계",
    "total_equity": "자본총계",
    "current_assets": "유동자산",
    "current_liabilities": "유동부채",
    "cash": "현금및현금성자산",
    "accounts_receivable": "매출채권",
    "short_term_debt": "단기차입금",
    "long_term_debt": "장기차입금",
    "financial_debt": "금융부채",
    "interest_expense": "이자비용",
    "operating_cash_flow": "영업활동현금흐름",
    "investing_cash_flow": "투자활동현금흐름",
    "financing_cash_flow": "재무활동현금흐름",
    "capex": "CAPEX",
    "retained_earnings": "이익잉여금",
    "beginning_cash": "기초 현금",
    "ending_cash": "기말 현금",
    "fx_effect_on_cash": "환율변동효과",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class EntityProfile:
    legal_name: str
    country: str = ""
    identifiers: dict[str, str] = field(default_factory=dict)
    official_website: str = ""
    industry: str = ""
    entity_type: str = "일반기업"
    reporting_scope: str = "연결"
    accounting_standard: str = "미확인"
    notes: str = ""
    entity_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class SourceDocument:
    name: str
    source_type: str
    url: str = ""
    local_path: str = ""
    mime_type: str = ""
    sha256: str = ""
    published_at: str = ""
    collected_at: str = field(default_factory=utc_now)
    status: str = "수집됨"
    note: str = ""
    source_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class FinancialFact:
    entity_id: str
    fiscal_year: int
    standard_item: str
    original_label: str
    original_value: float | None
    normalized_value: float | None
    currency: str
    unit_multiplier: float = 1.0
    period_start: str = ""
    period_end: str = ""
    period_type: str = "연간"
    reporting_scope: str = "연결"
    accounting_standard: str = "미확인"
    source_id: str = ""
    source_locator: str = ""
    published_at: str = ""
    collected_at: str = field(default_factory=utc_now)
    restated: bool = False
    report_version: str = ""
    extraction_method: str = "수동"
    validation_status: str = "미검증"
    user_value: float | None = None
    user_reason: str = ""
    edited_at: str = ""
    fact_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def effective_value(self) -> float | None:
        return self.user_value if self.user_value is not None else self.normalized_value

    def apply_correction(self, value: float | None, reason: str) -> None:
        self.user_value = value
        self.user_reason = reason.strip()
        self.edited_at = utc_now()
        self.validation_status = "사용자 수정"


@dataclass
class ValidationIssue:
    code: str
    severity: str
    fiscal_year: int | None
    message: str
    item_keys: list[str] = field(default_factory=list)
    status: str = "검토 필요"


@dataclass
class RatioResult:
    fiscal_year: int
    metric_key: str
    label: str
    value: float | None
    formula: str
    inputs: dict[str, float | None]
    status: str
    note: str = ""


@dataclass
class AnalysisProject:
    title: str
    entity: EntityProfile
    project_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    status: str = "작성 중"
    sources: list[SourceDocument] = field(default_factory=list)
    facts: list[FinancialFact] = field(default_factory=list)
    validations: list[ValidationIssue] = field(default_factory=list)
    ratios: list[RatioResult] = field(default_factory=list)
    narrative: dict[str, Any] = field(default_factory=dict)
    versions: dict[str, str] = field(
        default_factory=lambda: {
            "schema": "1.0",
            "extractor": "1.0",
            "validator": "1.0",
            "ratio_engine": "1.0",
            "prompt": "1.0",
            "rating_policy": "company-policy-2026-01",
        }
    )

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AnalysisProject":
        payload = dict(data)
        payload["entity"] = EntityProfile(**payload["entity"])
        payload["sources"] = [SourceDocument(**row) for row in payload.get("sources", [])]
        payload["facts"] = [FinancialFact(**row) for row in payload.get("facts", [])]
        payload["validations"] = [ValidationIssue(**row) for row in payload.get("validations", [])]
        payload["ratios"] = [RatioResult(**row) for row in payload.get("ratios", [])]
        return cls(**payload)


def facts_to_rows(facts: list[FinancialFact]) -> list[dict[str, Any]]:
    rows = []
    for fact in facts:
        rows.append(
            {
                "fact_id": fact.fact_id,
                "연도": fact.fiscal_year,
                "표준항목": fact.standard_item,
                "표준항목명": STANDARD_ITEMS.get(fact.standard_item, fact.standard_item),
                "원문항목": fact.original_label,
                "추출값": fact.normalized_value,
                "사용자수정값": fact.user_value,
                "적용값": fact.effective_value,
                "통화": fact.currency,
                "원문단위배수": fact.unit_multiplier,
                "범위": fact.reporting_scope,
                "기간시작": fact.period_start,
                "기간종료": fact.period_end,
                "출처위치": fact.source_locator,
                "추출방식": fact.extraction_method,
                "검증상태": fact.validation_status,
                "수정사유": fact.user_reason,
            }
        )
    return rows

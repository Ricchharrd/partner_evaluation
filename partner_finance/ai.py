from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
import os
import math
import urllib.request

from .schema import AnalysisProject, FinancialFact, SourceDocument, STANDARD_ITEMS


DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"


class AIProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_json(self, system: str, payload: dict, max_tokens: int = 3000) -> tuple[dict, dict]:
        raise NotImplementedError


class ClaudeProvider(AIProvider):
    name = "claude"

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 90):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL)
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate_json(self, system: str, payload: dict, max_tokens: int = 3000) -> tuple[dict, dict]:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        text_blocks = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
        response_text = "\n".join(text_blocks).strip()
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0]
        parsed = json.loads(response_text)
        meta = {"provider": self.name, "model": self.model, "usage": result.get("usage", {}), "request_id": result.get("id", "")}
        return parsed, meta


def _project_ai_payload(project: AnalysisProject) -> dict:
    confirmed = []
    for fact in project.facts:
        if fact.effective_value is None:
            continue
        confirmed.append(
            {
                "fiscal_year": fact.fiscal_year,
                "item": fact.standard_item,
                "value": fact.effective_value,
                "currency": fact.currency,
                "scope": fact.reporting_scope,
                "source_locator": fact.source_locator,
            }
        )
    return {
        "entity": {"legal_name": project.entity.legal_name, "country": project.entity.country, "entity_type": project.entity.entity_type},
        "confirmed_financial_facts": confirmed,
        "ratios": [ratio.__dict__ for ratio in project.ratios],
        "validation_issues": [issue.__dict__ for issue in project.validations],
        "sources": [{"id": s.source_id, "name": s.name, "url": s.url} for s in project.sources],
        "business_evidence": [row for row in project.narrative.get("business_evidence", []) if row.get("status") == "확인"],
        "approved_updates": [row for row in project.narrative.get("partner_updates", []) if row.get("status") == "승인"],
    }


def generate_project_narrative(project: AnalysisProject, provider: AIProvider) -> dict:
    payload = _project_ai_payload(project)
    input_hash = hashlib.sha256(json.dumps({"payload": payload, "provider": provider.name, "model": getattr(provider, "model", ""), "prompt": "2"}, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    cached = project.narrative.get("ai_cache") or {}
    if cached.get("input_hash") == input_hash and cached.get("result"):
        return cached["result"]
    system = """당신은 해외 건설·인프라 파트너사 재무검토 보고서 작성 보조자다.
제공된 확정 재무값과 출처만 사용한다. 산술을 다시 계산하거나 숫자를 바꾸지 않는다.
관찰된 사실과 원인 해석을 구분하고, 원인 근거가 없으면 '미확인'이라고 쓴다.
금융회사 또는 SPV에는 일반기업 평가를 적용하지 말고 별도 기준 필요를 명시한다.
반드시 JSON 객체만 반환한다. 키는 company_overview, observed_facts, interpretation,
review_points, limitations, source_citations이며 목록 항목은 간결한 한국어 문장이어야 한다."""
    result, meta = provider.generate_json(system, payload, max_tokens=3500)
    for key in ("observed_facts", "interpretation", "review_points", "limitations", "source_citations"):
        if not isinstance(result.get(key, []), list) or any(not isinstance(x, str) for x in result.get(key, [])):
            raise ValueError("AI 분석문 형식이 올바르지 않습니다.")
    result["generation_mode"] = f"{meta['provider']} API / {meta['model']}"
    project.narrative["ai_cache"] = {"input_hash": input_hash, "result": result, "meta": meta}
    return result


def extract_facts_from_text(
    text: str,
    entity_id: str,
    source: SourceDocument,
    provider: AIProvider,
    default_currency: str = "미확인",
    default_scope: str = "연결",
) -> tuple[list[FinancialFact], list[str], dict]:
    from .document_selection import select_financial_text
    clipped_text, selection_warnings = select_financial_text(text)
    payload = {
        "document_text": clipped_text,
        "required_items": [
            "revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity",
            "current_assets", "current_liabilities", "cash", "accounts_receivable", "short_term_debt", "long_term_debt",
            "financial_debt", "interest_expense", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow", "capex",
        ],
        "default_currency": default_currency,
        "default_scope": default_scope,
    }
    system = """재무보고서 텍스트에서 명시적으로 확인되는 값만 추출하라.
추론하거나 누락값을 0으로 채우지 마라. 연결/별도, 연도, 통화, 원문 단위와 페이지 표식을 보존하라.
반드시 JSON 객체만 반환한다. 형식은 {facts:[{fiscal_year, standard_item, original_label,
original_value, unit_multiplier, currency, reporting_scope, period_start, period_end, source_locator,
evidence_quote}], warnings:[string]}이다. 근거 문구가 없는 값은 제외한다."""
    result, meta = provider.generate_json(system, payload, max_tokens=5000)
    facts = []
    warnings = result.get("warnings") or []
    rows = result.get("facts", [])
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings) or not isinstance(rows, list):
        raise ValueError("AI 추출 결과 형식이 올바르지 않습니다.")
    warnings.extend(selection_warnings)
    for row in rows:
        if not isinstance(row, dict):
            warnings.append("잘못된 형식의 추출 후보를 제외했습니다.")
            continue
        try:
            original = float(row["original_value"])
            multiplier = float(row.get("unit_multiplier", 1))
            year = int(row["fiscal_year"])
        except (KeyError, TypeError, ValueError):
            continue
        quote = str(row.get("evidence_quote") or "").strip()
        if not all(math.isfinite(v) for v in (original, multiplier, original * multiplier)) or multiplier <= 0 or not 1900 <= year <= 2100:
            warnings.append("유효하지 않은 수치 또는 단위 후보를 제외했습니다.")
            continue
        if row.get("standard_item") not in STANDARD_ITEMS or not quote or " ".join(quote.split()) not in " ".join(clipped_text.split()):
            warnings.append("원문에서 근거 문구를 확인하지 못한 후보를 제외했습니다.")
            continue
        facts.append(
            FinancialFact(
                entity_id=entity_id,
                fiscal_year=year,
                standard_item=str(row.get("standard_item") or ""),
                original_label=str(row.get("original_label") or row.get("standard_item") or ""),
                original_value=original,
                normalized_value=original * multiplier,
                currency=str(row.get("currency") or default_currency).upper(),
                unit_multiplier=multiplier,
                period_start=str(row.get("period_start") or ""),
                period_end=str(row.get("period_end") or ""),
                reporting_scope=str(row.get("reporting_scope") or default_scope),
                source_id=source.source_id,
                source_locator=str(row.get("source_locator") or "위치 미확인") + " | 근거: " + quote,
                extraction_method=f"AI 구조화 추출 ({meta['model']})",
                validation_status="AI 추출-검토 필요",
            )
        )
    return facts, warnings, meta

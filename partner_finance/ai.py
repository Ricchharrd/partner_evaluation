from __future__ import annotations

from abc import ABC, abstractmethod
import hashlib
import json
import os
import math
import re
from copy import deepcopy
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

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout: int = 90, approval=None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model = model or os.getenv("ANTHROPIC_MODEL", DEFAULT_CLAUDE_MODEL)
        self.timeout = timeout
        self.approval = approval

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
        from .hitl import preflight
        request_body = json.loads(body)
        info = preflight({**request_body, "max_output_tokens": max_tokens})
        if info["blocked"] or info["sensitive"] or info["over_limit"]:
            raise ValueError("보안 또는 처리 규모 제한으로 외부 호출을 중단했습니다.")
        if self.approval is None:
            raise ValueError("외부 API 호출에는 요청별 사람의 승인이 필요합니다.")
        self.approval(request_body)
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
등급, 점수, 가중치, 감점 또는 협업 적합성을 계산하거나 추정하지 않는다. 이 작업은 사내 Claude 스킬과 담당자의 범위다.
반드시 JSON 객체만 반환한다. 키는 company_overview, observed_facts, interpretation,
review_points, limitations, source_citations이며 목록 항목은 간결한 한국어 문장이어야 한다."""
    result, meta = provider.generate_json(system, payload, max_tokens=3500)
    for key in ("observed_facts", "interpretation", "review_points", "limitations", "source_citations"):
        if not isinstance(result.get(key, []), list) or any(not isinstance(x, str) for x in result.get(key, [])):
            raise ValueError("AI 분석문 형식이 올바르지 않습니다.")
    result["generation_mode"] = f"{meta['provider']} API / {meta['model']}"
    project.narrative["ai_cache"] = {"input_hash": input_hash, "result": result, "meta": meta}
    return result


def extraction_request(text, default_currency="미확인", default_scope="연결"):
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
추론하거나 누락값을 0으로 채우지 마라. 표의 당기와 전기 비교열을 모두 추출하라.
연결/별도, 연도, 통화, 원문 단위와 페이지 표식을 보존하라. reporting_scope는 연결 또는 별도 또는 미확인이다.
net_income은 비지배지분 차감 전 연결 전체 세후이익이다. 귀속 순이익과 구분하라.
original_label은 원문 행 제목 그대로다. TOTAL LIABILITIES AND EQUITY는 total_liabilities가 아니다.
반드시 JSON 객체만 반환한다. 형식은 {facts:[{fiscal_year, standard_item, original_label,
original_value, unit_multiplier, currency, reporting_scope, period_start, period_end, source_locator,
evidence_quote}], warnings:[string]}이다. facts는 반드시 위 키를 사용하는 객체 배열이다.
original_value와 unit_multiplier는 쉼표나 통화기호 없는 JSON 숫자로 반환하라.
예: 원문 20,236 (Million Euro)는 original_value:20236, unit_multiplier:1000000, currency:"EUR"이다.
fiscal_year는 FY 문구 없이 정수 연도다. evidence_quote는 원문에서 연속된 문구를 그대로 복사하라.
순금융손익을 이자비용으로, 순차입금을 총금융부채로 대체하지 마라.
APM 또는 회사 정의 현금흐름을 정식 재무제표의 영업/투자/재무 현금흐름이나 CAPEX로 매핑하지 마라.
조정(adjusted)·재분류(reclassified) 손익을 정식 연결 손익보다 우선하지 마라.
관계자 거래(of which: related parties) 열을 해당 연도의 총액으로 사용하지 마라.
요청 default_scope와 다른 범위의 값은 제외하라. 범위가 불명확하면 미확인으로 남겨라.
해당 정의가 원문에 있으면 warnings에 한계를 적어라. 근거 문구가 없는 값은 제외한다."""
    return system, payload, clipped_text, selection_warnings


def extract_facts_from_text(
    text: str, entity_id: str, source: SourceDocument, provider: AIProvider,
    default_currency: str = "미확인", default_scope: str = "연결",
    cache: dict | None = None, force_refresh: bool = False,
    batch_state: dict | None = None, progress=None, _chunk=False,
) -> tuple[list[FinancialFact], list[str], dict]:
    from .document_selection import SELECTION_VERSION
    from .numeric_input import parse_number
    from .account_guards import mapping_problem, normalize_scope
    from .finance_batch import CHUNK_BYTES, extract_batch
    if not _chunk and len(text.encode("utf-8")) > CHUNK_BYTES and hasattr(provider, "approved_batch"):
        state = batch_state if batch_state is not None else {}
        return extract_batch(text, entity_id, source, provider, default_currency, default_scope,
                             state, force_refresh=force_refresh, progress=progress)
    system, payload, clipped_text, selection_warnings = extraction_request(text, default_currency, default_scope)
    output_tokens = 6000 if _chunk else (None if getattr(provider, "supports_unbounded_output", False) else 16000)
    cache_key = hashlib.sha256(json.dumps({"text_hash": hashlib.sha256(text.encode()).hexdigest(),
        "payload": payload, "system": system, "selection": SELECTION_VERSION,
        "provider": getattr(provider, "name", ""), "model": getattr(provider, "model", ""),
        "entity": entity_id, "source": source.source_id,
        "output_mode": output_tokens}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cached = cache.get(cache_key) if cache is not None and not force_refresh else None
    if cached:
        result, meta = deepcopy(cached["result"]), deepcopy(cached["meta"])
        meta.update(cache_hit=True, original_usage=meta.get("usage", {}), usage={})
    else:
        result, meta = provider.generate_json(system, payload, max_tokens=output_tokens)
        meta = {**meta, "cache_hit": False}
    meta.update(original_characters=len(text), selected_characters=len(clipped_text), selection_version=SELECTION_VERSION)
    raw_result = deepcopy(result)
    facts = []
    warnings = result.get("warnings") or []
    rows = result.get("facts")
    if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings) or not isinstance(rows, list):
        raise ValueError("AI 추출 결과 형식이 올바르지 않습니다.")
    warnings.extend(selection_warnings)
    rejected_numeric = 0
    for row in rows:
        if not isinstance(row, dict):
            warnings.append("잘못된 형식의 추출 후보를 제외했습니다.")
            continue
        try:
            original = parse_number(row["original_value"])
            multiplier = parse_number(row["unit_multiplier"])
            parsed_year = parse_number(row["fiscal_year"])
            if not math.isfinite(parsed_year) or not parsed_year.is_integer():
                raise ValueError("Invalid fiscal year")
            year = int(parsed_year)
        except (KeyError, TypeError, ValueError):
            rejected_numeric += 1
            continue
        quote = str(row.get("evidence_quote") or "").strip()
        # APM definitions elsewhere must not invalidate statutory statement rows.
        page_match = re.search(r"(?:PAGE|page|p\.)\s*(\d+)", str(row.get("source_locator", "")), re.I)
        page_context = ""
        if page_match:
            match = re.search(r"\[PAGE " + page_match.group(1) + r"\](.*?)(?=\[PAGE \d+\]|\Z)", clipped_text, re.S)
            page_context = match.group(1) if match else ""
            if not match or " ".join(quote.split()) not in " ".join(page_context.split()):
                warnings.append("페이지와 근거 문구가 일치하지 않는 후보를 제외했습니다.")
                continue
        problem = mapping_problem(row.get("standard_item"), str(row.get("original_label") or ""), quote, page_context)
        if problem:
            warnings.append(problem)
            continue
        scope = normalize_scope(row.get("reporting_scope") or "미확인")
        if scope not in {normalize_scope(default_scope), "미확인"}:
            warnings.append("요청한 연결/별도 범위와 다른 후보를 제외했습니다.")
            continue
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
                reporting_scope=normalize_scope(row.get("reporting_scope") or "미확인"),
                source_id=source.source_id,
                source_locator=str(row.get("source_locator") or "위치 미확인") + " | 근거: " + quote,
                extraction_method=f"AI 구조화 추출 ({meta['model']})",
                validation_status="AI 추출-검토 필요",
            )
        )
    if rejected_numeric:
        warnings.append(f"AI 후보 중 {rejected_numeric}건은 연도·숫자·단위 형식을 확인하지 못해 제외했습니다. 누락값을 0으로 채우지 않았습니다.")
    warnings.append(f"추출 결과: AI 후보 {len(rows)}건 / 반영 {len(facts)}건 / 제외 {len(rows) - len(facts)}건. 반영값도 원문 검토가 필요합니다.")
    if len({f.fiscal_year for f in facts}) < 2:
        warnings.append("비교연도 미확인: 원문 전기 비교열 누락 여부를 확인하십시오. 현재 자료만으로 3개년 분석을 완성하지 않습니다.")
    if cache is not None and not cached and facts:
        cache[cache_key] = {"result": raw_result, "meta": deepcopy(meta)}
        while len(cache) > 8:
            del cache[next(iter(cache))]
    if meta["cache_hit"]:
        warnings.append("동일 문서·모델·추출 조건의 저장 결과를 재사용했습니다. 외부 API를 호출하지 않았습니다.")
    return facts, warnings, meta

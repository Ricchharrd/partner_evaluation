"""Repair a saved PDF extraction without another paid model request."""
from __future__ import annotations

from dataclasses import asdict
import hashlib

from .ingest import extract_pdf_text
from .primary_statements import extract_primary_statements, sufficient_primary_coverage
from .schema import utc_now
from .workflow import log_action, recalculate


def repair_source_facts(project, source, content: bytes) -> int:
    if source.sha256 and hashlib.sha256(content).hexdigest() != source.sha256:
        raise ValueError("원문 파일이 이전 분석 때와 달라 자동 교체하지 않았습니다.")
    text, read_warnings = extract_pdf_text(content)
    facts, warnings = extract_primary_statements(text, project.entity.entity_id, source,
                                                  project.entity.reporting_scope)
    if not sufficient_primary_coverage(facts):
        raise ValueError("공식 연결 재무제표에서 핵심 항목을 충분히 확인하지 못했습니다. 기존 결과를 유지합니다.")
    previous = [fact for fact in project.facts if fact.source_id == source.source_id]
    if previous:
        project.narrative.setdefault("reextraction_history", []).append({
            "source_id": source.source_id, "at": utc_now(),
            "reason": "공식 연결 재무제표 행 재대조", "facts": [asdict(fact) for fact in previous],
        })
    project.facts = [fact for fact in project.facts if fact.source_id != source.source_id] + facts
    for entry in project.narrative.get("api_usage", []):
        if entry.get("source_id") == source.source_id and entry.get("conflicts"):
            entry["superseded_by"] = "공식 연결 재무제표 행 재대조"
    project.narrative.setdefault("api_usage", []).append({
        "source_id": source.source_id, "provider": "원문 표 파싱", "model": "primary-statements-1",
        "usage": {}, "repair": True, "at": utc_now(),
    })
    project.narrative["collection_warnings"] = [
        "이전 분할 추출에서 제외됐던 수치를 공식 연결 재무제표 행으로 재대조했습니다. "
        "기존값은 이력에 보관했습니다. 원문 및 계산값은 담당자가 확인해야 합니다.",
        *read_warnings, *warnings,
    ]
    recalculate(project)
    log_action(project, "공식 재무제표 재대조", f"{source.name}: {len(facts)}건, 이전값 {len(previous)}건 보관")
    return len(facts)

"""Explicit, resumable, full-coverage financial extraction with bounded API requests."""
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import re
import time

from .hitl import fingerprint
from .schema import FinancialFact
from .openai_provider import APIRequestError

CHUNK_BYTES = 20000
PLAN_VERSION = "full-coverage-batch-1"
REQUEST_GAP_SECONDS = 20
MAX_RETRY_WAIT = 90


def plan_chunks(text, limit=CHUNK_BYTES):
    """Prioritize statements and adjacent notes without dropping a source character."""
    matches = list(re.finditer(r"\[PAGE (\d+)\]", text))
    spans = []
    if not matches:
        spans.append((0, len(text), None))
    else:
        if matches[0].start():
            spans.append((0, matches[0].start(), None))
        for i, match in enumerate(matches):
            spans.append((match.start(), matches[i + 1].start() if i + 1 < len(matches) else len(text), int(match[1])))
    priority = set()
    terms = r"balance sheet|statement.{0,50}(?:financial position|income|cash flow)|income statement|notes to.{0,50}financial|재무상태표|손익계산서|현금흐름표|주석"
    for i, (start, end, _) in enumerate(spans):
        if re.search(terms, text[start:end], re.I):
            priority.update(range(max(0, i - 1), min(len(spans), i + 2)))
    chunks = []
    for i, (start, end, page) in enumerate(spans):
        cursor = start
        while cursor < end:
            prefix = ""
            if cursor > start:
                # Repeat page heading/header context when a single page needs splitting.
                prefix = text[start: min(start + 500, cursor)] + "\n"
            budget = limit - len(prefix.encode("utf-8"))
            raw = text[cursor:end].encode("utf-8")[:budget].decode("utf-8", errors="ignore")
            stop = cursor + len(raw)
            if stop < end:
                boundary = raw.rfind("\n")
                if boundary > len(raw) // 2:
                    stop = cursor + boundary + 1
            if stop <= cursor:
                raise ValueError("분할 크기가 문서 머리말보다 작습니다.")
            value = prefix + text[cursor:stop]
            chunks.append({"text": value, "spans": [[cursor, stop]], "pages": [page] if page is not None else [],
                           "priority": i in priority})
            cursor = stop
    # Pack adjacent page fragments to avoid one API call per short page.
    packed = []
    for chunk in chunks:
        if (packed and packed[-1]["priority"] == chunk["priority"] and
                len((packed[-1]["text"] + chunk["text"]).encode("utf-8")) <= limit and
                not set(packed[-1]["pages"]) & set(chunk["pages"])):
            packed[-1]["text"] += chunk["text"]
            packed[-1]["spans"].extend(chunk["spans"])
            packed[-1]["pages"] += chunk["pages"]
        else:
            packed.append(chunk)
    packed.sort(key=lambda chunk: (not chunk["priority"], chunk["spans"][0][0]))
    for i, chunk in enumerate(packed):
        chunk["id"] = f"{i + 1:03d}-" + hashlib.sha256(chunk["text"].encode()).hexdigest()[:12]
    return packed


def merge_facts(facts):
    groups = defaultdict(list)
    for fact in facts:
        key = (fact.entity_id, fact.fiscal_year, fact.standard_item)
        groups[key].append(fact)
    merged, conflicts = [], []
    for group in groups.values():
        if len({(f.normalized_value, f.currency, f.reporting_scope, f.period_start, f.period_end) for f in group}) > 1:
            conflicts.append({"year": group[0].fiscal_year, "item": group[0].standard_item,
                              "candidates": [asdict(f) for f in group]})
            continue
        fact = deepcopy(group[0])
        fact.source_locator = " || ".join(dict.fromkeys(f.source_locator for f in group))
        merged.append(fact)
    return merged, conflicts


def extract_batch(text, entity_id, source, provider, currency, scope, state, *,
                  force_refresh=False, progress=None, sleep=None):
    from .ai import extraction_request, extract_facts_from_text
    sleep = sleep or time.sleep
    chunks = plan_chunks(text)
    job_key = fingerprint({"version": PLAN_VERSION, "text": text, "entity": entity_id,
                           "source": source.sha256 or source.source_id, "scope": scope,
                           "currency": currency, "model": provider.model,
                           "prompt": extraction_request("", currency, scope)[0]})
    if state.get("key") != job_key or (force_refresh and state.get("status") == "completed"):
        state.clear()
        state.update(key=job_key, completed={}, attempts=[], status="prepared", plan=chunks)
    chunks = state["plan"]
    if state.pop("split_requested", False) and state.get("can_split"):
        revised = []
        for chunk in chunks:
            if chunk["id"] != state.get("failed_chunk"):
                revised.append(chunk)
                continue
            size = max(2500, len(chunk["text"].encode()) // 2)
            children = plan_chunks(chunk["text"], size)
            for child in children:
                child["id"] = chunk["id"] + "/" + child["id"]
            revised.extend(children)
        chunks = state["plan"] = revised
        state["can_split"] = False
    completed = state["completed"]
    pending = [c for c in chunks if c["id"] not in completed]
    emit = progress or (lambda event: None)
    summary = {"total_chunks": len(chunks), "completed_chunks": len(completed),
               "pending_chunks": len(pending), "max_requests": len(pending) * 2,
               "output_per_request": 6000, "original_characters": len(text),
               "coverage": "전체 텍스트, 재무제표 및 주석 후보 우선",
               "retry": "일시적 속도 제한만 구간별 1회, 최대 90초 대기, 그 외 중단",
               "pages": [{"id": c["id"], "pages": c["pages"], "priority": c["priority"]} for c in pending]}
    state["summary"] = summary
    requests = []
    for chunk in pending:
        system, payload, _, _ = extraction_request(chunk["text"], currency, scope)
        requests.append(provider.json_body(system, payload, max_tokens=6000))
    if pending:
        # Consent covers immutable request bodies, bounded retries and this invocation only.
        with provider.approved_batch(requests, summary):
            state["status"] = "running"
            for index, chunk in enumerate(pending):
                if index:
                    delay = provider.pause_before(requests[index], REQUEST_GAP_SECONDS)
                    if delay > MAX_RETRY_WAIT:
                        state.update(status="paused", failed_chunk=chunk["id"], can_split=False)
                        raise RuntimeError(f"API 처리량 회복까지 약 {delay:.0f}초가 필요합니다. 완료 {len(completed)}/{len(chunks)}구간은 보관했습니다. 대기 후 남은 구간을 다시 승인하세요.")
                    if delay:
                        emit({"phase": "waiting", "seconds": delay, "completed": len(completed), "total": len(chunks)})
                        sleep(delay)
                for attempt in range(2):
                    emit({"phase": "extracting", "chunk": chunk["id"], "pages": chunk["pages"],
                          "completed": len(completed), "total": len(chunks), "attempt": attempt + 1})
                    state["attempts"].append({"chunk": chunk["id"], "attempt": attempt + 1, "status": "started"})
                    try:
                        facts, warnings, meta = extract_facts_from_text(chunk["text"], entity_id, source, provider,
                            default_currency=currency, default_scope=scope, _chunk=True)
                    except Exception as exc:
                        state["attempts"][-1]["status"] = "failed"
                        delay = exc.retry_after if isinstance(exc, APIRequestError) else None
                        retry = (isinstance(exc, APIRequestError) and exc.code in {"rate_limit_exceeded", "slow_down", "server_is_overloaded"}
                                 and not exc.too_large and attempt == 0 and (delay is None or delay <= MAX_RETRY_WAIT))
                        if retry:
                            delay = max(1, delay if delay is not None else 60)
                            emit({"phase": "retry_wait", "seconds": delay, "completed": len(completed), "total": len(chunks)})
                            sleep(delay)
                            continue
                        can_split = ((isinstance(exc, APIRequestError) and (exc.too_large or exc.code == "context_length_exceeded"))
                                     or isinstance(exc, ValueError)) and len(chunk["text"].encode()) > 5000
                        state.update(status="paused", failed_chunk=chunk["id"], error=str(exc), can_split=can_split)
                        raise RuntimeError(f"분할 분석 {len(completed)}/{len(chunks)}구간 완료 후 중단했습니다. 완료 구간은 보관되며 기존 분석값은 변경하지 않았습니다. {exc}") from None
                    completed[chunk["id"]] = {"facts": [asdict(f) for f in facts], "warnings": warnings, "meta": meta}
                    state["attempts"][-1]["status"] = "completed"
                    emit({"phase": "completed", "completed": len(completed), "total": len(chunks)})
                    break
    all_facts, warnings, usage, new_usage = [], [], defaultdict(int), defaultdict(int)
    pending_ids = {c["id"] for c in pending}
    for chunk in chunks:
        result = completed[chunk["id"]]
        for row in result["facts"]:
            all_facts.append(FinancialFact(**{**row, "source_id": source.source_id}))
        warnings.extend(w for w in result["warnings"] if not w.startswith(("추출 결과:", "비교연도 미확인:", "전체 문서 분석:")))
        for key, value in result["meta"].get("usage", {}).items():
            if type(value) is int:
                usage[key] += value
                if chunk["id"] in pending_ids:
                    new_usage[key] += value
    facts, conflicts = merge_facts(all_facts)
    warnings.insert(0, f"전체 문서 {len(text):,}자를 {len(chunks)}구간으로 나누어 처리했습니다. 페이지를 자동 제외하지 않았습니다. 이미지는 별도 확인이 필요합니다.")
    if conflicts:
        warnings.append(f"상충하는 재무항목 {len(conflicts)}개는 계산에서 제외했습니다. 분할 분석 근거의 후보값을 원문과 대조하세요.")
    if len({f.fiscal_year for f in facts}) < 2:
        warnings.append("비교연도 미확인: 원문 전기 비교열을 확인하세요.")
    if not pending:
        warnings.append("완료된 분할 분석 결과를 재사용했습니다. 추가 API 호출은 없습니다.")
    state["status"] = "completed"
    state.pop("error", None)
    state.pop("failed_chunk", None)
    state.pop("can_split", None)
    meta = {"provider": provider.name, "model": provider.model, "cache_hit": not pending,
            "source_id": source.source_id,
            "usage": dict(new_usage), "cumulative_usage": dict(usage),
            "selection_version": PLAN_VERSION, "original_characters": len(text), "selected_characters": len(text),
            "chunks": len(chunks), "resumed_chunks": len(chunks) - len(pending),
            "conflicts": conflicts, "attempts": deepcopy(state["attempts"])}
    meta["usage_note"] = "usage는 이번 실행 성공 응답, cumulative_usage는 완료 구간 누적값입니다. 실패 또는 응답 유실 요청의 과금 여부는 API 사용 내역에서 확인하세요."
    return facts, list(dict.fromkeys(warnings)), meta

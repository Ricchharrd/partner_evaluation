"""Request-scoped consent and evidence-bound human review, without storing secrets."""
import hashlib
import json
import re
import time
from .schema import utc_now

# Cost-saving caps are temporarily disabled. Provider/model limits still apply.
MAX_INPUT_BYTES = None
MAX_OUTPUT_TOKENS = None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def review_digest(project):
    return fingerprint({"entity": project.entity.__dict__, "facts": [f.__dict__ for f in project.facts],
        "sources": [s.__dict__ for s in project.sources], "versions": project.versions,
        "research": project.narrative.get("research_briefs", []),
        "business": project.narrative.get("business_evidence", []),
        "updates": project.narrative.get("partner_updates", []),
        "warnings": project.narrative.get("collection_warnings", []),
        "route": project.narrative.get("analysis_route", "public_financials"),
        "ratios": [r.__dict__ for r in project.ratios],
        "validations": [v.__dict__ for v in project.validations],
        "fx": project.narrative.get("fx_display", []),
        "final_text": project.narrative.get("final", {})})


def current_review(project):
    record = project.narrative.get("hitl_review", {})
    return bool(record and record.get("digest") == review_digest(project))


def request_texts(value, depth=0):
    """Inspect actual text lines, including JSON encoded inside Responses input messages."""
    if isinstance(value, dict):
        for item in value.values():
            yield from request_texts(item, depth)
    elif isinstance(value, list):
        for item in value:
            yield from request_texts(item, depth)
    elif isinstance(value, str):
        if depth < 3 and value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except ValueError:
                pass
            else:
                yield from request_texts(parsed, depth + 1)
                return
        yield value


def preflight(body):
    raw = json.dumps(body, ensure_ascii=False)
    texts = list(request_texts(body))
    inspection = "\n".join(texts)
    blocked = bool(re.search(r"sk-[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|(?:password|비밀번호)\s*[:=]\s*[^\s]{6,}", inspection, re.I))
    sensitive = bool(re.search(r"strictly private|internal only|strictly confidential|private and confidential|대외비|사외비|비공개|주민등록|비밀번호|password\s*[:=]", inspection, re.I))
    # A public annual report can discuss confidentiality without being classified.
    classification = r"(?im)^\s*(?:classification\s*[:=-]\s*)?confidential(?:\s*[-:/|].*)?\s*$|(?:this\s+(?:document|report)|document\s+classification)\s*(?:is\s+|[:=-]\s*)confidential\b|confidential\s*[-:/|]\s*(?:not for|do not|internal)|confidential\s+(?:document|report)\b"
    sensitive = sensitive or any(re.search(classification, text) for text in texts)
    sensitive_terms = bool(re.search(r"\bconfidential(?:ity)?\b", inspection, re.I)) and not sensitive
    # Deliberately conservative heuristic, not a tokenizer or billing upper bound.
    tokens = len(raw.encode("utf-8"))
    output = body.get("max_output_tokens", body.get("max_tokens"))
    tools = bool(body.get("tools"))
    bounded_search = (type(body.get("max_tool_calls")) is int and 0 < body["max_tool_calls"] <= 3
                      and tools and all(t.get("type") == "web_search" and
                      t.get("search_context_size") == "low" for t in body["tools"]))
    cost = None  # No unverified or model-independent price assumptions.
    return {"hash": fingerprint(body), "model": body.get("model"), "estimated_input_tokens": tokens,
            "output_limit": output, "estimated_text_usd": cost, "web_tools": tools,
            "input_bytes": tokens, "high_volume": tokens > 20000 or (tools and not bounded_search),
            "bounded_search": bounded_search,
            "over_limit": ((MAX_INPUT_BYTES is not None and tokens > MAX_INPUT_BYTES)
                           or (MAX_OUTPUT_TOKENS is not None and output is not None and output > MAX_OUTPUT_TOKENS)),
            "blocked": blocked, "sensitive": sensitive, "sensitive_terms": sensitive_terms}


def consume_ticket(tickets, request_hash, now=None):
    ticket = tickets.pop(request_hash, None)
    return bool(ticket and ticket["expires"] >= (time.time() if now is None else now))


def authorize_request(project, body, *, action=None):
    import streamlit as st
    info = preflight(body)
    key = project.project_id + ":" + info["hash"]
    tickets = st.session_state.setdefault("hitl_tickets", {})
    if info["over_limit"]:
        raise ValueError("앱에 설정된 처리 한도를 초과했습니다. 일부 페이지를 자동 제외하지 않습니다.")
    if info["blocked"]:
        raise ValueError("보안 차단: 인증정보 의심 문자열이 있습니다. 원문에서 제거한 뒤 다시 검사하십시오. 승인으로 우회할 수 없습니다.")
    if info["sensitive"]:
        raise ValueError("공개자료 전용: 민감정보 표시가 탐지되었습니다. 원문 공개 여부를 확인하십시오. 비공개 자료는 사내 Claude에서만 처리하며 여기서 승인으로 우회할 수 없습니다.")
    if consume_ticket(tickets, key):
        project.narrative.setdefault("hitl_call_log", []).append({**info, "at": utc_now(), "status": "승인된 요청 시도"})
        return
    # Body stays in this session only; preview is never written to project audit logs.
    st.session_state["hitl_pending"] = {"project_id": project.project_id, "info": info, "body": body, "action": action}
    st.rerun()


def render_hitl(project, persist, *, include_fact_review=True, show_review=True, allowed_actions=None,
                finance_flow=False):
    import streamlit as st
    approved_action = None
    pending = st.session_state.get("hitl_pending")
    if pending and pending["project_id"] == project.project_id:
        info = preflight(pending["body"])
        batch = pending["body"].get("batch") if pending["body"].get("kind") == "finance_batch_v1" else None
        prefix = "hitl_" + info["hash"][:16]
        st.subheader("2. 실행 내용을 확인해 주세요" if finance_flow else "한 번 확인하고 바로 실행하세요")
        task = "공개 기사·사업정보 조사" if info["web_tools"] else "공개자료 AI 분석"
        st.write(f"**{project.entity.legal_name} · {task}**")
        st.caption("버튼을 누르면 이번 자료·검색어가 공개자료이며 회사 정책상 개인 OpenAI API 전송이 허용됨을 확인합니다. 비공개 자료는 사내 Claude에서만 처리하세요.")
        if batch:
            st.info(f"전체 {batch['total_chunks']}구간 중 {batch['completed_chunks']}구간 완료, 남은 {batch['pending_chunks']}구간을 순서대로 분석합니다.")
            st.write(f"기본 {batch['pending_chunks']}회, 일시적 제한 재시도 포함 최대 {batch['max_requests']}회 유료 요청을 승인합니다.")
            st.caption("재무제표와 주석 후보를 우선 처리한 뒤 나머지 페이지도 분석합니다. 요청 사이 대기시간이 있으며 중단 시 완료 구간을 현재 세션에 보관합니다.")
            st.caption("일시적 속도 제한만 구간별 1회 재시도합니다 (최대 90초 대기). 결제 문제, 큰 요청, 시간 초과는 자동 재시도하지 않습니다. 각 구간 출력은 최대 6,000토큰이며 잘린 응답을 완료로 처리하지 않습니다.")
        else:
            st.caption("유료 요청 1회를 승인합니다.")
        if info["bounded_search"]:
            st.caption("웹 검색 최대 3회 · 간략 검색 · 답변 길이 제한 적용. 검색·토큰 비용은 발생하며 사전 금액은 확정할 수 없습니다.")
        if info["high_volume"]:
            st.info("문서 전체를 분석하므로 처리시간과 비용이 늘어날 수 있습니다. 실행 후 결과 화면으로 자동 이동합니다." if finance_flow
                    else "입력이 크거나 검색 범위가 큽니다. 비용을 줄이려면 취소 후 필요한 페이지·범위만 선택하세요.")
        if finance_flow and info["output_limit"] is None and not batch:
            st.caption("보고서 전체 전송, 앱의 입력량·출력 토큰 절약 제한 해제. 모델 자체 처리량과 계정 사용 한도는 적용됩니다. 비용이 커질 수 있습니다.")
        if info["sensitive_terms"]:
            st.warning("본문에 기밀유지 관련 용어가 있으나 문서 기밀 표시로 확정하지 않았습니다. 원문이 실제 공개 보고서인지 확인한 뒤 승인하세요. 자동 탐지는 보안 승인을 대신하지 않습니다.")
        if info["blocked"] or info["sensitive"] or info["over_limit"]:
            st.error("보안 또는 처리 한도로 실행할 수 없습니다. 자료를 제거하거나 범위를 줄여 다시 준비하세요. 승인으로 우회할 수 없습니다.")
        with st.expander("전송 내용·모델·처리 한도 보기"):
            output_label = f"최대 {info['output_limit']:,}토큰" if info["output_limit"] is not None else "앱 상한 없음 (모델 한도 적용)"
            st.write(f"개인 OpenAI API / {info['model']} / 입력 {info['input_bytes']:,}바이트 / 출력 {output_label}")
            st.caption("바이트 수는 토큰 수가 아닙니다. 자동 탐지는 보안 승인이나 안전 보증이 아닙니다. 실제 사용량은 처리 후 확인하세요. API 키는 아래에 포함하지 않습니다.")
            st.json(pending["body"])
        volume = st.checkbox("추가 비용 가능성을 확인했으며 이 범위로 진행합니다.", key=prefix + "volume") if info["high_volume"] else True
        reachable = allowed_actions is None or pending.get("action") in allowed_actions or pending.get("action") is None
        if not reachable:
            st.info("이 요청을 실행하려면 자료 준비 단계의 원래 분석 경로로 돌아가거나 취소하세요.")
        ready = volume and reachable and not any(info[k] for k in ("blocked", "sensitive", "over_limit"))
        if st.button("승인하고 전체 분석 시작 (유료)" if finance_flow else "공개자료로 승인하고 실행", key=prefix + "approve", disabled=not ready, type="primary"):
            key = project.project_id + ":" + info["hash"]
            project.narrative.setdefault("hitl_approvals", []).append({**info, "at": utc_now(),
                "reviewer": "현재 세션 사용자 (본인 미인증)", "consent_method": "명시적 실행 버튼",
                "classification": "공개자료 (사용자 선언)", "basis": "공개성·외부전송 허용 여부에 대한 사용자 확인; 출처 검증 아님",
                "declared_scope": task, "action": pending.get("action"),
                "scope": f"동일 분할 계획 최대 {batch['max_requests']}회, 10분 이내 시작" if batch else "동일 요청 1회·10분 이내",
                **({"batch": batch} if batch else {})})
            persist()
            st.session_state.setdefault("hitl_tickets", {})[key] = {"expires": time.time() + 600}
            del st.session_state["hitl_pending"]
            approved_action = pending.get("action")
            if approved_action is None:
                st.info("상세 도구의 실행 승인을 기록했습니다. 해당 도구에서 실행하면 동일 요청 1회만 전송됩니다.")
        if st.button(("취소: 완료 구간 보관" if batch and batch['completed_chunks'] else "이전: 파일 다시 선택") if finance_flow else "취소", key=prefix + "cancel"):
            st.session_state.pop("hitl_pending", None)
            st.rerun()
    if not show_review or (not project.facts and not project.narrative.get("research_briefs")):
        return approved_action
    render_evidence_review(project, persist, include_fact_review=include_fact_review)
    return approved_action


def clear_action_tickets(project):
    """Discard unused consent after a resumed action, including failures or cache hits."""
    import streamlit as st
    tickets = st.session_state.get("hitl_tickets", {})
    for key in list(tickets):
        if key.startswith(project.project_id + ":"):
            tickets.pop(key, None)


def quick_review_blocker(project):
    from .workflow import is_current
    financial = project.narrative.get("analysis_route") != "news_only" and bool(project.facts)
    if not financial and not project.narrative.get("research_briefs"):
        return "확인할 분석 결과가 없습니다."
    if financial and (not is_current(project) or any(v.severity == "오류" for v in project.validations)):
        return "수치 오류를 수정한 뒤 확인하거나, 미검토 초안으로 전달하세요."
    return None


def record_quick_review(project):
    """Record an explicit user attestation, not automatic verification or final approval."""
    blocker = quick_review_blocker(project)
    if blocker:
        raise ValueError(blocker)
    if current_review(project):
        return
    at = utc_now()
    reviewer = "현재 세션 사용자 (본인 미인증)"
    note = "사용자가 결과·원문 근거·표시된 한계를 확인하고 전달 버튼을 누름. 자동 원문 검증이나 최종 승인 아님."
    approved_ids = []
    for brief in project.narrative.get("research_briefs", []):
        if brief.get("status") == "검토 대기":
            brief.update(status="승인", reviewer=reviewer, review_note=note, reviewed_at=at)
            approved_ids.append(brief["id"])
    project.narrative.pop("final", None)
    project.narrative.pop("ai_cache", None)
    project.narrative.pop("review", None)
    project.status = "검토 중"
    record = {"at": at, "reviewer": reviewer, "note": note, "decision": "검토 확인 · 한계 유지",
              "method": "통합 확인 버튼", "research_ids": approved_ids, "digest": review_digest(project)}
    project.narrative.setdefault("hitl_review_history", []).append(record)
    project.narrative["hitl_review"] = record


def render_evidence_review(project, persist, *, include_fact_review=True):
    import streamlit as st
    with st.expander("사람의 검토 · 대상/수치/예외/사업정보", expanded=not current_review(project)):
        st.write(f"대상: {project.entity.legal_name} / {project.entity.reporting_scope} / 연도: {sorted({f.fiscal_year for f in project.facts})}")
        st.caption("미확인 정보는 임의로 승인하지 말고 수정하거나 보류하십시오. 수정은 상세 편집 도구에서 합니다. 자료·계산·조사 결과가 바뀌면 이 확인은 무효화됩니다.")
        if include_fact_review:
            st.dataframe([{"연도": f.fiscal_year, "항목": f.standard_item, "원문명": f.original_label,
            "값": f.effective_value, "통화": f.currency, "단위배수": f.unit_multiplier,
            "근거": f.source_locator, "상태": f.validation_status} for f in project.facts], hide_index=True)
        for v in project.validations:
            if v.severity in {"오류", "경고"}:
                st.write(f"{v.severity} FY{v.fiscal_year}: {v.message}")
        st.caption("환율 대용치, APM, 누락값의 제한을 유지합니다. 승인만으로 수치를 바꾸거나 오류를 해제하지 않습니다.")
        digest = review_digest(project)
        with st.form("review_" + digest[:16]):
            reviewer = st.text_input("검토 담당자")
            identity = st.checkbox("법인·자료 종류·회계기간·연결/별도 기준을 확인했습니다.")
            numbers = st.checkbox("핵심 수치와 원문 근거·통화·단위를 대조했습니다." if include_fact_review else "공개 현안의 원문 출처·발표일·사건일을 확인했습니다. 재무평가는 사내에서 별도 수행합니다.")
            exceptions = st.checkbox("누락·APM·환율 등 예외와 분석 보류 범위를 확인했습니다.")
            business = st.checkbox("사업정보의 사실/해석을 구분하고 미검토 자료는 보류했습니다.")
            note = st.text_area("항목별 확인 내용·미해결 사항·추가 자료·조치 담당자")
            decision = st.selectbox("처리", ["보류 · 자료 보완", "검토 확인 · 한계 유지"])
            submitted = st.form_submit_button("검토 기록 저장")
        if submitted:
            if not reviewer.strip() or not note.strip():
                st.error("검토자와 확인·조치 내용을 입력하십시오.")
            elif decision.startswith("검토") and not all([identity, numbers, exceptions, business]):
                st.error("네 가지 확인을 완료하거나 보류로 기록하십시오.")
            else:
                record = {"at": utc_now(), "reviewer": reviewer, "note": note, "decision": decision, "digest": digest}
                project.narrative.setdefault("hitl_review_history", []).append(record)
                project.narrative["hitl_review"] = record if decision.startswith("검토") else {}
                persist()
                st.rerun()

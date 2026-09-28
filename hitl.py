"""Request-scoped consent and evidence-bound human review, without storing secrets."""
import hashlib
import json
import re
import time
from .schema import utc_now


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def review_digest(project):
    return fingerprint({"entity": project.entity.__dict__, "facts": [f.__dict__ for f in project.facts],
        "sources": [s.__dict__ for s in project.sources], "versions": project.versions,
        "research": project.narrative.get("research_briefs", []),
        "business": project.narrative.get("business_evidence", []),
        "updates": project.narrative.get("partner_updates", []),
        "policy": project.narrative.get("policy_evaluation", []),
        "warnings": project.narrative.get("collection_warnings", []),
        "final_text": project.narrative.get("final", {})})


def current_review(project):
    record = project.narrative.get("hitl_review", {})
    return bool(record and record.get("digest") == review_digest(project))


def preflight(body):
    raw = json.dumps(body, ensure_ascii=False)
    blocked = bool(re.search(r"sk-[A-Za-z0-9_-]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|(?:password|비밀번호)\s*[:=]\s*[^\s]{6,}", raw, re.I))
    sensitive = bool(re.search(r"confidential|strictly private|internal only|대외비|사외비|비공개|주민등록|비밀번호|password\s*[:=]", raw, re.I))
    # Deliberately conservative heuristic, not a tokenizer or billing upper bound.
    tokens = len(raw.encode("utf-8"))
    output = int(body.get("max_output_tokens", 5000))
    tools = bool(body.get("tools"))
    cost = (tokens * .1 + output * .5) / 1_000_000 if body.get("model") == "gpt-6-luna" else None
    return {"hash": fingerprint(body), "model": body.get("model"), "estimated_input_tokens": tokens,
            "output_limit": output, "estimated_text_usd": cost, "web_tools": tools,
            "high_volume": tokens > 20000 or tools, "blocked": blocked, "sensitive": sensitive}


def consume_ticket(tickets, request_hash, now=None):
    ticket = tickets.pop(request_hash, None)
    return bool(ticket and ticket["expires"] >= (time.time() if now is None else now))


def authorize_request(project, body):
    import streamlit as st
    info = preflight(body)
    key = project.project_id + ":" + info["hash"]
    tickets = st.session_state.setdefault("hitl_tickets", {})
    if info["blocked"]:
        raise ValueError("보안 차단: 인증정보 의심 문자열이 있습니다. 원문에서 제거한 뒤 다시 검사하십시오. 승인으로 우회할 수 없습니다.")
    if info["sensitive"]:
        raise ValueError("공개자료 전용: 민감정보 표시가 탐지되었습니다. 원문 공개 여부를 확인하십시오. 비공개 자료는 사내 Claude에서만 처리하며 여기서 승인으로 우회할 수 없습니다.")
    if consume_ticket(tickets, key):
        project.narrative.setdefault("hitl_call_log", []).append({**info, "at": utc_now(), "status": "승인된 요청 시도"})
        return
    # Body stays in this session only; preview is never written to project audit logs.
    st.session_state["hitl_pending"] = {"project_id": project.project_id, "info": info, "body": body}
    st.rerun()


def render_hitl(project, persist, *, include_fact_review=True):
    import streamlit as st
    pending = st.session_state.get("hitl_pending")
    if pending and pending["project_id"] == project.project_id:
        info = pending["info"]
        prefix = "hitl_" + info["hash"][:16]
        st.subheader("외부 AI 호출 전 확인 · 아직 전송하지 않았습니다")
        st.write(f"전송처: 개인 OpenAI API / 모델: {info['model']}")
        st.write(f"평가 대상: {project.entity.legal_name} / 국가: {project.entity.country or '미확인'}")
        st.write(f"입력 규모 추정: {info['estimated_input_tokens']:,} 토큰 / 출력 한도: {info['output_limit']:,} 토큰 / 이번 승인: 요청 1회")
        if info["estimated_text_usd"] is not None:
            st.caption(f"텍스트 예상비용 약 ${info['estimated_text_usd']:.4f}. UTF-8 바이트 기반 보수적 추정이며 실제 토큰·청구액과 다릅니다. Luna 입력 $0.10/출력 $0.50 per 1M 기준(2026-09-27). 검색료·도구 결과·장문 할증 제외, 비용 상한 보장 아님.")
        else:
            st.warning("이 모델의 비용은 계산하지 못했습니다. 서비스 요금을 별도로 확인하십시오.")
        if info["high_volume"]:
            st.warning("대량 입력 또는 웹 조사입니다. 필요한 페이지만 남기거나 범위를 줄일 수 있습니다. 검색 결과 토큰과 도구 비용은 사전에 확정할 수 없습니다.")
        if info["sensitive"]:
            st.warning("민감정보 표시가 탐지됐습니다. 공개자료 여부를 다시 확인하거나 회사의 외부전송 승인 근거를 기록하십시오.")
        st.caption("자동 탐지는 완전하지 않습니다. 탐지 없음은 보안 승인이나 안전 보증이 아닙니다. 사내 평가기준·후보사 목록도 내부정보일 수 있습니다.")
        with st.expander("실제로 전송할 내용 확인 (API 키 제외)"):
            st.json(pending["body"])
        reviewer = st.text_input("요청 확인자", key=prefix + "who")
        scope = st.text_input("문서 종류·대상 연도·연결/별도 또는 조사 범위", key=prefix + "scope")
        identity = st.checkbox("대상 법인과 이번 자료·조사 범위를 확인했습니다. 불명확한 정보는 잠정으로 유지합니다.", key=prefix + "identity")
        classification = st.selectbox("자료 보안 분류", ["선택 필요", "공개자료", "비공개·판단 불가 (사내 Claude에서 처리)"], key=prefix + "class")
        basis = st.text_input("공개 출처와 공개 여부 확인 근거", key=prefix + "basis")
        security = st.checkbox("전송 내용을 확인했으며 회사 정책상 이 전송이 허용됩니다. 금지된 전송을 승인하는 것이 아닙니다.", key=prefix + "security")
        cost = st.checkbox("표시된 규모와 비용 불확실성을 확인하고 이번 1회 처리를 승인합니다.", key=prefix + "cost")
        ready = reviewer.strip() and basis.strip() and scope.strip() and identity and security and cost and classification == "공개자료" and not info["blocked"] and not info["sensitive"]
        if st.button("승인 기록 후 계속", key=prefix + "approve", disabled=not ready):
            key = project.project_id + ":" + info["hash"]
            st.session_state.setdefault("hitl_tickets", {})[key] = {"expires": time.time() + 600}
            project.narrative.setdefault("hitl_approvals", []).append({**info, "at": utc_now(), "reviewer": reviewer,
                "classification": classification, "basis": basis, "declared_scope": scope, "scope": "동일 요청 1회·10분 이내"})
            persist()
            del st.session_state["hitl_pending"]
            st.session_state["hitl_next"] = "승인했습니다. 아래에서 방금 사용한 분석·조사 버튼을 다시 누르십시오. 동일 요청 1회만 전송됩니다."
            st.rerun()
        if st.button("취소 · 자료 제거/범위 축소 후 다시 준비", key=prefix + "cancel"):
            del st.session_state["hitl_pending"]
            st.rerun()
        st.info("승인하지 않으면 아래 분석 버튼을 눌러도 외부 호출은 실행되지 않습니다.")
    if st.session_state.get("hitl_next"):
        st.info(st.session_state.pop("hitl_next"))
    if not project.facts or not include_fact_review:
        return
    with st.expander("사람의 검토 · 대상/수치/예외/사업정보", expanded=not current_review(project)):
        st.write(f"대상: {project.entity.legal_name} / {project.entity.reporting_scope} / 연도: {sorted({f.fiscal_year for f in project.facts})}")
        st.caption("미확인 정보는 임의로 승인하지 말고 수정하거나 보류하십시오. 수정은 상세 편집 도구에서 합니다. 자료·계산·조사 결과가 바뀌면 이 확인은 무효화됩니다.")
        st.dataframe([{"연도": f.fiscal_year, "항목": f.standard_item, "원문명": f.original_label,
            "값": f.effective_value, "통화": f.currency, "단위배수": f.unit_multiplier,
            "근거": f.source_locator, "상태": f.validation_status} for f in project.facts], hide_index=True)
        for v in project.validations:
            if v.severity in {"오류", "경고"}:
                st.write(f"{v.severity} FY{v.fiscal_year}: {v.message}")
        st.caption("환율·Altman 대용치, APM, 누락값의 제한을 유지합니다. 승인만으로 수치를 바꾸거나 오류를 해제하지 않습니다.")
        digest = review_digest(project)
        with st.form("review_" + digest[:16]):
            reviewer = st.text_input("검토 담당자")
            identity = st.checkbox("법인·자료 종류·회계기간·연결/별도 기준을 확인했습니다.")
            numbers = st.checkbox("핵심 수치와 원문 근거·통화·단위를 대조했습니다.")
            exceptions = st.checkbox("누락·APM·환율·Z-score 등 예외와 평가 보류 범위를 확인했습니다.")
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

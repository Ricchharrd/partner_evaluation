from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pandas as pd
import streamlit as st

from .intelligence import collect_sec_updates, merge_updates, public_link, review_update
from .schema import utc_now
from .workflow import assessment_rows, is_current, log_action


def portfolio(store, owner):
    st.subheader("파트너 포트폴리오")
    projects = [store.load(row["project_id"], owner) for row in store.list_projects(owner)]
    if not projects:
        st.info("기업 분석을 생성하고 저장하면 이곳에 기업별 평가와 동향이 나타납니다.")
        return
    rows = []
    for project in projects:
        evaluations = project.narrative.get("policy_evaluation", [])
        last = evaluations[-1] if evaluations and is_current(project) else {}
        updates = project.narrative.get("partner_updates", [])
        review = project.narrative.get("review", {})
        rows.append({"기업": project.entity.legal_name, "국가": project.entity.country,
                     "역할": project.narrative.get("partner_role", "미등록"),
                     "평가연도": last.get("fiscal_year"), "잠정등급": last.get("grade") or "보류/미산정",
                     "상태": project.status if is_current(project) else "계산 필요",
                     "최근 검토": review.get("at", "미검토"),
                     "동향 검토대기": sum(u["status"] == "검토 대기" for u in updates),
                     "평가 후 승인동향": sum(u["status"] == "승인" and u["published_at"] > review.get("at", "9999")[:10] for u in updates),
                     "마지막 수집": project.narrative.get("last_monitor_check", "미수집")})
    a, b, c = st.columns(3)
    a.metric("등록 기업", len(projects))
    b.metric("검토 완료", sum(p.status == "검토 완료" and is_current(p) for p in projects))
    c.metric("동향 검토 대기", sum(r["동향 검토대기"] for r in rows))
    st.dataframe(rows, hide_index=True, width="stretch")
    selected = st.selectbox("기업 열기", range(len(projects)), format_func=lambda i: f"{projects[i].entity.legal_name} · {projects[i].project_id[:6]}")
    def open_selected():
        st.session_state.project = projects[selected]
        st.session_state.main_page = "기업 상세"
    st.button("선택 기업 상세 보기", type="primary", on_click=open_selected)
    st.caption("동향 승인만으로 재무점수는 변경되지 않습니다. 신규 공시는 재무자료 재수집·재검토의 계기입니다.")
    st.subheader("기업 간 재무비율 비교")
    metric = st.selectbox("비교 지표", ["operating_margin", "debt_ratio", "current_ratio", "interest_coverage"],
                          format_func=lambda k: {"operating_margin": "영업이익률 (%)", "debt_ratio": "부채비율 (%)", "current_ratio": "유동비율 (%)", "interest_coverage": "이자보상배율 (배)"}[k])
    chart = [{"연도": r.fiscal_year, "기업": f"{p.entity.legal_name} · {p.project_id[:6]}", "값": r.value * (1 if metric == "interest_coverage" else 100)}
             for p in projects if is_current(p) for r in p.ratios if r.metric_key == metric and r.value is not None and r.fiscal_year in sorted({f.fiscal_year for f in p.facts})[-3:]]
    if chart:
        st.line_chart(pd.DataFrame(chart).pivot(index="연도", columns="기업", values="값"))


def assessment(project):
    st.subheader("재무역량 평가표")
    if not is_current(project):
        st.info("자료 입력·검토에서 최신 입력으로 검증 및 계산을 실행하십시오.")
        return
    rows = assessment_rows(project)
    if rows:
        st.dataframe(pd.DataFrame(rows).fillna("미확인").astype(str), hide_index=True, width="stretch")
    for row in project.narrative.get("policy_evaluation", []):
        if row.get("reason"):
            st.warning(f"FY{row.get('fiscal_year', '-')}: {row['reason']}")
    st.caption("잠정 사내정책 평가. 누락값은 미확인으로 보류합니다. 검토 완료는 공식 신용등급 확정을 의미하지 않습니다.")
    with st.expander("계산 근거·환율·감점"):
        st.write("Altman: 장부자본 및 영업이익 대용치. 원형의 시가총액·EBIT 기반 점수와 다릅니다. Z<1이면 -2점, 연속된 2개년 동일 지표(영업CF 또는 순이익) 적자이면 추가 최대 -2점. 공사예가 가점 제외.")
        st.dataframe(project.narrative.get("fx_display", []), hide_index=True)
        for row in project.narrative.get("policy_evaluation", []):
            st.write(f"FY{row.get('fiscal_year', '-')}", row.get("currency_note", ""))
            st.dataframe(row.get("components", []), hide_index=True)
            st.write("감점", row.get("adjustments", []))


def business_panel(project, persist):
    st.subheader("사업역량 및 수행실적")
    st.caption("출처 근거를 등록하고 검토합니다. 자동 검색·독립적인 사실검증이 완료된 자료로 간주하지 않습니다.")
    roles = ["EPC", "개발·투자", "운영", "현지 파트너", "기타"]
    role = st.selectbox("평가 대상 역할", roles, index=roles.index(project.narrative.get("partner_role", "EPC")), key=f"role_{project.project_id}")
    with st.form(f"business_{project.project_id}"):
        title = st.text_input("실적 또는 확인사항")
        description = st.text_area("기업의 실제 수행 역할·사업규모·기간·상태")
        url = st.text_input("원문 출처 URL (HTTPS)")
        locator = st.text_input("근거 위치 (페이지·문단)")
        submitted = st.form_submit_button("근거 등록")
    if submitted:
        if not title.strip() or not description.strip() or not locator.strip() or not public_link(url):
            st.error("제목·내용·원문 위치와 HTTPS 출처를 입력하십시오.")
        else:
            project.narrative["partner_role"] = role
            project.narrative.setdefault("business_evidence", []).append({"id": uuid4().hex, "role": role, "title": title,
                "description": description, "source_url": url.strip(), "locator": locator, "status": "검토 대기", "collected_at": utc_now()})
            project.status = "재검토 필요"
            log_action(project, "사업근거 등록", title)
            persist()
            st.rerun()
    for row in project.narrative.get("business_evidence", []):
        with st.expander(f"{row['title']} · {row['status']}"):
            st.write(row["description"])
            st.link_button("원문 확인", row["source_url"])
            st.caption(row["locator"])
            with st.form(f"business_review_{row['id']}"):
                reviewer = st.text_input("검토자")
                note = st.text_input("검토의견")
                decision = st.selectbox("판정", ["확인", "추가 확인", "제외"])
                save = st.form_submit_button("사업근거 검토 저장")
            if save:
                if not reviewer.strip() or not note.strip():
                    st.error("검토자와 의견이 필요합니다.")
                else:
                    row.update(status=decision, reviewer=reviewer, review_note=note, reviewed_at=utc_now())
                    project.status = "재검토 필요"
                    project.narrative.pop("final", None)
                    project.narrative.pop("ai_cache", None)
                    log_action(project, "사업근거 검토", f"{row['id']}: {decision} / {reviewer} / {note}")
                    persist()
                    st.rerun()


def updates_panel(project, persist, user_agent):
    st.subheader("기업 동향")
    c1, c2 = st.columns(2)
    start = c1.date_input("공시 검색 시작", date.today() - timedelta(days=90))
    end = c2.date_input("공시 검색 종료", date.today())
    if st.button("SEC 공시 새로고침", disabled=not project.entity.identifiers.get("cik")):
        try:
            with st.spinner("SEC 최근 공시 목록을 수집합니다..."):
                count = merge_updates(project, collect_sec_updates(project.entity.identifiers["cik"], start, end, user_agent))
            persist()
            st.success(f"새 공시 {count}건. 기존 검토 상태는 유지됩니다.")
        except Exception as exc:
            st.error(f"공시 수집 실패: {exc}")
    st.caption("SEC recent 목록 범위만 조회합니다. 과거 전체 공시·일반 뉴스 자동 검색은 아직 연결하지 않았습니다.")
    with st.expander("공식 발표·뉴스 직접 등록"):
        with st.form(f"news_{project.project_id}"):
            title = st.text_input("동향 제목")
            summary = st.text_area("확인된 사실 / 추가 확인 사항")
            url = st.text_input("출처 URL")
            published = st.date_input("발표일")
            submitted = st.form_submit_button("검토 대기로 등록")
        if submitted:
            try:
                if not title.strip() or not summary.strip():
                    raise ValueError("제목과 내용을 입력하십시오.")
                merge_updates(project, [{"title": title, "summary": summary, "source_url": url, "published_at": published.isoformat(),
                                         "topic": "수동 등록", "source_name": "사용자 등록", "severity": "검토", "event_date": ""}])
                persist()
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
    rows = project.narrative.get("partner_updates", [])
    for row in sorted(rows, key=lambda r: r["published_at"], reverse=True):
        with st.expander(f"{row['published_at']} · {row['title']} · {row['status']}"):
            st.write(row["summary"])
            st.link_button("원문 열기", row["source_url"])
            st.caption(f"수집: {row['collected_at']} | 이벤트 발생일: {row.get('event_date') or '미확인'}")
            with st.form(f"update_{row['id']}"):
                reviewer = st.text_input("검토자")
                note = st.text_area("검토의견 / 재평가 필요 사유")
                decision = st.selectbox("처리", ["승인", "제외"])
                submit = st.form_submit_button("동향 검토 저장")
            if submit:
                try:
                    review_update(project, row["id"], decision == "승인", reviewer, note)
                    persist()
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def effort_panel(project, persist):
    st.subheader("시범 적용 및 검토 기록")
    with st.form(f"effort_{project.project_id}"):
        method = st.selectbox("작업 방식", ["기존 수작업", "Agent 활용"])
        stage = st.selectbox("단계", ["자료 확보", "자료 검증·수정", "분석", "보고서 검토"])
        minutes = st.number_input("실제 담당자 작업시간 (분)", min_value=0.0)
        waiting = st.number_input("별도 시스템 대기시간 (분)", min_value=0.0)
        scope = st.text_input("비교 범위·담당자·비고")
        submitted = st.form_submit_button("작업시간 기록")
    if submitted:
        if not scope.strip():
            st.error("비교 범위와 담당자를 기록하십시오.")
        else:
            project.narrative.setdefault("effort_log", []).append({"at": utc_now(), "method": method, "stage": stage,
                "minutes": minutes, "waiting_minutes": waiting, "scope": scope})
            persist()
            st.rerun()
    st.dataframe(project.narrative.get("effort_log", []), hide_index=True)
    st.caption("동일 범위의 비교가 확인되기 전에는 시간 절감률을 자동 산정하지 않습니다.")
    st.dataframe(project.narrative.get("audit_log", []), hide_index=True)
    st.write("평가 보관본 수:", len(project.narrative.get("assessment_history", [])))

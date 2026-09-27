from datetime import date
import streamlit as st

from .schema import AnalysisProject, EntityProfile
from .sec_adapter import candidate_rows, collect_sec_project
from .dart_adapter import search_dart_companies, collect_dart_project
from .workflow import recalculate, is_current, log_action
from .dashboard import assessment, portfolio
from .reports import build_word, build_excel
from .research import research_company_openai
from .openai_provider import OpenAIProvider, DEFAULT_OPENAI_MODEL
from .handoff import build_handoff, packet_content
from .discovery import find_candidates, collect_latest


def render(store, owner, secret, panels):
    def persist():
        store.save(st.session_state.project, owner)

    def home():
        st.session_state.project = None
        st.session_state.pop("simple_matches", None)

    project = st.session_state.project
    if project is None:
        st.subheader("어느 기업을 살펴볼까요?")
        with st.form("quick_search"):
            query = st.text_input("기업명 또는 종목코드", placeholder="예: Apple, AAPL, 삼성전자")
            submitted = st.form_submit_button("기업 찾기", type="primary")
        if submitted:
            st.session_state.pop("simple_matches", None)
            try:
                if not query.strip():
                    raise ValueError("기업명을 입력하십시오.")
                with st.spinner("연결된 공시에서 기업을 찾고 있습니다..."):
                    matches, notices = find_candidates(query, secret("DART_API_KEY"))
                st.session_state.simple_matches = {"query": query.strip(), "matches": matches, "notices": notices}
            except Exception as exc:
                st.error(f"기업 검색 실패: {exc}")
        result = st.session_state.get("simple_matches")
        if result and not isinstance(result, dict):
            st.session_state.pop("simple_matches", None)
            result = None
        if result:
            matches = result["matches"]
            for notice in result["notices"]:
                st.info(notice)
            if not matches:
                st.info("연결된 SEC·DART에서 기업을 확인하지 못했습니다. 다른 국가·비상장 기업도 보고서 파일로 계속할 수 있습니다.")
            else:
                if len(matches) == 1:
                    selected = 0
                    st.write(matches[0]["label"])
                else:
                    selected = st.selectbox("어느 기업인가요?", range(len(matches)), format_func=lambda i: matches[i]["label"])
                if st.button("평가 시작", type="primary"):
                    try:
                        with st.spinner("3개년 자료 수집 → 재무검증 → 평가표 작성 중입니다..."):
                            collected, warnings = collect_latest(matches[selected], secret("DART_API_KEY"))
                            recalculate(collected)
                            collected.narrative["collection_warnings"] = warnings
                            store.save(collected, owner)
                            st.session_state.project = collected
                        st.rerun()
                    except Exception as exc:
                        st.error(f"분석 실패: {exc}")
            if st.button(f"‘{result['query']}’ 보고서 파일로 계속하기"):
                st.session_state.project = AnalysisProject(f"{result['query']} 평가", EntityProfile(result["query"]))
                st.rerun()
        saved = store.list_projects(owner)
        if saved:
            st.subheader("이전에 살펴본 기업")
            for row in saved:
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{row['legal_name']}** · {row['status']}")
                if c2.button("결과 보기", key=f"open_{row['project_id']}", width="stretch"):
                    st.session_state.project = store.load(row["project_id"], owner)
                    st.rerun()
            with st.expander("여러 기업 비교"):
                portfolio(store, owner)
        st.caption("최근 공시된 연도부터 3개년을 자동 선택합니다. 자동수집이 어려우면 보고서 파일로 안내합니다.")
        with st.expander("처음이라면 예시로 둘러보기"):
            st.caption("실기업이 아닌 합성 예시입니다.")
            if st.button("예시 3개 불러오기"):
                from .demo import demo_projects
                existing = {r["project_id"] for r in saved}
                for p in demo_projects():
                    if p.project_id not in existing:
                        store.save(p, owner)
                st.rerun()
        return

    st.button("다른 기업 보기", on_click=home)
    st.subheader(project.entity.legal_name)
    advanced = st.toggle("상세 편집 도구", value=False, key=f"advanced_{project.project_id}")
    if project.facts and not is_current(project):
        recalculate(project)
        persist()
    if not project.facts:
        st.info("SEC·DART 밖의 기업은 공개 재무보고서를 넣어 주세요. 뉴스·사업정보 조사는 아래에서 별도로 할 수 있습니다.")
        upload = st.file_uploader("재무보고서 파일", type=["pdf", "xlsx", "csv"])
        st.caption("공개자료는 개인 OpenAI API로 정리하고, 내부 판단은 사내 Claude에서 수행합니다. 원문과 내부자료의 외부 전송 정책을 준수하십시오.")
        permitted = True
        if upload and upload.name.lower().endswith('.pdf') and secret("OPENAI_API_KEY"):
            permitted = st.checkbox("이 PDF는 공개자료이거나 개인 OpenAI API 전송을 승인받은 자료입니다.")
        if st.button("이 자료로 분석하기", disabled=upload is None or not permitted, type="primary"):
            try:
                from .ingest import parse_uploaded_file
                from .ai import extract_facts_from_text
                with st.spinner("보고서 읽기 → 수치 추출 → 검증 중입니다..."):
                    source, _ = store.save_source_bytes(project.project_id, upload.name, upload.getvalue(), upload.type or "")
                    if any(s.sha256 == source.sha256 for s in project.sources):
                        raise ValueError("이미 등록된 자료입니다. 아래 자료 수정 메뉴에서 확인하십시오.")
                    facts, warnings, text = parse_uploaded_file(upload.name, upload.getvalue(), project.entity.entity_id, source)
                    if text:
                        provider = OpenAIProvider(secret("OPENAI_API_KEY"), secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
                        if provider.available:
                            facts, ai_warnings, meta = extract_facts_from_text(text, project.entity.entity_id, source, provider)
                            project.narrative.setdefault("api_usage", []).append(meta)
                            warnings.extend(ai_warnings)
                        else:
                            warnings.append("PDF 자동 수치 추출은 OPENAI_API_KEY 설정이 필요합니다. 현재 원문만 저장했습니다.")
                        st.session_state.document_texts[source.source_id] = text
                    project.sources.append(source)
                    project.facts.extend(facts)
                    project.narrative["collection_warnings"] = warnings
                    if facts:
                        recalculate(project)
                    persist()
                st.rerun()
            except Exception as exc:
                st.error(f"자료 처리 실패: {exc}")
        for warning in project.narrative.get("collection_warnings", []):
            st.warning(warning)
    st.caption("변경사항은 저장됩니다. 아래 점수는 확인이 필요한 예비 평가입니다.")
    a, b = st.columns(2)
    if project.facts:
        a.download_button("보고서 받기 · Word", build_word(project), "partner_report.docx",
                          "application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch", type="primary")
        b.download_button("재무표 받기 · Excel", build_excel(project), "partner_financials.xlsx",
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
        assessment(project)
        st.download_button("사내 Claude 검토자료 받기", build_handoff(project), "claude_review_packet.zip", "application/zip", width="stretch")
        with st.expander("사내 Claude로 전달하는 방법"):
            st.write("전용 스킬을 한 번 등록한 뒤, 검토자료 ZIP을 업로드하십시오. ZIP을 읽지 못하면 압축을 풀고 요약 파일부터 첨부하십시오. 사내 Claude API 키는 필요하지 않습니다.")
            brief, _ = packet_content(project)
            st.download_button("요약 파일만 받기 (Markdown)", brief, "01_review_brief.md", "text/markdown")
    narrative = project.narrative.get("final") or project.narrative.get("basic", {})
    for line in narrative.get("observed_facts", [])[:3]:
        st.write(line)
    with st.expander("뉴스·사업정보 조사 및 확인"):
        key = secret("OPENAI_API_KEY")
        st.caption("공개 법인 식별정보만 검색에 사용합니다. 내부 점수·메모는 전송하지 않습니다. 검색 결과는 검토 전 초안이며 API 사용료가 발생할 수 있습니다.")
        if not key:
            st.info("자동 웹 조사는 관리자 API 연결 후 사용할 수 있습니다. SEC 공시 조회와 근거 직접 등록은 아래에서 가능합니다.")
        if st.button("뉴스·사업정보 조사", disabled=not key):
            try:
                with st.spinner("공개 웹 출처를 조사하고 있습니다..."):
                    research_company_openai(project, key, secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL))
                    persist()
                st.rerun()
            except Exception:
                st.error("웹 조사를 완료하지 못했습니다. API 권한·잔액·연결을 확인하고 다시 시도하십시오. 기존 결과는 유지됩니다.")
        for brief in reversed(project.narrative.get("research_briefs", [])):
            st.caption(f"{brief['collected_at']} · {brief['status']}")
            for section_index, section in enumerate(brief["sections"]):
                st.write(section["text"])
                for citation_index, citation in enumerate(section["citations"]):
                    st.link_button(f"{section_index + 1}.{citation_index + 1} {citation['title']} ({brief['id'][:6]})", citation["url"])
            with st.form(f"research_review_{brief['id']}"):
                reviewer = st.text_input("확인자")
                note = st.text_input("확인 의견")
                decision = st.selectbox("처리", ["승인", "제외"])
                accept = st.form_submit_button("조사 결과 검토 저장")
            if accept:
                if reviewer.strip() and note.strip():
                    brief.update(status=decision, reviewer=reviewer.strip(), review_note=note.strip())
                    project.status = "재검토 필요"
                    project.narrative.pop("final", None)
                    project.narrative.pop("ai_cache", None)
                    log_action(project, "웹 조사 검토", f"{brief['id']} / {decision} / {reviewer} / {note}")
                    persist()
                    st.rerun()
                else:
                    st.error("확인자와 의견을 입력하십시오.")
        if advanced:
            panels["updates"](project)
            panels["business"](project)
    issues = [issue for issue in project.validations if issue.severity in {"오류", "경고"}]
    if issues or project.narrative.get("collection_warnings"):
        with st.expander(f"확인이 필요한 내용 ({len(issues) + len(project.narrative.get('collection_warnings', []))}건)"):
            for warning in project.narrative.get("collection_warnings", []):
                st.write(warning)
            for issue in issues:
                st.write(f"FY{issue.fiscal_year}: {issue.message}")
            st.caption("수정이 필요할 때만 상단의 상세 편집 도구를 켜십시오.")
    if advanced:
        with st.expander("자료 수정·추가"):
            panels["review"](project)
            panels["input"](project)
            panels["validation"](project)
        with st.expander("분석문·작업 기록"):
            panels["ai"](project)
            panels["effort"](project)
    with st.expander("최종 검토 완료"):
        panels["export"](project)
    with st.expander("3개년 추이 보기"):
        import pandas as pd
        from .analysis import ratio_rows
        frame = pd.DataFrame(ratio_rows(project.ratios))
        if not frame.empty:
            metric = st.selectbox("추이 지표", frame["지표"].unique())
            selected = frame[frame["지표"] == metric].tail(3)
            st.line_chart(selected.set_index("연도")[["값"]])

from datetime import date
from pathlib import Path
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
from .hitl import authorize_request, render_hitl, render_evidence_review, current_review, clear_action_tickets


STEPS = ["1. 자료 준비", "2. 결과 확인", "3. 사내 전달"]


def next_step(project, index):
    st.session_state[f"step_{project.project_id}"] = STEPS[index]


def render(store, owner, secret, panels):
    def persist():
        store.save(st.session_state.project, owner)

    def home():
        st.session_state.project = None
        st.session_state.pop("simple_matches", None)

    project = st.session_state.project
    if project is None:
        st.subheader("어느 기업을 살펴볼까요?")
        st.write("기업명 하나로 시작하세요. 공개 보고서가 있으면 바로 올리고, 없으면 공개 기사만 조사할 수 있습니다.")
        with st.form("quick_search"):
            query = st.text_input("기업명", placeholder="예: Acciona, Webuild")
            direct = st.form_submit_button("이 기업으로 시작", type="primary")
            with st.expander("공시 자동수집 · 선택사항", expanded=False):
                st.caption("SEC·DART에 공시하는 기업에만 사용하는 옵션입니다. 위에 기업명 또는 종목코드를 입력하세요. 지원되는 경우 최근 3개년 자료를 수집합니다.")
                submitted = st.form_submit_button("SEC·DART 공시에서 찾기")
        if direct:
            if not query.strip():
                st.error("기업명을 입력해 주세요.")
            else:
                st.session_state.project = AnalysisProject(f"{query.strip()} 평가", EntityProfile(query.strip()))
                persist()
                st.rerun()
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
            if st.button(f"‘{result['query']}’ 공개자료로 계속하기"):
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
        st.caption("공시 검색 없이 시작할 수 있습니다. 공개 재무보고서를 올리거나 공개 기사만 조사하세요.")
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
    routes = ["공개 재무제표 + 공개 현안", "공개 현안만 · 비공개 재무제표는 사내 Claude"]
    route = st.radio("분석 경로", routes, index=1 if project.narrative.get("analysis_route") == "news_only" else 0,
                     key=f"route_{project.project_id}")
    news_only = route == routes[1]
    project.narrative["analysis_route"] = "news_only" if news_only else "public_financials"
    if news_only:
        st.info("여기서는 공개 기사·사업정보만 조사합니다. 비공개 재무제표는 아래 전달자료와 함께 사내 Claude에 넣으십시오. 계산·점수 산정도 Claude 안에서 수행합니다.")
    if project.status == "검토 완료" and not current_review(project):
        project.status = "재검토 필요"
        project.narrative.pop("review", None)
        persist()
    if project.facts and not is_current(project):
        recalculate(project)
        persist()
    has_results = bool(project.narrative.get("research_briefs") or (project.facts and not news_only))
    step_key = f"step_{project.project_id}"
    st.session_state.setdefault(step_key, STEPS[1] if has_results else STEPS[0])
    # Set before the navigation widget is created, including after a completed upload.
    requested_step = st.session_state.pop(f"next_step_{project.project_id}", None)
    if requested_step is not None:
        st.session_state[step_key] = STEPS[requested_step]
    stage = st.radio("진행 단계", STEPS, key=step_key, horizontal=True)
    st.caption("자료는 한 번만 올립니다. 결과를 확인한 뒤 사내 Claude용 전달자료를 받으세요.")
    approved_action = render_hitl(project, persist, include_fact_review=not news_only, show_review=False,
                                 allowed_actions=(["research"] + ([] if news_only else ["upload"])) if stage == STEPS[0] else [])

    if stage == STEPS[2]:
        st.subheader("사내 Claude에서 검토를 마무리하세요")
        if not has_results:
            st.info("전달할 자료가 아직 없습니다. 먼저 공개 보고서 분석이나 기사 조사를 진행해 주세요.")
            st.button("자료 준비로 이동", on_click=next_step, args=(project, 0))
            return
        if not current_review(project):
            st.warning("아직 원문·근거 확인이 끝나지 않았습니다. 내려받는 파일은 승인본이 아닌 검토용 초안입니다.")
            st.button("결과 확인으로 이동", on_click=next_step, args=(project, 1))
        else:
            st.success("현재 자료의 확인 기록이 있습니다. 최종 내부 판단은 사내 Claude에서 진행하세요.")
        st.download_button("Claude 전달자료 받기", build_handoff(project), "claude_review_packet.zip", "application/zip", width="stretch", type="primary")
        st.markdown("**1.** 전달자료를 받아 압축을 풉니다.\n\n**2.** 사내 Claude에 `01_review_brief.md`부터 첨부하고, 필요한 근거 파일을 추가합니다.\n\n**3.** 비공개 재무제표는 사내 Claude에만 첨부합니다. 최종 보고서는 담당자가 승인합니다.")
        st.info("사내 계산결과·검토의견·최종 보고서는 이 웹에 다시 올리지 마세요.")
        with st.expander("처음 사용하는 경우 · Claude 스킬 설치"):
            st.write("회사에서 허용한 사내 Claude 환경에 최신 스킬을 등록하세요. 내부 계산은 코드 실행 기능이 허용된 경우 사용할 수 있습니다.")
            skill_zip = Path(__file__).resolve().parents[1] / "deliverables" / "partner-review-skill.zip"
            if skill_zip.is_file():
                st.download_button("사내 Claude 스킬 받기", skill_zip.read_bytes(), "partner-review-skill.zip", "application/zip")
        with st.expander("Word·Excel 또는 요약 파일만 받기"):
            if project.facts and not news_only:
                st.download_button("Word 보고서", build_word(project), "partner_report.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                st.download_button("Excel 재무표", build_excel(project), "partner_financials.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            brief, _ = packet_content(project)
            st.download_button("요약 파일", brief, "01_review_brief.md", "text/markdown")
        return

    if stage == STEPS[1]:
        st.subheader("분석 결과를 원문과 확인하세요")
        if not has_results:
            st.info("확인할 결과가 아직 없습니다. 자료 준비 단계에서 분석을 시작해 주세요.")
            st.button("자료 준비로 이동", on_click=next_step, args=(project, 0))
            return
        issues = [v for v in project.validations if v.severity in {"오류", "경고"}] if not news_only else []
        errors = sum(v.severity == "오류" for v in issues)
        if errors:
            st.error(f"수치 오류 {errors}건을 먼저 확인해 주세요. 검토 승인만으로 오류가 해제되지는 않습니다.")
        elif not current_review(project):
            st.info("아래 결과와 출처를 읽고 ‘원문·근거 확인 기록’을 남겨 주세요. 미확인 항목은 보류할 수 있습니다.")
        else:
            st.success("현재 자료의 확인 기록이 저장됐습니다. 사내 전달 단계로 이동할 수 있습니다.")
        if project.facts and not news_only:
            assessment(project)
        warnings = project.narrative.get("collection_warnings", []) if not news_only else []
        if issues or warnings:
            with st.expander(f"확인할 항목 {len(issues) + len(warnings)}건", expanded=bool(errors)):
                for warning in warnings:
                    st.write(warning)
                for issue in issues:
                    st.write(f"FY{issue.fiscal_year}: {issue.message}")
        render_research(project, persist, secret, allow_run=False)
        st.markdown("### 원문·근거 확인 기록")
        render_evidence_review(project, persist, include_fact_review=not news_only)
        advanced = st.toggle("수치를 수정하거나 상세 내역 보기", value=False, key=f"advanced_{project.project_id}")
        if advanced:
            if not news_only:
                panels["review"](project)
                panels["validation"](project)
                with st.expander("다른 자료 추가 · SEC/DART/표 입력"):
                    panels["input"](project)
                with st.expander("분석문·작업시간 기록"):
                    panels["ai"](project)
                    panels["effort"](project)
                with st.expander("공개자료 검토 완료 · 백업"):
                    panels["export"](project)
                with st.expander("3개년 추이"):
                    import pandas as pd
                    from .analysis import ratio_rows
                    frame = pd.DataFrame(ratio_rows(project.ratios))
                    if not frame.empty:
                        metric = st.selectbox("추이 지표", frame["지표"].unique())
                        st.line_chart(frame[frame["지표"] == metric].tail(3).set_index("연도")[["값"]])
            with st.expander("사업 근거 직접 등록"):
                panels["updates"](project)
                panels["business"](project)
        with st.expander("AI 사용 내역 · 절약 모드"):
            usage = project.narrative.get("api_usage", [])
            st.write(f"재무 추출 기록 {len(usage)}건 / 저장 결과 재사용 {sum(bool(m.get('cache_hit')) for m in usage)}건")
            st.caption("같은 추출 결과는 재사용합니다. 계산·문서 출력에는 API를 호출하지 않습니다. 실제 청구액은 제공사에서 확인하세요.")
            if usage:
                st.json(usage[-1])
        st.button("다음 · 사내 전달자료 받기", type="primary", on_click=next_step, args=(project, 2))
        return

    st.subheader("공개자료를 준비해 주세요")
    reprocess = (st.checkbox("보고서 추가·재분석", value=False) if project.facts else True) if not news_only else False
    if project.facts and not news_only:
        st.success(f"재무수치 {len(project.facts)}건이 저장돼 있습니다. 같은 보고서를 다시 올릴 필요가 없습니다.")
    if reprocess:
        if project.sources and not project.facts:
            st.warning("재무수치 추출 0건: 원문만 저장된 상태입니다. 아직 평가표·보고서를 만들 수 없습니다. 같은 파일을 다시 선택해 분석하거나 재무제표가 포함된 다른 파일을 올려 주세요.")
        st.info("공개된 연차보고서·재무제표를 올려 주세요. 보고서가 없거나 비공개 자료만 있다면 위에서 ‘공개 현안만’ 경로를 선택하세요.")
        public_file = st.checkbox("공개된 재무보고서입니다. 비공개·거래처 제공 자료는 업로드하지 않습니다.")
        upload = st.file_uploader("공개 재무보고서 파일", type=["pdf", "xlsx", "csv"], disabled=not public_file)
        with st.expander("재분석 옵션 · 필요한 경우에만"):
            replace_confirmed = st.checkbox("같은 파일 재분석 시 기존 추출값·수정값을 이력에 보존하고 새 결과로 교체합니다.") if project.facts else False
            force_refresh = st.checkbox("저장 결과 대신 AI 새 추출 요청 (추가 비용·새 승인 필요)", value=False)
        st.caption("공개자료는 개인 OpenAI API로 정리하고, 내부 판단은 사내 Claude에서 수행합니다. 원문과 내부자료의 외부 전송 정책을 준수하십시오.")
        permitted = public_file
        run_upload = st.button("이 자료로 분석하기", disabled=upload is None or not permitted, type="primary")
        if run_upload or approved_action == "upload":
            try:
                if upload is None or not permitted:
                    raise ValueError("공개 보고서 파일을 선택한 뒤 다시 분석해 주세요.")
                from .ingest import parse_uploaded_file
                from .ai import extract_facts_from_text
                with st.spinner("보고서 읽기 → 수치 추출 → 검증 중입니다..."):
                    source, _ = store.save_source_bytes(project.project_id, upload.name, upload.getvalue(), upload.type or "")
                    existing_source = next((s for s in project.sources if s.sha256 == source.sha256), None)
                    if existing_source:
                        source = existing_source
                        if any(f.source_id == source.source_id for f in project.facts) and not replace_confirmed:
                            raise ValueError("기존 값 교체 확인란을 선택하십시오. 재분석에는 API 비용이 발생할 수 있습니다.")
                    facts, warnings, text = parse_uploaded_file(upload.name, upload.getvalue(), project.entity.entity_id, source)
                    if text:
                        provider = OpenAIProvider(secret("OPENAI_API_KEY"), secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL), approval=lambda body: authorize_request(project, body, action="upload"))
                        if provider.available:
                            facts, ai_warnings, meta = extract_facts_from_text(text, project.entity.entity_id, source, provider,
                                default_scope=project.entity.reporting_scope,
                                cache=project.narrative.setdefault("extraction_cache", {}), force_refresh=force_refresh)
                            project.narrative.setdefault("api_usage", []).append(meta)
                            warnings.extend(ai_warnings)
                        else:
                            warnings.append("PDF 자동 수치 추출은 OPENAI_API_KEY 설정이 필요합니다. 현재 원문만 저장했습니다.")
                        if facts:
                            st.session_state.document_texts.pop(source.source_id, None)
                        else:
                            st.session_state.document_texts[source.source_id] = text
                    if existing_source and facts:
                        previous = [f for f in project.facts if f.source_id == source.source_id]
                        if previous:
                            project.narrative.setdefault("reextraction_history", []).append({"source_id": source.source_id, "facts": [f.__dict__.copy() for f in previous]})
                            project.facts = [f for f in project.facts if f.source_id != source.source_id]
                    elif existing_source and project.facts and not facts:
                        raise ValueError("재추출 0건으로 기존 값은 유지했습니다. 새 자료나 원문 확인이 필요합니다.")
                    if not existing_source:
                        project.sources.append(source)
                    project.facts.extend(facts)
                    project.narrative["collection_warnings"] = warnings
                    if facts:
                        recalculate(project)
                    else:
                        warnings.append("재무수치 0건입니다. 손익계산서·재무상태표·현금흐름표가 포함된 문서인지 확인하십시오. 요약 프레젠테이션만으로는 평가가 어려울 수 있습니다.")
                    persist()
                if facts:
                    st.session_state[f"next_step_{project.project_id}"] = 1
                st.rerun()
            except Exception as exc:
                st.error(f"자료 처리 실패: {exc}")
            finally:
                clear_action_tickets(project)
        for warning in project.narrative.get("collection_warnings", []):
            st.warning(warning)
    with st.expander("공개 기사·사업정보 조사" + ("" if news_only else " · 선택"), expanded=news_only):
        render_research(project, persist, secret, allow_run=True, approved=approved_action == "research")
    if has_results:
        st.button("다음 · 결과 확인", type="primary", on_click=next_step, args=(project, 1))


def render_research(project, persist, secret, *, allow_run, approved=False):
    if allow_run:
        key = secret("OPENAI_API_KEY")
        st.caption("공개 법인 식별정보만 검색에 사용합니다. 내부 점수·메모는 전송하지 않습니다. 검색 결과는 검토 전 초안이며 API 사용료가 발생할 수 있습니다.")
        if not key:
            st.info("자동 웹 조사는 관리자 API 연결 후 사용할 수 있습니다. 공개 공시는 첫 화면의 SEC·DART 검색을 이용해 주세요.")
        if st.button("뉴스·사업정보 조사", disabled=not key) or approved:
            try:
                with st.spinner("공개 웹 출처를 조사하고 있습니다..."):
                    if not key:
                        raise ValueError("API 연결이 필요합니다.")
                    research_company_openai(project, key, secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL), action="research")
                    persist()
                st.session_state[f"next_step_{project.project_id}"] = 1
                st.rerun()
            except Exception:
                st.error("웹 조사를 완료하지 못했습니다. API 권한·잔액·연결을 확인하고 다시 시도하십시오. 기존 결과는 유지됩니다.")
            finally:
                clear_action_tickets(project)
        return
    for brief in reversed(project.narrative.get("research_briefs", [])):
        with st.expander(f"기사·사업정보 결과 · {brief['collected_at'][:10]} · {brief['status']}", expanded=True):
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

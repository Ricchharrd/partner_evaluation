from datetime import date
from types import SimpleNamespace
import streamlit as st

from .schema import AnalysisProject, EntityProfile
from .sec_adapter import candidate_rows, collect_sec_project
from .dart_adapter import search_dart_companies, collect_dart_project
from .workflow import recalculate, is_current, log_action, filter_interim_comparatives
from .dashboard import assessment, portfolio
from .reports import build_word, build_excel
from .research import research_company_openai
from .market_news import has_saved_news
from .openai_provider import OpenAIProvider, DEFAULT_OPENAI_MODEL
from .handoff import build_handoff, packet_content, build_claude_start
from .public_gpt import build_public_gpt_packet
from .discovery import find_candidates, collect_latest
from .hitl import (authorize_request, render_hitl, render_evidence_review, current_review,
                   clear_action_tickets, quick_review_blocker, record_quick_review)


FINANCE_UI_VERSION = 6
STEPS = ["1. 자료 준비", "2. 결과 확인", "3. 사내 전달"]


def render_progress(current, news_only=False):
    labels = ["뉴스 준비" if news_only else "파일 선택", "실행 승인", "결과 확인", "사내 전달"]
    items = []
    for index, label in enumerate(labels):
        active = index == current
        color = "#173c5e" if active else "#536577"
        background = "#e1edf7" if active else "#f2f5f8"
        items.append(f'<div style="flex:1;min-width:105px;padding:12px;border-radius:8px;'
                     f'background:{background};color:{color};font-weight:{700 if active else 400};'
                     f'border-bottom:3px solid {color if active else "transparent"}">{index + 1}. {label}</div>')
    st.markdown('<div aria-label="분석 진행 단계" style="display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 24px">'
                + "".join(items) + '</div>', unsafe_allow_html=True)


def next_step(project, index):
    st.session_state[f"step_{project.project_id}"] = STEPS[index]


def render_downloads(project, news_only):
    downloads = st.columns(2, gap="small")
    downloads[0].download_button("사내 Claude 전달파일 받기", build_claude_start(project), "00_claude_start.md",
                                 "text/markdown", width="stretch", type="primary", on_click="ignore")
    if has_saved_news(project):
        downloads[1].download_button("웹 ChatGPT용 공개 뉴스", build_public_gpt_packet(project),
                                     "00_public_chatgpt_news.md", "text/markdown", width="stretch", on_click="ignore")
        st.caption("웹 ChatGPT용 파일에는 공개 뉴스만 담습니다. 업로드 전 회사 정책과 기업명·기사 내용의 공개 가능성을 확인하세요.")
    st.caption("원문 검토 기록이 포함된 자료입니다. 최종 판단은 사내 Claude에서 합니다." if current_review(project)
               else "미검토 초안입니다. 사내 Claude에서 원문 확인과 최종 검토를 진행하세요.")
    if project.facts and not news_only:
        st.download_button("공개 재무검토 Word 초안", build_word(project), "public_financial_review.docx",
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document", on_click="ignore")
        st.caption("이 Word는 공개 재무 검토표입니다. 협업 적합성의 최종 점수·승인 보고서와는 다릅니다.")
    with st.expander("추가 파일 · 사용 방법 · 스킬 설치"):
        st.write("받은 00_claude_start.md 하나를 사내 Claude에 첨부하면 됩니다. 스킬이 보고서 초안을 작성하고 중요한 예외만 질문합니다.")
        st.caption("비공개 재무제표는 사내 Claude에만 첨부하며, 내부 결과는 이 웹에 다시 올리지 않습니다.")
        st.caption("Excel과 아래 파일은 공개자료 예비 산출물입니다. 내부 맥락을 결합한 최종 보고서는 사내 Claude에서 작성합니다.")
        st.download_button("전체 근거 ZIP (필요할 때만)", build_handoff(project), "claude_review_packet.zip", "application/zip", on_click="ignore")
        if project.facts and not news_only:
            st.download_button("Excel 재무표", build_excel(project), "partner_financials.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", on_click="ignore")
        brief, _ = packet_content(project)
        st.download_button("요약 파일", brief, "01_review_brief.md", "text/markdown", on_click="ignore")
        st.caption("사내 Claude 스킬은 내부 배포 경로에서만 설치합니다. 공개 웹에서는 제공하지 않습니다.")


def render(store, owner, secret, panels):
    def persist():
        store.save(st.session_state.project, owner)

    def home():
        st.session_state.pop("hitl_pending", None)
        st.session_state.pop(f"finance_request_{st.session_state.project.project_id}", None)
        clear_action_tickets(st.session_state.project)
        st.session_state.project = None
        st.session_state.pop("simple_matches", None)

    project = st.session_state.project
    if project is None:
        st.subheader("어느 기업을 살펴볼까요?")
        saved = store.list_projects(owner)
        if saved:
            st.caption("등록된 회사를 고르면 바로 이어서 분석할 수 있습니다.")
            for row in saved:
                c1, c2 = st.columns([4, 1])
                c1.write(f"**{row['legal_name']}** · {row['status']}")
                saved_project = store.load(row["project_id"], owner)
                label = "결과 보기" if saved_project.facts or saved_project.narrative.get("research_briefs") else "분석하기"
                if c2.button(label, key=f"open_{row['project_id']}", width="stretch"):
                    st.session_state.project = saved_project
                    st.rerun()
        st.write("목록에 없다면 기업명 하나로 시작하세요. SEC·DART 조회는 필요한 경우에만 사용합니다.")
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
                        with st.spinner("3개년 자료 수집 → 재무검증 → 비율 계산 중입니다..."):
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
        if saved:
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

    company_col, switch_col = st.columns([4, 1])
    company_col.subheader(project.entity.legal_name)
    switch_col.button("다른 기업 보기", on_click=home, width="stretch")
    routes = ["공개 재무제표 + 공개 현안", "공개 현안만 · 비공개 재무제표는 사내 Claude"]
    existing_results = bool(project.facts or project.narrative.get("research_briefs"))
    route_container = st.expander("분석 방식: 공개 재무자료 / 비공개 재무자료", expanded=False)
    with route_container:
        route = st.radio("분석 경로", routes, index=1 if project.narrative.get("analysis_route") == "news_only" else 0,
                         key=f"route_{project.project_id}", horizontal=True)
    news_only = route == routes[1]
    project.narrative["analysis_route"] = "news_only" if news_only else "public_financials"
    if news_only and not existing_results:
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
    if st.session_state[step_key] not in STEPS:
        st.session_state[step_key] = STEPS[1] if has_results else STEPS[0]
    # Set before the navigation widget is created, including after a completed upload.
    requested_step = st.session_state.pop(f"next_step_{project.project_id}", None)
    if requested_step is not None:
        st.session_state[step_key] = STEPS[requested_step]
    stage = st.session_state[step_key] if has_results else STEPS[0]
    pending = st.session_state.get("hitl_pending")
    pending = pending if pending and pending["project_id"] == project.project_id else None
    if pending:
        stage = STEPS[0]
    render_progress(1 if pending else (0 if stage == STEPS[0] else 2 if stage == STEPS[1] else 3), news_only)

    if stage == STEPS[2]:
        st.subheader("4. 사내 Claude로 전달하세요")
        st.write("아래 파일 하나를 받아 사내 Claude 대화에 첨부하세요. 웹에서 Claude로 자동 전송되지는 않습니다.")
        st.caption("비공개 재무제표는 사내 Claude에 별도로 첨부합니다. 내부 등급 산정과 최종 판단은 사내에서 진행합니다.")
        errors = sum(v.severity == "오류" for v in project.validations) if not news_only else 0
        if errors:
            st.warning(f"수치 오류 {errors}건이 남아 있습니다. 전달파일에도 표시되며, 확정 평가자료가 아닌 보완용 초안입니다.")
        render_downloads(project, news_only)
        st.button("이전: 분석 결과 확인", on_click=next_step, args=(project, 1))
        return

    if stage == STEPS[1]:
        st.subheader("3. 분석 결과를 확인해 주세요")
        if not has_results:
            st.info("확인할 결과가 아직 없습니다. 자료 준비 단계에서 분석을 시작해 주세요.")
            st.button("자료 준비로 이동", on_click=next_step, args=(project, 0))
            return
        issues = [v for v in project.validations if v.severity in {"오류", "경고"} or v.code == "INTERIM_PERIOD"] if not news_only else []
        errors = sum(v.severity == "오류" for v in issues)
        if errors:
            st.error(f"수치 오류 {errors}건을 먼저 확인해 주세요. 검토 승인만으로 오류가 해제되지는 않습니다.")
        if any(v.code == "INTERIM_PERIOD" for v in issues):
            st.info("중간 재무제표 수치는 보존했습니다. 연간 재무비율과 전년 연간실적 비교는 계산하지 않았습니다.")
        if project.narrative.get("research_followup_needed"):
            st.warning("재무 분석은 저장됐지만 공개 현안이 아직 없습니다. 필요한 경우 대시보드에서 뉴스를 업데이트하세요. 현재 전달파일에는 현안 누락이 표시됩니다.")
        summary = st.columns(3)
        summary[0].metric("추출한 재무수치", f"{len(project.facts) if not news_only else 0}건")
        summary[1].metric("분석 연도", ", ".join(str(y) for y in sorted({f.fiscal_year for f in project.facts})) if not news_only and project.facts else "해당 없음")
        summary[2].metric("수치 오류", f"{errors}건")
        if project.facts and not news_only:
            st.caption("표시된 재무값은 업로드한 재무제표의 보고 법인 기준입니다. 사업부 실적과 모회사 연결재무는 분리해 확인하세요.")
            with st.expander("재무비율과 계산 결과 보기", expanded=True):
                assessment(project)
        warnings = project.narrative.get("collection_warnings", []) if not news_only else []
        if issues or warnings:
            with st.expander(f"확인할 항목 {len(issues) + len(warnings)}건", expanded=bool(errors)):
                for warning in warnings:
                    st.write(warning)
                for issue in issues:
                    st.write(f"FY{issue.fiscal_year}: {issue.message}")
        st.caption("다음 단계에서는 현재 결과를 사내 검토용 초안으로 받습니다. 이 버튼이 원문 검증이나 최종 승인을 대신하지는 않습니다.")
        st.button("다음: 사내 전달자료 받기", type="primary", on_click=next_step, args=(project, 2))
        st.button("다른 파일 분석하기", on_click=next_step, args=(project, 0))
        advanced = st.toggle("검토·수정·추가 기능 (선택)", value=False, key=f"advanced_{project.project_id}")
        with st.expander("공개 뉴스 조사 결과", expanded=news_only):
            render_research(project, persist, secret, allow_run=False, detailed=advanced)
        if advanced:
            if current_review(project):
                st.success("현재 결과에 대한 확인 기록이 있습니다.")
            else:
                st.caption("직접 원문을 대조했다면 아래 버튼으로 확인을 기록할 수 있습니다. 누락·경고는 유지하며 최종 승인과는 별개입니다.")
                blocker = quick_review_blocker(project)
                if blocker:
                    st.warning(blocker)
                if st.button("원문 확인 완료로 기록", disabled=bool(blocker)):
                    record_quick_review(project)
                    persist()
                    st.rerun()
            with st.expander("담당자·의견을 별도로 기록하려면"):
                render_evidence_review(project, persist, include_fact_review=not news_only)
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
        return

    approved_action = None
    if not news_only:
        context_key = f"finance_request_{project.project_id}"
        error_key = f"finance_error_{project.project_id}"
        context = st.session_state.get(context_key)
        awaiting_approval = bool(pending and pending.get("action") == "upload")
        run_upload = False
        upload = None
        replace_confirmed = force_refresh = False
        if awaiting_approval:
            if not context:
                st.warning("선택한 파일의 준비 정보가 만료됐습니다. 파일을 다시 선택해 주세요. 아직 유료 요청은 실행하지 않았습니다.")
                if st.button("파일 선택으로 돌아가기", type="primary"):
                    st.session_state.pop("hitl_pending", None)
                    clear_action_tickets(project)
                    st.rerun()
                return
            upload = SimpleNamespace(name=context["name"], type=context["type"],
                                     getvalue=lambda: context["content"])
            replace_confirmed, force_refresh = context["replace"], context["refresh"]
            with st.container(border=True):
                st.write(f"선택한 파일: **{upload.name}**")
                st.caption(f"{len(context['content']) / 1024 / 1024:.1f} MB, 문서 전체 분석, 결과는 사내 검토용 초안")
                approved_action = render_hitl(project, persist, show_review=False,
                                             allowed_actions=["upload"], finance_flow=True)
        else:
            st.session_state.pop(context_key, None)
            st.subheader("1. 분석할 파일을 선택하세요")
            if st.session_state.get(error_key):
                st.error(st.session_state[error_key])
            st.write("공개된 재무보고서 PDF, Excel 또는 CSV 파일을 올려 주세요.")
            st.caption("전체 문서 분석 모드입니다. PDF는 다음 화면에서 전송 내용과 비용 발생을 확인한 뒤 실행합니다.")
            if project.facts:
                st.info(f"기존 재무수치 {len(project.facts)}건은 저장되어 있습니다. 새 분석이 실패해도 기존 수치는 유지합니다.")
            elif project.sources:
                st.info("이전 분석에서 재무수치를 확보하지 못했습니다. 파일을 다시 선택해 전체 문서 분석을 진행하세요.")
            with st.container(border=True):
                upload = st.file_uploader("공개 재무보고서 파일", type=["pdf", "xlsx", "csv"],
                                          key=f"finance_upload_{project.project_id}")
                if upload is not None:
                    st.caption(f"선택 완료: {upload.name}")
                if project.sources or project.facts:
                    with st.expander("기존 자료 재분석 옵션"):
                        if project.facts:
                            replace_confirmed = st.checkbox("같은 파일의 기존 값은 이력에 보관하고 새 추출값으로 교체합니다.")
                        force_refresh = st.checkbox("저장 결과 대신 새 AI 추출 (추가 비용)", value=False)
                run_upload = st.button("다음: 분석 준비", disabled=upload is None, type="primary")
                st.caption("지금은 파일을 읽고 요청을 준비합니다. AI 비용은 다음 화면에서 승인할 때 발생합니다. 표 형식 파일은 AI 없이 처리될 수 있습니다.")
            if has_saved_news(project):
                st.caption("저장된 기업 뉴스는 결과와 함께 재사용합니다. 뉴스 검색을 다시 실행할 필요가 없습니다.")
            else:
                st.caption("뉴스 없이 재무분석부터 진행할 수 있습니다. 뉴스 업데이트는 대시보드에서 별도로 실행합니다.")
        if run_upload or approved_action == "upload":
            try:
                if upload is None:
                    raise ValueError("공개 보고서 파일을 선택한 뒤 다시 분석해 주세요.")
                if run_upload:
                    st.session_state.pop(error_key, None)
                    st.session_state[context_key] = {"name": upload.name, "type": upload.type or "",
                        "content": upload.getvalue(), "replace": replace_confirmed, "refresh": force_refresh}
                from .ingest import parse_uploaded_file
                from .ai import extract_facts_from_text
                with st.status("보고서를 처리하고 있습니다. 완료되면 자동으로 이동합니다.", expanded=True) as progress:
                    st.write("파일 읽기와 전체 문서 준비")
                    source, _ = store.save_source_bytes(project.project_id, upload.name, upload.getvalue(), upload.type or "")
                    existing_source = next((s for s in project.sources if s.sha256 == source.sha256), None)
                    if existing_source:
                        source = existing_source
                        if any(f.source_id == source.source_id for f in project.facts) and not replace_confirmed:
                            raise ValueError("기존 값 교체 확인란을 선택하십시오. 재분석에는 API 비용이 발생할 수 있습니다.")
                    facts, warnings, text = parse_uploaded_file(upload.name, upload.getvalue(), project.entity.entity_id, source)
                    if text:
                        st.write("AI 재무수치 추출, 요청 승인 확인")
                        provider = OpenAIProvider(secret("OPENAI_API_KEY"), secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL), approval=lambda body: authorize_request(project, body, action="upload"))
                        if provider.available:
                            facts, ai_warnings, meta = extract_facts_from_text(text, project.entity.entity_id, source, provider,
                                default_scope=project.entity.reporting_scope,
                                cache=project.narrative.setdefault("extraction_cache", {}), force_refresh=force_refresh)
                            project.narrative.setdefault("api_usage", []).append(meta)
                            warnings.extend(ai_warnings)
                            facts, period_warnings = filter_interim_comparatives(
                                [fact for fact in project.facts if fact.source_id != source.source_id], facts)
                            warnings.extend(period_warnings)
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
                        st.write("추출한 수치 검증과 재무비율 계산")
                        recalculate(project)
                    else:
                        warnings.append("재무수치 0건입니다. 손익계산서·재무상태표·현금흐름표가 포함된 문서인지 확인하십시오. 요약 프레젠테이션만으로는 평가가 어려울 수 있습니다.")
                    persist()
                    progress.update(label="분석 결과를 저장했습니다." if facts else "추출한 재무수치가 없습니다. 이전 분석 상세에서 원인을 확인하세요.",
                                    state="complete" if facts else "error", expanded=False)
                if facts:
                    if has_saved_news(project) or project.narrative.get("research_briefs"):
                        project.narrative.pop("research_followup_needed", None)
                    else:
                        project.narrative["research_followup_needed"] = True
                    persist()
                    st.session_state[f"next_step_{project.project_id}"] = 1
                st.rerun()
            except Exception as exc:
                st.session_state[error_key] = f"분석을 완료하지 못했습니다: {exc} 기존 결과는 유지했습니다. 파일을 확인한 뒤 다시 준비해 주세요."
                st.rerun()
            finally:
                clear_action_tickets(project)
                active_pending = st.session_state.get("hitl_pending")
                if not active_pending or active_pending["project_id"] != project.project_id:
                    st.session_state.pop(context_key, None)
        if project.narrative.get("collection_warnings") and not awaiting_approval:
            with st.expander("이전 분석 기록과 상세 메시지 (현재 실행 상태가 아닙니다)"):
                for warning in project.narrative["collection_warnings"]:
                    st.write(warning)
    else:
        st.subheader("공개 뉴스로 사내 검토를 준비하세요")
        approved_action = render_hitl(project, persist, show_review=False, allowed_actions=["research"])
        render_research(project, persist, secret, allow_run=True, approved=approved_action == "research")
    if has_results and not pending:
        st.button("이전 분석 결과 보기", on_click=next_step, args=(project, 1))


def render_research(project, persist, secret, *, allow_run, approved=False, detailed=False):
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
                    project.narrative.pop("research_followup_needed", None)
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
            if not detailed:
                continue
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

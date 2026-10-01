from __future__ import annotations

import os
import hmac
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from partner_finance.ai import extract_facts_from_text, generate_project_narrative
from partner_finance.openai_provider import OpenAIProvider, DEFAULT_OPENAI_MODEL
from partner_finance.hitl import authorize_request
from partner_finance.analysis import build_basic_narrative, calculate_ratios, ratio_rows
from partner_finance.dart_adapter import collect_dart_project, search_dart_companies
from partner_finance.ingest import download_public_document, parse_uploaded_file, sample_csv_bytes
from partner_finance.policy import evaluate_company_policy
from partner_finance.reports import build_excel, build_word
from partner_finance.schema import AnalysisProject, EntityProfile, FinancialFact, STANDARD_ITEMS, facts_to_rows
from partner_finance.sec_adapter import candidate_rows, collect_sec_project
from partner_finance.storage import ProjectStore
from partner_finance.validation import validate_facts, validation_rows
from partner_finance.workflow import recalculate, invalidate, is_current, finalize, log_action
from partner_finance.dashboard import portfolio, assessment, business_panel, updates_panel, effort_panel


APP_TITLE = "해외 파트너 평가 · 동향 워크벤치"


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, os.getenv(name, default)))
    except Exception:
        return os.getenv(name, default)


@st.cache_resource
def _cached_store(path: str) -> ProjectStore:
    return ProjectStore(path)


def get_store() -> ProjectStore:
    return _cached_store(secret("DATA_DIR", str(ROOT / "data")))


def init_state():
    defaults = {
        "project": None,
        "messages": [],
        "sec_matches": [],
        "dart_matches": [],
        "document_texts": {},
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def add_message(level: str, message: str):
    st.session_state.messages.append((level, message))


def show_messages():
    messages = st.session_state.messages[-12:]
    for level, message in messages:
        getattr(st, level, st.info)(message)


def save_project():
    project = st.session_state.project
    if project:
        get_store().save(project, secret("APP_USER_ID", "local-user"))
        add_message("success", "분석을 저장했습니다.")


def render_sidebar():
    store = get_store()
    owner = secret("APP_USER_ID", "local-user")
    with st.sidebar:
        st.markdown("### 분석 파일")
        projects = store.list_projects(owner)
        options = {f"{row['legal_name']} · {row['updated_at'][:10]} · {row['status']}": row["project_id"] for row in projects}
        selected = st.selectbox("저장된 분석", ["새 분석"] + list(options), label_visibility="collapsed")
        c1, c2 = st.columns(2)
        if c1.button("불러오기", width="stretch", disabled=selected == "새 분석"):
            st.session_state.project = store.load(options[selected], owner)
            add_message("success", "저장된 분석을 불러왔습니다.")
            st.rerun()
        if c2.button("저장", type="primary", width="stretch", disabled=st.session_state.project is None):
            save_project()
        if st.button("새 기업 등록", width="stretch"):
            st.session_state.project = None
            st.session_state.main_page = "기업 상세"
            st.session_state.sec_matches = []
            st.session_state.sec_rows = []
            st.session_state.dart_rows = []
            st.session_state.document_texts = {}
            st.rerun()
        st.divider()
        st.markdown("### 지원 상태")
        st.caption("자동수집: SEC, OpenDART(API 키 필요)")
        st.caption("업로드: CSV, XLSX, PDF 텍스트, XBRL/iXBRL")
        st.caption("제한: ESEF 통합 검색 없음, 스캔 PDF OCR 미연결")
        st.caption("AI 전처리: OpenAI API / 최종 검토: 사내 Claude에 파일 전달")
        if st.button("합성 예시 3개 추가 (실기업 아님)"):
            from partner_finance.demo import demo_projects
            existing = {row["project_id"] for row in projects}
            for demo in demo_projects():
                if demo.project_id not in existing:
                    store.save(demo, owner)
            st.rerun()


def new_project_panel():
    with st.form("new_project"):
        st.subheader("새 분석 시작")
        c1, c2, c3 = st.columns([2, 1, 1])
        legal_name = c1.text_input("법인명", placeholder="예: ACCIONA, S.A.")
        country = c2.text_input("국가", placeholder="Spain")
        entity_type = c3.selectbox("법인 유형", ["일반기업", "금융회사", "프로젝트 SPV"])
        c4, c5, c6 = st.columns(3)
        scope = c4.selectbox("기본 재무범위", ["연결", "별도", "미확인"])
        standard = c5.selectbox("회계기준", ["미확인", "IFRS", "K-IFRS", "US GAAP", "Local GAAP"])
        industry = c6.text_input("업종", placeholder="Construction & Infrastructure")
        submitted = st.form_submit_button("빈 분석 만들기", type="primary")
    if submitted:
        if not legal_name.strip():
            st.error("법인명을 입력하십시오.")
        else:
            entity = EntityProfile(legal_name=legal_name.strip(), country=country.strip(), entity_type=entity_type, reporting_scope=scope, accounting_standard=standard, industry=industry.strip())
            st.session_state.project = AnalysisProject(title=f"{legal_name.strip()} 재무평가", entity=entity)
            st.rerun()


def source_input_panel(project: AnalysisProject):
    st.subheader("1. 자료 입력 및 자동수집")
    input_tabs = st.tabs(["SEC 검색", "OpenDART 검색", "보고서 URL", "파일 업로드", "수동 입력"])

    with input_tabs[0]:
        query = st.text_input("미국 상장사 ticker 또는 법인명", value=project.entity.identifiers.get("ticker", ""), key="sec_query")
        c1, c2 = st.columns([1, 3])
        if c1.button("SEC 후보 검색", width="stretch"):
            try:
                matches, rows = candidate_rows(query)
                st.session_state.sec_matches = matches
                st.session_state.sec_rows = rows
            except Exception as exc:
                st.error(f"SEC 검색 실패: {exc}")
        if st.session_state.get("sec_rows"):
            st.dataframe(st.session_state.sec_rows, hide_index=True, width="stretch")
            labels = [f"{m.company_name} | {m.ticker} | CIK {m.cik}" for m in st.session_state.sec_matches]
            selected_label = st.selectbox("대상 법인 확인", labels)
            y1, y2 = st.columns(2)
            start_year = y1.number_input("시작 연도", 2010, 2100, 2022, key="sec_start")
            end_year = y2.number_input("종료 연도", 2010, 2100, 2025, key="sec_end")
            if st.button("선택한 SEC 법인 수집", type="primary"):
                try:
                    match = st.session_state.sec_matches[labels.index(selected_label)]
                    collected, warnings = collect_sec_project(match, int(start_year), int(end_year))
                    collected.project_id = project.project_id
                    collected.created_at = project.created_at
                    if project.entity.identifiers.get("cik") == collected.entity.identifiers.get("cik"):
                        for key in ("partner_updates", "business_evidence", "partner_role", "effort_log", "audit_log", "assessment_history"):
                            if key in project.narrative:
                                collected.narrative[key] = project.narrative[key]
                    recalculate(collected)
                    st.session_state.project = collected
                    for warning in warnings:
                        add_message("warning", warning)
                    add_message("success", f"SEC에서 {len(collected.facts)}개 재무값을 수집했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"SEC 수집 실패: {exc}")

    with input_tabs[1]:
        dart_key = secret("DART_API_KEY")
        if not dart_key:
            st.info("OpenDART 자동수집에는 배포 환경의 DART_API_KEY가 필요합니다. 키 없이도 파일 업로드와 수동 분석은 가능합니다.")
        dart_query = st.text_input("국내 법인명 또는 종목코드", key="dart_query")
        if st.button("DART 후보 검색", disabled=not bool(dart_key)):
            try:
                matches, rows = search_dart_companies(dart_key, dart_query)
                st.session_state.dart_matches = matches
                st.session_state.dart_rows = rows
            except Exception as exc:
                st.error(f"DART 검색 실패: {exc}")
        if st.session_state.get("dart_rows"):
            st.dataframe(st.session_state.dart_rows, hide_index=True, width="stretch")
            labels = [f"{row['corp_name']} | {row.get('stock_code') or '비상장'} | {row['corp_code']}" for row in st.session_state.dart_matches]
            selected_label = st.selectbox("대상 법인 확인", labels, key="dart_selected")
            c1, c2, c3 = st.columns(3)
            start_year = c1.number_input("시작 연도", 2015, 2100, 2022, key="dart_start")
            end_year = c2.number_input("종료 연도", 2015, 2100, 2024, key="dart_end")
            scope = c3.selectbox("재무범위", ["CFS", "OFS"], format_func=lambda x: "연결" if x == "CFS" else "별도")
            if st.button("선택한 DART 법인 수집", type="primary"):
                try:
                    company = st.session_state.dart_matches[labels.index(selected_label)]
                    collected, warnings = collect_dart_project(dart_key, company, int(start_year), int(end_year), scope)
                    collected.project_id = project.project_id
                    collected.created_at = project.created_at
                    st.session_state.project = collected
                    for warning in warnings:
                        add_message("warning", warning)
                    add_message("success", f"OpenDART에서 {len(collected.facts)}개 재무값을 수집했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"DART 수집 실패: {exc}")

    with input_tabs[2]:
        url = st.text_input("공식 연차보고서·감사보고서 URL", placeholder="https://...")
        st.caption("공개 http/https 주소, 최대 25MB. 내부망·localhost·예약 IP 접근은 차단합니다.")
        if st.button("URL 자료 가져오기", type="primary"):
            try:
                filename, content, mime = download_public_document(url)
                source, duplicate = get_store().save_source_bytes(project.project_id, filename, content, mime)
                source.source_type = "보고서 URL"
                source.url = url
                facts, warnings, raw_text = parse_uploaded_file(filename, content, project.entity.entity_id, source)
                project.sources.append(source)
                project.facts.extend(facts)
                if raw_text:
                    st.session_state.document_texts[source.source_id] = raw_text
                for warning in warnings:
                    add_message("warning", warning)
                add_message("success", f"{filename}: {len(facts)}개 값을 처리했습니다." + (" 동일 파일을 재사용했습니다." if duplicate else ""))
                project.touch()
                st.rerun()
            except Exception as exc:
                st.error(f"URL 수집 실패: {exc}")

    with input_tabs[3]:
        uploads = st.file_uploader("PDF, XLSX, CSV, XBRL/iXBRL", type=["pdf", "xlsx", "csv", "xml", "xbrl", "xhtml", "html"], accept_multiple_files=True)
        if st.button("업로드 자료 처리", type="primary", disabled=not uploads):
            for upload in uploads or []:
                try:
                    content = upload.getvalue()
                    source, duplicate = get_store().save_source_bytes(project.project_id, upload.name, content, upload.type or "")
                    facts, warnings, raw_text = parse_uploaded_file(upload.name, content, project.entity.entity_id, source)
                    project.sources.append(source)
                    project.facts.extend(facts)
                    if raw_text:
                        st.session_state.document_texts[source.source_id] = raw_text
                    for warning in warnings:
                        add_message("warning", f"{upload.name}: {warning}")
                    add_message("success", f"{upload.name}: {len(facts)}개 값 처리" + (" (중복 원문 재사용)" if duplicate else ""))
                except Exception as exc:
                    add_message("error", f"{upload.name} 처리 실패: {exc}")
            project.touch()
            st.rerun()

    with input_tabs[4]:
        st.download_button("표준 입력 CSV 받기", sample_csv_bytes(), "financial_input_template.csv", "text/csv")
        st.caption("CSV/XLSX 템플릿을 채워 업로드하거나 아래에서 직접 행을 추가할 수 있습니다.")
        defaults = pd.DataFrame([{"연도": 2025, "표준항목": "revenue", "원문항목": "Revenue", "값": None, "통화": "USD", "단위배수": 1_000_000, "범위": project.entity.reporting_scope, "출처위치": "수동 입력"}])
        edited = st.data_editor(defaults, num_rows="dynamic", hide_index=True, width="stretch", key="manual_editor")
        if st.button("수동 입력값 추가"):
            count = 0
            source = next((s for s in project.sources if s.source_type == "수동 입력"), None)
            if source is None:
                from partner_finance.schema import SourceDocument
                source = SourceDocument(name="사용자 수동 입력", source_type="수동 입력", status="사용자 확인 필요")
                project.sources.append(source)
            for _, row in edited.iterrows():
                if pd.isna(row.get("값")) or not row.get("표준항목"):
                    continue
                value = float(row["값"])
                multiplier = float(row.get("단위배수") or 1)
                project.facts.append(FinancialFact(entity_id=project.entity.entity_id, fiscal_year=int(row["연도"]), standard_item=str(row["표준항목"]), original_label=str(row.get("원문항목") or row["표준항목"]), original_value=value, normalized_value=value * multiplier, currency=str(row.get("통화") or "미확인").upper(), unit_multiplier=multiplier, reporting_scope=str(row.get("범위") or project.entity.reporting_scope), accounting_standard=project.entity.accounting_standard, source_id=source.source_id, source_locator=str(row.get("출처위치") or "수동 입력"), extraction_method="수동 입력", validation_status="사용자 입력"))
                count += 1
            add_message("success", f"수동 값 {count}개를 추가했습니다.")
            project.touch()
            st.rerun()


def review_panel(project: AnalysisProject):
    st.subheader("2. 추출값 검토 및 수정")
    if not project.facts:
        st.info("먼저 자료를 수집하거나 입력하십시오.")
        return
    rows = pd.DataFrame(facts_to_rows(project.facts))
    editable = rows[["fact_id", "연도", "표준항목", "원문항목", "추출값", "사용자수정값", "통화", "범위", "출처위치", "검증상태", "수정사유"]]
    edited = st.data_editor(
        editable,
        hide_index=True,
        width="stretch",
        disabled=["fact_id", "연도", "표준항목", "원문항목", "추출값", "통화", "범위", "출처위치", "검증상태"],
        column_config={"fact_id": None, "사용자수정값": st.column_config.NumberColumn(format="%.4f"), "수정사유": st.column_config.TextColumn(width="large")},
        key=f"fact_review_editor_{project.project_id}",
    )
    st.caption("수정값을 입력해도 원래 추출값과 출처는 보존됩니다. 수정 시 사유를 함께 입력하십시오.")
    if st.button("수정사항 반영", type="primary"):
        lookup = {fact.fact_id: fact for fact in project.facts}
        changed = 0
        pending = []
        for _, row in edited.iterrows():
            fact = lookup[str(row["fact_id"])]
            new_value = None if pd.isna(row["사용자수정값"]) else float(row["사용자수정값"])
            reason = "" if pd.isna(row["수정사유"]) else str(row["수정사유"])
            if new_value != fact.user_value or reason != fact.user_reason:
                if new_value is not None and not reason.strip():
                    st.error(f"FY{fact.fiscal_year} {fact.standard_item}: 수정 사유가 필요합니다.")
                    return
                pending.append((fact, new_value, reason))
        for fact, new_value, reason in pending:
            old_value = fact.effective_value
            fact.apply_correction(new_value, reason)
            log_action(project, "재무값 수정", f"{fact.fact_id}: {old_value} -> {fact.effective_value} / {reason}")
            changed += 1
        if changed:
            recalculate(project)
        project.touch()
        add_message("success", f"수정사항 {changed}건을 반영했습니다.")
        st.rerun()


def validation_analysis_panel(project: AnalysisProject):
    st.subheader("3. 검증·재무비율·평가")
    c1, c2 = st.columns([1, 3])
    if c1.button("검증 및 계산 실행", type="primary", disabled=not project.facts):
        recalculate(project)
        add_message("success", "검증과 재무비율 계산을 완료했습니다.")
        st.rerun()
    if not project.validations and project.facts:
        st.info("검증 및 계산 실행을 눌러 최신 수정값을 반영하십시오.")
    tab1, tab2, tab3 = st.tabs(["검증 경고", "재무비율", "회사 평가표"])
    with tab1:
        if project.validations:
            st.dataframe(validation_rows(project.validations), hide_index=True, width="stretch")
        else:
            st.caption("검증 결과 없음")
    with tab2:
        if project.ratios:
            ratio_df = pd.DataFrame(ratio_rows(project.ratios))
            st.dataframe(ratio_df, hide_index=True, width="stretch")
            chart_df = ratio_df[ratio_df["지표"].isin(["영업이익률", "순이익률", "부채비율", "유동비율"])].pivot(index="연도", columns="지표", values="값")
        else:
            st.caption("계산 결과 없음")
    with tab3:
        evaluations = project.narrative.get("policy_evaluation") or []
        if not evaluations:
            st.caption("평가 결과 없음")
        elif evaluations and evaluations[0].get("status") == "별도 기준 필요":
            st.warning(evaluations[0]["reason"])
        else:
            for evaluation in evaluations:
                with st.expander(f"FY{evaluation.get('fiscal_year')} · {evaluation.get('grade') or '평가 보류'} · {evaluation.get('score') if evaluation.get('score') is not None else '-'}점", expanded=True):
                    if evaluation.get("reason"):
                        st.warning(evaluation["reason"])
                    st.dataframe(evaluation.get("components") or [], hide_index=True, width="stretch")
                    st.caption(f"Altman Z: {evaluation.get('altman_z', '-')} | {evaluation.get('currency_note', '')}")


def ai_report_panel(project: AnalysisProject):
    st.subheader("4. AI 해석 및 보고서")
    api_key = secret("OPENAI_API_KEY")
    model = secret("OPENAI_MODEL", DEFAULT_OPENAI_MODEL)
    provider = OpenAIProvider(api_key=api_key, model=model, approval=lambda body: authorize_request(project, body))
    permitted = st.checkbox("AI 전송 대상 자료·재무값·근거는 모두 공개자료이며 내부정보가 포함되지 않았습니다.")
    if st.session_state.document_texts:
        st.caption(f"AI 추출 대기 문서 {len(st.session_state.document_texts)}건 · 승인 범위를 명확히 하기 위해 한 번에 1건씩 처리합니다.")
        if st.button("문서 텍스트에서 재무값 AI 추출", disabled=not provider.available or not permitted):
            added = 0
            for source_id, text in list(st.session_state.document_texts.items())[:1]:
                source = next((item for item in project.sources if item.source_id == source_id), None)
                if not source:
                    continue
                try:
                    if any(f.source_id == source_id for f in project.facts):
                        raise ValueError("이미 추출된 문서입니다. 상단의 보고서 추가·재분석에서 교체 여부를 확인하십시오.")
                    facts, warnings, meta = extract_facts_from_text(text, project.entity.entity_id, source, provider,
                        default_scope=project.entity.reporting_scope, cache=project.narrative.setdefault("extraction_cache", {}))
                    project.narrative.setdefault("api_usage", []).append(meta)
                    project.facts.extend(facts)
                    added += len(facts)
                    if facts:
                        st.session_state.document_texts.pop(source_id, None)
                    for warning in warnings:
                        add_message("warning", warning)
                except Exception as exc:
                    add_message("error", f"{source.name} AI 추출 실패: {exc}")
            add_message("success", f"AI가 {added}개 후보 값을 추출했습니다. 모두 검토 필요 상태입니다.")
            project.touch()
            st.rerun()
    if not provider.available:
        st.info("OPENAI_API_KEY가 없어 AI 호출은 비활성화되어 있습니다. 계산과 사내 Claude 전달자료 생성은 계속 사용할 수 있습니다.")
    c1, c2 = st.columns(2)
    if c1.button("기본 분석문 생성", disabled=not project.ratios, width="stretch"):
        project.narrative["final"] = build_basic_narrative(project.facts, project.ratios)
        add_message("success", "확정 수치에 기반한 기본 분석문을 생성했습니다.")
        st.rerun()
    if c2.button("OpenAI 분석문 생성", disabled=not provider.available or not project.ratios or not permitted, type="primary", width="stretch"):
        try:
            project.narrative["final"] = generate_project_narrative(project, provider)
            add_message("success", f"OpenAI API({model}) 분석문을 생성했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(f"OpenAI API 호출 실패: {exc}")
    narrative = project.narrative.get("final") or project.narrative.get("basic")
    if narrative:
        st.markdown(f"**생성 방식:** {narrative.get('generation_mode', '미확인')}")
        st.markdown("**관찰된 사실**")
        for item in narrative.get("observed_facts", []):
            st.write("-", item)
        st.markdown("**해석 및 검토사항**")
        for item in narrative.get("interpretation", []) + narrative.get("review_points", []):
            st.write("-", item)


def export_panel(project: AnalysisProject):
    st.subheader("5. 저장 및 산출물")
    if not is_current(project):
        st.warning("현재 입력의 검증 및 계산이 필요합니다. 이전 평가 결과를 출력하지 않습니다.")
        return
    reviewer = st.text_input("최종 검토자")
    review_note = st.text_area("검토의견 및 미확인 항목 처리")
    acknowledged = st.checkbox("환율·Z-score 대용치와 미확인 항목을 확인했으며 예비검토 자료로 사용합니다.")
    c1, c2 = st.columns(2)
    if c1.button("검토 완료로 표시", disabled=not project.facts or not acknowledged):
        try:
            finalize(project, reviewer, review_note)
            save_project()
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if c2.button("현재 분석 저장", type="primary"):
        save_project()
        st.rerun()
    try:
        excel = build_excel(project)
        word = build_word(project)
        project_json = get_store().export_project_json(project)
        d1, d2, d3 = st.columns(3)
        safe_name = "".join(ch if ch.isalnum() else "_" for ch in project.entity.legal_name)[:40]
        d1.download_button("Excel 분석표", excel, f"{safe_name}_재무분석.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
        d2.download_button("Word 보고서", word, f"{safe_name}_재무평가.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", width="stretch")
        d3.download_button("분석 백업 JSON", project_json, f"{safe_name}_분석백업.json", "application/json", width="stretch")
    except Exception as exc:
        st.error(f"산출물 생성 실패: {exc}")


def render_project(project: AnalysisProject):
    if project.narrative.get("calculated_digest") and not is_current(project):
        invalidate(project)
    st.markdown(f"## {project.entity.legal_name}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("국가", project.entity.country or "미확인")
    c2.metric("법인 유형", project.entity.entity_type)
    c3.metric("재무값", len(project.facts))
    c4.metric("상태", project.status)
    if project.entity.entity_type in {"금융회사", "프로젝트 SPV"}:
        st.warning("이 대상은 일반 건설·인프라 기업과 동일한 평가기준을 적용하지 않습니다. 별도 기준이 필요합니다.")
    show_messages()
    assessment(project)
    tabs = st.tabs(["사업역량·동향", "자료 입력·검토", "보고서", "작업·검토 기록"])
    with tabs[0]:
        business_panel(project, save_project)
        updates_panel(project, save_project, secret("SEC_USER_AGENT"))
    with tabs[1]:
        source_input_panel(project)
        review_panel(project)
        validation_analysis_panel(project)
    with tabs[2]:
        ai_report_panel(project)
        export_panel(project)
    with tabs[3]:
        effort_panel(project, save_project)
    with st.expander("출처 및 지원 범위", expanded=False):
        st.dataframe([source.__dict__ for source in project.sources], hide_index=True, width="stretch") if project.sources else st.caption("등록된 출처 없음")
        st.markdown("SEC/OpenDART 구조화 공시는 코드로 우선 처리합니다. PDF는 텍스트 추출까지만 기본 제공하며 스캔 OCR은 미지원입니다. ESEF/iXBRL 파일은 업로드 처리하지만 유럽 전체 기업을 찾는 단일 검색 API는 제공하지 않습니다.")
    if project.ratios and is_current(project):
        st.subheader("최근 3개년 추이")
        ratio_df = pd.DataFrame(ratio_rows(project.ratios))
        years = sorted(ratio_df["연도"].unique())[-3:]
        chart = ratio_df[ratio_df["연도"].isin(years) & ratio_df["지표"].isin(["영업이익률", "순이익률", "부채비율", "유동비율"])]
        st.line_chart(chart.pivot(index="연도", columns="지표", values="값") * 100, y_label="%")


def main():
    st.set_page_config(page_title="파트너 살펴보기", layout="wide", initial_sidebar_state="collapsed")
    password = secret("APP_PASSWORD")
    if password and not st.session_state.get("authenticated"):
        st.title("파트너 평가 워크벤치 로그인")
        with st.form("login"):
            entered = st.text_input("접속 암호", type="password")
            submit = st.form_submit_button("접속")
        if submit:
            if hmac.compare_digest(entered.encode(), password.encode()):
                st.session_state.authenticated = True
                st.rerun()
            st.error("접속 암호가 일치하지 않습니다.")
        st.stop()
    st.markdown(
        """<style>
        .stApp { background: linear-gradient(135deg, #f6f1e8 0%, #eef4f2 100%); }
        .stMainBlockContainer { max-width: 1080px; padding-top: 2.5rem; }
        h1, h2, h3 { font-family: Georgia, 'Malgun Gothic', serif; color: #14213d; }
        [data-testid='stMetric'] { background:#fffdf8; border:1px solid #ded2c1; padding:14px; }
        .stButton button[kind='primary'] { background:#0f6b8c; border-color:#0f6b8c; }
        [data-testid='stRadio'] div[role='radiogroup'] { gap: .65rem; flex-wrap: wrap; }
        [data-testid='stRadioOption'], [data-testid='stRadio'] div[role='radiogroup'] > label {
            background: #fffdf8; border: 1px solid #ded2c1; border-radius: 8px;
            padding: .6rem .85rem; margin: 0;
        }
        [data-testid='stRadioOption'][data-selected='true'],
        [data-testid='stRadio'] div[role='radiogroup'] > label:has(input:checked) {
            border-color: #0f6b8c; background: #e8f2f3;
        }
        @media (max-width: 640px) {
            .stMainBlockContainer { padding: 1.5rem 1rem; }
            h1 { font-size: 2rem !important; }
            [data-testid='stRadioOption'] { padding: .5rem; }
            [data-testid='stRadio'] div[role='radiogroup'] > label { flex: 1 1 auto; }
        }
        </style>""",
        unsafe_allow_html=True,
    )
    st.title("파트너 살펴보기")
    st.caption("공개자료는 여기서 정리하고, 최종 판단은 사내 Claude에서 마무리합니다.")
    if not st.session_state.get("public_workspace_ack"):
        st.info("시작 전 확인: 이 웹에는 공개자료만 입력하세요. 거래처가 직접 제공한 비공개 재무제표와 내부 의견은 사내 Claude에서만 처리합니다.")
    if not st.checkbox("기업명·검색어·파일·메모를 포함해 이 웹서비스에는 공개 가능한 정보만 입력하겠습니다.", key="public_workspace_ack"):
        st.stop()
    init_state()
    if secret("SEC_USER_AGENT"):
        os.environ["SEC_USER_AGENT"] = secret("SEC_USER_AGENT")
    from partner_finance.simple_ui import render
    render(get_store(), secret("APP_USER_ID", "local-user"), secret, {
        "input": source_input_panel, "review": review_panel, "validation": validation_analysis_panel,
        "ai": ai_report_panel, "export": export_panel,
        "updates": lambda p: updates_panel(p, save_project, secret("SEC_USER_AGENT")),
        "business": lambda p: business_panel(p, save_project),
        "effort": lambda p: effort_panel(p, save_project),
    })
    if st.session_state.project is not None:
        get_store().save(st.session_state.project, secret("APP_USER_ID", "local-user"))
    st.divider()
    st.caption("예비 검토용 · 원문 확인 필요" + (" · 로컬 시범 모드: 공개 배포 전 접근통제 설정 필요" if not password else ""))


if __name__ == "__main__":
    main()

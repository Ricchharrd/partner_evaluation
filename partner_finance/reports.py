from __future__ import annotations

from io import BytesIO
from typing import Iterable

from .analysis import effective_fact_map, ratio_rows
from .schema import AnalysisProject, STANDARD_ITEMS, facts_to_rows
from .validation import validation_rows
from .periods import period_label


def _literal(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False)
    return value


def _autosize(ws):
    from openpyxl.utils import get_column_letter

    for column in range(1, ws.max_column + 1):
        width = max((len(str(ws.cell(row, column).value or "")) for row in range(1, ws.max_row + 1)), default=10)
        ws.column_dimensions[get_column_letter(column)].width = min(max(width + 2, 12), 45)


def _write_rows(ws, rows: list[dict]):
    from openpyxl.styles import Alignment, Font, PatternFill

    if not rows:
        ws.append(["자료 없음"])
        return
    headers = list(dict.fromkeys(key for row in rows for key in row))
    ws.append(headers)
    for row in rows:
        ws.append([_literal(row.get(header)) for header in headers])
    fill = PatternFill("solid", fgColor="14213D")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    _autosize(ws)


def build_excel(project: AnalysisProject) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from .storage import public_project_payload

    project = AnalysisProject.from_dict(public_project_payload(project.to_dict()))
    wb = Workbook()
    meta = wb.active
    meta.title = "분석개요"
    meta_rows = [
        ("분석명", project.title), ("프로젝트 ID", project.project_id), ("법인명", project.entity.legal_name),
        ("국가", project.entity.country), ("법인유형", project.entity.entity_type), ("연결/별도", project.entity.reporting_scope),
        ("회계기준", project.entity.accounting_standard), ("상태", project.status), ("최종수정", project.updated_at),
        ("주의", "예비 재무검토 자료이며 정식 신용평가가 아닙니다. 원문과 인적 검토가 필요합니다."),
    ]
    for row in meta_rows:
        meta.append([_literal(value) for value in row])
    meta["A1"].font = Font(bold=True)
    _autosize(meta)

    facts_ws = wb.create_sheet("추출·정규화 데이터")
    _write_rows(facts_ws, facts_to_rows(project.facts))
    ratios_ws = wb.create_sheet("재무비율")
    _write_rows(ratios_ws, ratio_rows(project.ratios))
    validations_ws = wb.create_sheet("검증경고")
    _write_rows(validations_ws, validation_rows(project.validations))
    sources_ws = wb.create_sheet("출처")
    _write_rows(sources_ws, [source.__dict__ for source in project.sources])
    corrections_ws = wb.create_sheet("수정이력")
    _write_rows(
        corrections_ws,
        [
            {
                "fact_id": fact.fact_id, "연도": fact.fiscal_year, "항목": fact.standard_item,
                "원래추출값": fact.normalized_value, "사용자수정값": fact.user_value,
                "수정사유": fact.user_reason, "수정일시": fact.edited_at, "근거": fact.source_locator,
            }
            for fact in project.facts if fact.user_value is not None
        ],
    )
    for name, rows in [
        ("사업역량 근거", project.narrative.get("business_evidence", [])),
        ("기업 동향", project.narrative.get("partner_updates", [])),
        ("작업시간", project.narrative.get("effort_log", [])),
        ("검토 기록", project.narrative.get("audit_log", [])),
        ("환율 근거", project.narrative.get("fx_display", [])),
        ("웹 조사", project.narrative.get("research_briefs", [])),
        ("HITL 검토", project.narrative.get("hitl_review_history", [])),
        ("외부전송 승인", project.narrative.get("hitl_approvals", [])),
    ]:
        _write_rows(wb.create_sheet(name), rows)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _set_cell_text(cell, value):
    cell.text = "-" if value is None else str(value)


def _add_bullets(document, values: Iterable[str]):
    values = list(values or [])
    if not values:
        document.add_paragraph("미확인")
        return
    for value in values:
        document.add_paragraph(str(value), style="List Bullet")


def build_word(project: AnalysisProject) -> bytes:
    from .hitl import current_review
    from .storage import public_project_payload
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt

    project = AnalysisProject.from_dict(public_project_payload(project.to_dict()))
    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Malgun Gothic"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "맑은 고딕")
    styles["Normal"].font.size = Pt(9.5)

    title = document.add_heading("해외 파트너사 공개 재무자료 검토 보고서", 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph(f"대상 법인: {project.entity.legal_name}")
    document.add_paragraph(f"국가 / 업종: {project.entity.country or '미확인'} / {project.entity.industry or '미확인'}")
    document.add_paragraph(f"분석 기준: {project.entity.reporting_scope}, {project.entity.accounting_standard}, 최종수정 {project.updated_at}")
    document.add_paragraph("본 보고서는 예비 검토 자료이며 정식 신용평가가 아닙니다. 원문 및 인적 검토가 필요합니다.")
    document.add_paragraph("HITL 사전 검토: " + ("현재 자료 확인 기록 있음" if current_review(project) else "미완료·재확인 필요 / 승인본 아님"))
    human_review = project.narrative.get("hitl_review", {})
    document.add_paragraph(f"확인자: {human_review.get('reviewer', '미확인')} / 미해결 사항·조치: {human_review.get('note', '미기록')}")
    status = project.status if current_review(project) else "미검토·재확인 필요"
    reviewer = project.narrative.get('review', {}).get('reviewer', '미검토') if current_review(project) else '미검토'
    document.add_paragraph(f"검토 상태: {status} | 검토자: {reviewer}")
    document.add_paragraph("이 웹 보고서는 공개 재무값과 비율만 정리합니다. 등급, 가중치, 감점과 최종 판단은 사내 Claude Enterprise 스킬에서 처리합니다.")
    document.add_paragraph("재무값은 업로드된 재무제표의 보고 법인과 연결·별도 범위 기준입니다. 사업부 실적이 별도로 제시되더라도 이를 사업부의 독립 재무상태나 현금흐름으로 해석하지 않습니다.")

    narrative = project.narrative.get("final") or project.narrative.get("basic") or {}
    document.add_heading("1. 기업개요", level=1)
    overview = narrative.get("company_overview") or "공식 자료 기반 기업개요가 아직 작성되지 않았습니다."
    if isinstance(overview, list):
        _add_bullets(document, overview)
    else:
        document.add_paragraph(str(overview))
    document.add_paragraph(f"평가 대상 법인 식별정보: {project.entity.identifiers or '미확인'}")
    if project.entity.entity_type in {"금융회사", "프로젝트 SPV"}:
        document.add_paragraph("이 법인의 재무비율 해석에는 일반 건설·인프라 기업과 다른 맥락이 필요합니다.")

    document.add_heading("2. 최근 재무요약", level=1)
    selected = effective_fact_map(project.facts)
    years = sorted({year for year, _ in selected})[-3:]
    summary_items = ["revenue", "operating_income", "net_income", "total_assets", "total_liabilities", "total_equity", "operating_cash_flow", "financial_debt"]
    table = document.add_table(rows=1, cols=1 + len(years))
    table.style = "Table Grid"
    _set_cell_text(table.rows[0].cells[0], "항목")
    for index, year in enumerate(years, start=1):
        _set_cell_text(table.rows[0].cells[index], period_label(project.facts, year))
    for item in summary_items:
        cells = table.add_row().cells
        _set_cell_text(cells[0], STANDARD_ITEMS.get(item, item))
        for index, year in enumerate(years, start=1):
            fact = selected.get((year, item))
            value = fact.effective_value if fact else None
            blocked = any(v.severity == "오류" and v.fiscal_year in (None, year) and (not v.item_keys or item in v.item_keys) for v in project.validations)
            _set_cell_text(cells[index], "검증 오류·확인 필요" if blocked else f"{value:,.0f} {fact.currency}" if value is not None and fact else "미확인")

    document.add_heading("3. 재무비율 및 변동 분석", level=1)
    if any(issue.code == "INTERIM_PERIOD" for issue in project.validations):
        document.add_paragraph("중간기간 수치는 원문 확인용으로 표시하며, 연간 비율과 전년 연간실적 비교에 사용하지 않았습니다.")
    latest_ratios = [row for row in project.ratios if row.fiscal_year in years]
    ratio_table = document.add_table(rows=1, cols=5)
    ratio_table.style = "Table Grid"
    for cell, label in zip(ratio_table.rows[0].cells, ["연도", "지표", "값", "산식", "상태"]):
        _set_cell_text(cell, label)
    for ratio in latest_ratios:
        cells = ratio_table.add_row().cells
        value = "미확인" if ratio.value is None else f"{ratio.value:,.4f}"
        for cell, content in zip(cells, [period_label(project.facts, ratio.fiscal_year), ratio.label, value, ratio.formula, ratio.status]):
            _set_cell_text(cell, content)
    document.add_heading("관찰된 사실", level=2)
    _add_bullets(document, narrative.get("observed_facts"))
    document.add_heading("해석", level=2)
    _add_bullets(document, narrative.get("interpretation"))

    document.add_heading("4. 주요 검토사항", level=1)
    _add_bullets(document, narrative.get("review_points"))
    error_messages = [f"[{issue.severity}] FY{issue.fiscal_year or '-'} {issue.message}" for issue in project.validations]
    _add_bullets(document, error_messages)

    document.add_heading("5. 누락 자료와 분석 한계", level=1)
    _add_bullets(document, narrative.get("limitations") or ["확인되지 않은 값은 0이 아닌 미확인으로 처리했습니다.", "원인 설명은 주석·공시 근거가 없는 경우 확정하지 않았습니다."])

    document.add_heading("6. 출처", level=1)
    for source in project.sources:
        document.add_paragraph(f"{source.name} | {source.url or source.local_path or '-'} | 수집일 {source.collected_at} | {source.note}")
    document.add_heading("7. 사업역량 근거 및 기업 동향", level=1)
    for row in project.narrative.get("business_evidence", []):
        if row.get("status") == "제외":
            continue
        document.add_paragraph(f"[{row['status']}] {row['title']} / {row['role']}\n{row['description']}\n{row['source_url']} | {row['locator']}")
    for row in project.narrative.get("partner_updates", []):
        if row.get("status") == "승인":
            document.add_paragraph(f"{row['published_at']} | {row['title']}\n{row['summary']}\n{row['source_url']}\n검토: {row.get('reviewer', '')} / {row.get('review_note', '')}")
    document.add_paragraph(f"최종 검토의견: {project.narrative.get('review', {}).get('note', '미검토')}")
    for brief in project.narrative.get("research_briefs", []):
        if brief.get("status") != "승인":
            continue
        document.add_heading("공개자료 조사 (담당자 검토)", level=2)
        document.add_paragraph(f"조사일: {brief['collected_at']} / 확인자: {brief.get('reviewer', '')}")
        for section in brief["sections"]:
            document.add_paragraph(section["text"])
            for citation in section["citations"]:
                document.add_paragraph(f"출처: {citation['title']} | {citation['url']}")

    output = BytesIO()
    document.save(output)
    return output.getvalue()

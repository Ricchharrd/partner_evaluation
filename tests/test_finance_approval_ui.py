import json
import tempfile
import unittest
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest


def finance_app():
    import streamlit as st
    from partner_finance.schema import AnalysisProject, EntityProfile
    from partner_finance.simple_ui import render
    from partner_finance.storage import ProjectStore

    if "project" not in st.session_state:
        st.session_state.project = AnalysisProject("Test", EntityProfile("Test Builder"))
        st.session_state.document_texts = {}
    render(ProjectStore(st.session_state.test_root), "test-user",
           lambda key, default=None: "test-key" if key == "OPENAI_API_KEY" else default, {})


class FinanceApprovalUITests(unittest.TestCase):
    def test_financial_download_exposes_public_evidence_to_private_skill(self):
        from partner_finance.schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument
        from partner_finance.simple_ui import render_downloads
        from partner_finance.workflow import recalculate

        entity = EntityProfile("Synthetic Builder")
        project = AnalysisProject("Synthetic review", entity)
        source = SourceDocument("annual.pdf", "공개 링크", url="https://example.com/annual.pdf")
        project.sources.append(source)
        project.facts.append(FinancialFact(
            entity.entity_id, 2025, "revenue", "Revenue", 100, 100_000_000, "EUR",
            unit_multiplier=1_000_000, period_start="2025-01-01", period_end="2025-12-31",
            source_id=source.source_id, source_locator="PAGE 12",
        ))
        recalculate(project)
        first, second = MagicMock(), MagicMock()
        with patch("partner_finance.simple_ui.st") as streamlit, \
                patch("partner_finance.simple_ui.has_saved_news", return_value=False), \
                patch("partner_finance.simple_ui.build_claude_start", return_value=b"start"), \
                patch("partner_finance.simple_ui.build_word", return_value=b"word"), \
                patch("partner_finance.simple_ui.build_handoff", return_value=b"zip"):
            streamlit.columns.return_value = (first, second)
            render_downloads(project, news_only=False)
        raw = next(call for call in first.download_button.call_args_list
                   if call.args[0] == "사내 스킬용 공개 원자료")
        self.assertEqual(raw.args[2], "02_evidence.json")
        packet = json.loads(raw.args[1])
        self.assertEqual(packet["facts"][0]["source_locator"], "PAGE 12")
        self.assertNotIn("policy_evaluation", packet)

    def test_company_input_precedes_compact_saved_selection_and_reuses_company(self):
        from partner_finance.storage import ProjectStore
        from partner_finance.schema import AnalysisProject, EntityProfile
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            saved = AnalysisProject("Webuild", EntityProfile("Webuild"))
            store.save(saved, "test-user")
            app = AppTest.from_function(finance_app, default_timeout=20)
            app.session_state.test_root = root
            app.session_state.project = None
            app.session_state.document_texts = {}
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input[0].label, "기업명")
            self.assertFalse(any(s.value == "어느 기업을 살펴볼까요?" for s in app.subheader))
            selection = next(e for e in app.expander if e.label.startswith("기존 기업에서"))
            self.assertFalse(selection.proto.expanded)
            app.text_input[0].set_value("webuild").run()
            next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
            self.assertEqual(app.session_state.project.project_id, saved.project_id)
            self.assertEqual(len(store.list_projects("test-user")), 1)

    def test_over_one_megabyte_is_approved_as_a_bounded_plan(self):
        text = "[PAGE 1] " + "Revenue 100 " * 120000
        with self.empty_result_app(text=text) as (app, request):
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertGreater(app.session_state.hitl_pending["info"]["input_bytes"], 1000000)
            manifest = app.session_state.hitl_pending["body"]
            self.assertEqual(manifest["kind"], "finance_batch_v1")
            count = manifest["batch"]["pending_chunks"]
            self.assertGreater(count, 1)
            next(c for c in app.checkbox if "추가 비용 가능성" in c.label).check().run()
            with patch("partner_finance.ingest.parse_uploaded_file", side_effect=AssertionError("Do not parse again")), \
                    patch("partner_finance.finance_batch.time.sleep"):
                next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            self.assertEqual(request.call_count, count)
            bodies = [json.loads(call.args[0].data) for call in request.call_args_list]
            self.assertTrue(all(body["max_output_tokens"] == 6000 for body in bodies))
            self.assertTrue(all(len(json.loads(body["input"][1]["content"])["document_text"].encode()) <= 20000 for body in bodies))
            self.assertEqual(request.call_args.kwargs["timeout"], 600)

    def test_link_download_is_reused_for_approval_and_source_url_saved(self):
        url = "https://example.com/public.pdf"
        document = {"kind": "document", "url": url, "name": "public.pdf", "type": "application/pdf", "content": b"public report"}
        with self.empty_result_app() as (app, request), \
                patch("partner_finance.public_documents.fetch_public_document", return_value=document) as fetch:
            next(b for b in app.button if b.label == "이전: 파일 다시 선택").click().run()
            next(r for r in app.radio if r.label == "자료 가져오는 방법").set_value("공개 링크 (권장)").run()
            next(t for t in app.text_input if t.label == "공개 재무보고서 또는 IR 페이지 주소").set_value(url).run()
            next(b for b in app.button if b.label == "다음: 분석 준비").click().run()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(request.call_count, 0)
            next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").click().run()
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(request.call_count, 1)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state.project.sources[0].url, url)
            self.assertEqual(app.session_state.project.sources[0].source_type, "공개 링크")

    def test_failed_batch_keeps_prepared_file_and_reapproves_only_remaining(self):
        text = ''.join(f'[PAGE {i}]\n' + 'Public report. ' * 1200 for i in range(1,4))
        with self.empty_result_app(text=text) as (app, request), patch('partner_finance.finance_batch.time.sleep'):
            response = request.return_value
            request.side_effect = [response, RuntimeError('connection ended')]
            next(c for c in app.checkbox if '추가 비용 가능성' in c.label).check().run()
            next(b for b in app.button if b.label == '승인하고 전체 분석 시작 (유료)').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 2)
            self.assertTrue(any(b.label == '남은 구간 이어서 분석 준비' for b in app.button))
            app.run()
            self.assertEqual(request.call_count, 2)
            request.side_effect = None
            with patch('partner_finance.ingest.parse_uploaded_file', side_effect=AssertionError('no reparse')):
                next(b for b in app.button if b.label == '남은 구간 이어서 분석 준비').click().run()
                self.assertEqual(request.call_count, 2)
                self.assertEqual(app.session_state.hitl_pending['body']['batch']['completed_chunks'], 1)
                next(c for c in app.checkbox if '추가 비용 가능성' in c.label).check().run()
                next(b for b in app.button if b.label == '승인하고 전체 분석 시작 (유료)').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 4)
            self.assertEqual(app.session_state.hitl_tickets, {})

    def test_landing_page_requires_document_selection_without_ai_call(self):
        url = "https://example.com/ir"
        with self.empty_result_app() as (app, request), patch("partner_finance.public_documents.fetch_public_document",
                return_value={"kind": "links", "url": url, "links": [{"title": "Report 2025", "url": url + "/report.pdf"}]}):
            next(b for b in app.button if b.label == "이전: 파일 다시 선택").click().run()
            next(r for r in app.radio if r.label == "자료 가져오는 방법").set_value("공개 링크 (권장)").run()
            next(t for t in app.text_input if t.label == "공개 재무보고서 또는 IR 페이지 주소").set_value(url).run()
            next(b for b in app.button if b.label == "다음: 분석 준비").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 0)
            self.assertTrue(app.selectbox)
            self.assertTrue(next(b for b in app.button if b.label == "다음: 분석 준비").disabled)

    @contextmanager
    def empty_result_app(self, *, text="[PAGE 1] Revenue 100", fail=False):
        upload = BytesIO(b"public report")
        upload.name, upload.type = "public.pdf", "application/pdf"
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps({"facts": [], "warnings": ["No matching facts"]})}]}]}).encode()
        with tempfile.TemporaryDirectory() as root, \
                patch("streamlit.file_uploader", return_value=upload), \
                patch("partner_finance.ingest.parse_uploaded_file", return_value=([], [], text)), \
                patch("urllib.request.urlopen", return_value=response,
                      side_effect=RuntimeError("API unavailable") if fail else None) as request:
            app = AppTest.from_function(finance_app, default_timeout=20)
            app.session_state.test_root = root
            app.run()
            next(r for r in app.radio if r.label == "자료 가져오는 방법").set_value("파일 업로드").run()
            next(b for b in app.button if b.label == "다음: 분석 준비").click().run()
            yield app, request

    def test_zero_facts_returns_to_preparation_with_collapsed_history(self):
        with self.empty_result_app() as (app, request):
            next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(any(b.label == "다음: 분석 준비" for b in app.button))
            history = next(e for e in app.expander if e.label.startswith("이전 분석 기록"))
            self.assertFalse(history.proto.expanded)
            self.assertFalse(app.get("download_button"))
            self.assertFalse(app.warning)

    def test_api_failure_does_not_repeat_or_leave_consent_active(self):
        with self.empty_result_app(fail=True) as (app, request):
            next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("API unavailable" in e.value for e in app.error))
            self.assertEqual(app.session_state.hitl_tickets, {})
            app.run()
            self.assertEqual(request.call_count, 1)
            self.assertTrue(any(b.label == "다음: 분석 준비" for b in app.button))

    def test_large_request_still_requires_cost_confirmation(self):
        with self.empty_result_app(text="[PAGE 1] " + "Revenue 100 " * 2500) as (app, request):
            button = next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)")
            self.assertTrue(button.disabled)
            next(c for c in app.checkbox if "추가 비용 가능성" in c.label).check().run()
            self.assertFalse(next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").disabled)
            self.assertEqual(request.call_count, 0)

    def test_missing_prepared_file_never_calls_api(self):
        with self.empty_result_app() as (app, request):
            project = app.session_state.project
            del app.session_state[f"finance_request_{project.project_id}"]
            app.run()
            self.assertEqual(request.call_count, 0)
            self.assertTrue(any(b.label == "파일 선택으로 돌아가기" for b in app.button))
            self.assertFalse(any("분석 시작" in b.label for b in app.button))

    def test_approval_resumes_upload_and_shows_results_once(self):
        upload = BytesIO(b"public report")
        upload.name = "public.pdf"
        upload.type = "application/pdf"
        result = {"facts": [{"fiscal_year": 2025, "standard_item": "revenue",
            "original_label": "Revenue", "original_value": 100,
            "unit_multiplier": 1, "currency": "EUR", "reporting_scope": "연결",
            "period_start": "2025-01-01", "period_end": "2025-12-31",
            "source_locator": "PAGE 1", "evidence_quote": "Revenue 100"}], "warnings": []}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "status": "completed", "output": [{"type": "message", "content": [
                {"type": "output_text", "text": json.dumps(result)}]}]}).encode()
        with tempfile.TemporaryDirectory() as root, \
                patch("streamlit.file_uploader", return_value=upload), \
                patch("partner_finance.ingest.parse_uploaded_file", return_value=([], [], "[PAGE 1] Revenue 100")), \
                patch("urllib.request.urlopen", return_value=response) as request:
            app = AppTest.from_function(finance_app, default_timeout=20)
            app.session_state.test_root = root
            app.run()
            next(r for r in app.radio if r.label == "자료 가져오는 방법").set_value("파일 업로드").run()
            next(b for b in app.button if b.label == "다음: 분석 준비").click().run()
            self.assertEqual(request.call_count, 0)
            self.assertEqual(app.session_state.hitl_pending["action"], "upload")
            self.assertFalse(any(b.label == "다음: 분석 준비" for b in app.button))
            self.assertEqual(len(app.get("file_uploader")), 0)
            next(b for b in app.button if b.label == "이전: 파일 다시 선택").click().run()
            self.assertEqual(request.call_count, 0)
            self.assertTrue(any(b.label == "다음: 분석 준비" for b in app.button))
            next(b for b in app.button if b.label == "다음: 분석 준비").click().run()
            next(b for b in app.button if b.label == "승인하고 전체 분석 시작 (유료)").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(any(h.value == "3. 분석 결과를 확인해 주세요" for h in app.subheader))
            self.assertEqual(len(app.get("download_button")), 0)
            next(b for b in app.button if b.label == "다음: 사내 전달자료 받기").click().run()
            self.assertTrue(any(h.value == "4. 사내 Claude로 전달하세요" for h in app.subheader))
            self.assertFalse(app.exception)
            self.assertTrue(app.get("download_button"))
            next(b for b in app.button if b.label == "이전: 분석 결과 확인").click().run()
            app.run()
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()

import os
import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from partner_finance.storage import ProjectStore
from partner_finance.workflow import recalculate
from tests.helpers import sample_project


APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def financial_app():
    app = AppTest.from_file(str(APP), default_timeout=30)
    app.session_state.workspace_view = "재무 상세분석"
    return app.run()


class AppFlowTests(unittest.TestCase):
    def test_public_financials_continue_to_news_with_request_scoped_consent(self):
        upload = BytesIO(b"synthetic construction PDF")
        upload.name, upload.type = "synthetic.pdf", "application/pdf"
        finance = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"facts": []}'}]}]}
        news = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Synthetic construction news",
            "annotations": [{"type": "url_citation", "title": "Synthetic source", "url": "https://example.com"}]}]}]}

        def extraction(text, entity_id, source, provider, **kwargs):
            _, meta = provider.generate_json("Synthetic extraction", {"text": text}, 100)
            facts = sample_project().facts
            for fact in facts:
                fact.entity_id, fact.source_id = entity_id, source.source_id
            return facts, [], meta

        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network, patch("streamlit.file_uploader", return_value=upload), patch("partner_finance.ingest.parse_uploaded_file", return_value=([], [], "Synthetic public report")), patch("partner_finance.ai.extract_facts_from_text", side_effect=extraction):
            network.return_value.__enter__.return_value.read.side_effect = [json.dumps(finance).encode(), json.dumps(news).encode()]
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            app.text_input[0].set_value("Synthetic construction")
            next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
            next(c for c in app.checkbox if c.label.startswith("공개된 재무보고서")).check().run()
            next(b for b in app.button if b.label == "이 자료로 분석하기").click().run()
            network.assert_not_called()
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(network.call_count, 1)
            self.assertTrue(app.session_state.project.facts)
            self.assertEqual(app.session_state.hitl_pending["action"], "research")
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(network.call_count, 2)
            self.assertEqual(next(r for r in app.radio if r.label == "진행 단계").value, "2. 결과 확인")
            self.assertTrue(any(b.label == "Claude 전달자료 받기" for b in app.get("download_button")))
            self.assertFalse(app.text_input)
            app.run()
            self.assertEqual(network.call_count, 2)

    def test_news_one_click_approval_calls_api_once(self):
        payload = {"status": "completed", "output": [{"type": "message", "content": [{
            "type": "output_text", "text": "Synthetic construction news",
            "annotations": [{"type": "url_citation", "title": "Synthetic source", "url": "https://example.com"}]}]}]}
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network:
            network.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            app.text_input[0].set_value("Synthetic construction")
            next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
            next(r for r in app.radio if r.label == "분석 경로").set_value("공개 현안만 · 비공개 재무제표는 사내 Claude").run()
            next(b for b in app.button if b.label == "뉴스·사업정보 조사").click().run()
            self.assertFalse(app.exception)
            network.assert_not_called()
            self.assertFalse(app.text_input)
            self.assertFalse(any("추가 비용" in c.label for c in app.checkbox))
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(next(r for r in app.radio if r.label == "진행 단계").value, "2. 결과 확인")
            self.assertEqual(len(app.session_state.project.narrative["research_briefs"]), 1)
            app.run()
            self.assertEqual(network.call_count, 1)

    def test_pdf_one_click_approval_resumes_upload(self):
        upload = BytesIO(b"synthetic public PDF")
        upload.name, upload.type = "synthetic.pdf", "application/pdf"
        payload = {"status": "completed", "output": [{"type": "message", "content": [{
            "type": "output_text", "text": '{"facts": [], "warnings": []}'}]}]}
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network, patch("streamlit.file_uploader", return_value=upload), patch("partner_finance.ingest.parse_uploaded_file", return_value=([], [], "Synthetic annual report revenue 2025 100 EUR")):
            network.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            app.text_input[0].set_value("Synthetic construction")
            next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
            next(c for c in app.checkbox if c.label.startswith("공개된 재무보고서")).check().run()
            next(b for b in app.button if b.label == "이 자료로 분석하기").click().run()
            self.assertFalse(app.exception)
            network.assert_not_called()
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(network.call_count, 1)
            self.assertEqual(len(app.session_state.project.sources), 1)
            self.assertEqual(len(app.session_state.project.narrative["api_usage"]), 1)
            app.run()
            self.assertEqual(network.call_count, 1)

    def test_direct_start_empty_states_and_private_route(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test"}):
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            optional = next(e for e in app.expander if e.label == "공시 자동수집 · 선택사항")
            self.assertFalse(optional.proto.expanded)
            self.assertTrue(any(b.label == "SEC·DART 공시에서 찾기" for b in optional.button))
            next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
            self.assertTrue(app.error)
            app.text_input[0].set_value("Synthetic construction preview")
            with patch("partner_finance.simple_ui.find_candidates") as search:
                next(b for b in app.button if b.label == "이 기업으로 시작").click().run()
                search.assert_not_called()
            self.assertFalse(app.exception)
            self.assertFalse(any(t.label == "검토 담당자" for t in app.text_input))
            self.assertEqual(next(r for r in app.radio if r.label == "진행 단계").value, "1. 자료 준비")
            next(r for r in app.radio if r.label == "진행 단계").set_value("3. 사내 전달").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("전달할 자료가 아직" in v.value for v in app.info))
            next(b for b in app.button if b.label == "자료 준비로 이동").click().run()
            next(r for r in app.radio if r.label == "분석 경로").set_value("공개 현안만 · 비공개 재무제표는 사내 Claude").run()
            self.assertFalse(app.exception)
            self.assertFalse(app.get("file_uploader"))
            self.assertTrue(any(b.label == "뉴스·사업정보 조사" for b in app.button))

    def test_news_review_handoff_and_return_keeps_results(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test"}):
            from partner_finance.schema import AnalysisProject, EntityProfile
            project = AnalysisProject("Synthetic news", EntityProfile("Synthetic construction"))
            project.narrative["analysis_route"] = "news_only"
            project.narrative["research_briefs"] = [{"id": "synthetic-brief", "collected_at": "2026-10-01", "status": "검토 대기", "sections": [{"text": "Synthetic public evidence", "citations": [{"title": "Synthetic source", "url": "https://example.com"}]}]}]
            ProjectStore(root).save(project, "ui-test")
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            next(b for b in app.button if b.label == "결과 보기").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.text_input)
            self.assertFalse(app.text_area)
            self.assertFalse(app.selectbox)
            self.assertFalse(any("확인했습니다" in c.label for c in app.checkbox))
            self.assertFalse(any(b.label == "뉴스·사업정보 조사" for b in app.button))
            self.assertEqual(next(r for r in app.radio if r.label == "진행 단계").value, "2. 결과 확인")
            self.assertTrue(any("미검토 초안" in w.value for w in app.caption))
            self.assertTrue(any(b.label == "Claude 전달자료 받기" for b in app.get("download_button")))
            self.assertFalse(any(b.label == "Word 보고서" for b in app.get("download_button")))
            self.assertFalse(any(b.label == "원문 확인 완료로 기록" for b in app.button))
            self.assertFalse(any(e.label == "AI 사용 내역 · 절약 모드" for e in app.expander))
            self.assertEqual(app.session_state.project.narrative["research_briefs"][0]["status"], "검토 대기")
            self.assertFalse(app.session_state.project.narrative.get("hitl_review"))
            self.assertTrue(any("Synthetic public evidence" in m.value for m in app.markdown))
            app.toggle[0].set_value(True).run()
            next(b for b in app.button if b.label == "원문 확인 완료로 기록").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(next(r for r in app.radio if r.label == "진행 단계").value, "2. 결과 확인")
            from partner_finance.hitl import current_review
            self.assertTrue(current_review(app.session_state.project))
            self.assertEqual(app.session_state.project.narrative["research_briefs"][0]["status"], "승인")
            self.assertNotEqual(app.session_state.project.status, "검토 완료")
            self.assertTrue(current_review(ProjectStore(root).load(project.project_id, "ui-test")))
            app.run()
            self.assertEqual(len(app.session_state.project.narrative["hitl_review_history"]), 1)

    def test_financial_error_blocks_quick_review_but_not_draft(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test"}):
            project = sample_project()
            next(f for f in project.facts if f.standard_item == "total_liabilities").normalized_value = 1
            recalculate(project)
            ProjectStore(root).save(project, "ui-test")
            app = financial_app()
            app.checkbox(key="public_workspace_ack").check().run()
            next(b for b in app.button if b.label == "결과 보기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.label == "Claude 전달자료 받기" for b in app.get("download_button")))
            self.assertTrue(any("미검토 초안" in w.value for w in app.caption))
            app.toggle[0].set_value(True).run()
            self.assertTrue(next(b for b in app.button if b.label == "원문 확인 완료로 기록").disabled)
            self.assertFalse(app.exception)

    def test_portfolio_detail_and_new_entity(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test"}):
            store = ProjectStore(root)
            for i in range(3):
                project = sample_project()
                project.entity.legal_name = f"Synthetic UI {i}"
                recalculate(project)
                store.save(project, "ui-test")
            app = financial_app()
            self.assertFalse(app.exception)
            self.assertFalse(app.text_input)
            app.checkbox(key="public_workspace_ack").check().run()
            self.assertEqual(len(app.text_input), 1)
            self.assertFalse(app.number_input)
            self.assertFalse(any(s.label == "자료 출처" for s in app.selectbox))
            next(b for b in app.button if b.label == "결과 보기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(t.value == "재무역량 평가표" for t in app.subheader))
            self.assertFalse(any(b.label == "검증 및 계산 실행" for b in app.button))
            route = next(r for r in app.radio if r.label == "분석 경로")
            route.set_value("공개 현안만 · 비공개 재무제표는 사내 Claude").run()
            self.assertFalse(app.exception)
            self.assertFalse(any(t.value == "재무역량 평가표" for t in app.subheader))
            self.assertFalse(any(b.label == "이 자료로 분석하기" for b in app.button))
            next(r for r in app.radio if r.label == "분석 경로").set_value("공개 재무제표 + 공개 현안").run()
            app.toggle[0].set_value(True).run()
            next(b for b in app.button if b.label == "검증 및 계산 실행").click().run()
            self.assertFalse(app.exception)
            next(b for b in app.button if b.label == "다른 기업 보기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(t.label == "기업명" for t in app.text_input))
            next(t for t in app.text_input if t.label == "기업명").set_value("Synthetic overseas company")
            with patch("partner_finance.simple_ui.find_candidates", return_value=([], [])):
                next(b for b in app.button if b.label == "SEC·DART 공시에서 찾기").click().run()
            next(b for b in app.button if "공개자료로 계속하기" in b.label).click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.label == "이 자료로 분석하기" for b in app.button))
            self.assertTrue(any(b.label == "뉴스·사업정보 조사" for b in app.button))

    def test_password_gate(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "test-pass"}):
            app = financial_app()
            self.assertFalse(app.exception)
            self.assertFalse(app.radio)
            app.text_input[0].set_value("wrong")
            app.button[0].click().run()
            self.assertTrue(app.error)
            app.text_input[0].set_value("test-pass")
            app.button[0].click().run()
            self.assertFalse(app.exception)
            app.checkbox(key="public_workspace_ack").check().run()
            self.assertTrue(any(t.label == "기업명" for t in app.text_input))


if __name__ == "__main__":
    unittest.main()

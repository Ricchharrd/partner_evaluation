import json
import tempfile
import unittest
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
            next(b for b in app.button if b.label == "이 자료로 분석하기").click().run()
            self.assertEqual(request.call_count, 0)
            self.assertEqual(app.session_state.hitl_pending["action"], "upload")
            self.assertFalse(any(b.label == "이 자료로 분석하기" for b in app.button))
            next(b for b in app.button if b.label == "취소").click().run()
            self.assertEqual(request.call_count, 0)
            self.assertTrue(any(b.label == "이 자료로 분석하기" for b in app.button))
            next(b for b in app.button if b.label == "이 자료로 분석하기").click().run()
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(request.call_count, 1)
            self.assertTrue(any(h.value == "분석 결과와 사내 전달자료" for h in app.subheader))
            app.run()
            self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()

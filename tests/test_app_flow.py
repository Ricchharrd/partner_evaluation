import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from partner_finance.storage import ProjectStore
from partner_finance.workflow import recalculate
from tests.helpers import sample_project


APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class AppFlowTests(unittest.TestCase):
    def test_portfolio_detail_and_new_entity(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "ui-test"}):
            store = ProjectStore(root)
            for i in range(3):
                project = sample_project()
                project.entity.legal_name = f"Synthetic UI {i}"
                recalculate(project)
                store.save(project, "ui-test")
            app = AppTest.from_file(str(APP), default_timeout=30).run()
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
            self.assertTrue(any(t.label == "기업명 또는 종목코드" for t in app.text_input))
            next(t for t in app.text_input if t.label == "기업명 또는 종목코드").set_value("Synthetic overseas company")
            with patch("partner_finance.simple_ui.find_candidates", return_value=([], [])):
                next(b for b in app.button if b.label == "기업 찾기").click().run()
            next(b for b in app.button if "공개자료로 계속하기" in b.label).click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.label == "이 자료로 분석하기" for b in app.button))
            self.assertTrue(any(b.label == "뉴스·사업정보 조사" for b in app.button))

    def test_password_gate(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "test-pass"}):
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.radio)
            app.text_input[0].set_value("wrong")
            app.button[0].click().run()
            self.assertTrue(app.error)
            app.text_input[0].set_value("test-pass")
            app.button[0].click().run()
            self.assertFalse(app.exception)
            app.checkbox(key="public_workspace_ack").check().run()
            self.assertTrue(any(t.label == "기업명 또는 종목코드" for t in app.text_input))


if __name__ == "__main__":
    unittest.main()

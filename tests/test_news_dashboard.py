import tempfile
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

from partner_finance.market_news import ensure_featured_companies, saved_articles
from partner_finance.storage import ProjectStore
from partner_finance.public_gpt import build_public_gpt_packet
from partner_finance.schema import AnalysisProject, EntityProfile


def dashboard_app():
    import streamlit as st
    from partner_finance.market_ui import render_market
    from partner_finance.storage import ProjectStore
    render_market(ProjectStore(st.session_state.test_root), "test-user",
                  lambda key, default=None: default)


def connected_dashboard_app():
    import streamlit as st
    from partner_finance.market_ui import render_market
    from partner_finance.storage import ProjectStore
    render_market(ProjectStore(st.session_state.test_root), 'test-user',
                  lambda key, default=None: 'test-key' if key == 'OPENAI_API_KEY' else default)


class NewsDashboardTests(unittest.TestCase):
    def test_duplicate_company_projects_have_one_market_selector(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            ensure_featured_companies(store, 'test-user')
            duplicate = AnalysisProject('별도 Webuild 분석', EntityProfile('Webuild'))
            duplicate.narrative['market_watch'] = True
            store.save(duplicate, 'test-user')
            app = AppTest.from_function(dashboard_app, default_timeout=20)
            app.session_state.test_root = root
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value, '3개')
            self.assertEqual(sum('Webuild · EPC' in option for option in app.pills[0].options), 1)

    def test_update_requires_consent_then_saves_and_displays_new_article(self):
        url = 'https://www.webuildgroup.com/en/media/press-releases/test-public-news/'
        payload = {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed', 'action': {'sources': [{'url': url}]}},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'articles': [{
                'title': 'Webuild 새 수주 발표', 'summary': '회사가 공개한 신규 수주 소식이다.',
                'published_at': date.today().isoformat(), 'source_url': url,
                'source_name': 'Webuild', 'topic': '수주 및 사업'}]})}]}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as root, patch('urllib.request.urlopen', return_value=response) as request:
            app = AppTest.from_function(connected_dashboard_app, default_timeout=20)
            app.session_state.test_root = root
            app.run()
            next(b for b in app.button if b.label == '회사 소식 보기').click().run()
            next(b for b in app.button if b.label == '최신 뉴스 가져오기 (유료)').click().run()
            request.assert_not_called()
            next(b for b in app.button if b.label == '공개자료로 승인하고 실행').click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.error)
            request.assert_called_once()
            self.assertTrue(any(s.value == 'Webuild 새 수주 발표' for s in app.subheader))
            store = ProjectStore(root)
            webuild = next(p for p in store.list_projects('test-user') if p['legal_name'] == 'Webuild')
            saved = store.load(webuild['project_id'], 'test-user')
            self.assertEqual(len(saved_articles(saved)), 3)
            self.assertEqual(saved.narrative['market_last_count'], 1)

    def test_seed_is_idempotent_public_and_exportable(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            ensure_featured_companies(store, "test-user")
            ensure_featured_companies(store, "test-user")
            projects = [store.load(r["project_id"], "test-user") for r in store.list_projects("test-user")]
            self.assertEqual(len(projects), 3)
            self.assertEqual(sum(len(saved_articles(p)) for p in projects), 6)
            for project in projects:
                self.assertNotIn("market_last_checked", project.narrative)
                packet = build_public_gpt_packet(project).decode("utf-8-sig")
                for article in saved_articles(project):
                    self.assertIn(article["source_url"], packet)

    def test_excluded_seed_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            ensure_featured_companies(store, "test-user")
            project = store.load(store.list_projects("test-user")[0]["project_id"], "test-user")
            project.narrative["research_briefs"][0]["status"] = "제외"
            store.save(project, "test-user")
            ensure_featured_companies(store, "test-user")
            self.assertFalse(saved_articles(store.load(project.project_id, "test-user")))

    def test_dashboard_reads_filters_and_drills_down_without_network(self):
        with tempfile.TemporaryDirectory() as root, patch("urllib.request.urlopen") as request:
            app = AppTest.from_function(dashboard_app, default_timeout=20)
            app.session_state.test_root = root
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual([m.value for m in app.metric[:2]], ["3개", "6건"])
            next(t for t in app.text_input if t.label == "뉴스 찾기").set_value("Digital Realty").run()
            self.assertTrue(any(s.value == "전체 기업 소식, 1건" for s in app.subheader))
            next(t for t in app.text_input if t.label == "뉴스 찾기").set_value("").run()
            next(b for b in app.button if b.label == "회사 소식 보기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(s.value == "Webuild 소식, 2건" for s in app.subheader))
            next(b for b in app.button if b.label == "재무 상세분석").click().run()
            self.assertEqual(app.session_state.workspace_view, "재무 상세분석")
            self.assertEqual(app.session_state.project.entity.legal_name, "Webuild")
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

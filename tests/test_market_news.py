from datetime import date, timedelta
import json
import os
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from partner_finance.market_news import add_company, collect_news, parse_news, saved_articles, has_saved_news
from partner_finance.storage import ProjectStore
from partner_finance.handoff import build_claude_start
from partner_finance.hitl import current_review, review_digest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def response(rows=None, cited=True):
    if rows is None:
        rows = [{"title": "Synthetic construction award", "summary": "Synthetic news for testing only.",
                 "source_url": "https://example.com/news", "source_name": "Synthetic source",
                 "published_at": date.today().isoformat(), "topic": "수주 및 사업"}]
    return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text",
        "text": json.dumps({"articles": rows}), "annotations": [
            {"type": "url_citation", "url": r["source_url"]} for r in rows] if cited else []}]}]}


class MarketNewsTests(unittest.TestCase):
    def test_financial_upload_reuses_collected_news_without_second_search(self):
        from tests.helpers import sample_project
        upload = BytesIO(b"synthetic csv")
        upload.name, upload.type = "test.csv", "text/csv"

        def parse(name, content, entity_id, source):
            facts = sample_project().facts
            for fact in facts:
                fact.entity_id, fact.source_id = entity_id, source.source_id
            return facts, [], ""

        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "market-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network:
            store = ProjectStore(root)
            p = add_company(store, "market-test", "Synthetic builder")
            network.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            with patch("partner_finance.market_news.authorize_request"):
                collect_news(p, store, "market-test", "test", "test-model")
            network.reset_mock()
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            app.checkbox(key="public_workspace_ack").check().run()
            app.button_group(key="market_company").set_value(p.project_id).run()
            with patch("streamlit.file_uploader", return_value=upload), patch("partner_finance.ingest.parse_uploaded_file", side_effect=parse):
                next(b for b in app.button if b.label == "재무 상세분석").click().run()
                next(c for c in app.checkbox if c.label.startswith("공개된 재무보고서")).check().run()
                next(b for b in app.button if b.label == "이 자료로 분석하기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state.project.facts)
            self.assertTrue(has_saved_news(app.session_state.project))
            network.assert_not_called()

    def test_validated_sources_dates_and_deduplication(self):
        payload = response()
        self.assertEqual(len(parse_news(payload)), 1)
        with self.assertRaises(ValueError):
            parse_news(response(cited=False))
        row = json.loads(payload["output"][0]["content"][0]["text"])["articles"][0]
        for value in ["2020-01-01", (date.today() + timedelta(days=1)).isoformat(), "unknown"]:
            with self.assertRaises(ValueError):
                parse_news(response([{**row, "published_at": value}]))
        self.assertEqual(len(parse_news(response([row, row]))), 1)
        self.assertEqual(parse_news(response([])), [])
        with self.assertRaises(ValueError):
            parse_news({**payload, "status": "incomplete"})

    def test_owner_cooldown_and_restart(self):
        with TemporaryDirectory() as root:
            store = ProjectStore(root)
            p = add_company(store, "alice", "Example Construction")
            again = add_company(store, "alice", " example   construction ")
            self.assertEqual(p.project_id, again.project_id)
            other = add_company(store, "bob", "Example Construction")
            self.assertNotEqual(p.project_id, other.project_id)
            self.assertTrue(store.claim_news_refresh(p.project_id, "alice", now=100))
            self.assertFalse(ProjectStore(root).claim_news_refresh(p.project_id, "alice", now=200))
            self.assertEqual(store.news_refresh_remaining(p.project_id, "alice", now=200), 3500)
            with self.assertRaises(KeyError):
                store.claim_news_refresh(p.project_id, "bob", now=4000)
            self.assertTrue(store.claim_news_refresh(p.project_id, "alice", now=3700))

    def test_collection_handoff_and_invalidates_review_without_financial_changes(self):
        with TemporaryDirectory() as root, patch("partner_finance.market_news.authorize_request") as consent, patch("urllib.request.urlopen") as network:
            store = ProjectStore(root)
            p = add_company(store, "alice", "Example Construction")
            p.narrative["hitl_review"] = {"digest": review_digest(p)}
            network.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            collect_news(p, store, "alice", "test", "test-model")
            consent.assert_called_once()
            self.assertFalse(current_review(p))
            self.assertFalse(p.facts)
            self.assertTrue(has_saved_news(p))
            self.assertEqual(len(saved_articles(p)), 1)
            with patch("urllib.request.urlopen", side_effect=AssertionError("Reading must not call API")):
                restored = ProjectStore(root).load(p.project_id, "alice")
                packet = build_claude_start(restored).decode()
                self.assertIn("Synthetic construction award", packet)
                self.assertIn("https://example.com/news", packet)
            p.entity.legal_name = "Different entity"
            self.assertFalse(has_saved_news(p))
            self.assertEqual(saved_articles(p), [])
            self.assertNotIn("Synthetic construction award", build_claude_start(p).decode())

    def test_failure_keeps_existing_news_and_consumes_attempt(self):
        with TemporaryDirectory() as root, patch("partner_finance.market_news.authorize_request"), patch("urllib.request.urlopen") as network:
            store = ProjectStore(root)
            p = add_company(store, "alice", "Example Construction")
            p.narrative["research_briefs"] = [{"id": "existing", "sections": [], "status": "검토 대기"}]
            network.return_value.__enter__.return_value.read.return_value = json.dumps(response(cited=False)).encode()
            with self.assertRaises(ValueError):
                collect_news(p, store, "alice", "test", "test-model")
            self.assertEqual(p.narrative["research_briefs"][0]["id"], "existing")
            self.assertGreater(store.news_refresh_remaining(p.project_id, "alice"), 0)

    def test_default_news_approval_once_reopen_and_financial_drilldown(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "market-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network:
            network.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            app.checkbox(key="public_workspace_ack").check().run()
            self.assertEqual(app.radio(key="workspace_view").value, "기업 뉴스")
            self.assertFalse(app.get("file_uploader"))
            next(b for b in app.button if b.label == "Acciona 추가").click().run()
            network.assert_not_called()
            next(b for b in app.button if b.label == "업데이트").click().run()
            network.assert_not_called()
            self.assertEqual(app.session_state.hitl_pending["action"], "market_news")
            next(b for b in app.button if b.label == "공개자료로 승인하고 실행").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(network.call_count, 1)
            self.assertTrue(any(x.value == "Synthetic construction award" for x in app.subheader))
            self.assertTrue(next(b for b in app.button if b.label == "업데이트").disabled)
            app.run()
            self.assertEqual(network.call_count, 1)
            next(b for b in app.button if b.label == "재무 상세분석").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key="workspace_view").value, "재무 상세분석")
            self.assertTrue(app.get("file_uploader"))
            self.assertTrue(has_saved_news(app.session_state.project))
            app.radio(key="workspace_view").set_value("기업 뉴스").run()
            self.assertTrue(any(x.value == "Synthetic construction award" for x in app.subheader))
            self.assertEqual(network.call_count, 1)
            reopened = AppTest.from_file(str(APP), default_timeout=30).run()
            reopened.checkbox(key="public_workspace_ack").check().run()
            self.assertFalse(reopened.exception)
            self.assertTrue(any(x.value == "Synthetic construction award" for x in reopened.subheader))
            self.assertEqual(network.call_count, 1)

    def test_dashboard_read_only_without_ack_and_topic_filter(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "market-test", "OPENAI_API_KEY": "test-only"}), patch("urllib.request.urlopen") as network:
            store = ProjectStore(root)
            project = add_company(store, "market-test", "Synthetic builder")
            network.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            with patch("partner_finance.market_news.authorize_request"):
                collect_news(project, store, "market-test", "test", "test-model")
            network.reset_mock()
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, "대시보드")
            self.assertTrue(any(h.value == "Synthetic construction award" for h in app.subheader))
            app.button_group(key="market_company").set_value(project.project_id).run()
            self.assertTrue(next(b for b in app.button if b.label == "재무 상세분석").disabled)
            self.assertTrue(next(b for b in app.button if b.label == "업데이트").disabled)
            next(s for s in app.selectbox if s.label == "뉴스 주제").set_value("소송 및 규제").run()
            self.assertFalse(any(h.value == "Synthetic construction award" for h in app.subheader))
            next(s for s in app.selectbox if s.label == "뉴스 주제").set_value("전체").run()
            self.assertTrue(any(h.value == "Synthetic construction award" for h in app.subheader))
            next(b for b in app.button if b.label == "전체 기업 소식").click().run()
            self.assertEqual(app.button_group(key="market_company").value, "all")
            network.assert_not_called()

    def test_manage_companies_registration_never_calls_api(self):
        with TemporaryDirectory() as root, patch.dict(os.environ, {"DATA_DIR": root, "APP_PASSWORD": "", "APP_USER_ID": "market-test"}), patch("urllib.request.urlopen") as network:
            app = AppTest.from_file(str(APP), default_timeout=30).run()
            self.assertTrue(next(b for b in app.button if b.label == "관심 기업에 추가").disabled)
            app.radio(key="workspace_view").set_value("관심 기업 관리").run()
            app.checkbox(key="public_workspace_ack").check().run()
            next(t for t in app.text_input if t.label == "기업명").set_value("Synthetic builder")
            next(b for b in app.button if b.label == "관심 기업에 추가").click().run()
            self.assertFalse(app.exception)
            app.radio(key="workspace_view").set_value("기업 뉴스").run()
            self.assertEqual(len(ProjectStore(root).list_projects("market-test")), 1)
            self.assertTrue(app.button_group(key="market_company").value)
            network.assert_not_called()

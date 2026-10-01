import json
from io import BytesIO
import unittest
from urllib.error import HTTPError
from unittest.mock import patch, MagicMock
from zipfile import ZipFile

from partner_finance.openai_provider import OpenAIProvider, response_text
from partner_finance.handoff import build_handoff
from partner_finance.workflow import recalculate
from partner_finance.research import research_company_openai
from tests.helpers import sample_project


class OpenAIHandoffTests(unittest.TestCase):
    def test_http_diagnostic_does_not_expose_server_message(self):
        body = {"error": {"code": "unsupported_value", "param": "text.format.type", "message": "SECRET_KEY PRIVATE_DOCUMENT"}}
        error = HTTPError("https://api.openai.com/v1/responses", 400, "Bad request", {}, BytesIO(json.dumps(body).encode()))
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                OpenAIProvider("SECRET_KEY", approval=lambda body: None).request({})
        message = str(caught.exception)
        self.assertIn("text.format.type", message)
        self.assertIn("unsupported_value", message)
        self.assertNotIn("SECRET_KEY", message)
        self.assertNotIn("PRIVATE_DOCUMENT", message)

    def test_research_cache_and_minimal_identity(self):
        project = sample_project()
        project.entity.notes = "INTERNAL_NOTE"
        payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Public overview", "annotations": [{"type": "url_citation", "url": "https://www.sec.gov/", "title": "SEC"}]}]}]}
        with patch.object(OpenAIProvider, "request", return_value=payload) as call:
            first = research_company_openai(project, "test", "model")
            self.assertEqual(first, research_company_openai(project, "test", "model"))
        self.assertEqual(call.call_count, 1)
        self.assertNotIn("INTERNAL_NOTE", call.call_args.args[0]["input"])
        self.assertEqual(first["status"], "검토 대기")

    def test_json_request_is_bounded_and_not_stored(self):
        payload = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"facts": []}'}]}]}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch("urllib.request.urlopen", return_value=response) as call:
            result, meta = OpenAIProvider("test-key", approval=lambda body: None).generate_json("Extract", {}, 9999)
        body = json.loads(call.call_args.args[0].data)
        self.assertFalse(body["store"])
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertEqual(body["reasoning"], {"effort": "none"})
        self.assertEqual(body["max_output_tokens"], 5000)
        self.assertEqual(body["input"][0]["role"], "system")
        self.assertIn("JSON", body["input"][0]["content"])
        self.assertEqual(json.loads(body["input"][1]["content"]), {})
        self.assertEqual(result, {"facts": []})
        self.assertEqual(meta["provider"], "openai")

    def test_missing_key_and_incomplete_response_fail(self):
        with self.assertRaises(ValueError):
            OpenAIProvider("").request({})
        with self.assertRaises(ValueError):
            response_text({"status": "incomplete"})
        with self.assertRaises(ValueError):
            response_text({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]})

    def test_packet_is_local_and_preserves_evidence(self):
        project = sample_project()
        project.sources[0].local_path = "SECRET_LOCAL_PATH"
        project.narrative["api_usage"] = [{"secret": "SHOULD_NOT_EXPORT"}]
        recalculate(project)
        with patch("urllib.request.urlopen", side_effect=AssertionError("No network during export")):
            packet = build_handoff(project)
        with ZipFile(BytesIO(packet)) as archive:
            self.assertEqual(len(archive.namelist()), 5)
            brief = archive.read("01_review_brief.md").decode()
            raw = archive.read("02_evidence.json").decode()
        evidence = json.loads(raw)
        self.assertEqual(evidence["schema"], "partner-review-packet/1.0")
        self.assertEqual(len(evidence["facts"]), len(project.facts))
        self.assertNotIn("SECRET_LOCAL_PATH", raw)
        self.assertNotIn("SHOULD_NOT_EXPORT", raw)
        self.assertIn("대용치", brief)
        self.assertIn("누락은 0이 아니", brief)

    def test_stale_packet_rejected(self):
        project = sample_project()
        recalculate(project)
        project.facts[0].normalized_value += 1
        with self.assertRaises(ValueError):
            build_handoff(project)

import unittest
import json
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from partner_finance.openai_provider import OpenAIProvider, safe_api_error


class OpenAIProviderErrorTests(unittest.TestCase):
    def test_model_context_error_is_distinguished_without_echoing_message(self):
        error = HTTPError("https://api.openai.com/v1/responses", 400, "Bad Request", {},
                          BytesIO(json.dumps({"error": {"code": "context_length_exceeded", "message": "SECRET"}}).encode()))
        message = safe_api_error(error)
        self.assertIn("모델의 최대", message)
        self.assertNotIn("SECRET", message)

    def test_output_budget_can_be_omitted_without_clamping_other_requests(self):
        provider = OpenAIProvider("test-key", approval=lambda body: None)
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "{}"}]}]}
        with patch.object(provider, "request", return_value=response) as request:
            provider.generate_json("Extract", {}, max_tokens=None)
            self.assertNotIn("max_output_tokens", request.call_args.args[0])
            provider.generate_json("Extract", {}, max_tokens=32000)
            self.assertEqual(request.call_args.args[0]["max_output_tokens"], 32000)

    def test_invalid_response_body_is_identified(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"not json"
        provider = OpenAIProvider("test-key", approval=lambda body: None)
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "API 응답 본문이 JSON이 아닙니다"):
                provider.request({})

    def test_fenced_json_is_accepted_without_exposing_invalid_content(self):
        provider = OpenAIProvider("test-key", approval=lambda body: None)
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "```json\n{\"facts\": []}\n```"}]}]}
        with patch.object(provider, "request", return_value=response):
            self.assertEqual(provider.generate_json("Extract", {})[0], {"facts": []})
        response["output"][0]["content"][0]["text"] = "SECRET_NOT_JSON"
        with patch.object(provider, "request", return_value=response):
            with self.assertRaises(ValueError) as caught:
                provider.generate_json("Extract", {})
        self.assertNotIn("SECRET_NOT_JSON", str(caught.exception))

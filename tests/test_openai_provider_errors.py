import unittest
from unittest.mock import MagicMock, patch

from partner_finance.openai_provider import OpenAIProvider


class OpenAIProviderErrorTests(unittest.TestCase):
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

import unittest
from partner_finance.research import parse_research_response


class ResearchTests(unittest.TestCase):
    def test_only_cited_sections_survive(self):
        rows = parse_research_response({"stop_reason": "end_turn", "content": [
            {"type": "text", "text": "unsupported"},
            {"type": "text", "text": "supported", "citations": [{"type": "web_search_result_location", "url": "https://example.com/source", "title": "Source"}]}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], "supported")

    def test_incomplete_or_uncited_search_is_not_success(self):
        for payload in [{"stop_reason": "max_tokens"}, {"stop_reason": "pause_turn"}, {"content": [{"type": "text", "text": "no evidence"}]}]:
            with self.assertRaises(ValueError):
                parse_research_response(payload)

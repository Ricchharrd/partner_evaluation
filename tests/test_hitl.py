import unittest
from unittest.mock import patch
from partner_finance.hitl import preflight, consume_ticket, review_digest, current_review
from partner_finance.openai_provider import OpenAIProvider
from partner_finance.workflow import recalculate, finalize
from tests.helpers import sample_project


class HITLTests(unittest.TestCase):
    def test_no_network_without_approval(self):
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                OpenAIProvider("key").request({"input": "public report"})
            network.assert_not_called()

    def test_credentials_block_even_with_approval(self):
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                OpenAIProvider("key", approval=lambda body: None).request({"input": "sk-" + "x" * 30})
            network.assert_not_called()
        self.assertTrue(preflight({"input": "대외비"})["sensitive"])

    def test_volume_and_tools(self):
        self.assertTrue(preflight({"input": "x" * 21000})["high_volume"])
        self.assertTrue(preflight({"tools": [{"type": "web_search"}]})["high_volume"])
        self.assertIsNone(preflight({"model": "unknown"})["estimated_text_usd"])

    def test_single_use_expiry_and_scope(self):
        tickets = {"one": {"expires": 100}}
        self.assertFalse(consume_ticket(tickets, "other", 50))
        self.assertTrue(consume_ticket(tickets, "one", 50))
        self.assertFalse(consume_ticket(tickets, "one", 50))
        self.assertFalse(consume_ticket({"one": {"expires": 40}}, "one", 50))

    def test_final_approval_and_invalidation(self):
        project = sample_project()
        recalculate(project)
        with self.assertRaises(ValueError):
            finalize(project, "reviewer", "note")
        project.narrative["hitl_review"] = {"reviewer": "reviewer", "digest": review_digest(project)}
        finalize(project, "reviewer", "note")
        self.assertTrue(current_review(project))
        project.narrative["research_briefs"] = [{"text": "new evidence"}]
        self.assertFalse(current_review(project))
        with self.assertRaises(ValueError):
            finalize(project, "reviewer", "note")

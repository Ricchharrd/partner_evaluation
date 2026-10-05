import unittest
import json

from partner_finance.document_selection import select_financial_text, SELECTION_VERSION
from partner_finance.hitl import preflight


class DocumentSelectionTests(unittest.TestCase):
    def test_public_confidentiality_discussion_is_not_a_document_classification(self):
        text = ("This allows anonymous or confidential \n(at their own discretion) notifications.\n"
                "Webuild is committed to protecting the confidentiality of corporate information.")
        body = {"input": [{"role": "user", "content": json.dumps({"document_text": text})}]}
        self.assertFalse(preflight(body)["sensitive"])
        self.assertTrue(preflight(body)["sensitive_terms"])
        for marker in ("CONFIDENTIAL", "Classification: Confidential", "This report is confidential",
                       "Strictly confidential", "INTERNAL ONLY", "대외비"):
            body["input"][0]["content"] = json.dumps({"document_text": marker + "\nRevenue 100"})
            self.assertTrue(preflight(body)["sensitive"], marker)
        self.assertTrue(preflight({"input": "sk-" + "a" * 32})["blocked"])

    def test_full_report_preserved_beyond_old_budget(self):
        text = "\n\n".join(f"[PAGE {i}]\n" + "Company strategy. " * 300 for i in range(1, 58))
        selected, warnings = select_financial_text(text)
        self.assertEqual(selected, text)
        self.assertIn("57개 페이지", warnings[0])
        self.assertNotEqual(SELECTION_VERSION, "statements-4")

    def test_contents_cannot_hide_later_statements(self):
        text = "[PAGE 2]\nCONTENTS Consolidated income statement 10 Balance sheet and cash flow 14\n"
        text += "[PAGE 3]\nLEGAL DISCLAIMER\n"
        text += "[PAGE 11]\nC O N S O L I D A T E D  I N C O M E\nRevenue 2025 2024 20236 19190\n"
        text += "[PAGE 15]\nC O N S O L I D A T E D  B A L A N C E\nTOTAL ASSETS 35850 34620\n"
        self.assertEqual(select_financial_text(text)[0], text)

    def test_explicit_limit_fails_instead_of_truncating(self):
        with self.assertRaises(ValueError):
            select_financial_text("report" * 100, budget=100)

    def test_full_report_output_budget_and_security(self):
        body = {"input": "x" * 300000, "max_output_tokens": 16000}
        self.assertFalse(preflight(body)["over_limit"])
        self.assertFalse(preflight({**body, "input": "x" * 2000001})["over_limit"])
        self.assertFalse(preflight({"input": "x" * 2000001})["over_limit"])
        self.assertIsNone(preflight({"input": "report"})["output_limit"])
        self.assertTrue(preflight({**body, "input": "internal only"})["sensitive"])

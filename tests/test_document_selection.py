import unittest
from partner_finance.document_selection import select_financial_text


class DocumentSelectionTests(unittest.TestCase):
    def test_late_financial_pages_are_selected(self):
        intro = "\n\n".join(f"[PAGE {i}]\n" + "Company strategy. " * 300 for i in range(1, 30))
        text = intro + "\n[PAGE 30]\nConsolidated balance sheet Total assets 2025 1000\n[PAGE 31]\nCash flow 2025 200"
        selected, warnings = select_financial_text(text)
        self.assertIn("[PAGE 30]", selected)
        self.assertIn("[PAGE 31]", selected)
        self.assertLessEqual(len(selected), 60000)
        self.assertTrue(warnings)

    def test_short_document_preserved(self):
        self.assertEqual(select_financial_text("Short report"), ("Short report", []))

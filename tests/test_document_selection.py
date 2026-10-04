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

    def test_spaced_korean_statement_titles_beat_subsidiary_notes(self):
        text = "[PAGE 2]\n목 차 연결재무제표 110 연결재무제표 주석 116\n"
        text += "[PAGE 5]\n별도 재무요약 " + "매출 2026 999 " * 15
        text += "[PAGE 6]\n반 기 연 결 재 무 상 태 표 " + "자산 2026 100 " * 15
        text += "[PAGE 7]\n부채와자본총계 " + "2026 100 " * 8
        text += "[PAGE 8]\n반 기 연 결 포 괄 손 익 계 산 서 " + "매출액 2026 50 " * 15
        text += "[PAGE 10]\n반 기 연 결 현 금 흐 름 표 " + "영업활동 2026 20 " * 15
        text += "".join(f"[PAGE {page}]\n일반 주석\n" for page in range(11, 23))
        text += "[PAGE 23]\n삼성바이오로직스 요약 연결재무상태표 " + "자산 2026 999 " * 15
        selected, _ = select_financial_text(text)
        for page in (6, 7, 8, 10):
            self.assertIn(f"[PAGE {page}]", selected)
        self.assertNotIn("[PAGE 5]", selected)
        self.assertNotIn("[PAGE 23]", selected)

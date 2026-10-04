import unittest

from partner_finance.schema import FinancialFact
from partner_finance.workflow import filter_interim_comparatives
from partner_finance.workflow import recalculate
from partner_finance.schema import AnalysisProject, EntityProfile
from partner_finance.periods import period_label
from partner_finance.validation import validate_facts
from partner_finance.handoff import packet_content, build_claude_start
from partner_finance.reports import build_word
from io import BytesIO
from docx import Document


def fact(year, item, start, end):
    return FinancialFact(entity_id="samsung", fiscal_year=year, standard_item=item,
                         original_label=item, original_value=1, normalized_value=1,
                         currency="KRW", reporting_scope="연결", period_start=start, period_end=end)


class PeriodGuardTests(unittest.TestCase):
    def test_interim_comparatives_do_not_replace_existing_full_year(self):
        existing = [fact(2025, "revenue", "2025-01-01", "2025-12-31")]
        incoming = [fact(2026, "revenue", "2026-01-01", "2026-06-30"),
                    fact(2025, "revenue", "2025-01-01", "2025-06-30"),
                    fact(2025, "total_assets", "", "2025-12-31")]
        kept, warnings = filter_interim_comparatives(existing, incoming)
        self.assertEqual([item.fiscal_year for item in kept], [2026])
        self.assertIn("2건", warnings[0])

    def test_annual_comparatives_remain_available(self):
        incoming = [fact(2025, "revenue", "2025-01-01", "2025-12-31"),
                    fact(2024, "revenue", "2024-01-01", "2024-12-31")]
        self.assertEqual(filter_interim_comparatives([], incoming), (incoming, []))

    def test_interim_is_not_presented_as_annual_error_or_yoy_shock(self):
        project = AnalysisProject("삼성물산 연결 검토", EntityProfile("삼성물산(주)"))
        project.facts = [fact(2025, "revenue", "2025-01-01", "2025-12-31"),
                         fact(2026, "revenue", "2026-01-01", "2026-06-30")]
        project.facts[0].normalized_value = 100
        project.facts[1].normalized_value = 200
        recalculate(project)
        self.assertIn("INTERIM_PERIOD", {v.code for v in project.validations})
        self.assertNotIn("LARGE_YOY_CHANGE", {v.code for v in project.validations})
        self.assertNotIn("CONTEXT_BLOCK", {v.code for v in project.validations})
        self.assertTrue(all(r.value is None for r in project.ratios if r.fiscal_year == 2026))
        self.assertEqual(period_label(project.facts, 2026), "2026년 중간 (2026-01-01~2026-06-30)")
        brief, _ = packet_content(project)
        self.assertIn("2026년 중간", brief.decode("utf-8"))
        self.assertIn("사업부", build_claude_start(project).decode("utf-8"))
        report = Document(BytesIO(build_word(project)))
        self.assertIn("사업부", " ".join(p.text for p in report.paragraphs))

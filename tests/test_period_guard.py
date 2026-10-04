import unittest

from partner_finance.schema import FinancialFact
from partner_finance.workflow import filter_interim_comparatives


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

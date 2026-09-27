import unittest

from partner_finance.analysis import calculate_ratios, safe_ratio
from partner_finance.policy import evaluate_company_policy
from partner_finance.schema import AnalysisProject, EntityProfile
from partner_finance.validation import validate_facts

from tests.helpers import fact, sample_project


class AnalysisValidationTests(unittest.TestCase):
    def test_missing_and_invalid_denominator_are_not_zero(self):
        self.assertEqual(safe_ratio(None, 10), (None, "입력값 누락"))
        self.assertEqual(safe_ratio(1, 0), (None, "분모가 0"))
        self.assertEqual(safe_ratio(1, -1, positive_denominator=True), (None, "분모가 음수여서 비교 불가"))

    def test_ratios_and_negative_equity(self):
        project = sample_project()
        project.facts.append(fact(2025, "total_liabilities", 100))
        project.facts.append(fact(2025, "total_equity", -20))
        ratios = calculate_ratios(project.facts)
        debt_2025 = next(row for row in ratios if row.fiscal_year == 2025 and row.metric_key == "debt_ratio")
        self.assertIsNone(debt_2025.value)
        self.assertIn("음수", debt_2025.status)
        margin_2024 = next(row for row in ratios if row.fiscal_year == 2024 and row.metric_key == "operating_margin")
        self.assertAlmostEqual(margin_2024.value, 0.1)

    def test_validation_detects_balance_currency_and_large_change(self):
        facts = [
            fact(2023, "revenue", 100, currency="USD"),
            fact(2023, "operating_income", 10, currency="EUR"),
            fact(2023, "net_income", 5), fact(2023, "total_assets", 100),
            fact(2023, "total_liabilities", 80), fact(2023, "total_equity", 10),
            fact(2024, "revenue", 300), fact(2024, "operating_income", 20),
            fact(2024, "net_income", 10), fact(2024, "total_assets", 200),
            fact(2024, "total_liabilities", 120), fact(2024, "total_equity", 80),
        ]
        codes = {issue.code for issue in validate_facts(facts)}
        self.assertIn("BALANCE_MISMATCH", codes)
        self.assertIn("MIXED_CURRENCY", codes)
        self.assertIn("LARGE_YOY_CHANGE", codes)
        self.assertIn("CASHFLOW_UNVERIFIABLE", codes)

    def test_user_correction_preserves_extracted_value(self):
        row = fact(2024, "revenue", 100)
        row.apply_correction(110, "원문 재확인")
        self.assertEqual(row.normalized_value, 100)
        self.assertEqual(row.effective_value, 110)
        self.assertEqual(row.user_reason, "원문 재확인")

    def test_policy_holds_financial_company_and_missing_inputs(self):
        entity = EntityProfile("Bank", entity_type="금융회사")
        project = AnalysisProject("Bank", entity)
        self.assertEqual(evaluate_company_policy(project)[0]["status"], "별도 기준 필요")
        project = sample_project()
        project.ratios = calculate_ratios(project.facts)
        evaluation = evaluate_company_policy(project)
        self.assertEqual(evaluation[-1]["status"], "산정")


if __name__ == "__main__":
    unittest.main()

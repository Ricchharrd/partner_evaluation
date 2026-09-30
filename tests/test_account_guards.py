import unittest
from io import BytesIO
from docx import Document
from partner_finance.account_guards import mapping_problem, normalize_scope
from partner_finance.validation import validate_facts
from partner_finance.workflow import recalculate
from partner_finance.reports import build_word
from tests.helpers import sample_project


class AccountGuardTests(unittest.TestCase):
    def test_wrong_accounts_rejected(self):
        for key, label in [("total_liabilities", "TOTAL LIABILITIES AND EQUITY"),
                           ("net_income", "Attributable Net Profit"),
                           ("interest_expense", "Net financial result"),
                           ("financial_debt", "Net financial debt"),
                           ("investing_cash_flow", "Net investment cashflow")]:
            self.assertTrue(mapping_problem(key, label))
        self.assertFalse(mapping_problem("net_income", "Profit after Taxes"))
        self.assertTrue(mapping_problem("operating_cash_flow", "Operating cashflow", document="Operating Cash Flow: This APM represents the capacity"))

    def test_scopes_and_stock_flow_periods(self):
        project = sample_project()
        for i, f in enumerate(project.facts):
            f.reporting_scope = "Consolidated" if i % 2 else "연결"
            f.period_end = f"{f.fiscal_year}-12-31"
            f.period_start = f"{f.fiscal_year}-01-01" if f.standard_item == "revenue" else ""
        recalculate(project)
        self.assertFalse(any(v.code in {"MIXED_SCOPE", "MIXED_PERIOD", "CONTEXT_BLOCK"} for v in project.validations))
        self.assertEqual(normalize_scope("separate"), "별도")

    def test_bad_legacy_value_masked_in_report(self):
        project = sample_project()
        fact = next(f for f in project.facts if f.standard_item == "total_liabilities")
        fact.original_label = "TOTAL LIABILITIES AND EQUITY"
        recalculate(project)
        self.assertTrue(any(v.code == "ACCOUNT_MAPPING" for v in project.validations))
        document = Document(BytesIO(build_word(project)))
        self.assertTrue(any("검증 오류·확인 필요" in c.text for t in document.tables for r in t.rows for c in r.cells))

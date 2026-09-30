import unittest
from partner_finance.numeric_input import parse_number
from partner_finance.ai import extract_facts_from_text
from partner_finance.schema import SourceDocument


class Stub:
    def __init__(self, rows):
        self.rows = rows

    def generate_json(self, *args, **kwargs):
        return {"facts": self.rows, "warnings": []}, {"model": "test"}


class NumericExtractionTests(unittest.TestCase):
    def test_grouped_and_signed_numbers(self):
        for value, expected in [("20,236", 20236), ("-1,074", -1074), ("(1,074)", -1074), ("1,000,000", 1000000)]:
            self.assertEqual(parse_number(value), expected)
        for value in ["1.234,56", "1,23", None, True, "Million Euro"]:
            with self.assertRaises(ValueError):
                parse_number(value)

    def test_grouped_fact_survives_and_invalid_is_reported(self):
        row = {"original_value": "20,236", "unit_multiplier": "1,000,000", "fiscal_year": 2025,
               "standard_item": "revenue", "currency": "EUR", "evidence_quote": "Revenues 20,236 19,190"}
        facts, warnings, _ = extract_facts_from_text("[PAGE 11]\nRevenues 20,236 19,190", "entity",
            SourceDocument("report", "pdf"), Stub([row, {**row, "unit_multiplier": "unknown"}]))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].normalized_value, 20236000000)
        self.assertTrue(any("AI 후보 2건 / 반영 1건 / 제외 1건" in w for w in warnings))

    def test_missing_facts_not_silent_empty(self):
        with self.assertRaises(ValueError):
            extract_facts_from_text("report", "entity", SourceDocument("report", "pdf"), Stub(None))

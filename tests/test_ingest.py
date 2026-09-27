from io import BytesIO
import unittest

from openpyxl import Workbook

from partner_finance.ingest import parse_csv, parse_xlsx, sample_csv_bytes, validate_public_url
from partner_finance.schema import SourceDocument


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceDocument(name="input", source_type="합성 테스트")

    def test_csv_template_parses_and_applies_unit(self):
        facts, warnings = parse_csv(sample_csv_bytes(), "entity", self.source)
        self.assertFalse(warnings)
        revenue = next(row for row in facts if row.standard_item == "revenue")
        self.assertEqual(revenue.normalized_value, 1_200_000_000)
        self.assertIn("CSV row", revenue.source_locator)

    def test_simple_wide_xlsx_parses(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["항목", 2023, 2024])
        ws.append(["매출액", 100, 120])
        ws.append(["자산총계", 200, 240])
        buffer = BytesIO()
        wb.save(buffer)
        facts, _ = parse_xlsx(buffer.getvalue(), "entity", self.source)
        self.assertEqual(len(facts), 4)
        self.assertEqual({row.fiscal_year for row in facts}, {2023, 2024})

    def test_ssrf_protection_rejects_localhost(self):
        with self.assertRaises(ValueError):
            validate_public_url("http://127.0.0.1/report.pdf")


if __name__ == "__main__":
    unittest.main()

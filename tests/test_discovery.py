import unittest
from unittest.mock import patch
from partner_finance.discovery import find_candidates, latest_sec_year, collect_latest
from tests.helpers import sample_project


class DiscoveryTests(unittest.TestCase):
    def test_latest_annual_excludes_quarters(self):
        rows = [
            {"fy": 2024, "fp": "FY", "form": "10-K", "start": "2024-01-01", "end": "2024-12-31"},
            {"fy": 2025, "fp": "Q3", "form": "10-Q", "start": "2025-01-01", "end": "2025-09-30"},
        ]
        self.assertEqual(latest_sec_year({"facts": {"us-gaap": {"Revenue": {"units": {"USD": rows}}}}}), 2024)
        with self.assertRaises(ValueError):
            latest_sec_year({})

    def test_missing_dart_key_not_claimed_no_data(self):
        with patch("partner_finance.discovery.candidate_rows") as sec:
            rows, notices = find_candidates("삼성전자")
        self.assertEqual(rows, [])
        self.assertIn("설정되지", notices[0])
        sec.assert_not_called()

    def test_sources_combined_and_failures_reported(self):
        with patch("partner_finance.discovery.search_dart_companies", return_value=([{"corp_name": "Test", "corp_code": "1"}], [])), patch("partner_finance.discovery.candidate_rows", side_effect=TimeoutError):
            rows, notices = find_candidates("Test", "test-key")
        self.assertEqual(rows[0]["source"], "DART")
        self.assertIn("실패", notices[0])

    def test_dart_latest_and_scope_reused(self):
        from datetime import date
        year = date.today().year - 1
        def fetch(key, corp, target, scope):
            return [{}] if target == year and scope == "OFS" else []
        with patch("partner_finance.discovery._fetch_financial_rows", side_effect=fetch), patch("partner_finance.discovery.collect_dart_project", return_value=(sample_project(), [])) as collect:
            project, warnings = collect_latest({"source": "DART", "match": {"corp_code": "1"}}, "test-key")
        self.assertEqual(collect.call_args.args[-3:], (year - 2, year, "OFS"))
        self.assertEqual(project.narrative["automatic_period"]["end"], year)
        self.assertTrue(warnings)

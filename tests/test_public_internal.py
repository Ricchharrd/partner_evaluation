from dataclasses import asdict
import json
import unittest
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from unittest.mock import patch

from partner_finance.handoff import packet_content
from partner_finance.offline_review import calculate_internal, approval_digest
from partner_finance.schema import AnalysisProject, EntityProfile
from partner_finance.workflow import recalculate
from tests.helpers import sample_project


def internal_payload():
    project = sample_project()
    facts = []
    for f in project.facts:
        facts.append({"fiscal_year": f.fiscal_year, "standard_item": f.standard_item,
            "original_label": f.original_label, "original_value": f.original_value,
            "currency": f.currency, "source_id": f.source_id, "source_locator": f.source_locator,
            "quote": str(f.original_value), "period_end": f"{f.fiscal_year}-12-31", "reporting_scope": "연결"})
    payload = {"schema": "internal-financial-input/1.0", "entity": asdict(project.entity),
            "human_review": {"confirmed": True, "reviewer": "Tester", "note": "Synthetic data checked"}, "facts": facts}
    payload["human_review"]["input_digest"] = approval_digest(payload)
    return payload


class PublicInternalTests(unittest.TestCase):
    def test_packaged_cli_runs_without_app_dependencies(self):
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            with ZipFile(root / "deliverables" / "partner-review-skill.zip") as z:
                self.assertFalse(any(".env" in n or "secrets" in n or "__pycache__" in n for n in z.namelist()))
                z.extractall(tmp)
            data = tmp / "input.json"
            data.write_text(json.dumps(internal_payload()), encoding="utf-8")
            output = tmp / "result.json"
            run = subprocess.run([sys.executable, "-S", str(tmp / "partner-financial-review/scripts/calculate.py"), str(data), str(output)],
                                 capture_output=True, text=True, cwd=tmp)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["schema"], "internal-financial-result/1.0")

    def test_private_marker_blocks_even_with_callback(self):
        from partner_finance.openai_provider import OpenAIProvider
        with patch("urllib.request.urlopen") as network:
            with self.assertRaises(ValueError):
                OpenAIProvider("test", approval=lambda body: None).request({"input": "CONFIDENTIAL financial statement"})
            network.assert_not_called()

    def test_news_only_packet_without_financial_calculation(self):
        p = AnalysisProject("Example", EntityProfile("Example"))
        brief, raw = packet_content(p)
        evidence = json.loads(raw)
        self.assertEqual(evidence["analysis_route"], "news_only")
        self.assertEqual(evidence["facts"], [])
        self.assertIn("Claude", brief.decode())

    def test_news_route_excludes_old_financials_without_deleting(self):
        p = sample_project()
        recalculate(p)
        p.narrative["analysis_route"] = "news_only"
        evidence = json.loads(packet_content(p)[1])
        for key in ("facts", "ratios", "sources", "policy_evaluation"):
            self.assertEqual(evidence[key], [])
        self.assertTrue(p.facts)

    def test_offline_matches_public_rubric_without_network(self):
        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            result = calculate_internal(internal_payload())
        p = sample_project()
        recalculate(p)
        self.assertEqual([r["score"] for r in result["policy_evaluation"]],
                         [r["score"] for r in p.narrative["policy_evaluation"]])

    def test_requires_review_and_evidence(self):
        payload = internal_payload()
        payload["human_review"]["confirmed"] = False
        with self.assertRaises(ValueError):
            calculate_internal(payload)
        payload = internal_payload()
        payload["facts"][0]["quote"] = ""
        with self.assertRaises(ValueError):
            calculate_internal(payload)

    def test_missing_fx_holds_score_but_keeps_ratios(self):
        payload = internal_payload()
        for fact in payload["facts"]:
            fact["currency"] = "EUR"
        payload["human_review"]["input_digest"] = approval_digest(payload)
        result = calculate_internal(payload)
        self.assertTrue(all(r["score"] is None for r in result["policy_evaluation"]))
        self.assertTrue(any(r["value"] is not None for r in result["ratios"]))

    def test_balance_error_blocks_scores(self):
        payload = internal_payload()
        for f in payload["facts"]:
            if f["standard_item"] == "total_liabilities":
                f["original_value"] *= 4
        payload["human_review"]["input_digest"] = approval_digest(payload)
        result = calculate_internal(payload)
        self.assertTrue(all(r["score"] is None for r in result["policy_evaluation"]))

    def test_nonfinite_and_mismatched_fx_rejected(self):
        payload = internal_payload()
        payload["facts"][0]["original_value"] = float("nan")
        with self.assertRaises(ValueError):
            calculate_internal(payload)
        payload = internal_payload()
        payload["fx_rates"] = [{"fiscal_year": 2024, "currency": "EUR"}]
        with self.assertRaises(ValueError):
            calculate_internal(payload)

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from partner_finance.benchmark import compare_runs
from partner_finance.handoff import build_claude_start, build_handoff, packet_content
from partner_finance.report_workpaper import build_report_workpaper
from partner_finance.workflow import recalculate
from tests.helpers import sample_project
from tests.test_public_internal import internal_payload


def construction_project():
    p = sample_project()
    p.entity.legal_name = "Synthetic Infrastructure Co."
    for fact in p.facts:
        fact.period_start = f"{fact.fiscal_year}-01-01"
        fact.period_end = f"{fact.fiscal_year}-12-31"
    p.narrative["research_briefs"] = [{"id": "synthetic-construction", "status": "검토 대기", "collected_at": "2026-10-01",
        "sections": [{"text": "합성 시나리오: 해당 법인은 건설 컨소시엄의 시공 참여자이며 단독 EPC 실적은 미확인.",
                      "citations": [{"title": "Synthetic evidence only", "url": "https://example.com/synthetic-construction"}]}]},
        {"id": "excluded", "status": "제외", "collected_at": "2026-10-01",
         "sections": [{"text": "EXCLUDED_CLAIM", "citations": []}]}]
    recalculate(p)
    return p


class ReportFlowTests(unittest.TestCase):
    def test_public_single_file_to_report_workpaper_without_network_or_recalculation(self):
        p = construction_project()
        p.narrative["collection_warnings"] = ["Selected pages only; not full original"]
        with patch("urllib.request.urlopen", side_effect=AssertionError("No network")), patch(
                "partner_finance.analysis.calculate_ratios", side_effect=AssertionError("Do not recalculate")):
            text = build_claude_start(p).decode()
            archive = build_handoff(p)
        for title in ("1. 기업개요", "2. 재무 검토", "3. 공개 현안 및 사업역량", "4. 종합 검토", "5. 추가 확인사항"):
            self.assertIn(title, text)
        self.assertIn("1,200,000,000.00 KRW", text)
        self.assertIn("synthetic-construction", text)
        self.assertIn("Selected pages only", text)
        self.assertNotIn("EXCLUDED_CLAIM", text)
        self.assertIn("미승인", text)
        self.assertNotIn("|\n\n|", text)
        self.assertLess(len(text), 30000)
        with ZipFile(BytesIO(archive)) as z:
            self.assertIn("00_claude_start.md", z.namelist())
            self.assertIn("EXCLUDED_CLAIM", z.read("02_evidence.json").decode())

    def test_error_is_not_promoted_to_financial_conclusion(self):
        p = construction_project()
        next(f for f in p.facts if f.standard_item == "total_liabilities").normalized_value *= 4
        recalculate(p)
        text = build_claude_start(p).decode()
        self.assertIn("오류·상충으로 보류", text)
        self.assertIn("재무 오류가 있어", text)
        self.assertIn("synthetic-construction", text)

    def test_news_only_does_not_leak_old_financials(self):
        p = construction_project()
        p.narrative["analysis_route"] = "news_only"
        text = build_claude_start(p).decode()
        self.assertNotIn("1,200,000,000.00", text)
        self.assertIn("공개 재무평가 없음", text)

    def test_packaged_private_path_and_entity_mismatch(self):
        p = construction_project()
        p.narrative["analysis_route"] = "news_only"
        raw = packet_content(p)[1]
        root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            with ZipFile(root / "deliverables/partner-review-skill.zip") as z:
                z.extractall(tmp)
            packet, source, report = tmp / "packet.json", tmp / "input.json", tmp / "report.md"
            packet.write_bytes(raw)
            source.write_text(json.dumps(internal_payload()), encoding="utf-8")
            command = [sys.executable, "-S", str(tmp / "partner-financial-review/scripts/prepare_report.py"),
                       str(packet), str(report), "--internal-input", str(source)]
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("사내 전용", report.read_text(encoding="utf-8"))
            self.assertTrue(report.with_suffix(".internal-result.json").exists())
            changed = json.loads(raw)
            changed["entity"]["legal_name"] = "Different company"
            packet.write_text(json.dumps(changed), encoding="utf-8")
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("Entity mismatch", run.stderr)
            packet.write_bytes(raw)
            payload = internal_payload()
            payload["human_review"]["confirmed"] = False
            source.write_text(json.dumps(payload), encoding="utf-8")
            run = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("Human confirmation", run.stderr)

    def test_measurement_is_quality_gated_and_scope_matched(self):
        rows = [{"case_id": "synthetic", "input_set_id": "v1", "output_scope": "five-section-report",
                 "method": method, "active_minutes": minutes, "elapsed_minutes": minutes + 10,
                 "quality_passed": True} for method, minutes in (("manual", 100), ("claude_only", 80), ("web_skill", 60))]
        result = compare_runs(rows)
        self.assertEqual(result["comparisons"][1]["metrics"]["active_minutes"]["reduction_percent"], 25)
        rows[-1]["quality_passed"] = False
        result = compare_runs(rows)
        self.assertIsNone(result["comparisons"][1]["metrics"]["active_minutes"]["reduction_percent"])
        rows[-1]["input_set_id"] = "different"
        self.assertEqual(compare_runs(rows)["comparisons"][1]["matched_cases"], 0)
        self.assertFalse(compare_runs([])["comparisons"][0]["eligible_for_savings_claim"])

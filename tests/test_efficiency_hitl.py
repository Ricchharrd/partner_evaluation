import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

from partner_finance.ai import extract_facts_from_text, ClaudeProvider
from partner_finance.document_selection import select_financial_text
from partner_finance.hitl import current_review, review_digest
from partner_finance.offline_review import calculate_internal
from partner_finance.openai_provider import OpenAIProvider
from partner_finance.schema import SourceDocument
from partner_finance.workflow import recalculate
from tests.helpers import sample_project
from tests.test_public_internal import internal_payload


class Stub:
    name, model = "stub", "test-model"

    def __init__(self, row):
        self.row, self.calls = row, 0

    def generate_json(self, *args, **kwargs):
        self.calls += 1
        return {"facts": [self.row.copy()], "warnings": []}, {"model": self.model, "usage": {"input_tokens": 100}}


def row():
    return {"original_value": 100, "unit_multiplier": 1, "fiscal_year": 2025,
            "standard_item": "operating_income", "original_label": "Operating profit",
            "currency": "EUR", "reporting_scope": "연결", "source_locator": "PDF PAGE 10",
            "evidence_quote": "Operating profit 100"}


class EfficiencyHITLTests(unittest.TestCase):
    def test_cache_reuse_and_context_changes(self):
        source, cache, stub = SourceDocument("report", "pdf"), {}, Stub(row())
        text = "[PAGE 10]\nOperating profit 100"
        def run(**kw):
            return extract_facts_from_text(text, "entity", source, stub, cache=cache, **kw)
        first = run()
        second = run()
        self.assertEqual(stub.calls, 1)
        self.assertTrue(second[2]["cache_hit"])
        self.assertEqual(second[2]["usage"], {})
        self.assertEqual(first[0][0].normalized_value, second[0][0].normalized_value)
        run(force_refresh=True)
        stub.model = "other-model"
        run()
        run(default_currency="USD")
        self.assertEqual(stub.calls, 4)

    def test_empty_results_not_cached(self):
        cache, stub = {}, Stub({**row(), "evidence_quote": "not in source"})
        for _ in range(2):
            extract_facts_from_text("[PAGE 10]\nOperating profit 100", "entity", SourceDocument("report", "pdf"), stub, cache=cache)
        self.assertEqual(cache, {})

    def test_scope_adjusted_and_wrong_page_rejected(self):
        for change in ({"reporting_scope": "별도"}, {"original_label": "Adjusted operating profit"}, {"source_locator": "PAGE 11"}):
            facts, _, _ = extract_facts_from_text("[PAGE 10]\nOperating profit 100", "entity", SourceDocument("report", "pdf"), Stub({**row(), **change}))
            self.assertFalse(facts)

    def test_statement_continuation_before_apm(self):
        text = "[PAGE 1]\nAdjusted reclassified statement of profit or loss " + "100 " * 80
        text += "[PAGE 10]\nConsolidated statement of cash flows " + "10 " * 40
        text += "[PAGE 11]\nInvesting activities " + "20 " * 40
        text += "[PAGE 12]\nFinancing activities " + "30 " * 40
        selected, _ = select_financial_text(text, 800)
        self.assertIn("[PAGE 11]", selected)
        self.assertIn("[PAGE 12]", selected)
        self.assertNotIn("[PAGE 1]", selected)

    def test_cash_adjustment_rows_do_not_hide_primary_statement_cluster(self):
        text = "[PAGE 281]\nConsolidated statement of financial position " + "100 " * 20
        text += "[PAGE 282]\nConsolidated statement of profit or loss " + "200 " * 20
        text += "[PAGE 283]\nConsolidated statement of cash flows Profit adjusted by: " + "300 " * 20
        text += "[PAGE 284]\nInvesting activities " + "400 " * 20
        text += "[PAGE 508]\nOther company consolidated statement of financial position " + "500 " * 20
        selected, _ = select_financial_text(text)
        for page in (281, 282, 283, 284):
            self.assertIn(f"[PAGE {page}]", selected)

    def test_limit_blocks_network_even_with_approval(self):
        with patch("urllib.request.urlopen") as net:
            with self.assertRaises(ValueError):
                OpenAIProvider("test", approval=lambda b: None).request({"input": "x" * 240001})
            net.assert_not_called()

    def test_legacy_provider_cannot_bypass_human_approval(self):
        with patch("urllib.request.urlopen") as net:
            with self.assertRaises(ValueError):
                ClaudeProvider("test").generate_json("public data", {})
            net.assert_not_called()

    def test_route_or_calculation_changes_invalidate_review(self):
        p = sample_project()
        recalculate(p)
        p.narrative["hitl_review"] = {"digest": review_digest(p)}
        p.narrative["analysis_route"] = "news_only"
        self.assertFalse(current_review(p))
        p.narrative["hitl_review"] = {"digest": review_digest(p)}
        p.ratios[0].value = 123
        self.assertFalse(current_review(p))

    def test_internal_approval_does_not_survive_input_changes(self):
        p = internal_payload()
        calculate_internal(p)
        p["facts"][0]["original_value"] += 1
        with self.assertRaisesRegex(ValueError, "stale"):
            calculate_internal(p)

    def test_local_evidence_subset_preserves_warnings(self):
        path = Path(__file__).resolve().parents[1] / "deliverables/partner-financial-review/scripts/select_evidence.py"
        spec = importlib.util.spec_from_file_location("select_evidence", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        packet = {"schema": "partner-review-packet/1.0", "facts": [
            {"standard_item": "revenue", "fiscal_year": 2025, "source_id": "s"},
            {"standard_item": "cash", "fiscal_year": 2025}], "sources": [{"id": "s"}],
            "validations": [{"fiscal_year": None, "message": "global warning"}]}
        with patch("socket.socket", side_effect=AssertionError("No network")):
            selected = module.select_evidence(packet, 2025, ["revenue"])
        self.assertEqual(len(selected["facts"]), 1)
        self.assertTrue(selected["validations"])
        with self.assertRaises(ValueError):
            module.select_evidence(packet)

import base64
import gzip
from io import BytesIO
import json
import unittest
from zipfile import ZipFile

from partner_finance.handoff import build_handoff, packet_content
from partner_finance.schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument
from partner_finance.workflow import recalculate


class HandoffBundleTests(unittest.TestCase):
    def test_direct_raw_packet_has_facts_and_sources_but_no_private_rubric(self):
        entity = EntityProfile("Synthetic Infrastructure Co.")
        project = AnalysisProject("Synthetic review", entity)
        source = SourceDocument("annual.pdf", "공개 PDF", url="https://example.com/annual.pdf")
        project.sources.append(source)
        project.facts.append(FinancialFact(
            entity.entity_id, 2025, "revenue", "Revenue", 100, 100_000_000, "EUR",
            unit_multiplier=1_000_000, period_start="2025-01-01", period_end="2025-12-31",
            source_id=source.source_id, source_locator="PAGE 12",
        ))
        recalculate(project)
        _, evidence = packet_content(project)
        packet = json.loads(evidence)
        self.assertEqual(packet["analysis_route"], "public_financials")
        self.assertEqual(packet["facts"][0]["source_locator"], "PAGE 12")
        self.assertEqual(packet["sources"][0]["url"], source.url)
        self.assertNotIn("policy_evaluation", packet)
        self.assertNotIn("rating_policy", evidence.decode("utf-8"))

    def test_one_file_and_zip_contain_identical_public_evidence(self):
        project = AnalysisProject("Synthetic review", EntityProfile("Synthetic Infrastructure Co."))
        project.narrative["analysis_route"] = "news_only"
        with ZipFile(BytesIO(build_handoff(project))) as bundle:
            start = bundle.read("00_claude_start.md").decode("utf-8")
            evidence = bundle.read("02_evidence.json")
        marker = "<!-- partner-evidence-gzip-base64:v1 -->"
        end = "<!-- /partner-evidence-gzip-base64:v1 -->"
        encoded = start.split(marker, 1)[1].split(end, 1)[0].strip()
        self.assertEqual(gzip.decompress(base64.b64decode(encoded)), evidence)
        packet = json.loads(evidence)
        self.assertEqual(packet["analysis_route"], "news_only")
        self.assertNotIn("policy_evaluation", packet)


if __name__ == "__main__":
    unittest.main()

import base64
import gzip
from io import BytesIO
import json
import unittest
from zipfile import ZipFile

from partner_finance.handoff import build_handoff
from partner_finance.schema import AnalysisProject, EntityProfile


class HandoffBundleTests(unittest.TestCase):
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

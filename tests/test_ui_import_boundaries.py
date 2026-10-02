"""The dashboard must remain available if optional Claude packaging is unavailable."""

from pathlib import Path
import os
import subprocess
import sys
import unittest


class UIImportBoundariesTest(unittest.TestCase):
    def test_optional_handoff_does_not_block_ui_import(self):
        script = """
import sys
import types

sys.modules["partner_finance.handoff"] = types.ModuleType("partner_finance.handoff")
from partner_finance.market_ui import render_market
from partner_finance.simple_ui import render
assert callable(render_market)
assert callable(render)
"""
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

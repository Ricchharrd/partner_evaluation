import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from partner_finance import hitl, simple_ui, runtime


class RuntimeTests(unittest.TestCase):
    def test_current_release_can_hash_every_deployed_module(self):
        self.assertEqual(len(runtime.module_release()), 16)

    def test_module_release_changes_with_source_content(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "simple_ui.py"
            source.write_text("FINANCE_UI_VERSION = 9\n", encoding="utf-8")
            before = runtime.module_release(Path(directory), ("simple_ui",))
            source.write_text("FINANCE_UI_VERSION = 10\n", encoding="utf-8")
            self.assertNotEqual(before, runtime.module_release(Path(directory), ("simple_ui",)))

    def test_dependencies_refresh_before_view_and_only_once_per_release(self):
        previous = runtime._loaded_release
        try:
            with patch.object(runtime.importlib, "reload") as reload_module:
                runtime.refresh_finance_modules("test-release")
                names = [call.args[0].__name__ for call in reload_module.call_args_list]
                self.assertLess(names.index("partner_finance.hitl"), names.index("partner_finance.simple_ui"))
                self.assertLess(runtime.HOT_MODULES.index("primary_statements"),
                                runtime.HOT_MODULES.index("simple_ui"))
                count = reload_module.call_count
                runtime.refresh_finance_modules("test-release")
                self.assertEqual(reload_module.call_count, count)
        finally:
            runtime._loaded_release = previous

import sys
import unittest
from unittest.mock import patch

from partner_finance import hitl, simple_ui, runtime


class RuntimeTests(unittest.TestCase):
    def test_dependencies_refresh_before_view_and_only_once_per_release(self):
        previous = runtime._loaded_release
        try:
            with patch.object(runtime.importlib, "reload") as reload_module:
                runtime.refresh_finance_modules("test-release")
                names = [call.args[0].__name__ for call in reload_module.call_args_list]
                self.assertLess(names.index("partner_finance.hitl"), names.index("partner_finance.simple_ui"))
                count = reload_module.call_count
                runtime.refresh_finance_modules("test-release")
                self.assertEqual(reload_module.call_count, count)
        finally:
            runtime._loaded_release = previous

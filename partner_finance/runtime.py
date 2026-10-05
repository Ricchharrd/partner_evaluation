"""Keep hot-deployed Streamlit views and their imported helpers on one release."""
import importlib
import sys
from threading import RLock

_lock = RLock()
_loaded_release = None


def refresh_finance_modules(release):
    global _loaded_release
    with _lock:
        if _loaded_release == release:
            return
        importlib.invalidate_caches()
        # Dependencies first: views bind helper functions with from-imports.
        for name in ("periods", "document_selection", "hitl", "openai_provider", "ai",
                     "ingest", "public_documents", "validation", "analysis", "workflow", "workpaper",
                     "reports", "handoff", "research", "market_news", "dashboard",
                     "simple_ui", "market_ui"):
            module = sys.modules.get("partner_finance." + name)
            if module is not None:
                importlib.reload(module)
        _loaded_release = release

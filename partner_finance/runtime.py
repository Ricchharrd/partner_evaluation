"""Keep hot-deployed Streamlit views and their imported helpers on one release."""
from hashlib import sha256
import importlib
from pathlib import Path
import sys
from threading import RLock

RUNTIME_VERSION = 2
HOT_MODULES = (
    "periods", "document_selection", "hitl", "openai_provider",
    "primary_statements", "ai", "ingest", "finance_batch", "primary_repair",
    "public_documents", "validation", "analysis", "workflow", "report_workpaper",
    "reports", "handoff", "research", "market_news", "dashboard",
    "simple_ui", "market_ui",
)
_lock = RLock()
_loaded_release = None


def module_release(root: Path | None = None, modules: tuple[str, ...] = HOT_MODULES) -> str:
    root = root or Path(__file__).resolve().parent
    digest = sha256()
    for name in modules:
        digest.update(name.encode("ascii"))
        digest.update((root / f"{name}.py").read_bytes())
    return digest.hexdigest()[:16]


def refresh_finance_modules(release):
    global _loaded_release
    with _lock:
        if _loaded_release == release:
            return
        importlib.invalidate_caches()
        # Dependencies first: views bind helper functions with from-imports.
        for name in HOT_MODULES:
            module = sys.modules.get("partner_finance." + name)
            if module is not None:
                importlib.reload(module)
        _loaded_release = release

"""Package only reviewed source; never include project data or secrets."""
from pathlib import Path
from shutil import copy2
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent
SKILL = ROOT / "deliverables" / "partner-financial-review"
MODULES = ("schema", "analysis", "validation", "account_guards", "policy", "offline_review", "report_workpaper")


def main():
    target = SKILL / "scripts" / "partner_finance"
    target.mkdir(parents=True, exist_ok=True)
    for name in MODULES:
        copy2(ROOT / "partner_finance" / (name + ".py"), target / (name + ".py"))
    with ZipFile(ROOT / "deliverables" / "partner-review-skill.zip", "w", ZIP_DEFLATED) as z:
        paths = [SKILL / "SKILL.md", SKILL / "references" / "internal-calculation.md", SKILL / "references" / "report-format.md",
                 SKILL / "scripts" / "calculate.py", SKILL / "scripts" / "select_evidence.py", SKILL / "scripts" / "prepare_report.py"]
        paths += [target / (name + ".py") for name in MODULES]
        for p in paths:
            z.write(p, p.relative_to(SKILL.parent).as_posix())
    with ZipFile(ROOT / "deliverables" / "public-internal-streamlit-deploy.zip", "w", ZIP_DEFLATED) as z:
        paths = list((ROOT / "partner_finance").rglob("*.py"))
        paths += [ROOT / p for p in ("streamlit_app.py", "requirements.txt", ".gitignore", ".streamlit/config.toml", "PUBLIC_INTERNAL_GUIDE.md")]
        paths.append(ROOT / "deliverables" / "partner-review-skill.zip")
        for p in paths:
            z.write(p, p.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()

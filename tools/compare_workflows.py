"""Local-only measurement comparison. Example: python tools/compare_workflows.py runs.json"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from partner_finance.benchmark import compare_runs

parser = argparse.ArgumentParser()
parser.add_argument("measurements", type=Path)
args = parser.parse_args()
print(json.dumps(compare_runs(json.loads(args.measurements.read_text(encoding="utf-8-sig"))),
                 ensure_ascii=False, indent=2, allow_nan=False))

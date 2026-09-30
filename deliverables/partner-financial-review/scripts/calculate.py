"""Run only in the corporate Claude code-execution environment."""
import argparse
import json
from pathlib import Path
from partner_finance.offline_review import calculate_internal, approval_digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--digest", action="store_true", help="Print input fingerprint only; does not approve or calculate")
    args = parser.parse_args()
    if args.output and args.input.resolve() == args.output.resolve():
        raise ValueError("Output must not overwrite input")
    payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if args.digest:
        print(approval_digest(payload))
        return
    if not args.output:
        parser.error("output is required unless --digest is used")
    result = calculate_internal(payload)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main()

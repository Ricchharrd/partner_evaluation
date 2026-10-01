"""Prepare one report workpaper locally; reuse public values or calculate private inputs once."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from partner_finance.report_workpaper import build_report_workpaper
from partner_finance.offline_review import calculate_internal


def prepare(packet, internal_input=None):
    if packet.get("schema") != "partner-review-packet/1.0":
        raise ValueError("Unsupported packet schema")
    result = deepcopy(packet)
    internal_result = None
    if internal_input is not None:
        public_name = packet.get("entity", {}).get("legal_name", "").strip().casefold()
        private_name = internal_input.get("entity", {}).get("legal_name", "").strip().casefold()
        if not public_name or public_name != private_name:
            raise ValueError("Entity mismatch: ask the user; never combine different legal entities")
        if packet.get("facts"):
            raise ValueError("Public financials already exist; do not silently overwrite them with private figures")
        internal_result = calculate_internal(internal_input)
        for key in ("entity", "facts", "ratios", "validations", "policy_evaluation"):
            result[key] = internal_result[key]
        result.update(analysis_route="internal_financials", data_boundary="corporate Claude only",
                      hitl={"review_current": False, "review": {}, "internal_extraction_review": internal_result["human_review"]})
        result["sources"] = [{"id": sid, "name": "사내 첨부 재무제표", "url": ""}
                             for sid in sorted({f["source_id"] for f in internal_result["facts"]})]
    text = build_report_workpaper(result)
    if internal_result:
        text = "사내 전용: 비공개 재무자료 포함. 외부 웹·개인 API에 업로드 금지.\n\n" + text
    return text, internal_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--internal-input", type=Path)
    args = parser.parse_args()
    packet = json.loads(args.packet.read_text(encoding="utf-8-sig"))
    internal = json.loads(args.internal_input.read_text(encoding="utf-8-sig")) if args.internal_input else None
    text, calculation = prepare(packet, internal)
    calculation_path = args.output.with_suffix(".internal-result.json")
    inputs = [p.resolve() for p in (args.packet, args.internal_input) if p is not None]
    if args.output.resolve() in inputs or (calculation and calculation_path.resolve() in inputs):
        raise ValueError("Output must not overwrite an input")
    args.output.write_text(text, encoding="utf-8")
    if calculation:
        calculation_path.write_text(json.dumps(calculation, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
    print("Prepared report workpaper locally. Interpretation and final human approval remain outstanding.")


if __name__ == "__main__":
    main()

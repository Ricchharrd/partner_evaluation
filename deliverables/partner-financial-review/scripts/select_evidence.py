"""Read only requested evidence from a local public packet. No network."""
import argparse
import json
from pathlib import Path


def select_evidence(packet, year=None, items=(), fact_ids=(), limit=12):
    if packet.get("schema") != "partner-review-packet/1.0":
        raise ValueError("Unsupported evidence schema")
    if not items and not fact_ids:
        raise ValueError("Select explicit item keys or fact IDs; do not dump the whole packet")
    if not 1 <= limit <= 30:
        raise ValueError("Limit must be 1..30")
    rows = [f for f in packet.get("facts", []) if (year is None or f.get("fiscal_year") == year)
            and (f.get("standard_item") in items or f.get("fact_id") in fact_ids)]
    selected = rows[:limit]
    sources = {f.get("source_id") for f in selected}
    return {"entity": packet.get("entity"), "hitl": packet.get("hitl"),
            "matched": len(rows), "truncated": len(rows) > limit, "facts": selected,
            "sources": [s for s in packet.get("sources", []) if s.get("id") in sources],
            "validations": [v for v in packet.get("validations", []) if year is None or v.get("fiscal_year") in (None, year)],
            "notice": "Subset only. Omitted facts are not missing or zero. Global/year warnings remain visible."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("packet", type=Path)
    parser.add_argument("--year", type=int)
    parser.add_argument("--item", action="append", default=[])
    parser.add_argument("--fact-id", action="append", default=[])
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()
    data = json.loads(args.packet.read_text(encoding="utf-8-sig"))
    print(json.dumps(select_evidence(data, args.year, args.item, args.fact_id, args.limit), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

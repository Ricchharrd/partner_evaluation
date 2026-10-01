"""Compare supplied same-scope task measurements; never infer savings from test counts."""
from math import isfinite

METHODS = ("manual", "claude_only", "web_skill")


def compare_runs(records):
    indexed = {}
    for row in records:
        key = tuple(row.get(k, "").strip() for k in ("case_id", "input_set_id", "output_scope"))
        method = row.get("method")
        if not all(key) or method not in METHODS:
            raise ValueError("Each record needs case, same input set, output scope and a supported method")
        identity = key + (method,)
        if identity in indexed:
            raise ValueError("Duplicate case/method: use a separate case ID for each repeated trial")
        for metric in ("active_minutes", "elapsed_minutes"):
            value = row.get(metric)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError("Measured minutes must be positive finite numbers")
        if row["elapsed_minutes"] < row["active_minutes"]:
            raise ValueError("Single-reviewer elapsed time cannot be below active work time")
        if type(row.get("quality_passed")) is not bool:
            raise ValueError("Record an explicit quality review result")
        indexed[identity] = row
    comparisons = []
    for baseline in ("manual", "claude_only"):
        cases = sorted({k[:3] for k in indexed if k[3] == baseline and k[:3] + ("web_skill",) in indexed})
        pairs = [(indexed[k + (baseline,)], indexed[k + ("web_skill",)]) for k in cases]
        failed = sum(not (a["quality_passed"] and b["quality_passed"]) for a, b in pairs)
        item = {"baseline": baseline, "matched_cases": len(pairs), "quality_failed_pairs": failed,
                "eligible_for_savings_claim": bool(pairs) and not failed, "metrics": {}}
        if pairs:
            for metric in ("active_minutes", "elapsed_minutes"):
                before = sum(a[metric] for a, _ in pairs)
                after = sum(b[metric] for _, b in pairs)
                item["metrics"][metric] = {"before": before, "after": after,
                    "reduction_percent": round((before - after) / before * 100, 2) if not failed else None}
        comparisons.append(item)
    return {"basis": "User-supplied measurements; not independently verified",
            "records": len(records), "comparisons": comparisons,
            "notice": "Same case/input/output scope only. Include transfer, original checks, corrections and report review. Active time is not end-to-end lead time. Do not generalize a single trial."}

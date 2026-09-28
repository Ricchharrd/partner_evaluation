"""Deterministic internal calculator. No network, API, or external storage."""
from dataclasses import asdict
from math import isfinite

from .schema import AnalysisProject, EntityProfile, FinancialFact, STANDARD_ITEMS
from .analysis import calculate_ratios
from .validation import validate_facts
from .policy import evaluate_company_policy
from .account_guards import normalize_scope


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{label}: finite JSON number required")
    return value


def calculate_internal(payload):
    if payload.get("schema") != "internal-financial-input/1.0":
        raise ValueError("Unsupported input schema")
    review = payload.get("human_review", {})
    if review.get("confirmed") is not True or not review.get("reviewer", "").strip() or not review.get("note", "").strip():
        raise ValueError("Human confirmation of extracted facts is required before calculation")
    entity = EntityProfile(**payload["entity"])
    if not entity.legal_name.strip():
        raise ValueError("Legal entity required")
    project = AnalysisProject("Internal financial review", entity)
    if not payload.get("facts"):
        raise ValueError("No financial facts; news alone cannot produce a financial score")
    for row in payload["facts"]:
        if row.get("standard_item") not in STANDARD_ITEMS:
            raise ValueError("Unknown financial item")
        year = row["fiscal_year"]
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2200:
            raise ValueError("Invalid fiscal year")
        for key in ("source_id", "source_locator", "original_label", "quote", "currency", "period_end", "reporting_scope"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise ValueError(f"Missing evidence/context: {key}")
        value = row["original_value"]
        multiplier = number(row.get("unit_multiplier", 1), "unit_multiplier")
        if multiplier <= 0:
            raise ValueError("Positive unit multiplier required")
        normalized = None if value is None else number(number(value, "original_value") * multiplier, "normalized_value")
        project.facts.append(FinancialFact(
            entity_id=entity.entity_id, fiscal_year=year, standard_item=row["standard_item"],
            original_label=row["original_label"], original_value=value, normalized_value=normalized,
            currency=row["currency"].upper(), unit_multiplier=multiplier,
            period_start=row.get("period_start", ""), period_end=row["period_end"],
            reporting_scope=normalize_scope(row["reporting_scope"]),
            accounting_standard=row.get("accounting_standard", entity.accounting_standard),
            source_id=row["source_id"], source_locator=row["source_locator"] + " | " + row["quote"],
            extraction_method="Claude internal extraction; human checked",
        ))
    rates = {}
    for row in payload.get("fx_rates", []):
        year = row["fiscal_year"]
        facts = [f for f in project.facts if f.fiscal_year == year]
        if not facts or {f.currency for f in facts} != {row.get("currency")}:
            raise ValueError("FX currency/year does not match financial facts")
        if year in rates:
            raise ValueError("Duplicate FX year")
        if not row.get("source") or not row.get("period_start") or not row.get("period_end"):
            raise ValueError("FX source and actual fiscal period required")
        if any(f.period_end != row["period_end"] or (f.period_start and f.period_start != row["period_start"]) for f in facts):
            raise ValueError("FX period does not match fiscal period")
        rate = number(row["average_krw_per_unit"], "FX rate")
        if rate <= 0:
            raise ValueError("Positive FX rate required")
        rates[year] = rate
    project.validations = validate_facts(project.facts)
    project.ratios = calculate_ratios(project.facts)
    error_years = {v.fiscal_year for v in project.validations if v.severity == "오류"}
    for ratio in project.ratios:
        if None in error_years or ratio.fiscal_year in error_years:
            ratio.value, ratio.status = None, "검증 오류로 보류"
    evaluations = evaluate_company_policy(project, rates)
    for row in evaluations:
        if None in error_years or row.get("fiscal_year") in error_years:
            row.update(score=None, grade=None, status="보류", reason="재무 검증 오류", components=[])
        row["provisional"] = True
    return {"schema": "internal-financial-result/1.0", "boundary": "corporate Claude only; never upload to Streamlit",
            "entity": asdict(entity), "human_review": review, "facts": [asdict(f) for f in project.facts],
            "validations": [asdict(v) for v in project.validations], "ratios": [asdict(r) for r in project.ratios],
            "policy_evaluation": evaluations, "fx_rates": payload.get("fx_rates", []),
            "limitations": ["Original evidence must be checked by a person; quote presence is not verification.",
                "Only revenue/operating income thresholds use supplied fiscal-period average FX. Ratios use original currency. Not full K-IFRS translation.",
                "Altman uses book equity/operating income proxies, not original market equity/EBIT. Missing adjustment inputs remain unresolved.",
                "Modified rubric matches the bundled application policy; final internal approval is separate."]}

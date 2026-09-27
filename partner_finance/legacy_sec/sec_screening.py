from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import html
import re

from .sec_edgar_client import CompanyMatch, fetch_company_facts, fetch_filing_document_html


ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
GRADES = ["AAA", "AA", "A", "BB", "B", "CC", "C", "D"]
GRADE_POINTS = {
    "AAA": 100,
    "AA": 95,
    "A": 90,
    "BB": 80,
    "B": 70,
    "CC": 60,
    "C": 50,
    "D": 40,
}
RATING_WEIGHTS = {
    "revenue": 15.0,
    "operating_income": 10.0,
    "interest_coverage": 7.5,
    "financial_debt_to_operating_income": 7.5,
    "operating_cf_to_financial_debt": 15.0,
    "debt_ratio": 15.0,
    "receivable_turnover_days": 15.0,
    "current_ratio": 15.0,
}

CONCEPTS = {
    "revenue": [
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomerIncludingAssessedTax"),
        ("us-gaap", "RevenueFromContractWithCustomer"),
    ],
    "net_income": [
        ("us-gaap", "NetIncomeLoss"),
        ("us-gaap", "ProfitLoss"),
    ],
    "operating_income": [
        ("us-gaap", "OperatingIncomeLoss"),
    ],
    "interest_expense": [
        ("us-gaap", "InterestExpenseNonOperating"),
        ("us-gaap", "InterestExpense"),
        ("us-gaap", "InterestExpenseDebt"),
        ("us-gaap", "InterestAndDebtExpense"),
        ("us-gaap", "FinanceLeaseInterestExpense"),
        ("us-gaap", "InterestExpenseBorrowings"),
    ],
    "total_assets": [
        ("us-gaap", "Assets"),
    ],
    "total_liabilities": [
        ("us-gaap", "Liabilities"),
    ],
    "total_equity": [
        ("us-gaap", "StockholdersEquity"),
        ("us-gaap", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    ],
    "current_assets": [
        ("us-gaap", "AssetsCurrent"),
    ],
    "current_liabilities": [
        ("us-gaap", "LiabilitiesCurrent"),
    ],
    "cash": [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    ],
    "accounts_receivable": [
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "AccountsReceivableNet"),
        ("us-gaap", "ReceivablesNetCurrent"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "retained_earnings": [
        ("us-gaap", "RetainedEarningsAccumulatedDeficit"),
        ("us-gaap", "AccumulatedDeficit"),
    ],
}

FINANCIAL_DEBT_COMPONENTS = {
    "short_term_borrowings": [
        ("us-gaap", "ShortTermBorrowings"),
        ("us-gaap", "ShortTermDebt"),
        ("us-gaap", "CommercialPaper"),
    ],
    "current_debt": [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "CurrentPortionOfLongTermDebt"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsCurrent"),
    ],
    "noncurrent_debt": [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"),
    ],
}
FINANCIAL_DEBT_FALLBACKS = [
    ("us-gaap", "LongTermDebtAndFinanceLeaseObligations"),
    ("us-gaap", "LongTermDebt"),
]


@dataclass
class ScreeningResult:
    company_name: str
    ticker: str
    cik: str
    sic: str | None
    sic_description: str | None
    fiscal_year: int
    fiscal_period: str
    form: str
    filed: str
    period_start: str | None
    period_end: str | None
    metrics: dict
    notes: dict
    red_flags: list[str]
    rating: dict = field(default_factory=dict)


@dataclass
class ScreeningError:
    input_query: str
    company_name: str
    ticker: str
    cik: str
    fiscal_year: int | None
    error_message: str


def clean_abs(value):
    if value is None:
        return None
    return abs(value)


def safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _unit_priority(unit_name: str) -> int:
    if unit_name == "USD":
        return 0
    if unit_name == "pure":
        return 1
    return 9


def _parse_sec_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _duration_days(row: dict) -> int | None:
    start = _parse_sec_date(row.get("start"))
    end = _parse_sec_date(row.get("end"))
    if start is None or end is None:
        return None
    return (end - start).days


def _annual_duration_score(row: dict) -> int:
    duration = _duration_days(row)
    if duration is None:
        return -10_000
    return -abs(duration - 365)


def _is_target_annual_row(row: dict, fiscal_year: int) -> bool:
    form = str(row.get("form") or "")
    fy = row.get("fy")
    fp = str(row.get("fp") or "")
    if form not in ANNUAL_FORMS:
        return False
    if not fy or int(fy) != int(fiscal_year):
        return False
    return not fp or fp == "FY"


def sic_as_int(sic: str | None) -> int | None:
    if not sic:
        return None
    digits = "".join(ch for ch in str(sic) if ch.isdigit())
    if not digits:
        return None
    return int(digits)


def is_manufacturing_sic(sic: str | None) -> bool:
    value = sic_as_int(sic)
    return value is not None and 2000 <= value <= 3999


def is_financial_sic(sic: str | None) -> bool:
    value = sic_as_int(sic)
    return value is not None and 6000 <= value <= 6799


def _normalize_fact_text(value: str | None) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(value or ""))
    text = re.sub(r"[^a-zA-Z0-9]+", " ", text)
    return " ".join(text.lower().split())


def _interest_expense_relevance(taxonomy: str, concept_name: str, concept_map: dict) -> int:
    name_label = _normalize_fact_text(f"{taxonomy} {concept_name} {concept_map.get('label') or ''}")
    description = _normalize_fact_text(concept_map.get("description") or "")
    searchable = f"{name_label} {description}"

    if "interest expense excluding financial products" in name_label:
        return 100
    if "interest expense excluding financial" in name_label:
        return 95
    if "interest expense excluding financial products" in description:
        return 80
    if "interest expense of financial products" in searchable:
        return 0
    if "interest expense" not in name_label:
        return 0

    excluded_terms = [
        "interest income",
        "interest earned",
        "interest rate",
        "gain",
        "loss",
        "derivative",
        "notional",
        "fair value",
        "capitalized",
        "tax",
    ]
    if any(term in searchable for term in excluded_terms):
        return 0
    return 50


def annual_years_available(company_facts: dict) -> list[int]:
    years = set()
    for taxonomy_map in (company_facts.get("facts") or {}).values():
        for concept_map in taxonomy_map.values():
            for unit_rows in (concept_map.get("units") or {}).values():
                for row in unit_rows:
                    form = str(row.get("form") or "")
                    fy = row.get("fy")
                    fp = str(row.get("fp") or "")
                    if form in ANNUAL_FORMS and fy and (not fp or fp == "FY"):
                        years.add(int(fy))
    return sorted(years)


def pick_annual_fact(company_facts: dict, taxonomy: str, concept: str, fiscal_year: int) -> dict | None:
    facts = ((company_facts.get("facts") or {}).get(taxonomy) or {}).get(concept) or {}
    units = facts.get("units") or {}
    candidates: list[tuple[tuple, dict]] = []

    for unit_name, rows in units.items():
        for row in rows:
            frame = str(row.get("frame") or "")
            if not _is_target_annual_row(row, fiscal_year):
                continue
            end = str(row.get("end") or "")
            filed = str(row.get("filed") or "")
            score = (end, _annual_duration_score(row), filed, -_unit_priority(unit_name), frame)
            enriched = dict(row)
            enriched["unit"] = unit_name
            enriched["concept"] = f"{taxonomy}:{concept}"
            candidates.append((score, enriched))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def pick_first_annual_fact(company_facts: dict, concept_options: list[tuple[str, str]], fiscal_year: int) -> dict | None:
    for taxonomy, concept in concept_options:
        matched_fact = pick_annual_fact(company_facts, taxonomy, concept, fiscal_year)
        if matched_fact:
            return matched_fact
    return None


def pick_interest_expense_fallback(company_facts: dict, fiscal_year: int) -> dict | None:
    candidates: list[tuple[tuple, dict]] = []
    for taxonomy, concept_map_by_name in (company_facts.get("facts") or {}).items():
        for concept_name, concept_map in concept_map_by_name.items():
            relevance = _interest_expense_relevance(str(taxonomy), str(concept_name), concept_map)
            if relevance <= 0:
                continue
            for unit_name, rows in (concept_map.get("units") or {}).items():
                for row in rows:
                    if not _is_target_annual_row(row, fiscal_year):
                        continue
                    end = str(row.get("end") or "")
                    filed = str(row.get("filed") or "")
                    score = (relevance, end, _annual_duration_score(row), filed, -_unit_priority(unit_name))
                    enriched = dict(row)
                    enriched["unit"] = unit_name
                    enriched["concept"] = f"{taxonomy}:{concept_name}"
                    enriched["custom_label"] = concept_map.get("label")
                    candidates.append((score, enriched))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _parse_amount_millions(value: str) -> float | None:
    cleaned = str(value or "").strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        amount = float(cleaned) * 1_000_000
    except ValueError:
        return None
    return -amount if is_negative else amount


def parse_interest_expense_from_filing_html(html_text: str, fiscal_year: int) -> float | None:
    text = html.unescape(re.sub(r"<[^>]+>", " ", html_text or ""))
    text = re.sub(r"\s+", " ", text)
    sentence_pattern = re.compile(
        rf"interest expense excluding financial products\s+in\s+{int(fiscal_year)}\s+was\s+\$?\s*([()]?[\d,]+(?:\.\d+)?[()]?)\s+million",
        re.IGNORECASE,
    )
    sentence_match = sentence_pattern.search(text)
    if sentence_match:
        return _parse_amount_millions(sentence_match.group(1))

    other_income_pattern = re.compile(
        rf"detail of other income/\(expense\), net for\s+{int(fiscal_year)}.*?interest expense\s+\(?\s*\$?\s*([()]?[\d,]+(?:\.\d+)?[()]?)\s*\)?",
        re.IGNORECASE,
    )
    other_income_match = other_income_pattern.search(text)
    if other_income_match:
        return _parse_amount_millions(other_income_match.group(1))

    return None


def accession_for_year(company_facts: dict, fiscal_year: int) -> str | None:
    for taxonomy, concept in (
        CONCEPTS["revenue"]
        + CONCEPTS["operating_income"]
        + CONCEPTS["net_income"]
        + CONCEPTS["operating_cash_flow"]
    ):
        matched_fact = pick_annual_fact(company_facts, taxonomy, concept, fiscal_year)
        if matched_fact and matched_fact.get("accn"):
            return str(matched_fact.get("accn"))
    return None


def pick_interest_expense_from_filing_html(company_facts: dict, cik: str, fiscal_year: int) -> tuple[float | None, str]:
    accession = accession_for_year(company_facts, fiscal_year)
    if not accession:
        return None, "No annual filing accession found for raw 10-K interest expense fallback."
    try:
        html_text = fetch_filing_document_html(cik, accession)
        value = parse_interest_expense_from_filing_html(html_text, fiscal_year)
    except Exception as exc:
        return None, f"Raw 10-K interest expense fallback failed for accession {accession}: {exc}"
    if value is None:
        return None, f"Raw 10-K fallback did not find a separately disclosed interest expense amount in accession {accession}."
    return value, f"Parsed raw 10-K text fallback for separately disclosed interest expense ({accession})."


def altman_zone(score: float | None, model_key: str | None) -> str | None:
    if score is None or not model_key:
        return None
    if score < 1.0:
        return "Penalty trigger"
    return "No penalty"


def compute_altman_z(metrics: dict, sic: str | None, sic_description: str | None) -> dict:
    total_assets = metrics.get("total_assets")
    total_liabilities = metrics.get("total_liabilities")
    if total_assets in (None, 0) or total_liabilities in (None, 0):
        return {
            "score": None,
            "model": "N/A",
            "model_key": None,
            "zone": None,
            "note": "Policy Altman Z-score unavailable because total assets or total liabilities are missing.",
        }

    working_capital = metrics.get("working_capital")
    retained_earnings = metrics.get("retained_earnings")
    ebit_proxy = metrics.get("operating_income")
    book_equity = metrics.get("total_equity")
    revenue = metrics.get("revenue")
    missing = [
        label
        for label, value in [
            ("working capital", working_capital),
            ("retained earnings", retained_earnings),
            ("operating income / EBIT proxy", ebit_proxy),
            ("book equity", book_equity),
            ("revenue", revenue),
        ]
        if value is None
    ]
    if missing:
        return {
            "score": None,
            "model": "N/A",
            "model_key": None,
            "zone": None,
            "note": "Policy Altman Z-score unavailable because " + ", ".join(missing) + " are missing.",
        }

    x1 = safe_div(working_capital, total_assets)
    x2 = safe_div(retained_earnings, total_assets)
    x3 = safe_div(ebit_proxy, total_assets)
    x4 = safe_div(book_equity, total_liabilities)
    x5 = safe_div(revenue, total_assets)
    if None in (x1, x2, x3, x4, x5):
        return {
            "score": None,
            "model": "N/A",
            "model_key": None,
            "zone": None,
            "note": "Policy Altman Z-score unavailable because one or more ratio inputs could not be calculated.",
        }

    score = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 0.99 * x5
    model = "Policy Altman Z (uniform original formula; book-equity proxy)"
    model_key = "policy_uniform_original"

    return {
        "score": score,
        "model": model,
        "model_key": model_key,
        "zone": altman_zone(score, model_key),
        "note": (
            "Company policy applies the original public-manufacturing Altman formula uniformly. "
            f"SEC SIC shown for reference: {sic or '-'} ({sic_description or '-'}). "
            "X4 uses book equity as a market-equity proxy because SEC companyfacts does not provide market capitalization."
        ),
        "x1_working_capital_to_assets": x1,
        "x2_retained_earnings_to_assets": x2,
        "x3_ebit_proxy_to_assets": x3,
        "x4_book_equity_to_liabilities": x4,
        "x5_sales_to_assets": x5,
    }


def extract_financial_debt(company_facts: dict, fiscal_year: int) -> tuple[float | None, str]:
    total = 0.0
    matched = []
    for component_name, concept_options in FINANCIAL_DEBT_COMPONENTS.items():
        matched_fact = pick_first_annual_fact(company_facts, concept_options, fiscal_year)
        if matched_fact is None:
            continue
        total += matched_fact.get("val") or 0
        matched.append(f"{component_name}={matched_fact.get('concept')}")

    if matched:
        return total, "Summed debt components: " + " | ".join(matched)

    fallback = pick_first_annual_fact(company_facts, FINANCIAL_DEBT_FALLBACKS, fiscal_year)
    if fallback:
        return fallback.get("val"), f"Used debt fallback concept {fallback.get('concept')}."
    return None, f"No standard annual SEC financial debt fact matched for FY{fiscal_year}."


def extract_metrics_for_year(
    company_facts: dict,
    fiscal_year: int,
    cik: str | None = None,
    sic: str | None = None,
    sic_description: str | None = None,
) -> tuple[dict, dict]:
    metrics = {}
    notes = {}
    for metric_key, concept_options in CONCEPTS.items():
        matched_fact = pick_first_annual_fact(company_facts, concept_options, fiscal_year)
        if matched_fact is None:
            metrics[metric_key] = None
            notes[metric_key] = f"No standard annual SEC fact matched for FY{fiscal_year}."
        else:
            value = matched_fact.get("val")
            if metric_key == "retained_earnings" and str(matched_fact.get("concept") or "").endswith(":AccumulatedDeficit") and value is not None and value > 0:
                value = -value
            metrics[metric_key] = value
            notes[metric_key] = f"Matched {matched_fact.get('concept')} ({matched_fact.get('form')}, FY{matched_fact.get('fy')})."

    financial_debt, debt_note = extract_financial_debt(company_facts, fiscal_year)
    metrics["financial_debt"] = financial_debt
    notes["financial_debt"] = debt_note

    if metrics.get("interest_expense") is None:
        fallback = pick_interest_expense_fallback(company_facts, fiscal_year)
        if fallback:
            metrics["interest_expense"] = fallback.get("val")
            label = fallback.get("custom_label") or "-"
            notes["interest_expense"] = (
                f"Matched custom annual interest expense fallback {fallback.get('concept')} "
                f"({label}, {fallback.get('form')}, FY{fallback.get('fy')})."
            )
        elif cik:
            raw_value, raw_note = pick_interest_expense_from_filing_html(company_facts, cik, fiscal_year)
            if raw_value is not None:
                metrics["interest_expense"] = raw_value
                notes["interest_expense"] = raw_note
            else:
                notes["interest_expense"] = f"{notes['interest_expense']} {raw_note}"

    metrics["interest_expense_abs"] = clean_abs(metrics.get("interest_expense"))
    metrics["debt_ratio"] = safe_div(metrics.get("total_liabilities"), metrics.get("total_equity"))
    metrics["current_ratio"] = safe_div(metrics.get("current_assets"), metrics.get("current_liabilities"))
    metrics["operating_margin"] = safe_div(metrics.get("operating_income"), metrics.get("revenue"))
    metrics["net_margin"] = safe_div(metrics.get("net_income"), metrics.get("revenue"))
    metrics["roa"] = safe_div(metrics.get("net_income"), metrics.get("total_assets"))
    metrics["interest_coverage"] = safe_div(metrics.get("operating_income"), metrics.get("interest_expense_abs"))
    metrics["financial_debt_to_operating_income"] = safe_div(metrics.get("financial_debt"), metrics.get("operating_income"))
    metrics["operating_cf_to_financial_debt"] = safe_div(metrics.get("operating_cash_flow"), metrics.get("financial_debt"))
    metrics["working_capital"] = (
        None
        if metrics.get("current_assets") is None or metrics.get("current_liabilities") is None
        else metrics["current_assets"] - metrics["current_liabilities"]
    )
    altman = compute_altman_z(metrics, sic, sic_description)
    metrics["altman_z_score"] = altman.get("score")
    metrics["altman_z_model"] = altman.get("model")
    metrics["altman_z_zone"] = altman.get("zone")
    metrics["altman_x1_working_capital_to_assets"] = altman.get("x1_working_capital_to_assets")
    metrics["altman_x2_retained_earnings_to_assets"] = altman.get("x2_retained_earnings_to_assets")
    metrics["altman_x3_ebit_proxy_to_assets"] = altman.get("x3_ebit_proxy_to_assets")
    metrics["altman_x4_book_equity_to_liabilities"] = altman.get("x4_book_equity_to_liabilities")
    metrics["altman_x5_sales_to_assets"] = altman.get("x5_sales_to_assets")
    notes["altman_z_score"] = altman.get("note")
    return metrics, notes


def infer_filing_meta_for_year(company_facts: dict, fiscal_year: int) -> dict:
    meta_priority = (
        CONCEPTS["revenue"]
        + CONCEPTS["operating_income"]
        + CONCEPTS["net_income"]
        + CONCEPTS["operating_cash_flow"]
        + CONCEPTS["total_assets"]
        + CONCEPTS["total_liabilities"]
    )
    for taxonomy, concept in meta_priority:
        matched_fact = pick_annual_fact(company_facts, taxonomy, concept, fiscal_year)
        if matched_fact:
            return {
                "fiscal_year": int(matched_fact.get("fy") or fiscal_year),
                "fiscal_period": str(matched_fact.get("fp") or "FY"),
                "form": str(matched_fact.get("form") or ""),
                "filed": str(matched_fact.get("filed") or ""),
                "period_start": str(matched_fact.get("start") or "") or None,
                "period_end": str(matched_fact.get("end") or "") or None,
            }

    latest = None
    for taxonomy_name, taxonomy_map in (company_facts.get("facts") or {}).items():
        if taxonomy_name != "us-gaap":
            continue
        for concept_map in taxonomy_map.values():
            for unit_rows in (concept_map.get("units") or {}).values():
                for row in unit_rows:
                    if not _is_target_annual_row(row, fiscal_year):
                        continue
                    candidate = {
                        "fiscal_year": int(row.get("fy") or fiscal_year),
                        "fiscal_period": str(row.get("fp") or "FY"),
                        "form": str(row.get("form") or ""),
                        "filed": str(row.get("filed") or ""),
                        "period_start": str(row.get("start") or "") or None,
                        "period_end": str(row.get("end") or "") or None,
                        "duration_score": _annual_duration_score(row),
                    }
                    score = (
                        candidate["period_end"] or "",
                        candidate["duration_score"],
                        candidate["filed"],
                    )
                    if latest is None or score > latest[0]:
                        latest = (score, candidate)
    if latest:
        latest[1].pop("duration_score", None)
        return latest[1]
    return {"fiscal_year": fiscal_year, "fiscal_period": "FY", "form": "-", "filed": "-", "period_start": None, "period_end": None}


def final_grade(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 90:
        return "A"
    if score >= 80:
        return "B1"
    if score >= 70:
        return "B2"
    if score >= 50:
        return "C"
    return "D"


def grade_higher_better(value: float | None, thresholds: list[tuple[str, float]]) -> str | None:
    if value is None:
        return None
    for grade, threshold in thresholds:
        if value >= threshold:
            return grade
    return "D"


def grade_lower_better(value: float | None, thresholds: list[tuple[str, float]]) -> str | None:
    if value is None:
        return None
    for grade, threshold in thresholds:
        if value <= threshold:
            return grade
    return "D"


def grade_debt_ratio(value: float | None) -> str | None:
    if value is None:
        return None
    if value <= 1.0:
        return "AAA"
    if value <= 2.0:
        return "AA"
    if value <= 3.0:
        return "BB"
    if value <= 4.0:
        return "CC"
    return "D"


def grade_current_ratio(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 1.0:
        return "AA"
    if value >= 0.9:
        return "BB"
    if value >= 0.8:
        return "CC"
    return "D"


def grade_financial_debt_to_operating_income(value: float | None, operating_income_krw: float | None) -> str | None:
    if operating_income_krw is not None and operating_income_krw <= 0:
        return "D"
    return grade_lower_better(
        value,
        [
            ("AAA", 0.25),
            ("AA", 0.75),
            ("A", 1.50),
            ("BB", 2.75),
            ("B", 4.50),
            ("CC", 6.50),
            ("C", 9.00),
        ],
    )


def translate_usd(value: float | None, rate: float | None) -> float | None:
    if value is None or rate in (None, 0):
        return None
    return value * rate


def millions_krw(value_krw: float | None) -> float | None:
    if value_krw is None:
        return None
    return value_krw / 1_000_000


def component(metric_key: str, label: str, value, grade: str | None, weight: float, note: str = "") -> dict:
    effective_grade = grade or "D"
    return {
        "metric_key": metric_key,
        "label": label,
        "value": value,
        "grade": effective_grade,
        "weight": weight,
        "points": GRADE_POINTS[effective_grade],
        "weighted_points": GRADE_POINTS[effective_grade] * weight / 100,
        "note": note or ("Missing data conservatively scored as D." if grade is None else ""),
    }


def build_score_adjustments(result: ScreeningResult, prior_result: ScreeningResult | None) -> list[dict]:
    adjustments = []
    altman_score = result.metrics.get("altman_z_score")
    if altman_score is not None and altman_score < 1.0:
        adjustments.append(
            {
                "key": "altman_z_below_1",
                "label": "Altman Z-score < 1.0",
                "points": -2.0,
                "note": "Company policy deduction using the uniform original Altman Z-score formula.",
            }
        )

    consecutive_ocf_loss = (
        prior_result is not None
        and result.metrics.get("operating_cash_flow") is not None
        and prior_result.metrics.get("operating_cash_flow") is not None
        and result.metrics["operating_cash_flow"] < 0
        and prior_result.metrics["operating_cash_flow"] < 0
    )
    consecutive_net_loss = (
        prior_result is not None
        and result.metrics.get("net_income") is not None
        and prior_result.metrics.get("net_income") is not None
        and result.metrics["net_income"] < 0
        and prior_result.metrics["net_income"] < 0
    )
    if consecutive_ocf_loss or consecutive_net_loss:
        reasons = []
        if consecutive_ocf_loss:
            reasons.append("operating cash flow")
        if consecutive_net_loss:
            reasons.append("net income")
        adjustments.append(
            {
                "key": "two_year_consecutive_losses",
                "label": "Two-year consecutive OCF or net loss",
                "points": -2.0,
                "note": "Maximum 2-point deduction triggered by consecutive negative " + " and ".join(reasons) + ".",
            }
        )

    return adjustments


def build_company_rating(
    result: ScreeningResult,
    prior_result: ScreeningResult | None,
    exchange_rates: dict,
) -> dict:
    rates = exchange_rates.get((result.cik, result.fiscal_year), exchange_rates.get(result.fiscal_year, {}))
    prior_rates = (
        exchange_rates.get((prior_result.cik, prior_result.fiscal_year), exchange_rates.get(prior_result.fiscal_year, {}))
        if prior_result
        else {}
    )
    closing_rate = rates.get("closing")
    average_rate = rates.get("average")
    prior_closing_rate = prior_rates.get("closing")

    revenue_krw = translate_usd(result.metrics.get("revenue"), average_rate)
    operating_income_krw = translate_usd(result.metrics.get("operating_income"), average_rate)
    interest_expense_krw = translate_usd(result.metrics.get("interest_expense_abs"), average_rate)
    operating_cf_krw = translate_usd(result.metrics.get("operating_cash_flow"), average_rate)
    financial_debt_krw = translate_usd(result.metrics.get("financial_debt"), closing_rate)
    accounts_receivable_krw = translate_usd(result.metrics.get("accounts_receivable"), closing_rate)

    prior_accounts_receivable_krw = None
    if prior_result is not None:
        prior_accounts_receivable_krw = translate_usd(prior_result.metrics.get("accounts_receivable"), prior_closing_rate)

    average_receivable_krw = accounts_receivable_krw
    receivable_note = "Current year accounts receivable used because prior-year SEC receivable was unavailable."
    if accounts_receivable_krw is not None and prior_accounts_receivable_krw is not None:
        average_receivable_krw = (accounts_receivable_krw + prior_accounts_receivable_krw) / 2
        receivable_note = "Average of current and prior year accounts receivable."

    receivable_turnover_days = None
    if average_receivable_krw is not None and revenue_krw not in (None, 0):
        receivable_turnover_days = average_receivable_krw / revenue_krw * 365

    revenue_mil = millions_krw(revenue_krw)
    operating_income_mil = millions_krw(operating_income_krw)
    financial_debt_to_op = safe_div(financial_debt_krw, operating_income_krw)
    ocf_to_debt = safe_div(operating_cf_krw, financial_debt_krw)
    interest_coverage = safe_div(operating_income_krw, interest_expense_krw)
    interest_coverage_grade = grade_higher_better(
        interest_coverage,
        [("AAA", 20), ("AA", 15), ("A", 10), ("BB", 5), ("B", 2.25), ("CC", 1), ("C", 0.5)],
    )
    interest_coverage_note = ""
    if interest_coverage is None and result.metrics.get("interest_expense_abs") is None and operating_income_krw is not None:
        interest_coverage_grade = "AAA"
        interest_coverage_note = (
            "Interest expense is not separately disclosed in SEC facts/raw 10-K; "
            "scored AAA per current working rule, not by treating interest expense as zero."
        )

    components = [
        component(
            "revenue",
            "Revenue",
            revenue_mil,
            grade_higher_better(
                revenue_mil,
                [("AAA", 40000), ("AA", 15000), ("A", 12000), ("BB", 7000), ("B", 3500), ("CC", 1000), ("C", 250)],
            ),
            RATING_WEIGHTS["revenue"],
            "KRW million translated at average USD/KRW rate.",
        ),
        component(
            "operating_income",
            "Operating Income",
            operating_income_mil,
            grade_higher_better(
                operating_income_mil,
                [("AAA", 4000), ("AA", 2000), ("A", 1500), ("BB", 750), ("B", 250), ("CC", 125), ("C", 60)],
            ),
            RATING_WEIGHTS["operating_income"],
            "KRW million translated at average USD/KRW rate.",
        ),
        component(
            "interest_coverage",
            "Interest Coverage",
            interest_coverage,
            interest_coverage_grade,
            RATING_WEIGHTS["interest_coverage"],
            interest_coverage_note,
        ),
        component(
            "financial_debt_to_operating_income",
            "Financial Debt / Operating Income",
            financial_debt_to_op,
            grade_financial_debt_to_operating_income(financial_debt_to_op, operating_income_krw),
            RATING_WEIGHTS["financial_debt_to_operating_income"],
            "Financial debt translated at closing rate; operating income at average rate.",
        ),
        component(
            "operating_cf_to_financial_debt",
            "Operating CF / Financial Debt",
            ocf_to_debt,
            grade_higher_better(ocf_to_debt, [("AAA", 1.0), ("AA", 0.8), ("A", 0.55), ("BB", 0.35), ("B", 0.2), ("CC", 0.1), ("C", 0.05)]),
            RATING_WEIGHTS["operating_cf_to_financial_debt"],
            "Operating cash flow translated at average rate; financial debt at closing rate.",
        ),
        component(
            "debt_ratio",
            "Liabilities / Equity",
            result.metrics.get("debt_ratio"),
            grade_debt_ratio(result.metrics.get("debt_ratio")),
            RATING_WEIGHTS["debt_ratio"],
            "Duplicate threshold groups use the lower grade, per user instruction.",
        ),
        component(
            "receivable_turnover_days",
            "Receivable Turnover Days",
            receivable_turnover_days,
            grade_lower_better(
                receivable_turnover_days,
                [("AAA", 30), ("AA", 40), ("A", 50), ("BB", 60), ("B", 70), ("CC", 80), ("C", 90)],
            ),
            RATING_WEIGHTS["receivable_turnover_days"],
            receivable_note,
        ),
        component(
            "current_ratio",
            "Current Ratio",
            result.metrics.get("current_ratio"),
            grade_current_ratio(result.metrics.get("current_ratio")),
            RATING_WEIGHTS["current_ratio"],
            "Duplicate threshold groups use the lower grade, per user instruction.",
        ),
    ]
    base_score = sum(item["weighted_points"] for item in components)
    score_adjustments = build_score_adjustments(result, prior_result)
    adjustment_total = sum(item["points"] for item in score_adjustments)
    weighted_score = max(0.0, min(100.0, base_score + adjustment_total))
    return {
        "exchange_rates": {
            "closing": closing_rate,
            "average": average_rate,
            "closing_date": rates.get("closing_date"),
            "average_start": rates.get("average_start"),
            "average_end": rates.get("average_end"),
            "source": rates.get("source"),
        },
        "translated_metrics": {
            "revenue_mil_krw": revenue_mil,
            "operating_income_mil_krw": operating_income_mil,
            "financial_debt_mil_krw": millions_krw(financial_debt_krw),
            "operating_cf_mil_krw": millions_krw(operating_cf_krw),
            "accounts_receivable_mil_krw": millions_krw(accounts_receivable_krw),
            "receivable_turnover_days": receivable_turnover_days,
        },
        "components": components,
        "base_score": base_score,
        "score_adjustments": score_adjustments,
        "score_adjustment_total": adjustment_total,
        "weighted_score": weighted_score,
        "final_grade": final_grade(weighted_score),
        "point_scale_note": (
            "Component grade points are configurable: AAA=100, AA=95, A=90, BB=80, B=70, CC=60, C=50, D=40. "
            "Score adjustments: Altman Z-score below 1.0 deducts 2 points; two-year consecutive negative operating cash flow or net income deducts 2 points maximum. "
            "Construction-price bonus is not applied because no construction estimate input is available."
        ),
    }


def apply_company_ratings(results: list[ScreeningResult], exchange_rates: dict[int, dict[str, float]]) -> None:
    by_company: dict[str, list[ScreeningResult]] = {}
    for result in results:
        by_company.setdefault(result.cik, []).append(result)

    for company_results in by_company.values():
        company_results.sort(key=lambda item: item.fiscal_year)
        for index, result in enumerate(company_results):
            prior_result = company_results[index - 1] if index > 0 else None
            result.rating = build_company_rating(result, prior_result, exchange_rates)


def build_red_flags(metrics: dict) -> list[str]:
    flags = []
    if metrics.get("altman_z_score") is not None and metrics["altman_z_score"] < 1:
        flags.append("Preliminary red flag: Altman Z-score is below 1.0.")
    if metrics.get("debt_ratio") is not None and metrics["debt_ratio"] >= 2:
        flags.append("Preliminary red flag: liabilities / equity is 200% or higher.")
    if metrics.get("current_ratio") is not None and metrics["current_ratio"] < 1:
        flags.append("Preliminary red flag: current ratio is below 1.0x.")
    if metrics.get("operating_cash_flow") is not None and metrics["operating_cash_flow"] < 0:
        flags.append("Preliminary red flag: operating cash flow is negative.")
    if metrics.get("net_margin") is not None and metrics["net_margin"] < 0:
        flags.append("Preliminary red flag: net margin is negative.")
    if metrics.get("working_capital") is not None and metrics["working_capital"] < 0:
        flags.append("Preliminary red flag: working capital is negative.")
    if not flags:
        flags.append("No automatic red flag triggered. Human review is still required.")
    return flags


def screen_company_year(match: CompanyMatch, company_facts: dict, fiscal_year: int) -> ScreeningResult:
    metrics, notes = extract_metrics_for_year(
        company_facts,
        fiscal_year,
        cik=match.cik,
        sic=match.sic,
        sic_description=match.sic_description,
    )
    filing_meta = infer_filing_meta_for_year(company_facts, fiscal_year)
    return ScreeningResult(
        company_name=match.company_name,
        ticker=match.ticker,
        cik=match.cik,
        sic=match.sic,
        sic_description=match.sic_description,
        fiscal_year=fiscal_year,
        fiscal_period=filing_meta["fiscal_period"],
        form=filing_meta["form"],
        filed=filing_meta["filed"],
        period_start=filing_meta.get("period_start"),
        period_end=filing_meta.get("period_end"),
        metrics=metrics,
        notes=notes,
        red_flags=build_red_flags(metrics),
    )


def select_target_years(company_facts: dict, start_year: int, end_year: int) -> list[int]:
    if start_year > end_year:
        raise ValueError("Start year cannot be later than end year.")
    available = annual_years_available(company_facts)
    return [year for year in available if start_year <= year <= end_year]


def screen_company(match: CompanyMatch) -> ScreeningResult:
    company_facts = fetch_company_facts(match.cik)
    available = annual_years_available(company_facts)
    if not available:
        raise ValueError("No annual SEC facts found.")
    return screen_company_year(match, company_facts, max(available))


def screen_companies(
    matches: list[CompanyMatch],
    start_year: int,
    end_year: int,
    exchange_rates: dict[int, dict[str, float]] | None = None,
) -> tuple[list[ScreeningResult], list[ScreeningError]]:
    results: list[ScreeningResult] = []
    errors: list[ScreeningError] = []
    for match in matches:
        try:
            company_facts = fetch_company_facts(match.cik)
            target_years = select_target_years(company_facts, start_year, end_year)
            if not target_years:
                errors.append(
                    ScreeningError(
                        input_query=match.ticker or match.company_name,
                        company_name=match.company_name,
                        ticker=match.ticker,
                        cik=match.cik,
                        fiscal_year=None,
                        error_message=f"No annual SEC facts found between {start_year} and {end_year}.",
                    )
                )
                continue
            for fiscal_year in target_years:
                try:
                    results.append(screen_company_year(match, company_facts, fiscal_year))
                except Exception as exc:
                    errors.append(
                        ScreeningError(
                            input_query=match.ticker or match.company_name,
                            company_name=match.company_name,
                            ticker=match.ticker,
                            cik=match.cik,
                            fiscal_year=fiscal_year,
                            error_message=str(exc),
                        )
                    )
        except Exception as exc:
            errors.append(
                ScreeningError(
                    input_query=match.ticker or match.company_name,
                    company_name=match.company_name,
                    ticker=match.ticker,
                    cik=match.cik,
                    fiscal_year=None,
                    error_message=str(exc),
                )
            )
    results.sort(key=lambda item: (item.company_name, item.fiscal_year))
    if exchange_rates:
        apply_company_ratings(results, exchange_rates)
    return results, errors


def result_to_summary_row(result: ScreeningResult) -> dict:
    return {
        "Company": result.company_name,
        "Ticker": result.ticker,
        "CIK": result.cik,
        "SIC": result.sic,
        "SIC Description": result.sic_description,
        "Fiscal Year": result.fiscal_year,
        "Form": result.form,
        "Filed": result.filed,
        "Period Start": result.period_start,
        "Period End": result.period_end,
        "Revenue": result.metrics.get("revenue"),
        "Operating Income": result.metrics.get("operating_income"),
        "Net Income": result.metrics.get("net_income"),
        "Retained Earnings": result.metrics.get("retained_earnings"),
        "Total Assets": result.metrics.get("total_assets"),
        "Total Liabilities": result.metrics.get("total_liabilities"),
        "Total Equity": result.metrics.get("total_equity"),
        "Financial Debt": result.metrics.get("financial_debt"),
        "Interest Expense": result.metrics.get("interest_expense_abs"),
        "Accounts Receivable": result.metrics.get("accounts_receivable"),
        "Liabilities / Equity": result.metrics.get("debt_ratio"),
        "Current Ratio": result.metrics.get("current_ratio"),
        "Operating Margin": result.metrics.get("operating_margin"),
        "Net Margin": result.metrics.get("net_margin"),
        "ROA": result.metrics.get("roa"),
        "Interest Coverage": result.metrics.get("interest_coverage"),
        "Financial Debt / Operating Income": result.metrics.get("financial_debt_to_operating_income"),
        "Operating CF / Financial Debt": result.metrics.get("operating_cf_to_financial_debt"),
        "Operating Cash Flow": result.metrics.get("operating_cash_flow"),
        "Working Capital": result.metrics.get("working_capital"),
        "Altman Z-Score": result.metrics.get("altman_z_score"),
        "Altman Z Model": result.metrics.get("altman_z_model"),
        "Altman Z Zone": result.metrics.get("altman_z_zone"),
        "Altman X1 WC / Assets": result.metrics.get("altman_x1_working_capital_to_assets"),
        "Altman X2 Retained Earnings / Assets": result.metrics.get("altman_x2_retained_earnings_to_assets"),
        "Altman X3 EBIT Proxy / Assets": result.metrics.get("altman_x3_ebit_proxy_to_assets"),
        "Altman X4 Book Equity / Liabilities": result.metrics.get("altman_x4_book_equity_to_liabilities"),
        "Altman X5 Sales / Assets": result.metrics.get("altman_x5_sales_to_assets"),
        "Internal Base Score": result.rating.get("base_score") if result.rating else None,
        "Internal Score Adjustment": result.rating.get("score_adjustment_total") if result.rating else None,
        "Internal Score": result.rating.get("weighted_score") if result.rating else None,
        "Internal Grade": result.rating.get("final_grade") if result.rating else None,
        "Closing USD/KRW": result.rating.get("exchange_rates", {}).get("closing") if result.rating else None,
        "Average USD/KRW": result.rating.get("exchange_rates", {}).get("average") if result.rating else None,
        "Red Flag Count": len(result.red_flags),
    }


def result_to_rating_rows(result: ScreeningResult) -> list[dict]:
    if not result.rating:
        return []
    rows = []
    for item in result.rating.get("components", []):
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
                "Metric": item["label"],
                "Value": item["value"],
                "Grade": item["grade"],
                "Weight": item["weight"],
                "Points": item["points"],
                "Weighted Points": item["weighted_points"],
                "Note": item["note"],
            }
        )
    for adjustment in result.rating.get("score_adjustments", []):
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
                "Metric": adjustment["label"],
                "Value": None,
                "Grade": "Adjustment",
                "Weight": None,
                "Points": adjustment["points"],
                "Weighted Points": adjustment["points"],
                "Note": adjustment["note"],
            }
        )
    return rows


def result_to_note_rows(result: ScreeningResult) -> list[dict]:
    rows = [
        {
            "Company": result.company_name,
            "Ticker": result.ticker,
            "Fiscal Year": result.fiscal_year,
            "Metric": key,
            "Note": note,
        }
        for key, note in result.notes.items()
    ]
    if result.rating:
        rows.append(
            {
                "Company": result.company_name,
                "Ticker": result.ticker,
                "Fiscal Year": result.fiscal_year,
                "Metric": "internal_rating",
                "Note": result.rating.get("point_scale_note", ""),
            }
        )
    return rows


def result_to_flag_rows(result: ScreeningResult) -> list[dict]:
    return [
        {
            "Company": result.company_name,
            "Ticker": result.ticker,
            "Fiscal Year": result.fiscal_year,
            "Seq": index,
            "Flag": flag,
        }
        for index, flag in enumerate(result.red_flags, start=1)
    ]


def error_to_row(error: ScreeningError) -> dict:
    return {
        "Input": error.input_query,
        "Company": error.company_name,
        "Ticker": error.ticker,
        "CIK": error.cik,
        "Fiscal Year": error.fiscal_year if error.fiscal_year is not None else "-",
        "Error": error.error_message,
    }

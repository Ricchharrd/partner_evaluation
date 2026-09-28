from __future__ import annotations

from datetime import date, datetime


FX_SOURCE_LABEL = "Hardcoded FRED AEXKOUS annual average and DEXKOUS year-end/last observed KRW per 1 USD"

# FRED AEXKOUS provides annual average KRW/USD. FRED DEXKOUS provides daily spot
# rates; the closing rate below uses Dec. 31 where available, otherwise the last
# available observation before year-end.
HARDCODED_USD_KRW_RATES = {
    2020: {
        "average": 1180.5554,
        "closing": 1086.11,
        "closing_date": "2020-12-31",
    },
    2021: {
        "average": 1144.8911,
        "closing": 1188.59,
        "closing_date": "2021-12-30",
    },
    2022: {
        "average": 1291.7796,
        "closing": 1260.18,
        "closing_date": "2022-12-30",
    },
    2023: {
        "average": 1306.7637,
        "closing": 1290.97,
        "closing_date": "2023-12-29",
    },
    2024: {
        "average": 1363.4381,
        "closing": 1477.86,
        "closing_date": "2024-12-31",
    },
    2025: {
        "average": 1421.3963,
        "closing": 1444.55,
        "closing_date": "2025-12-31",
    },
}


def available_fx_years() -> list[int]:
    return sorted(HARDCODED_USD_KRW_RATES)


def min_fx_year() -> int:
    return min(available_fx_years())


def max_fx_year() -> int:
    return max(available_fx_years())


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def fiscal_period_from_result(result) -> tuple[date, date]:
    end_date = parse_date(getattr(result, "period_end", None))
    start_date = parse_date(getattr(result, "period_start", None))
    if end_date is None:
        end_date = date(int(result.fiscal_year), 12, 31)
    if start_date is None:
        start_date = date(int(result.fiscal_year), 1, 1)
    return start_date, end_date


def fx_for_year(fiscal_year: int) -> dict[str, float | str]:
    if fiscal_year not in HARDCODED_USD_KRW_RATES:
        raise ValueError(
            f"No hardcoded USD/KRW rate is available for FY{fiscal_year}. "
            f"Available years: {min_fx_year()}-{max_fx_year()}."
        )
    rate = HARDCODED_USD_KRW_RATES[fiscal_year]
    return {
        "closing": rate["closing"],
        "average": rate["average"],
        "closing_date": rate["closing_date"],
        "source": FX_SOURCE_LABEL,
    }


def build_exchange_rates_for_results(results) -> dict[tuple[str, int], dict[str, float | str]]:
    exchange_rates = {}
    for result in results:
        start_date, end_date = fiscal_period_from_result(result)
        rate = fx_for_year(int(result.fiscal_year))
        exchange_rates[(result.cik, int(result.fiscal_year))] = {
            "closing": rate["closing"],
            "average": rate["average"],
            "closing_date": rate["closing_date"],
            "average_start": start_date.isoformat(),
            "average_end": end_date.isoformat(),
            "source": rate["source"],
            "basis": "Calendar-year hardcoded FX proxy; not company-specific fiscal period daily average.",
        }
    return exchange_rates


def exchange_rate_rows(exchange_rates: dict[tuple[str, int], dict[str, float | str]]) -> list[dict]:
    rows = []
    for (cik, fiscal_year), rate in sorted(exchange_rates.items(), key=lambda item: (item[0][0], item[0][1])):
        rows.append(
            {
                "CIK": cik,
                "Fiscal Year": fiscal_year,
                "Closing USD/KRW": rate.get("closing"),
                "Closing Rate Date": rate.get("closing_date"),
                "Average USD/KRW": rate.get("average"),
                "Average Period": f"{rate.get('average_start')} ~ {rate.get('average_end')}",
                "Source": rate.get("source"),
                "Basis": rate.get("basis"),
            }
        )
    return rows

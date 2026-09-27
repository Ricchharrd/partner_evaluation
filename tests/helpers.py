from partner_finance.schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument


def fact(year, item, value, *, currency="KRW", entity_id="entity", multiplier=1, scope="연결"):
    return FinancialFact(
        entity_id=entity_id,
        fiscal_year=year,
        standard_item=item,
        original_label=item,
        original_value=value,
        normalized_value=value,
        currency=currency,
        unit_multiplier=multiplier,
        reporting_scope=scope,
        source_id="source",
        source_locator=f"synthetic:{year}:{item}",
    )


def sample_project():
    entity = EntityProfile(legal_name="Synthetic Infrastructure Co.", country="Testland", accounting_standard="IFRS", entity_id="entity")
    project = AnalysisProject(title="합성 테스트", entity=entity)
    project.sources.append(SourceDocument(name="synthetic.csv", source_type="합성 테스트", source_id="source"))
    for year, scale in [(2023, 1.0), (2024, 1.2)]:
        values = {
            "revenue": 1_000_000_000 * scale,
            "operating_income": 100_000_000 * scale,
            "net_income": 60_000_000 * scale,
            "total_assets": 2_000_000_000 * scale,
            "total_liabilities": 1_200_000_000 * scale,
            "total_equity": 800_000_000 * scale,
            "current_assets": 700_000_000 * scale,
            "current_liabilities": 500_000_000 * scale,
            "financial_debt": 600_000_000 * scale,
            "interest_expense": 20_000_000 * scale,
            "operating_cash_flow": 120_000_000 * scale,
            "capex": 40_000_000 * scale,
            "accounts_receivable": 150_000_000 * scale,
            "retained_earnings": 300_000_000 * scale,
        }
        project.facts.extend(fact(year, key, value) for key, value in values.items())
    return project

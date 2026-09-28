"""Synthetic demonstration data only; never represents actual partners."""
from .schema import AnalysisProject, EntityProfile, FinancialFact, SourceDocument
from .workflow import recalculate


def demo_projects():
    projects = []
    for i, role in enumerate(["EPC", "개발·투자", "운영"]):
        entity = EntityProfile(f"[합성 예시] Partner {i + 1}", country="TEST ONLY", accounting_standard="IFRS")
        project = AnalysisProject(f"합성 시연 {i + 1} - 실기업 아님", entity, project_id=f"synthetic-demo-{i + 1}")
        source = SourceDocument("합성 테스트 데이터 (공시자료 아님)", "합성 예시", source_id=f"demo-source-{i}")
        project.sources.append(source)
        project.narrative["partner_role"] = role
        for year in [2023, 2024, 2025]:
            scale = (1 + i) * (1 + (year - 2023) * .12) * 1_000_000
            values = {"revenue": 20000, "operating_income": 1500, "net_income": 900,
                      "total_assets": 30000, "total_liabilities": 18000, "total_equity": 12000,
                      "current_assets": 13000, "current_liabilities": 9000, "financial_debt": 6000,
                      "interest_expense": 150 + 50 * i, "operating_cash_flow": 2200,
                      "capex": 1000, "accounts_receivable": 2300, "retained_earnings": 4000}
            for item, value in values.items():
                project.facts.append(FinancialFact(entity.entity_id, year, item, item, value * scale, value * scale,
                    "KRW", period_start=f"{year}-01-01", period_end=f"{year}-12-31", accounting_standard="IFRS",
                    source_id=source.source_id, source_locator=f"synthetic/{year}/{item}"))
        recalculate(project)
        projects.append(project)
    return projects

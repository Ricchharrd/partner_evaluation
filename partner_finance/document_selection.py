"""Select likely financial pages locally before sending bounded text to AI."""
import re


def select_financial_text(text, budget=60_000):
    pages = re.findall(r"\[PAGE \d+\].*?(?=\[PAGE \d+\]|\Z)", text, re.S)
    if len(text) <= budget:
        return text, []
    if not pages:
        return text[:budget], ["페이지 구분이 없어 앞 60,000자만 분석합니다. 누락 여부를 확인하십시오."]
    terms = ("balance sheet", "financial position", "income statement", "profit or loss",
             "cash flow", "changes in equity", "interest expense", "finance costs",
             "retained earnings", "재무상태표", "손익계산서", "현금흐름표",
             "balance de situación", "cuenta de pérdidas", "flujos de efectivo")
    scores = []
    for page in pages:
        lower = page.lower()
        hits = sum(term in lower for term in terms)
        numbers = len(re.findall(r"\d[\d,.]*", page))
        scores.append(hits * 10 + (min(numbers, 100) / 20 if hits else 0))
    ranked = sorted(range(len(pages)), key=lambda i: (-scores[i], i))
    candidates = []
    for i in ranked:
        if scores[i] > 0:
            candidates.append(i)
    # Keep financial tables ahead of adjacent context and introductory pages.
    for i in candidates[:]:
        candidates.extend(j for j in (i - 1, i + 1) if 0 <= j < len(pages))
    candidates.extend(range(len(pages)))
    chosen = {}
    remaining = budget
    for i in dict.fromkeys(candidates):
        page = pages[i]
        if len(page) + 2 <= remaining:
            chosen[i] = page
            remaining -= len(page) + 2
    if not chosen:
        return text[:budget], ["페이지 길이가 분석 한도를 초과하여 일부 텍스트만 분석합니다."]
    selected = "\n\n".join(chosen[i] for i in sorted(chosen))
    locations = ", ".join(re.match(r"\[PAGE (\d+)\]", chosen[i]).group(1) for i in sorted(chosen))
    return selected, [f"재무제표 관련 페이지를 우선 선택했습니다 (PDF 페이지: {locations}). 전체 원문이 아닌 일부 분석이며 누락·통화·연결 기준을 확인하십시오."]

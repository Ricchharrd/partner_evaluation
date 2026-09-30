"""Local, bounded selection of statement blocks; never calls an AI service."""
import re

SELECTION_VERSION = "statements-2"
DEFAULT_BUDGET = 36_000
TERMS = ("balance sheet", "financial position", "income statement", "profit or loss",
         "cash flow", "changes in equity", "interest expense", "finance costs",
         "재무상태표", "손익계산서", "현금흐름표", "balance de situación",
         "cuenta de pérdidas", "flujos de efectivo")
PRIMARY = re.compile(r"consolidated (?:statement(?:s)? of (?:financial position|profit or loss|cash flows?)|balance sheet|income statement)|연결\s*(?:재무상태표|손익계산서|현금흐름표)", re.I)


def select_financial_text(text, budget=DEFAULT_BUDGET):
    if budget < 100:
        raise ValueError("분석 텍스트 한도가 너무 작습니다.")
    pages = re.findall(r"\[PAGE \d+\].*?(?=\[PAGE \d+\]|\Z)", text, re.S)
    if not pages:
        return (text, []) if len(text) <= budget else (text[:budget], [f"페이지 구분이 없어 앞 {budget:,}자만 분석합니다. 누락 여부를 확인하십시오."])
    scores, primary = [], []
    for i, page in enumerate(pages):
        lower = " ".join(page.lower().split())
        header = lower[:650]
        hits = sum(term in lower for term in TERMS)
        numbers = len(re.findall(r"\d[\d,.]*", page))
        adjusted = bool(re.search(r"(?:adjusted|reclassified)\s+(?:consolidated\s+)?(?:statement|income|balance|cash)", header)) or "alternative performance" in header
        is_primary = bool(PRIMARY.search(header)) and numbers >= 12 and not adjusted
        if is_primary:
            primary.append(i)
        scores.append((100 if is_primary else 0) + hits * 10 + (min(numbers, 100) / 20 if hits else 0) - (80 if adjusted else 0))
    ranked = sorted(range(len(pages)), key=lambda i: (-scores[i], i))
    # Statement continuations precede unrelated notes and management APM tables.
    def family(i):
        header = " ".join(pages[i].lower().split())[:650]
        return {kind for kind, terms in {
            "balance": ("financial position", "balance sheet", "재무상태표"),
            "income": ("profit or loss", "income statement", "손익계산서"),
            "cash": ("cash flow", "현금흐름표"),
        }.items() if any(t in header for t in terms)}
    clusters = [(i, [j for j in primary if i <= j <= i + 7]) for i in primary]
    complete = [(i, group) for i, group in clusters if len(set().union(*(family(j) for j in group))) == 3]
    if complete:
        start, group = min(complete, key=lambda pair: pair[0])
        # A complete contiguous set beats disconnected subsidiaries/notes.
        candidates = list(range(max(0, start - 1), min(len(pages), max(group) + 3)))
    else:
        candidates = list(primary)
        for i in primary:
            candidates.extend(j for j in (i + 1, i + 2, i - 1) if 0 <= j < len(pages))
        candidates.extend(i for i in ranked if scores[i] > 0)
    if not candidates:
        if len(text) <= budget:
            return text, []
        candidates = list(range(len(pages)))
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
    return selected, [f"재무제표와 연속 표 우선 선택 (PDF 페이지: {locations}; 원문 {len(text):,}자 → 전송 {len(selected):,}자). 일부 분석이며 누락·통화·연결 기준을 확인하십시오."]

"""Preserve the complete extracted report; never silently select or clip pages."""
import re

SELECTION_VERSION = "full-report-1"


def select_financial_text(text, budget=None):
    if budget is not None and len(text) > budget:
        raise ValueError("문서 전체가 지정한 처리 한도를 넘었습니다. 일부 페이지를 자동 제외하지 않습니다.")
    pages = re.findall(r"\[PAGE (\d+)\]", text)
    coverage = f"텍스트가 추출된 {len(pages)}개 페이지" if pages else "추출 텍스트 전체"
    return text, [f"전체 문서 분석: {coverage}, {len(text):,}자. 토큰 절약용 페이지 선별·텍스트 자르기를 적용하지 않았습니다. 이미지 속 숫자는 별도 확인이 필요합니다."]

# 사내 전용 계산 절차

공개 패킷에 재무평가가 없거나 별도 내부 재무제표를 검토할 때 사용한다.
회사 정책상 허용된 Claude 코드 실행 환경에서만 실행한다. 도구가 없으면
외부 서비스로 우회하지 말고 계산 실행 불가와 필요한 관리자 설정을 알린다.

1. 필요한 재무제표 페이지만 읽는다. 법인·회계기간·통화·단위·연결/별도를 확인한다.
2. 아래 JSON으로 수치를 추출한다. `quote`는 원문의 숫자·계정명을 포함한다.
   누락은 null 또는 행 제외. 같은 계정의 상충 값은 임의 선택하지 않는다.
   원문 금액을 `original_value`에 넣고 단위배수는 한 번만 적용한다.
3. `python scripts/calculate.py internal_input.json --digest`로 입력 지문을 구한다.
   원문대조표와 지문을 사용자에게 제시한다. 사람의 실제 확인 답변을 받은 후에만
   `human_review.confirmed`를 true로 하고 확인자·확인 내용·`input_digest`를 기록한다.
   지문 명령은 계산이나 승인 처리를 하지 않는다. 입력/환율 변경 시 새 확인이 필요하다.
4. `python scripts/calculate.py internal_input.json internal_result.json` 실행.
   이 스킬 폴더를 기준으로 한다. 네트워크·API·외부 파일 전송은 필요 없다.
5. 결과의 검증 오류는 보류한다. 기사 근거와 내부 계산결과를 연결해 초안을 작성한다.
   최종 승인과 결과 보관은 사내에서만 수행한다.

입력 예시 (가상 기업의 일부 항목; 이 예시로 전체 점수는 산정되지 않음):
```json
{
  "schema": "internal-financial-input/1.0",
  "entity": {"legal_name": "Example Co", "country": "Example", "accounting_standard": "IFRS"},
  "human_review": {"confirmed": false, "reviewer": "", "note": "", "input_digest": ""},
  "facts": [{
    "fiscal_year": 2025, "standard_item": "revenue", "original_label": "Revenue",
    "original_value": 100, "unit_multiplier": 1000000, "currency": "EUR",
    "period_start": "2025-01-01", "period_end": "2025-12-31", "reporting_scope": "연결",
    "source_id": "internal-statement-2025", "source_locator": "PDF p.12",
    "quote": "Revenue 100 (EUR million)"
  }],
  "fx_rates": []
}
```

허용 계정키: revenue, operating_income, net_income, total_assets, total_liabilities,
total_equity, current_assets, current_liabilities, cash, accounts_receivable,
short_term_debt, long_term_debt, financial_debt, interest_expense,
operating_cash_flow, investing_cash_flow, financing_cash_flow, capex,
retained_earnings, beginning_cash, ending_cash, fx_effect_on_cash.
재무상태표 항목은 period_start를 빈 문자열로 두고 실제 기말일을 넣는다.

KRW는 환율 1. 다른 통화의 금액평가에는 검증된 평균환율이 필요하다.
fx_rates 행: fiscal_year, currency, average_krw_per_unit (양수), period_start,
period_end, source. 실제 회계기간이 일치해야 한다. 출처가 없으면 추정하지 않는다.
환율 확보를 위해 내부 데이터를 외부에 전송하지 않는다.
금액평가에 필요한 환율이 없어도 원통화 비율은 계산하되 전체 점수는 보류한다.
이는 완전한 K-IFRS 재무제표 환산이 아니다.

공개 앱과 동일한 수정 평가표를 사용한다. Altman은 현재 장부자본·영업이익
대용치이고 원형 산식과 다르다. 이 한계를 숨기거나 부도확률로 표현하지 않는다.
검증 오류·미확인 이자비용을 0 또는 AAA로 바꾸지 않는다.

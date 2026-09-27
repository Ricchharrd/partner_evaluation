# 개인 OpenAI 처리 → 사내 Claude 검토

## 구성
공개 공시/승인된 자료 → Streamlit → Python 계산 + OpenAI 추출·조사 → 검토자료 다운로드 → 담당자가 사내 Claude에 전달 → 최종 검토.

계산과 파일 생성에는 AI 호출이 필요하지 않습니다. OpenAI는 PDF 추출, 기업 조사, 선택적 분석문 작성에 사용합니다. 사내 Claude API는 연결하지 않습니다. 내보내기만으로 OpenAI 호출이 발생하지 않습니다.

## 설정
Streamlit 배포 설정의 Secrets에 아래 값을 넣습니다. 키는 GitHub, 화면 캡처, 보고서, 대화에 넣지 마십시오.

```toml
OPENAI_API_KEY = "실제 개인 OpenAI API 키"
OPENAI_MODEL = "gpt-4.1-mini"
SEC_USER_AGENT = "YourCompany contact@example.com"
```

로컬에서는 `.streamlit/secrets.toml`을 사용합니다. `.env.example`은 설명용이며 자동 로드하지 않습니다. 국내 공시를 사용하면 DART_API_KEY도 별도로 설정합니다. 기존 ANTHROPIC_API_KEY는 현재 화면에서 사용하지 않습니다.

## 사용 순서
1. 기업을 찾아 평가하거나 공개 재무자료를 입력합니다. 개인 API로 보내는 자료인지 확인한 뒤 동의합니다.
2. 수치·원문·회계기간·통화를 확인합니다. AI 추출은 검증 완료가 아닙니다.
3. `사내 Claude 검토자료 받기`로 패킷을 받습니다. 최소 입력은 요약 Markdown입니다. 근거가 필요하면 ZIP을 풀어 JSON도 첨부합니다.
4. 사내 Claude의 Customize → Skills에서 `partner-review-skill.zip`을 등록합니다. 조직 관리자 설정과 코드 실행/Skills 사용 권한에 따라 등록이 제한될 수 있습니다.
5. 패킷의 `03_REQUEST.txt` 내용을 검토 요청으로 사용합니다. 스킬 등록이 불가능하면 SKILL.md 내용을 사내 승인된 프로젝트 지침으로 사용합니다.

## 비용·보안
- ChatGPT 구독과 API 사용료는 별개입니다. 개인 API 비용 한도와 알림은 본인 API 프로젝트에서 확인합니다.
- 현재 앱은 출력 토큰 제한, PDF 앞 60,000자 제한, 조사 캐시로 호출량을 줄입니다. 월별 금액 차단 기능은 없으며 45달러 내 이용을 보장하지 않습니다.
- PDF 뒷부분의 재무제표가 잘릴 수 있습니다. 필요한 페이지를 분리한 공개 PDF 또는 구조화된 Excel을 사용하고 누락 경고를 검토합니다.
- `store=False`를 사용하지만 무보관 계약이나 회사의 외부 전송 승인을 뜻하지 않습니다.
- 공개 공시라도 후보사 목록, 내부 평가 기준, 메모 및 결합된 평가 결과는 내부정보일 수 있습니다. 개인 API/외부 배포 범위는 회사 승인을 받으십시오.
- 패킷은 필요한 근거를 포함하므로 외부 공개용이 아닙니다. API 키와 원문 전체, 로컬 저장경로는 패킷에 의도적으로 포함하지 않습니다. 자유입력 내용의 민감정보 자동 탐지는 제공하지 않습니다.
- 현재 환율 및 Altman 대용치 제한은 패킷에 표시됩니다. 정식 평가 확정 전 별도 검증이 필요합니다.

공식 참고: https://support.claude.com/en/articles/12512180-use-skills-in-claude

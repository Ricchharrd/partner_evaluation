# 해외 파트너사 재무평가 워크벤치

## 현재 AI 연결 방식

현재 화면은 개인 OpenAI API로 공개자료를 처리하고, 검토 패킷을 다운로드하여 사내 Claude에서 수동 검토하는 구성입니다. 최신 설정은 [OPENAI_CLAUDE_GUIDE.md](OPENAI_CLAUDE_GUIDE.md)를 따르십시오. 아래 과거 Claude API 설정 설명보다 이 안내가 우선합니다. 사내 Claude용 스킬은 `deliverables/partner-review-skill.zip`입니다.

## 통합 시범 버전 업데이트

기업별 평가·사업근거·SEC 공시 동향·검토·보고서 화면을 연결했습니다. 실행 순서와 현재 한계는 [PILOT_GUIDE.md](PILOT_GUIDE.md)를 먼저 확인하십시오. 로컬 실행은 `run_workbench.bat`입니다. 공개 배포는 수행하지 않았습니다.

건설·인프라 PPP 업무에서 해외 파트너사의 공개 공시 또는 사용자 제공 자료를 검토하고, 출처가 붙은 재무분석표와 한국어 보고서를 만드는 Streamlit MVP입니다.

이 도구는 정식 신용평가를 대체하지 않습니다. 숫자와 근거를 한 화면에서 검토하고 수정한 뒤 예비 분석자료를 만드는 용도입니다.

## 구현된 사용자 흐름

1. 저장된 분석 열기 또는 신규 법인 생성
2. SEC/OpenDART 검색, 보고서 URL, 파일 업로드, 수동 입력 중 하나로 자료 수집
3. 법인·연도·연결/별도·회계기준 확인
4. 원문 근거가 붙은 추출값 검토 및 수정 사유 기록
5. 검증 경고와 누락 항목 확인
6. Python으로 재무비율과 회사 평가표 계산
7. Claude API 또는 규칙 기반으로 한국어 분석문 생성
8. Word 보고서, Excel 분석표, JSON 백업 다운로드
9. SQLite에 저장된 분석 다시 열기

## 실제 지원 범위

| 입력 경로 | 지원 상태 | 비고 |
|---|---|---|
| SEC EDGAR | 지원 | 미국 상장사 후보 선택, companyfacts 연차값, 기존 SEC fallback 재사용 |
| OpenDART | 지원 | `DART_API_KEY` 필요, 단일회사 전체 재무제표 API 사용 |
| CSV | 지원 | 표준 long-format 템플릿 |
| XLSX | 지원 | 표준 long-format 또는 첫 열 항목/나머지 열 연도인 단순 표 |
| XBRL/iXBRL | 부분 지원 | 표준 IFRS/US-GAAP 계정명 일부 매핑. 회사 확장 taxonomy는 수동 검토 필요 |
| 텍스트 PDF | 부분 지원 | 텍스트 보존·스캔 여부 판별. Claude API 실행 시 후보 값 추출 |
| 스캔 PDF | 미지원 | OCR/비전 환경 미연결을 화면에 표시 |
| 보고서 URL | 지원 | 공개 http/https, 최대 25MB, 내부 IP/localhost 차단 |
| ESEF 기업 검색 | 미지원 | ESEF는 문서 형식이며 단일 유럽 기업 조회 API가 아님. 공식 파일 URL 또는 업로드 사용 |
| 기업개요 외부 웹검색 | 미지원 | 현재는 공식 보고서/사용자 입력 근거만 사용 |

기존 `../sec_mvp`와 `../dart_v2` 앱은 제거하거나 변경하지 않았습니다. 새 앱은 기존 SEC 수집·평가 로직의 보존 사본을 어댑터로 사용합니다.

## 공통 데이터 구조

각 재무값은 법인, 회계연도, 기간, 연결/별도, 회계기준, 통화, 원문 단위, 원문값, 정규화값, 원문 항목명, 표준 항목, 출처, 페이지/셀/XBRL 태그, 공시일, 수집일, 재작성 여부, 추출상태를 저장합니다.

사용자 수정값은 별도 필드로 저장됩니다. 원래 추출값과 출처는 덮어쓰지 않습니다. 누락값은 `0`이 아닌 `None/미확인`으로 유지합니다.

## 검증과 계산

- 자산 = 부채 + 자본 검증(원문 단위와 금액 규모를 고려한 허용오차)
- 기초현금 + 영업/투자/재무CF + 환율효과 = 기말현금 검증
- 연도·통화·기간·연결/별도 혼합 경고
- 필수 항목 누락, 출처 충돌, 75% 이상 전년 대비 변동 경고
- 영업이익률, 순이익률, ROA, 유동비율, 부채비율, 이자보상배율, 금융부채/영업이익, 영업CF/금융부채, 영업CF 마진, FCF, 매출성장률
- 분모 0·음수·입력 누락은 계산 보류와 이유 표시

기존 회사 평가표도 구현했습니다. 다만 필수 입력이나 환율이 없으면 임의로 D를 부여하지 않고 평가를 보류합니다. 금융회사와 프로젝트 SPV는 일반기업 점수를 산출하지 않습니다. SEC 기존 앱의 과거 평가결과는 호환 정보로 함께 보존됩니다.

## Claude API

AI 공급자 계층은 `partner_finance/ai.py`로 분리했습니다. 키가 없으면 파일 관리, 수동 수정, 검증, 비율, 평가, Word·Excel 출력이 모두 작동합니다.

기본 모델은 환경변수 `ANTHROPIC_MODEL`로 관리하며 현재 기본값은 `claude-sonnet-4-6`입니다. 운영 전에 Anthropic의 [모델 수명주기 문서](https://docs.anthropic.com/en/docs/about-claude/model-deprecations)를 다시 확인하십시오. 호출은 공식 Messages API `POST /v1/messages`를 사용합니다.

AI는 산술 계산을 하지 않습니다. 확정값·Python 계산 비율·검증 경고를 받아 보고 문구만 생성합니다. 문서 추출 결과는 항상 `AI 추출-검토 필요`로 저장됩니다.

## 로컬 실행

Python 3.11 이상을 권장합니다.

```powershell
cd partner_financial_workbench
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:SEC_USER_AGENT = "YourCompany PartnerReview contact@example.com"
streamlit run streamlit_app.py
```

선택 설정:

```powershell
$env:DART_API_KEY = "..."
$env:ANTHROPIC_API_KEY = "..."
$env:ANTHROPIC_MODEL = "claude-sonnet-4-6"
$env:APP_USER_ID = "employee-id"
```

Streamlit Cloud에서는 `.streamlit/secrets.toml`에 같은 이름으로 설정하십시오. 실제 비밀값 파일은 `.gitignore`에 포함되어 있습니다.

## GitHub / Streamlit 배포

1. 이 폴더를 GitHub 저장소 루트로 올립니다.
2. Streamlit Community Cloud에서 `streamlit_app.py`를 entry point로 선택합니다.
3. Advanced settings의 Secrets에 필요한 키와 `SEC_USER_AGENT`를 입력합니다.
4. 공유 운영에서는 Community Cloud의 임시 로컬 디스크를 영구 저장소로 간주하면 안 됩니다. `ProjectStore`를 PostgreSQL/S3 등으로 교체하십시오.
5. 인증 앞단 또는 플랫폼 SSO에서 `APP_USER_ID`를 주입해 사용자별 데이터를 분리하십시오.

현재 SQLite 저장소는 단일 인스턴스·소규모 파일럿용입니다. 여러 사용자와 여러 인스턴스가 동시에 사용하는 운영환경에서는 외부 영구 DB와 object storage가 필요합니다.

## 테스트

```powershell
python -m unittest discover -s tests -v
```

테스트는 합성 데이터임을 명시하며 다음을 확인합니다.

- 누락값을 0으로 처리하지 않는 비율 계산
- 음수 자본에서 부채비율 보류
- 대차 불일치, 통화 혼합, 급격한 변동 경고
- 사용자 수정값과 원값 동시 보존
- SQLite 저장/재열기
- Word·Excel 생성 및 필수 sheet/본문 존재
- CSV/XLSX 표준 입력 파싱

SEC/OpenDART/Claude API의 실호출 테스트는 네트워크와 키가 있는 환경에서 별도로 수행해야 합니다. 키 없이 실행한 검증을 API 통과로 표시하지 않습니다.

## 외부 의존성과 남은 제한

- SEC는 API 키가 없지만 식별 가능한 `SEC_USER_AGENT`와 적절한 호출 속도가 필요합니다.
- OpenDART와 Claude는 각 API 키가 필요합니다.
- ACCIONA 같은 유럽 기업은 공식 ESEF/연차보고서 URL 또는 파일이 필요합니다.
- OCR/비전, 공식 기업정보 웹검색, 국가별 상업 데이터 계약, 환율 전 기간 자동수집은 아직 연결하지 않았습니다.
- 연결재무 결과는 특정 계약 법인·자회사 지급능력을 대신하지 않습니다.

# 실제 검증 결과

검증일: 2026-09-23

## 자동 테스트

실행 명령:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

결과: **10개 테스트 모두 통과**

- 누락값, 0·음수 분모 처리
- 음수 자본 시 부채비율 계산 보류
- 대차 불일치·통화 혼합·급격한 변동 경고
- 사용자 수정값과 원래 추출값 동시 보존
- 금융회사 별도 기준 처리
- CSV 단위 정규화와 XLSX wide table 처리
- localhost URL 차단
- 사용자별 SQLite 접근 분리와 파일 해시 중복 방지
- Word·Excel 생성 및 필수 내용 확인

`compileall`도 오류 없이 통과했습니다.

## Streamlit 실행

- `streamlit_app.py` import 성공
- Streamlit `AppTest` 초기화: exception 0건
- 신규 법인 생성 후 화면 렌더링: exception 0건, metric 4개, 입력/검토/검증/산출 구역 확인
- headless 서버 기동: `http://localhost:8511/_stcore/health` 응답 `ok`
- 메인 페이지 HTTP 상태: `200`

## 합성 데이터 전체 흐름

합성 건설·인프라 기업 2개년 자료로 다음을 실행했습니다.

```text
자료 28개 -> 비율 22개 -> 검증 -> 회사평가 -> SQLite 저장/재열기 -> Excel/Word 생성
```

결과:

- Excel: 13,886 bytes
- Word: 38,472 bytes
- 재열기 후 자료 및 비율 수 동일
- 회사평가 예시 최종등급: B2
- 현금흐름 연결 항목이 없는 2개 연도는 `검증 불가` 2건으로 표시됨. 통과로 처리하지 않음.

합성 산출물은 `test-output/`에 생성했으며 실제 공개자료 산출물과 구분됩니다.

## 실제 공개자료 검증

### SEC / Apple Inc.

- 검색어: `AAPL`
- 후보 확인: Apple Inc., CIK `0000320193`, SIC `3571`
- 대상 연도: FY2023-FY2025
- 공통 스키마 변환 결과: 재무값 42개
- 확인 연도: 2023, 2024, 2025
- 수집 오류/경고: 0건

SEC 공식 ticker 목록, submissions, companyfacts 및 기존 10-K 보조 파싱 로직을 사용했습니다.

### ACCIONA, S.A.

공식 2024 연결 연차보고서 URL:

`https://www.acciona.com/content/dam/acciona-global/documentos/inversores/cuentas-anuales/consolidated-annual-accounts-2024.pdf`

- 다운로드 성공: 5,599,901 bytes
- MIME: `application/pdf`
- 문서: 270쪽 텍스트 PDF
- 앞 8쪽 시험 추출: 22,985자
- `ACCIONA` 문자열 확인

전체 재무값의 자동 확정은 수행하지 않았습니다. PDF 숫자 추출은 Claude API 실행 후에도 사용자 검토가 필요한 후보값으로만 저장됩니다.

## 수행하지 못한 검증

- OpenDART 실호출: `DART_API_KEY` 미제공
- Claude API 실호출: `ANTHROPIC_API_KEY` 미제공
- 스캔 PDF OCR/비전: 처리 환경 미연결
- 실제 Streamlit Cloud 배포: GitHub 저장소와 배포 권한 미제공
- 여러 인스턴스의 동시 사용자 부하: 외부 DB/object storage 미연결

위 항목은 통과한 것으로 표시하지 않습니다. 키와 배포 권한이 제공되면 같은 화면 흐름에서 추가 검증할 수 있습니다.

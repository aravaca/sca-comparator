# SCA Comparator

같은 프로젝트를 **BlackDuck, Sparrow, Snyk** 세 도구로 **스캔부터 비교까지 자동으로** 수행한다.

## 해결하는 문제

단순 이름@버전 비교를 넘어 다음과 같은 문제들을 해결한다.

1. **표기 차이** — 같은 패키지인데 표기만 다름 (`SciPy`↔`scipy`, `Python-Markdown`↔`markdown`,
   `vitejs`↔`vite`, `rollup/rollup`↔`rollup` 등) → 검토후보로 선별
2. **버전 다중 매칭** — 같은 이름인데 버전이 여러 개(`vite 7.0.6` vs `7.0.6 + 7.3.5`) →
   이름 단위로 그룹화해 버전 **집합**을 비교
3. **검출 경로 비교** — 컴포넌트를 검출하게 된 출처(매니페스트 경로)를 명확히 할 수 있음
4. **시각화** — 엑셀 요약탭 + HTML 대시보드(벤다이어그램)로 결과를 한눈에 확인

## 동작 흐름

세 도구를 **동시에**(ThreadPoolExecutor) 처리한다. 총 소요시간 ≈ 가장 느린 도구 하나.

| 도구 | 스캔 | 조회 |
| --- | --- | --- |
| **BlackDuck** | Detect(`.jar`) 로컬 실행 → 서버 업로드 | BOM REST API |
| **Sparrow** | 클라이언트 CLI(`create analysis`) | GraphQL |
| **Snyk** | CLI(`snyk test --all-projects`) | (로컬 결과 즉시) |

스캔은 `.env` 의 `*_AUTO_SCAN` 으로 켜고 끈다. **끄면 기존 결과만 조회**하므로,
이미 스캔해 둔 상태라면 비교만 빠르게 돌릴 수 있다.

## 폴더 구조

```
comparator/
├── main.py                 # 진입점: 스캔 → 수집 → 비교 → 출력
├── requirements.txt
├── .env                    # 직접 생성 (.env.example 참고)
├── config/
│   └── aliases.json        # 수동 alias 테이블 (직접 편집)
├── clients/
│   ├── sparrow_client.py   # GraphQL 조회 (컴포넌트 + 자산경로)
│   ├── sparrow_scan.py     # CLI 로 프로젝트 생성 + 분석 실행
│   ├── blackduck_client.py # BOM REST 조회
│   ├── blackduck_scan.py   # Detect 로컬 실행
│   └── snyk_client.py      # CLI 실시간 로컬 스캔
├── core/                   # 비교 핵심 로직
│   ├── compare.py          # 이름 그룹화 + 버전 집합 비교/분류
│   ├── normalize.py        # 이름 정규화 + alias 로딩
│   └── matching.py         # rapidfuzz 로 검토후보 매칭
├── output/
│   ├── report.py           # 콘솔 출력 + JSON 저장
│   ├── matrix_excel.py     # 3자 비교 엑셀
│   └── dashboard_html.py   # HTML 대시보드 (벤다이어그램)
└── docs/
    └── SCA_불일치_기록.md   # 불일치 원인 케이스 정리
```

## 실행

```bash
pip install -r requirements.txt
python main.py
```

결과는 `.env` 의 결과 경로에 날짜별로 쌓인다. **`C:\Exception` 하위로 지정하면 DRM 회피 가능.**

```
C:\Exception\results\2026-08-03\
   multi_150912.xlsx    # 비교 매트릭스 (탭: 매트릭스/버전충돌/자동병합/검토후보/도구별단독/요약)
   multi_150912.json    # 원본 데이터
   multi_150912.html    # 대시보드 (더블클릭 → 브라우저)
```

## 설정 (.env)

**판교 ↔ 본사 이동 시에는 `IP` 한 줄만 바꾸면 된다.** 나머지는 이 값을 참조한다.

자주 바꾸는 값:

| 키 | 설명 |
| --- | --- |
| `SCAN_PATH` | 세 도구가 **공통으로** 스캔할 소스 경로 |
| `PROJECT_KEY` | 스패로우 프로젝트 키 (없으면 자동 생성됨) |

## 주의사항

**중첩 폴더** — `분석대상/jenkins-master/jenkins-master/` 처럼 한 겹 더 들어가 있으면
매니페스트를 못 찾아 0건이 나온다. `SCAN_PATH` 는 **매니페스트가 실제로 있는 폴더**를 가리킬 것.
(블랙덕은 `BLACKDUCK_SEARCH_DEPTH` 로 하위 탐색 깊이를 조절한다)

**스패로우 분석 대기** — 분석이 끝나도 서버가 결과를 반영하는 데 시간이 더 걸린다.
CLI 의 `--sync project` 는 이 시점을 제대로 알려주지 못하고 무한 대기하므로 쓰지 않는다.
대신 `--sync analysis` 로 받고 **스크립트가 직접 재조회**한다(`SPARROW_RETRY_*`).

**devDependencies** — Snyk 는 기본적으로 제외한다. `SNYK_DEV_DEPS=true` 여야 다른 두 도구와
검출 범위가 맞는다(끄면 검출 수가 크게 줄어든다).

**블랙덕 버전 누적** — `BLACKDUCK_VERSION_AUTO=true` 면 실행마다 새 버전이 생겨 기존 BOM 이
보존되지만, 서버에 버전이 계속 쌓인다. 라이선스 한도가 있으면 주기적으로 정리할 것.

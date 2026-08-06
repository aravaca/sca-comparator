# SCA Comparator

동일한 프로젝트의 소스코드를 **BlackDuck, Sparrow, Snyk** 세 SCA 경쟁사 도구로 **스캔부터 결과값 비교까지 자동으로** 수행하는 자동화 소프트웨어. 

## 해결하는 문제

단순한 컴포넌트의 이름과 버전 비교를 넘어 다음과 같은 문제들을 해결한다.

1. **표기 차이** — 같은 패키지인데 표기만 다름 (`SciPy`↔`scipy`, `Python-Markdown`↔`markdown`,
   `vitejs`↔`vite`, `rollup/rollup`↔`rollup` 등) → 일치 컴포넌트로 자동 병합하거나 유사도 점수를 바탕으로 검토후보 선별
2. **버전 다중 매칭** — 같은 이름인데 버전이 여러 개(`vite 7.0.6` vs `7.0.6 + 7.3.5`) →
   이름 단위로 그룹화해 버전 **집합**을 비교
3. **검출 경로 비교** — 컴포넌트를 검출하게 된 출처(매니페스트 경로)를 명확히 추적할 수 있음
4. **시각화** — 엑셀 검출 합계 요약탭 + HTML 대시보드(벤다이어그램)로 결과를 한눈에 확인

## 동작 흐름

세 도구를 **동시에**(네트워크 I/O 작업으로, ThreadPoolExecutor 사용) 처리한다. 스캔을 제외한 결과값 비교 소요시간 ≈ 약 1~2분 내외. 스캔 시간은 소스코드의 용량과 도구에 따라 기하급수적으로 늘어날 수 있음.

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

1. .env 파일 작성

2. 

```bash
pip install -r requirements.txt
python main.py
```

결과는 `.env` 의 `SAVE_PATH`에 날짜폴더/분석시간HHMMSS별로 쌓인다.

```
C:\Exception\results\2026-08-03\
   multi_150912.xlsx    # 비교 매트릭스 (탭: 매트릭스/버전충돌/자동병합/검토후보/도구별단독/요약)
   multi_150912.json    # 원본 데이터
   multi_150912.html    # 대시보드 (더블클릭 → 브라우저)
```

## 테스트 (중요!!!)

비교 로직(`core/`)을 수정할 때 **반드시 테스팅 도구를 먼저 돌려주세요** 엑셀은 로직이 틀려도 정상적으로
생성되기 때문에, 잘못된 결과를 알아채기 어렵습니다.

```bash
pip install pytest
py -m pytest tests/ -q
```

테스트 케이스는 전부 **실제 비교에서 나왔던 문제**를 고정한 것이며 테스팅 해보고 싶은 케이스로 아무렇게나 바꿔주셔도 됩니다.

| 파일 | 지키는 것 |
| --- | --- |
| `test_normalize.py` | 이름/버전 정규화 (`big.js`→`big-js`, npm scope 보존, Go pseudo-version 통일) |
| `test_matching.py` | 붙어야 할 표기차(`feross/buffer`↔`buffer`) vs **붙으면 안 되는 쌍**(`rehype-minify`↔`rehype-stringify`) |
| `test_compare.py` | 버전 집합 판정, 커버리지 분류, **자동병합 오병합 방지** |


## 설정 (.env)

자주 바꾸는 값:

| 키 | 설명 |
| --- | --- |
| `SCAN_PATH` | 세 도구가 **공통으로** 스캔할 소스 경로 |
| `PROJECT_KEY` | 생성될 프로젝트의 키/이름. **비워두면** `<오늘날짜>_<SCAN_PATH 마지막 폴더명>` 으로 자동 생성 (예: `20260804_airllm-main`) |


## 주의사항 / 인수인계 사항

1. **중첩 폴더** — `C:/jenkins-master/jenkins-master-core/다양한 파일들..` 처럼 한 겹 더 들어가 있으면
매니페스트를 못 찾아 0건이 나온다. `SCAN_PATH` 는 **매니페스트가 실제로 있는 가장 가까운 폴더**를 가리킬 것.

2. **스캔 없이 비교만** — 실시간 스캔 (호출) 없이 이미 스캔된 결과를 호출하여 비교만 하고 싶다면
`SPARROW_AUTO_SCAN=false`과 `BLACKDUCK_AUTO_SCAN=false` 설정 

3. **스패로우 분석 대기** — 분석이 끝나도 서버가 결과를 반영하는 데 시간이 더 걸린다.
CLI 의 `--sync project` 는 이 시점을 제대로 알려주지 못하고 무한 대기하므로 쓰지 않는다.
대신 `--sync analysis` 로 받고 **스크립트가 직접 재조회**한다(`SPARROW_RETRY_*`).

4. **스패로우 대용량 프로젝트** - 분석 시 2~3시간 시간 초과되거나 타임아웃되어 스크립트가 종료되고 액셀 결과파일을 확인하는데
스패로우만 텅 비어있는 경우가 있음. 웹훅이 따로 있다고 하니병합하는 것도 괜찮은 아이디어이고 웹에서 먼저 분석을 진행한 뒤 
`SPARROW_AUTO_SCAN=false` 설정 후 graphql 컴포넌트 조회만 날리는 것도 대안책. `SPARROW_SCAN_TIMEOUT=10800`(단위:초) 를 무작정
늘려보는 것도 괜찮은 선택지.  

5. **devDependencies** — Snyk CLI 분석은 기본적으로 제외한다. `SNYK_DEV_DEPS=true` 여야 다른 두 도구와
검출 범위가 맞는다(끄면 검출 수가 크게 줄어든다).

6. **블랙덕 버전 누적** — `BLACKDUCK_VERSION_AUTO=true` 면 실행마다 새 버전이 생겨 기존 BOM 이
보존되지만, 서버에 버전이 계속 쌓인다. 라이선스 한도가 있으면 주기적으로 정리할 것.

7. **테스팅 도구** — 본 프로젝트의 코드 정상 작동여부를 테스트 할 수 있는 테스팅 도구를 생성했으니 위에 **테스트** 항목 확인.
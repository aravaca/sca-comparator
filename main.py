import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import config.env  # noqa: F401  (.env 로드 + PROJECT_KEY 자동생성)
from clients.sparrow_client import get_access_token, fetch_sparrow_components, PROJ_KEY
from clients.sparrow_scan import run_sparrow_scan
from clients.blackduck_client import get_blackduck_components
from clients.blackduck_scan import run_blackduck_scan
from clients.snyk_client import fetch_snyk_components, SNYK_PROJECT_PATH
from core.compare import compare_multi
from core.matching import find_review_candidates_multi
from output.report import print_report_multi, save_json_multi
from output.matrix_excel import export_matrix_excel
from output.dashboard_html import export_dashboard_html

# RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
RESULTS_DIR = os.getenv("SAVE_PATH")

# 스캔 직후 스패로우가 0건일 때 재조회 횟수/간격 (결과 반영 지연 대비)
SPARROW_RETRY_COUNT = int(os.getenv("SPARROW_RETRY_COUNT", "6"))
SPARROW_RETRY_WAIT = int(os.getenv("SPARROW_RETRY_WAIT", "20"))


# 세 도구는 서로 독립적이고 전부 I/O 대기(네트워크/subprocess)라 GIL 영향 없이
# 스레드로 동시에 돌릴 수 있다. 총 시간 ≈ 가장 느린 도구 하나.
def _collect_sparrow() -> list[dict]:
    # 스캔(CLI) → 조회 순서. 스캔을 건너뛰거나 실패해도 기존 분석결과 조회로 이어진다.
    scanned = run_sparrow_scan()
    token = get_access_token()
    components = fetch_sparrow_components(PROJ_KEY, token)

    # 분석이 끝나도 서버가 결과를 프로젝트 데이터에 반영하는 데 시간이 더 걸린다.
    # CLI 의 --sync project 는 이 시점을 제대로 알려주지 못하고 무한 대기해서
    # (서버는 Completed 인데 CLI 만 대기), 완료 판단을 우리가 직접 한다.
    if scanned and not components:
        total_wait = SPARROW_RETRY_COUNT * SPARROW_RETRY_WAIT
        print(f"  스패로우 0건 - 결과 반영을 기다립니다 (최대 {total_wait // 60}분 "
              f"{total_wait % 60}초, {SPARROW_RETRY_WAIT}초 간격)")
        for attempt in range(1, SPARROW_RETRY_COUNT + 1):
            time.sleep(SPARROW_RETRY_WAIT)
            components = fetch_sparrow_components(PROJ_KEY, token)
            if components:
                print(f"  스패로우 {len(components)}건 확보 "
                      f"(재조회 {attempt}회째, {attempt * SPARROW_RETRY_WAIT}초 대기)\n")
                break
            print(f"     아직 0건... {attempt}/{SPARROW_RETRY_COUNT}")
        else:
            print("  스패로우가 계속 0건입니다. 웹에서 분석 완료/컴포넌트 검출을 확인해 주세요.\n")

    return components


def _collect_blackduck() -> list[dict]:
    # 스캔(Detect) → 조회 를 한 스레드에서 순차로. 그 사이 스패로우·Snyk 는 병렬로 진행된다.
    # 반환된 버전명을 그대로 조회에 넘겨야 '이번에 스캔한 버전' 을 정확히 읽는다.
    # 스캔을 건너뛰거나 실패하면 None 이 와서 기존처럼 첫 번째 버전을 조회한다.
    scanned_version = run_blackduck_scan()
    return get_blackduck_components(scanned_version)


def _collect_snyk() -> list[dict]:
    if not SNYK_PROJECT_PATH:
        print("  SNYK_PROJECT_PATH 미설정 — Snyk 제외하고 진행합니다.")
        return []
    return fetch_snyk_components(SNYK_PROJECT_PATH)


def main():
    # 1) 세 SCA 도구에서 컴포넌트 '동시' 수집 (I/O-bound → ThreadPoolExecutor)
    collectors = {
        "blackduck": _collect_blackduck,
        "sparrow": _collect_sparrow,
        "snyk": _collect_snyk,
    }
    collected: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=len(collectors)) as ex:
        futures = {name: ex.submit(fn) for name, fn in collectors.items()}
        for name, fut in futures.items():
            try:
                collected[name] = fut.result() or []
            except Exception as e:
                print(f"  {name} 컴포넌트 수집 실패 — 제외하고 진행합니다: {e}")
                collected[name] = []

    # 2) 이름 정규화 + 버전 그룹화로 3자(가능한 도구만큼) 비교 (표시 순서 고정)
    components_by_source = {
        "blackduck": collected["blackduck"],
        "sparrow": collected["sparrow"],
    }
    if collected.get("snyk"):
        components_by_source["snyk"] = collected["snyk"]

    result = compare_multi(components_by_source)
    if result.get("auto_merged"):
        print(f"  자동병합(이름표기만 다르고 버전 동일) {len(result['auto_merged'])}건\n")

    # 2-1) 이름 표기만 달라 '단독'으로 잡힌 같은 패키지 후보 추출 (자동 병합 X)
    candidates = find_review_candidates_multi(result)
    print(f"  검토후보(이름 표기차 의심) {len(candidates)}건\n")

    # 3) 출력: 콘솔 + JSON + Excel(매트릭스)
    #    날짜별 폴더(YYYY-MM-DD) 안에 시간(HHMMSS)으로 파일명을 찍어 덮어쓰지 않고 누적 저장
    now = datetime.now()
    day_dir = os.path.join(RESULTS_DIR, now.strftime("%Y-%m-%d"))
    os.makedirs(day_dir, exist_ok=True)
    stamp = now.strftime("%H%M%S")
    base = f"multi_{stamp}"

    print_report_multi(result)
    save_json_multi(result, os.path.join(day_dir, f"{base}.json"))
    export_matrix_excel(result, candidates, os.path.join(day_dir, f"{base}.xlsx"))
    export_dashboard_html(result, candidates, os.path.join(day_dir, f"{base}.html"))


if __name__ == "__main__":
    main()

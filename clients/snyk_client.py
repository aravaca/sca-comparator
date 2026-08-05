import json
import os
import shutil
import subprocess

import requests
import urllib3
import config.env  # noqa: F401  (.env 로드 + PROJECT_KEY 자동생성)

# 사내망 자체 인증서 사용 시 발생하는 SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Snyk REST API 기본값: https://api.snyk.io (EU/AU 리전은 api.eu.snyk.io 등)
SNYK_API_URL = os.getenv("SNYK_API_URL", "https://api.snyk.io").rstrip("/")
SNYK_TOKEN = os.getenv("SNYK_TOKEN")
SNYK_ORG_ID = os.getenv("SNYK_ORG_ID")
# REST API는 날짜 기반 version 쿼리 파라미터가 필수 (미지정 시 최신 GA 사용)
SNYK_API_VERSION = os.getenv("SNYK_API_VERSION", "2024-10-15")
# 스캔할 프로젝트 경로 (BlackDuck/Sparrow가 스캔한 것과 같은 소스여야 비교가 의미있음)
# 스캔 대상 경로는 세 도구가 '같은 소스' 를 봐야 비교가 성립하므로 SCAN_PATH 하나로 통일.
# (예전 설정과의 호환을 위해 SNYK_PROJECT_PATH 도 계속 인정)
SNYK_PROJECT_PATH = os.getenv("SCAN_PATH") or os.getenv("SNYK_PROJECT_PATH")
# devDependencies 포함 여부. snyk 은 기본적으로 prod 의존성만 스캔하는데,
# Sparrow/BlackDuck 은 dev 까지 포함하므로 공정 비교를 위해 기본 true.
SNYK_DEV_DEPS = os.getenv("SNYK_DEV_DEPS", "true").lower() in ("1", "true", "yes")

# ─────────────────────────────────────────────────────────────────────────────
# [중요] REST API의 org 데이터 엔드포인트(/rest/orgs/{id}/projects, .../sbom 등)는
# Free/하위 플랜에서 403 Forbidden 으로 막혀 있음 (Enterprise 전용).
# /rest/self·/rest/orgs 같은 신원 엔드포인트만 열려 있어 연결 확인용으로만 씀.
# 따라서 실제 컴포넌트 수집은 로컬 `snyk` CLI( snyk test --print-graph )로 한다.
# ─────────────────────────────────────────────────────────────────────────────

# Snyk는 API 토큰을 헤더에 "token <TOKEN>" 형식으로 그대로 넣음 (Bearer 발급 절차 없음)
def _auth_headers() -> dict:
    return {
        "Authorization": f"token {SNYK_TOKEN}",
        "Accept": "application/vnd.api+json",
    }


def check_connection() -> bool:
    """/rest/self 를 호출해 토큰이 유효한지(연결 200) 확인한다."""
    print(" 스닉(Snyk) 서버 연결 확인 중...")

    if not SNYK_TOKEN:
        print("  SNYK_TOKEN 이 .env 에 설정되어 있지 않습니다.")
        return False

    url = f"{SNYK_API_URL}/rest/self"
    resp = requests.get(
        url,
        headers=_auth_headers(),
        params={"version": SNYK_API_VERSION},
        verify=False,
    )

    print(f" 요청 URL: {resp.url}")
    print(f" 상태 코드: {resp.status_code}")

    if resp.status_code != 200:
        print(f" 연결 실패! 응답: {resp.text[:300]}")
        return False

    # self 응답에서 사용자 정보 살짝 찍어보기 (테스트용)
    attrs = resp.json().get("data", {}).get("attributes", {})
    who = attrs.get("username") or attrs.get("email") or attrs.get("name") or "(이름 없음)"
    print(f" 연결 성공! 인증된 사용자: {who}\n")
    return True


def get_orgs() -> list[dict]:
    """토큰으로 접근 가능한 조직(org) 목록 조회 — SNYK_ORG_ID 찾을 때 사용."""
    print(" 스닉 조직(org) 목록 조회 중...")
    url = f"{SNYK_API_URL}/rest/orgs"
    resp = requests.get(
        url,
        headers=_auth_headers(),
        params={"version": SNYK_API_VERSION, "limit": 100},
        verify=False,
    )
    if resp.status_code != 200:
        print(f" 조직 조회 실패! 상태 코드: {resp.status_code}\n응답: {resp.text[:300]}")
        return []

    orgs = resp.json().get("data", [])
    print(f" 총 {len(orgs)}개의 조직을 찾았습니다.")
    for org in orgs:
        name = org.get("attributes", {}).get("name", "(이름 없음)")
        print(f"   - {name}  (id: {org.get('id')})")
    return orgs


def get_projects(org_id: str) -> list[dict]:
    """org 하위 프로젝트 목록 조회 — SNYK_PROJECT_ID 찾을 때 사용.

    Snyk의 project는 매니페스트 파일 단위(예: package.json, pom.xml)라
    한 리포(target)에 여러 개가 있을 수 있음. target 이름으로 묶어서 보여줌.
    """
    print(f" 스닉 프로젝트 목록 조회 중... (org: {org_id})")

    all_projects = []
    url = f"{SNYK_API_URL}/rest/orgs/{org_id}/projects"
    params = {"version": SNYK_API_VERSION, "limit": 100}

    # REST API는 커서 기반 페이지네이션 (links.next 따라가기)
    while url:
        resp = requests.get(url, headers=_auth_headers(), params=params, verify=False)
        if resp.status_code != 200:
            print(f" 프로젝트 조회 실패! 상태 코드: {resp.status_code}\n응답: {resp.text[:300]}")
            return []

        body = resp.json()
        all_projects.extend(body.get("data", []))

        # 다음 페이지: links.next 는 이미 version/cursor 포함된 상대경로라 params 비움
        next_link = body.get("links", {}).get("next")
        if next_link:
            url = next_link if next_link.startswith("http") else f"{SNYK_API_URL}{next_link}"
            params = None
        else:
            url = None

    print(f" 총 {len(all_projects)}개의 프로젝트를 찾았습니다.")
    for proj in all_projects:
        attrs = proj.get("attributes", {})
        name = attrs.get("name", "(이름 없음)")
        ptype = attrs.get("type", "-")  # npm, maven, ...
        print(f"   - {name}  [{ptype}]  (id: {proj.get('id')})")
    return all_projects


def _snyk_cli_cmd() -> list[str]:
    """snyk 실행 커맨드 결정: PATH에 snyk 있으면 그걸, 없으면 npx로 폴백."""
    exe = shutil.which("snyk")
    if exe:
        return [exe]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "snyk"]
    raise RuntimeError(
        " snyk CLI 를 찾을 수 없습니다. `npm i -g snyk` 로 설치하거나 Node(npx)를 준비하세요."
    )


def _parse_depgraph_block(block_json: str) -> list[dict]:
    """단일 depGraph JSON 문자열 -> [{name, version, purl}] (루트 노드 제외)."""
    graph = json.loads(block_json)
    pm = graph.get("pkgManager", {}).get("name", "unknown")

    g = graph.get("graph", {})
    root_node_id = g.get("rootNodeId")
    root_pkg_id = next(
        (n.get("pkgId") for n in g.get("nodes", []) if n.get("nodeId") == root_node_id),
        None,
    )

    out = []
    for p in graph.get("pkgs", []):
        if p.get("id") == root_pkg_id:
            continue
        info = p.get("info", {})
        name, version = info.get("name"), info.get("version")
        if not name or not version:
            continue
        out.append({
            "name": name,
            "version": version,
            "purl": f"pkg:{pm}/{name}@{version}",
        })
    return out


def _iter_depgraph_blocks(out: str):
    """--print-graph 출력에서 (json, target) 블록들을 모두 순회한다.

    형식(매니페스트마다 반복): DepGraph data: / <json> / DepGraph target: / <file> / DepGraph end
    """
    lines = out.splitlines()
    idx = 0
    while True:
        try:
            i = lines.index("DepGraph data:", idx)
            j = lines.index("DepGraph target:", i)
            k = lines.index("DepGraph end", j)
        except ValueError:
            return
        target = lines[j + 1] if j + 1 < len(lines) else ""
        yield "\n".join(lines[i + 1:j]), target
        idx = k + 1


def fetch_snyk_components(project_path: str | None = None) -> list[dict]:
    """
    `snyk test --all-projects --print-graph` 로 프로젝트의 **모든 매니페스트**
    (예: go.mod + docs/package.json)를 스캔해 [{name, version, purl}] 로 반환한다.

    주의: 기본 `snyk test` 는 루트에서 감지된 매니페스트 하나만 스캔하므로,
    하이브리드(예: Go + npm) 프로젝트에서는 --all-projects 가 필수.
    Free 플랜에서도 동작하며, SNYK_TOKEN 은 CLI 가 환경변수에서 자동으로 읽는다.
    """
    path = project_path or SNYK_PROJECT_PATH
    if not path:
        raise RuntimeError(" 스캔할 경로가 없습니다. SNYK_PROJECT_PATH 를 .env 에 설정하세요.")

    is_file = os.path.isfile(path)
    scan_target = ["--file=" + path] if is_file else ["--all-projects"]
    cwd = os.path.dirname(path) or "." if is_file else path
    print(f" 스닉 컴포넌트 추출 중... (snyk test {' '.join(scan_target)} --print-graph, path: {path})")

    cmd = _snyk_cli_cmd() + ["test", *scan_target, "--print-graph"]
    if SNYK_DEV_DEPS:
        cmd.append("--dev")  # devDependencies 포함 (Sparrow/BlackDuck 과 범위 맞춤)
    # snyk test 는 취약점이 있으면 exit code 1 을 내므로 returncode 로 실패 판정하지 않는다.
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
    )
    out = proc.stdout or ""
    if "DepGraph data:" not in out:
        raise RuntimeError(
            f" snyk 그래프 추출 실패 (exit={proc.returncode})\n"
            f"stdout: {out[:300]}\nstderr: {(proc.stderr or '')[:300]}"
        )

    # 여러 매니페스트의 그래프를 모두 파싱해 병합 (name@version 기준 중복 제거 위해 dict 사용).
    # 각 컴포넌트가 나온 매니페스트(=자산 경로)를 file_paths 로 함께 수집한다.
    merged: dict[str, dict] = {}
    targets = []
    for block_json, target in _iter_depgraph_blocks(out):
        targets.append(target)
        tnorm = target.replace("\\", "/")  # 경로 구분자 통일
        for c in _parse_depgraph_block(block_json):
            key = f"{c['name']}@{c['version']}"
            if key in merged:
                merged[key]["file_paths"].add(tnorm)
            else:
                c["file_paths"] = {tnorm}
                merged[key] = c

    components = []
    for c in merged.values():
        c["file_paths"] = sorted(c["file_paths"])  # set -> 정렬된 list
        components.append(c)
    print(f" 스닉 총 {len(components)}개의 컴포넌트를 찾았습니다! "
          f"(매니페스트 {len(targets)}개: {', '.join(targets) or '-'})\n")
    return components


if __name__ == "__main__":
    # 1) REST 연결 확인 (신원 엔드포인트 — 플랜 무관하게 200)
    check_connection()
    num_peeks = 10
    # 2) 실제 컴포넌트 수집은 CLI 로 (org 데이터 REST 는 Free 플랜에서 403)
    if SNYK_PROJECT_PATH:
        comps = fetch_snyk_components(SNYK_PROJECT_PATH)
        for c in comps[:num_peeks]:
            print(f"   - {c['name']}@{c['version']}  ({c['purl']})")
        if len(comps) > num_peeks:
            print(f"   ... 외 {len(comps) - num_peeks}개")
    else:
        print("  SNYK_PROJECT_PATH 를 .env 에 설정하면 컴포넌트 목록을 추출합니다.")

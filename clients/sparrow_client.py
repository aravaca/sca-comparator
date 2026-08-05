import requests
import urllib3
import os
from concurrent.futures import ThreadPoolExecutor
import config.env  # noqa: F401  (.env 로드 + PROJECT_KEY 자동생성)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

username = os.getenv("USERNAME1")
pwd = os.getenv("PASSWORD1")
IP = os.getenv("IP")
PORT = os.getenv("PORT")
PROJ_KEY = os.getenv("PROJECT_KEY")

# 서버 주소는 스킴까지 설정으로 뺀다.
#  - 평소 서버(192.168.70.154:10880) 는 https, 판교 분사 서버(172.30.1.28:10610) 는 http.
#  - sparrow_scan.py(스캔) 와 반드시 같은 서버를 가리켜야 한다. 다르면 'A 에 스캔, B 에서 조회'.
SCHEME = os.getenv("SPARROW_SCHEME", "http")
BASE_URL = os.getenv("SPARROW_BASE_URL") or f"{SCHEME}://{IP}:{PORT}"

# 자산(파일) 경로 수집 여부. 컴포넌트 1개당 쿼리 1번이라 개수가 많으면 느려진다.
FETCH_PATHS = os.getenv("SPARROW_FETCH_PATHS", "true").lower() in ("1", "true", "yes")
PATH_WORKERS = int(os.getenv("SPARROW_PATH_WORKERS", "8"))


# 컴포넌트별 자산경로는 목록 쿼리에 없다(SCAComponentDto 에 경로 필드 자체가 없음).
# selectSCAComponentTargetsById 로 컴포넌트마다 따로 조회해야 한다.
_TARGETS_QUERY = """
query T($projectKey: String!, $workId: Long!, $componentId: Long!, $filter: TargetFilter!) {
  selectSCAComponentTargetsById(projectKey: $projectKey, workId: $workId,
                                componentId: $componentId, filter: $filter) {
    total
    list { name path }
  }
}
"""


def _fetch_paths_for(component: dict, project_key: str, access_token: str) -> list[str]:
    """컴포넌트 1개의 자산(파일) 경로 목록. 실패해도 비교가 멈추지 않도록 [] 로 흡수."""
    work_id, comp_id = component.get("workId"), component.get("id")
    if work_id is None or comp_id is None:
        return []
    try:
        resp = requests.post(
            f"{BASE_URL}/graphql",
            json={
                "query": _TARGETS_QUERY,
                "variables": {
                    "projectKey": project_key,
                    "workId": work_id,
                    "componentId": comp_id,
                    "filter": {"page": {"page": 0, "size": 50}},
                },
            },
            headers={"Authorization": f"Bearer {access_token}"},
            verify=False,
            timeout=30,
        )
        body = resp.json()
        if resp.status_code != 200 or body.get("errors"):
            return []
        items = body["data"]["selectSCAComponentTargetsById"]["list"]
        # 중복 제거하면서 순서 유지
        return list(dict.fromkeys(t["path"] for t in items if t.get("path")))
    except Exception:
        return []


def attach_file_paths(components: list[dict], project_key: str, access_token: str) -> None:
    """각 컴포넌트에 file_paths 를 채운다(제자리 수정). I/O 대기라 스레드로 병렬 처리."""
    if not components:
        return
    print(f" 스패로우 자산경로 수집 중... ({len(components)}건)")
    with ThreadPoolExecutor(max_workers=PATH_WORKERS) as ex:
        results = ex.map(lambda c: _fetch_paths_for(c, project_key, access_token), components)
        for comp, paths in zip(components, results):
            comp["file_paths"] = paths
    found = sum(1 for c in components if c.get("file_paths"))
    print(f" 자산경로 {found}/{len(components)}건 수집 완료\n")

def get_access_token() -> str:
    print(" 스패로우(엔터프라이즈) 서버 로그인 중...")

    # [1단계] 공개키 발급
    pubkey_response = requests.get(
        f"{BASE_URL}/api/1.0/auth/publicKey",
        verify=False,
    )
    if pubkey_response.status_code != 200:
        raise RuntimeError(
            f" 공개키 조회 실패! 상태 코드: {pubkey_response.status_code}\n응답: {pubkey_response.text[:300]}"
        )
    public_key_b64 = pubkey_response.text.strip()

    # [2단계] 비밀번호 암호화
    encrypt_response = requests.post(
        f"{BASE_URL}/test/encrypt/user-password",
        json={"password": pwd, "key": public_key_b64},
        verify=False,
    )
    if encrypt_response.status_code != 200:
        raise RuntimeError(
            f" 비밀번호 암호화 실패! 상태 코드: {encrypt_response.status_code}\n응답: {encrypt_response.text[:300]}"
        )
    encrypt_pwd = encrypt_response.text.strip()

    # [3단계] 토큰 발급
    token_response = requests.post(
        f"{BASE_URL}/api/1.0/auth",
        json={"username": username, "password": encrypt_pwd},
        verify=False,
    )
    if token_response.status_code != 200:
        raise RuntimeError(
            f" 로그인 실패! 상태 코드: {token_response.status_code}\n응답: {token_response.text[:300]}"
        )

    access_token = token_response.json().get("access")
    if not access_token:
        raise RuntimeError(
            f" 로그인 실패! access 토큰 없음. 응답: {token_response.text[:300]}"
        )

    print(" 스패로우 로그인 성공! (access 토큰 획득)\n")
    return access_token

#컴포넌트 목록 조회 LIST
def fetch_sparrow_components(project_key: str, access_token: str) -> list[dict]:
    graphql_query = """
    query selectSCAComponents($projectKey: String!, $filter: SCAComponentFilter!) {
    selectSCAComponents(projectKey: $projectKey, filter: $filter) {
        page
        size
        total
        filtered
        list {
        id
        name
        version
        originalVersionMessage
        versionId
        websiteLinks
        repositoryName
        repositoryUri
        publishedDate
        description
        licenses { shortId }
        provider
        copyrights
        osInfo
        osVersion
        architecture
        packageManager
        excludedStatus
        purl
        workId
        managers {
            id
            userId
            name
            email
            avatar { id objectPath extension }
        }
        }
    }
    }
    """

    variables = {
        # 인자로 받은 키를 쓴다(예전엔 전역 PROJ_KEY 를 써서 인자가 무시됐다)
        "projectKey": project_key,
        "filter": {
            "page": {"page": 0, "size": 20},
            "sorts": [{"key": "name", "order": "asc"}],
        },
    }

    print(" 스패로우 컴포넌트 리스트 추출 중...")
    all_components = []
    page = 0

    while True:
        variables["filter"]["page"]["page"] = page
        resp = requests.post(
            f"{BASE_URL}/graphql",
            json={"query": graphql_query, "variables": variables},
            headers={"Authorization": f"Bearer {access_token}"},
            verify=False,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f" 컴포넌트 조회 실패! 상태 코드: {resp.status_code}\n응답: {resp.text[:300]}"
            )

        body = resp.json()
        if body.get("errors"):
            raise RuntimeError(f" GraphQL 에러: {body['errors']}")

        data = body["data"]["selectSCAComponents"]
        all_components.extend(data["list"])
        if len(all_components) >= data["total"]:
            break
        page += 1

    print(f" 스패로우 총 {len(all_components)}개의 컴포넌트를 찾았습니다!\n")

    # 자산경로는 목록 응답에 없어서 컴포넌트별로 따로 붙여야 한다.
    if FETCH_PATHS:
        attach_file_paths(all_components, project_key, access_token)

    # return type is list of dicts, each dict contains component details like name, version, purl, etc.
    return all_components

if __name__ == "__main__":
    token = get_access_token()
    components = fetch_sparrow_components(PROJ_KEY, token)
    # print(components[:5])  # 첫 번째 컴포넌트 샘플 출력
    #여기서 건질만한게 name 버전 purl 정도? c.get()으로 호출 가능 
    # print(f"받은 컴포넌트 수: {len(components)}")
    # print(type(components))
    sparrow_keys = {f"{c['name']}@{c['version']}" for c in components}
    # print(len(sparrow_keys))
    # print(list(sparrow_keys)[:5])  # 샘플 확인
    # 이름@버전 형태의 리스트로 출력
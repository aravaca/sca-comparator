import re
import requests
import urllib3
import os
import sys
from dotenv import load_dotenv

# 사내망 자체 인증서 사용 시 발생하는 SSL 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

BLACKDUCK_URL = os.getenv("BLACKDUCK_URL")
API_TOKEN = os.getenv("BLACKDUCK_API_TOKEN")
PROJECT_NAME = os.getenv("BLACKDUCK_PROJECT_KEY")
# 컴포넌트별 '선언 매니페스트' 경로까지 수집할지 (BOM 전체 matched-files 1회 페이지네이션)
FETCH_PATHS = os.getenv("BLACKDUCK_FETCH_PATHS", "true").lower() in ("1", "true", "yes")
# ==========================================

# 컴포넌트 식별 키: /components/{id}/versions/{id} (origin 은 무시해 매칭 안정화)
_COMP_KEY = re.compile(r"/components/[0-9a-fA-F-]+/versions/[0-9a-fA-F-]+")


def _comp_key(url: str) -> str:
    """matched-files 의 전역 component URL 과 BOM 컴포넌트 href 를 같은 키로 맞춘다."""
    m = _COMP_KEY.search(url or "")
    return m.group(0) if m else (url or "")


def _manifest_root(declared_path: str) -> str | None:
    """declaredComponentPath 에서 '선언 매니페스트'(프로젝트경로 + 패키지매니저)만 뽑는다.

      'no-mistakes-docs/docs/-npm/mermaid/11.14.0/...'  ->  'no-mistakes-docs/docs (npm)'
    (뒤의 의존성 체인은 버리고, Snyk 의 매니페스트 경로와 같은 수준으로 맞춤)
    """
    if not declared_path:
        return None
    idx = declared_path.find("/-")
    if idx == -1:
        return declared_path
    root = declared_path[:idx]
    pkgmgr = declared_path[idx + 2:].split("/", 1)[0]
    return f"{root} ({pkgmgr})" if pkgmgr else root


def _fetch_declared_path_map(version_href: str, headers: dict) -> dict[str, set]:
    """BOM 전체 matched-files 를 페이지네이션으로 받아 컴포넌트키 -> {선언 매니페스트} 맵 생성."""
    path_map: dict[str, set] = {}
    PAGE = 100
    offset = 0
    while True:
        r = requests.get(f"{version_href}/matched-files", headers=headers,
                         params={"limit": PAGE, "offset": offset}, verify=False)
        if r.status_code != 200:
            break
        data = r.json()
        items = data.get("items", [])
        for it in items:
            root = _manifest_root(it.get("declaredComponentPath"))
            if not root:
                continue
            for m in it.get("matches", []):
                key = _comp_key(m.get("component"))
                if key:
                    path_map.setdefault(key, set()).add(root)
        offset += PAGE
        if offset >= data.get("totalCount", 0) or not items:
            break
    return path_map


def get_blackduck_components(version_name: str | None = None):
    """version_name 을 주면 그 버전의 BOM 을, 없으면 기존처럼 첫 번째 버전을 조회한다.

    (자동스캔이 회차별 새 버전을 만들면 versions[0] 이 그 버전이라는 보장이 없어서 필요)
    """
    print(" 블랙덕 API 자동화 스크립트 시작...\n")

    # [1단계] API 토큰으로 Bearer 토큰(임시 출입증) 발급받기
    print(" 블랙덕 서버에 로그인 중...")
    auth_url = f"{BLACKDUCK_URL}/api/tokens/authenticate"
    auth_headers = {"Authorization": f"token {API_TOKEN}"}
    
    try:
        auth_resp = requests.post(auth_url, headers=auth_headers, verify=False)
    except requests.RequestException as e:
        print(f"오류 - 아마도 BLUEMAX VPN을 키지 않았거나 서버가 다운됨: {e}")
        sys.exit(1) #main.py를 종료 



    if auth_resp.status_code != 200:
        print(f" 로그인 실패! 상태 코드: {auth_resp.status_code}\n응답: {auth_resp.text[:300]}")
        return

    bearer_token = auth_resp.json().get("bearerToken")
    if not bearer_token:
        print(f"  로그인 실패! bearerToken 없음. 응답: {auth_resp.text[:300]}")
        return

    base_headers = {"Authorization": f"Bearer {bearer_token}"}
    print(" 로그인 성공! (Bearer 토큰 획득)\n")

    # [2단계] 프로젝트 이름으로 검색해서 '프로젝트 URL' 찾기
    print(f" '{PROJECT_NAME}' 프로젝트 검색 중...")
    proj_url = f"{BLACKDUCK_URL}/api/projects?q=name:{PROJECT_NAME}"
    proj_resp = requests.get(proj_url, headers=base_headers, verify=False)
    if proj_resp.status_code != 200:
        print(f" 프로젝트 검색 실패! 상태 코드: {proj_resp.status_code}\n응답: {proj_resp.text[:300]}")
        return

    projects = proj_resp.json().get("items", [])
    if not projects:
        print(" 프로젝트를 찾을 수 없습니다. 이름을 확인해 주세요.")
        return

    project_href = projects[0]["_meta"]["href"]
    print(" 프로젝트 찾기 성공!\n")

    # [3단계] 해당 프로젝트의 '최신 버전 URL' 찾기
    print(" 프로젝트 버전 검색 중...")
    vers_resp = requests.get(f"{project_href}/versions", headers=base_headers, verify=False)
    if vers_resp.status_code != 200:
        print(f" 버전 검색 실패! 상태 코드: {vers_resp.status_code}\n응답: {vers_resp.text[:300]}")
        return

    versions = vers_resp.json().get("items", [])
    if not versions:
        print(" 프로젝트에 등록된 버전이 없습니다.")
        return

    if version_name:
        matched = [v for v in versions if v.get("versionName") == version_name]
        if not matched:
            names = ", ".join(v.get("versionName", "?") for v in versions[:10])
            print(f" 버전 '{version_name}' 을 찾지 못했습니다. (서버 버전들: {names})")
            return
        version_href = matched[0]["_meta"]["href"]
        print(f" 버전 찾기 성공! ('{version_name}')\n")
    else:
        version_href = versions[0]["_meta"]["href"]
        print(f" 버전 찾기 성공! ('{versions[0].get('versionName')}')\n")

    # [4단계] BOM 컴포넌트 리스트 가져오기 (페이지네이션)
    print(" 컴포넌트 리스트(이름, 버전, purl) 추출 중...")
    bom_headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Accept": "application/vnd.blackducksoftware.bill-of-materials-6+json"
    }

    PAGE_SIZE = 100
    components = []
    offset = 0
    while True:
        bom_url = f"{version_href}/components?limit={PAGE_SIZE}&offset={offset}"
        bom_resp = requests.get(bom_url, headers=bom_headers, verify=False)
        if bom_resp.status_code != 200:
            print(f" BOM 조회 실패! 상태 코드: {bom_resp.status_code}\n응답: {bom_resp.text[:300]}")
            return
        data = bom_resp.json()
        components.extend(data.get("items", []))
        if len(components) >= data.get("totalCount", 0):
            break
        offset += PAGE_SIZE
    
    print(f" 총 {len(components)}개의 컴포넌트를 찾았습니다!\n")

    # [5단계] '선언 매니페스트' 경로 수집 — BOM 전체 matched-files 1회(페이지네이션)로 매핑.
    #         declaredComponentPath 에서 프로젝트경로+패키지매니저만 뽑아 Snyk 와 같은 수준으로 맞춤.
    if FETCH_PATHS:
        print(" 선언 매니페스트 경로 수집 중... (BOM 전체 matched-files)")
        path_map = _fetch_declared_path_map(version_href, bom_headers)
        for comp in components:
            key = _comp_key(comp.get("_meta", {}).get("href", ""))
            comp["file_paths"] = sorted(path_map.get(key, []))
        matched = sum(1 for c in components if c["file_paths"])
        print(f" 경로 매핑 완료! ({matched}/{len(components)}개 컴포넌트에 매니페스트 연결)\n")
    else:
        for comp in components:
            comp["file_paths"] = []

    print("-" * 50)
    
    # 추출한 데이터 출력해보기 (테스트용)
    for comp in components:
        name = comp.get("componentName", "이름 없음")
        version = comp.get("componentVersionName", "버전 없음")
        
        # purl은 origins 배열 안에 숨어있으므로 파싱이 필요함
        origins = comp.get("origins", [])
        purl = "purl 없음"
        if origins and len(origins) > 0:
            # 첫 번째 origin에서 purl 추출
            purl = origins[0].get("purl", "purl 없음")
            
        # print(f"[{name}] | 버젼: {version} | PURL: {purl}")
        
    print("-" * 50)
    print(" 테스트 스크립트 실행 완료!")

    return components  # 컴포넌트 리스트 반환

# 함수 실행
if __name__ == "__main__":
    get_blackduck_components()

    
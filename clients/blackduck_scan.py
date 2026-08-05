"""블랙덕 Detect 를 로컬에서 직접 실행해 스캔까지 자동화.

기존 blackduck_client.py 는 '이미 서버에 올라간 BOM 을 조회'만 했다.
이 모듈은 그 앞단(= 사람이 UI 로 하던 스캔)을 대신 실행한다.
"""

import os
import subprocess
import sys
from datetime import datetime
import config.env  # noqa: F401  (.env 로드 + PROJECT_KEY 자동생성)

BLACKDUCK_URL = os.getenv("BLACKDUCK_URL")
API_TOKEN = os.getenv("BLACKDUCK_API_TOKEN")
PROJECT_NAME = os.getenv("BLACKDUCK_PROJECT_KEY")

# 버전명 정책
#  - BLACKDUCK_PROJECT_VERSION 을 지정하면 그 값을 그대로 쓴다(고정).
#  - 비우고 BLACKDUCK_VERSION_AUTO=true 면 실행마다 '<접두사>-YYYYMMDD-HHMMSS' 를 새로 만든다.
#    -> 기존 BOM(2.576-SNAPSHOT)을 건드리지 않고, 회차별로 따로 남아 서로 덮어쓰지 않는다.
#  - 둘 다 비우면 Detect 가 매니페스트에서 자동추출(= 기존 2.576-SNAPSHOT 을 덮어씀).
PROJECT_VERSION = os.getenv("BLACKDUCK_PROJECT_VERSION", "").strip()
VERSION_AUTO = os.getenv("BLACKDUCK_VERSION_AUTO", "false").lower() in ("1", "true", "yes")
VERSION_PREFIX = os.getenv("BLACKDUCK_VERSION_PREFIX", "auto").strip() or "auto"

# Detect jar 경로 (설치 폴더 그대로)
DETECT_JAR = os.getenv(
    "BLACKDUCK_DETECT_JAR",
    r"C:\Users\chschj_global\AppData\Roaming\Black Duck Detect\detect\10.7.0\detect-10.7.0.jar",
)
# 스캔 대상 소스 경로. 세 도구가 같은 소스를 봐야 하므로 SCAN_PATH 하나로 통일.
# (BLACKDUCK_SCAN_PATH 를 따로 주면 그게 우선 - 예외적으로 다른 경로를 봐야 할 때만)
SCAN_PATH = (
    os.getenv("BLACKDUCK_SCAN_PATH")
    or os.getenv("SCAN_PATH")
    or os.getenv("SNYK_PROJECT_PATH")
)
# 스캔 자동 실행 여부. false 면 기존처럼 '조회만' 한다.
AUTO_SCAN = os.getenv("BLACKDUCK_AUTO_SCAN", "false").lower() in ("1", "true", "yes")
# Detect 가 끝나도 서버 BOM 처리에는 시간이 더 걸린다. 그 대기까지 포함한 타임아웃(초).
SCAN_TIMEOUT = int(os.getenv("BLACKDUCK_SCAN_TIMEOUT", "3600"))
# 매니페스트 탐색 깊이. Detect 기본값 0 은 지정 폴더만 본다.
# 기존 UI 스캔 설정과 동일하게 10 을 기본으로 둔다(멀티모듈 하위 pom 까지 도달).
SEARCH_DEPTH = int(os.getenv("BLACKDUCK_SEARCH_DEPTH", "10"))
# Detector Accuracy Requirements. UI 설정과 동일하게 NONE.
ACCURACY = os.getenv("BLACKDUCK_ACCURACY", "NONE").strip()
# 사용할 도구. 비우면 Detect 기본값(DETECTOR + SIGNATURE_SCAN)을 그대로 써서
# 기존 UI 스캔 결과와 조건을 맞춘다. 매니페스트만 보려면 'DETECTOR' 로 지정.
DETECT_TOOLS = os.getenv("BLACKDUCK_DETECT_TOOLS", "").strip()


def resolve_version_name() -> str:
    """이번 실행에서 쓸 버전명. 빈 문자열이면 Detect 자동추출에 맡긴다."""
    if PROJECT_VERSION:
        return PROJECT_VERSION
    if VERSION_AUTO:
        return f"{VERSION_PREFIX}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    return ""


def _build_cmd(version_name: str) -> list[str]:
    cmd = [
        "java",
        "-jar",
        DETECT_JAR,
        f"--blackduck.url={BLACKDUCK_URL}",
        f"--blackduck.api.token={API_TOKEN}",
        f"--detect.project.name={PROJECT_NAME}",
        f"--detect.source.path={SCAN_PATH}",
        # 사내망 자체 인증서 -> 검증 생략 (blackduck_client 의 verify=False 와 동일 맥락)
        "--blackduck.trust.cert=true",
        # UI 설정과 동일: 하위 모듈 pom 까지 도달하도록 깊이 확보
        f"--detect.detector.search.depth={SEARCH_DEPTH}",
        f"--detect.accuracy.required={ACCURACY}",
        # Intelligent = 서버에 결과를 남기는 일반 스캔 (RAPID/STATELESS 아님)
        "--detect.blackduck.scan.mode=INTELLIGENT",
        # 서버가 BOM 계산을 끝낼 때까지 대기. 없으면 직후 조회 시 '예전 BOM' 을 읽는다.
        "--detect.wait.for.results=true",
        "--logging.level.detect=INFO",
    ]
    if DETECT_TOOLS:
        cmd.append(f"--detect.tools={DETECT_TOOLS}")
    if version_name:
        cmd.append(f"--detect.project.version.name={version_name}")
    return cmd


def run_blackduck_scan() -> str | None:
    """Detect 실행.

    반환값이 곧 '조회해야 할 버전명' 이다.
      - 스캔 성공 & 버전명 지정  -> 그 버전명 (조회가 이 버전을 정확히 찾아가야 함)
      - 스캔 성공 & 자동추출     -> "" (조회는 기존처럼 versions[0])
      - 스킵/실패                -> None (조회는 기존처럼 versions[0])
    """
    if not AUTO_SCAN:
        print("  블랙덕 자동스캔 꺼짐(BLACKDUCK_AUTO_SCAN=false) - 기존 BOM 을 조회합니다.")
        return None

    missing = [
        name
        for name, val in (
            ("BLACKDUCK_URL", BLACKDUCK_URL),
            ("BLACKDUCK_API_TOKEN", API_TOKEN),
            ("BLACKDUCK_PROJECT_KEY", PROJECT_NAME),
            ("스캔 경로(BLACKDUCK_SCAN_PATH 또는 SNYK_PROJECT_PATH)", SCAN_PATH),
        )
        if not val
    ]
    if missing:
        print(f"  블랙덕 스캔 설정 부족 - {', '.join(missing)} 없음. 조회만 진행합니다.")
        return None

    if not os.path.isfile(DETECT_JAR):
        print(f"  Detect jar 없음: {DETECT_JAR} - 조회만 진행합니다.")
        return None

    if not os.path.isdir(SCAN_PATH):
        print(f"  스캔 대상 폴더 없음: {SCAN_PATH} - 조회만 진행합니다.")
        return None

    version_name = resolve_version_name()
    tools_label = DETECT_TOOLS or "Detect 기본(DETECTOR+SIGNATURE_SCAN)"
    print(f"  블랙덕 Detect 스캔 시작 - {PROJECT_NAME} / {version_name or '(자동추출)'}")
    print(f"     대상: {SCAN_PATH}")
    print(f"     깊이 {SEARCH_DEPTH} / 정확도 {ACCURACY} / 도구 {tools_label}")
    print("     (서버 BOM 처리 대기까지 포함, 수 분 이상 걸릴 수 있습니다)")

    try:
        proc = subprocess.run(
            _build_cmd(version_name),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=SCAN_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(f"  블랙덕 스캔 타임아웃({SCAN_TIMEOUT}초) - 조회만 진행합니다.")
        return None
    except FileNotFoundError:
        print("  java 실행 불가 (PATH 확인 필요) - 조회만 진행합니다.")
        return None

    if proc.returncode != 0:
        # Detect 는 실패 원인을 마지막 줄들에 요약해 출력한다.
        tail = "\n".join((proc.stdout or "").strip().splitlines()[-15:])
        print(f"  블랙덕 스캔 실패 (exit {proc.returncode}) - 조회만 진행합니다.")
        if tail:
            print(f"     --- Detect 로그 끝부분 ---\n{tail}")
        return None

    print(f"  블랙덕 Detect 스캔 완료 - 버전 '{version_name or '(자동추출)'}'\n")
    return version_name


if __name__ == "__main__":
    sys.exit(0 if run_blackduck_scan() is not None else 1)
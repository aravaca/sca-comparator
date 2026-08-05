"""스패로우 Enterprise 클라이언트 CLI 로 분석을 트리거한다.

sparrow_client.py 는 '이미 분석된 결과를 GraphQL 로 조회'만 했다.
이 모듈은 그 앞단(= 사람이 웹 UI 에서 '분석 시작' 누르던 일)을 대신한다.

핵심 옵션(공식 문서 기준):
  create analysis -k <프로젝트키> -s <서버> -u <계정> -p <비밀번호파일>
      --type full --profile <작업프로파일> --target-type file --path <경로>
      --sync analysis|project
종료코드: 0 완료 / 1 실패 / 2 중지

--sync 값 주의:
  analysis = 분석 작업 자체가 끝나면 반환 (웹 UI 의 '분석 완료 100%' 와 같은 시점)
  project  = 그 이후 '프로젝트 데이터 반영'까지 추가로 대기 — 이 단계가 서버에서
             지연되면 분석은 끝났는데 CLI 만 하염없이 기다리는 것처럼 보인다.
             (실제로 이 증상 때문에 기본값을 analysis 로 낮췄다.)
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import config.env  # noqa: F401  (.env 로드 + PROJECT_KEY 자동생성)

CLI_PATH = os.getenv(
    "SPARROW_CLI_PATH", r"C:\sparrow\2605.2\client\sparrow-cli.cmd"
)
# 분석을 요청할 서버.
# 주의: 여기(스캔)와 sparrow_client.py 의 IP/PORT(조회)가 다른 서버를 가리키면
#       'A 에 스캔하고 B 에서 조회' 가 되어 결과가 어긋난다. 반드시 같은 서버여야 한다.
#       평소 = 192.168.70.154:10880 / 판교 분사에서만 = 172.30.1.28:10610
_IP = os.getenv("IP")
_PORT = os.getenv("PORT")
_SCHEME = os.getenv("SPARROW_CLI_SCHEME", "https")
SERVER = os.getenv("SPARROW_CLI_SERVER") or (
    f"{_SCHEME}://{_IP}:{_PORT}" if _IP and _PORT else ""
)

USERNAME = os.getenv("USERNAME1")
PASSWORD = os.getenv("PASSWORD1")
PROJECT_KEY = os.getenv("PROJECT_KEY")

# 작업 프로파일 이름. 클라이언트 scan_option.json 에서 쓰던 값이 기본값.
PROFILE = os.getenv("SPARROW_PROFILE", "컴포넌트 분석 검출 규칙")
ANALYSIS_TYPE = os.getenv("SPARROW_ANALYSIS_TYPE", "full")  # full | adhoc
# 분석 대상 경로 (세 도구 공통 SCAN_PATH)
SCAN_PATH = os.getenv("SPARROW_SCAN_PATH") or os.getenv("SCAN_PATH") or os.getenv("SNYK_PROJECT_PATH")
# 확장자. 비우면 전체 확장자를 분석한다(문서: "*" 또는 빈 값 = 전체).
EXTENSIONS = [e.strip() for e in os.getenv("SPARROW_EXTENSIONS", "").split(",") if e.strip()]

AUTO_SCAN = os.getenv("SPARROW_AUTO_SCAN", "false").lower() in ("1", "true", "yes")
SCAN_TIMEOUT = int(os.getenv("SPARROW_SCAN_TIMEOUT", "3600"))
# analysis = 분석 작업이 끝나면 반환.  <- 기본값
# project  = '프로젝트 데이터 반영'까지 대기. 쓰지 말 것.
#
# project 로 두면 서버가 이미 Completed 인데도 CLI 가 오지 않는 신호를 계속 기다린다.
# (실측: 분석 15:09:08 Completed, 컴포넌트 678건 즉시 조회 가능 —
#  그런데도 CLI 는 "progress": 0 을 찍은 채 무한 대기)
# 그래서 CLI 의 sync 에 의존하지 않고, 분석 종료 후 우리가 직접 조회를 재시도한다
# (main.py 의 SPARROW_RETRY_* / 아래 wait_until_ready).
SYNC_MODE = os.getenv("SPARROW_SYNC_MODE", "analysis").strip() or "analysis"
# 프로젝트가 없으면 분석 전에 자동으로 만든다.
# (스패로우는 프로젝트가 먼저 있어야 분석이 걸리고, 없으면 조회 시 404 로 컴포넌트가 0개가 된다)
AUTO_CREATE_PROJECT = os.getenv(
    "SPARROW_AUTO_CREATE_PROJECT", "true"
).lower() in ("1", "true", "yes")


def _build_cmd(pw_file: str) -> list[str]:
    cmd = [
        CLI_PATH,
        "create",
        "analysis",
        "-k", PROJECT_KEY,
        "-s", SERVER,
        "-u", USERNAME,
        "-p", pw_file,
        "--type", ANALYSIS_TYPE,
        "--profile", PROFILE,
        "--target-type", "file",
        "--path", SCAN_PATH,
        "--sync", SYNC_MODE,
    ]
    for ext in EXTENSIONS:
        cmd += ["--extension", ext]
    return cmd


def _run_cli(args: list[str], timeout: int = 180) -> tuple[int, str]:
    """CLI 를 조용히 실행하고 (종료코드, 출력) 을 돌려준다. 짧은 명령(get/create project)용."""
    try:
        proc = subprocess.run(
            [CLI_PATH] + args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return -1, f"(타임아웃 {timeout}초)"
    except FileNotFoundError:
        return -1, f"(CLI 실행 불가: {CLI_PATH})"


def ensure_project(pw_file: str) -> bool:
    """PROJECT_KEY 프로젝트가 없으면 만든다. 이미 있거나 생성 성공하면 True.

    스패로우는 웹에서 프로젝트를 먼저 만들어 둬야 분석이 걸린다.
    안 만든 채로 분석하면 조회 단계에서 404(존재하지 않는 프로젝트)가 나고
    엑셀의 스패로우 칸이 통째로 비게 된다.
    """
    common = ["-s", SERVER, "-u", USERNAME, "-p", pw_file]

    code, out = _run_cli(["get", "project"] + common + ["-k", PROJECT_KEY])
    if code == 0:
        print(f"  스패로우 프로젝트 '{PROJECT_KEY}' 확인됨")
        return True

    if not AUTO_CREATE_PROJECT:
        print(f"  스패로우 프로젝트 '{PROJECT_KEY}' 없음 (자동생성 꺼짐)")
        return False

    print(f"  스패로우 프로젝트 '{PROJECT_KEY}' 없음 - 새로 생성합니다")
    # create project 는 JSON 파일로만 입력받는다. 최소 스키마는 {"key": ...} 뿐이며
    # name 을 생략하면 key 와 같은 이름으로 만들어진다.
    json_file = None
    try:
        fd, json_file = tempfile.mkstemp(prefix="spproj_", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"key": PROJECT_KEY, "name": PROJECT_KEY}, f, ensure_ascii=False)

        code, out = _run_cli(["create", "project"] + common + ["-f", json_file])
    finally:
        if json_file and os.path.exists(json_file):
            try:
                os.remove(json_file)
            except OSError:
                pass

    if code == 0:
        print(f"  스패로우 프로젝트 '{PROJECT_KEY}' 생성 완료")
        return True

    print(f"  스패로우 프로젝트 생성 실패 - {out.strip()[:300]}")
    return False


def _stream_until_exit(proc, lines: list[str]) -> int | None:
    """CLI 출력을 실시간으로 흘리면서 '프로세스 종료'를 기준으로 끝낸다.

    왜 이렇게까지 하냐면:
      sparrow-cli.cmd 는 cmd.exe -> java(updater) -> java(cli) 로 이어진다.
      cmd.exe 가 끝나도 손자 java 가 stdout 파이프 핸들을 붙잡고 있으면
      파이프에 EOF 가 오지 않는다. 그래서 `for line in proc.stdout:` 로 읽으면
      스캔이 끝났는데도 루프가 안 끝나 무한 대기한다(실제로 발생한 증상).
      게다가 그 루프에는 타임아웃이 걸리지 않아 SCAN_TIMEOUT 도 소용이 없었다.

    그래서 읽기는 별도 스레드에 맡기고, 본체는 proc.poll() 로 종료를 직접 감시한다.
    반환값: 종료코드, 타임아웃이면 None.
    """
    q: "queue.Queue[str | None]" = queue.Queue()

    def _reader():
        try:
            for line in proc.stdout:
                q.put(line.rstrip("\n"))
        except Exception:
            pass
        finally:
            q.put(None)  # EOF 표시(올 수도, 안 올 수도 있다)

    threading.Thread(target=_reader, daemon=True).start()

    deadline = time.monotonic() + SCAN_TIMEOUT
    last_out = time.monotonic()
    grace_until = None  # 프로세스 종료 후 남은 출력을 마저 받는 시간

    while True:
        try:
            item = q.get(timeout=1.0)
            if item is not None:
                print(f"     | {item}", flush=True)
                lines.append(item)
                last_out = time.monotonic()
                continue
        except queue.Empty:
            pass

        rc = proc.poll()
        if rc is not None:
            # 프로세스는 끝났다. 남은 출력만 잠깐 더 받고 종료(파이프 EOF 를 기다리지 않는다).
            if grace_until is None:
                grace_until = time.monotonic() + 3
            elif time.monotonic() > grace_until:
                return rc

        now = time.monotonic()
        if now > deadline:
            return None
        # 오래 조용하면 살아있다는 표시를 남긴다(멈춘 것처럼 보이지 않게)
        if now - last_out > 60:
            mins = int((now - last_out) // 60)
            print(f"     | ... 분석 진행 중 (출력 없이 {mins}분 경과, 최대 "
                  f"{SCAN_TIMEOUT // 60}분 대기)", flush=True)
            last_out = now


def _kill_tree(pid: int) -> None:
    """Windows 에서 cmd.exe -> java(updater) -> java(cli) 로 이어지는 자식들까지 확실히 종료.
    subprocess 의 기본 kill()/terminate() 는 직계 자식(cmd.exe)만 죽이고
    그 아래 java 프로세스는 안 죽어서 리소스가 계속 남는 문제가 있었다."""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


def run_sparrow_scan() -> bool:
    """분석 트리거. 성공 시 True, 스킵/실패 시 False (호출측은 기존 결과를 조회)."""
    if not AUTO_SCAN:
        print("  스패로우 자동스캔 꺼짐(SPARROW_AUTO_SCAN=false) - 기존 분석결과를 조회합니다.")
        return False

    missing = [
        n
        for n, v in (
            ("PROJECT_KEY", PROJECT_KEY),
            ("서버(SPARROW_CLI_SERVER 또는 IP/PORT)", SERVER),
            ("USERNAME1", USERNAME),
            ("PASSWORD1", PASSWORD),
            ("스캔 경로(SCAN_PATH)", SCAN_PATH),
        )
        if not v
    ]
    if missing:
        print(f"  스패로우 스캔 설정 부족 - {', '.join(missing)} 없음. 조회만 진행합니다.")
        return False

    if not os.path.isfile(CLI_PATH):
        print(f"  sparrow-cli 없음: {CLI_PATH} - 조회만 진행합니다.")
        return False

    if not os.path.isdir(SCAN_PATH):
        print(f"  스캔 대상 폴더 없음: {SCAN_PATH} - 조회만 진행합니다.")
        return False

    # CLI 는 비밀번호를 '파일 경로' 로만 받는다(-p). 평문이 디스크에 남지 않도록
    # 임시파일에 쓰고 finally 에서 반드시 삭제한다.
    pw_file = None
    proc = None
    lines: list[str] = []
    try:
        fd, pw_file = tempfile.mkstemp(prefix="sp_", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(PASSWORD)

        # 분석보다 먼저: 프로젝트가 존재해야 한다(없으면 만든다).
        if not ensure_project(pw_file):
            print("  프로젝트를 준비하지 못했습니다 - 조회만 진행합니다.")
            return False

        print(f"  스패로우 CLI 분석 시작 - {PROJECT_KEY} @ {SERVER}")
        print(f"     대상: {SCAN_PATH}")
        print(f"     유형 {ANALYSIS_TYPE} / 프로파일 '{PROFILE}' / 확장자 {EXTENSIONS or '전체'} / --sync {SYNC_MODE}")
        print("     (아래부터 CLI 실시간 출력)")
        print("     " + "-" * 50)

        # capture_output=True 는 프로세스가 끝날 때까지 아무 것도 안 보여줘서
        # '멈춘 건지 진행 중인지' 구분이 안 됐다. Popen 으로 한 줄씩 즉시 출력한다.
        # stdin=DEVNULL: CLI 가 혹시 대화식으로 뭘 물어봐도(안 보이는 채로) 무한 대기하지
        # 않고 즉시 EOF 로 실패하도록 막는다.
        proc = subprocess.Popen(
            _build_cmd(pw_file),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        returncode = _stream_until_exit(proc, lines)
        if returncode is None:
            print(f"  스패로우 스캔 타임아웃({SCAN_TIMEOUT}초) - 프로세스 강제 종료 후 조회만 진행합니다.")
            _kill_tree(proc.pid)
            return False
    except FileNotFoundError:
        print(f"  sparrow-cli 실행 불가: {CLI_PATH} - 조회만 진행합니다.")
        return False
    except BaseException:
        # Ctrl+C 등으로 우리 쪽이 중단돼도 java 자식이 안 죽고 남는 문제가 있었다.
        if proc is not None:
            _kill_tree(proc.pid)
        raise
    finally:
        if pw_file and os.path.exists(pw_file):
            try:
                os.remove(pw_file)
            except OSError:
                print(f"  경고: 임시 비밀번호 파일 삭제 실패 - 직접 지워주세요: {pw_file}")

    print("     " + "-" * 50)

    # 문서 기준 종료코드: 0 완료 / 1 실패 / 2 중지
    if returncode == 0:
        print("  스패로우 분석 완료\n")
        return True

    reason = {1: "분석 실패", 2: "분석 중지"}.get(returncode, f"오류코드 {returncode}")
    tail = "\n".join(lines[-15:])
    print(f"  스패로우 스캔 {reason} - 조회만 진행합니다.")
    if tail:
        print(f"     --- CLI 로그 끝부분 ---\n{tail}")
    return False


if __name__ == "__main__":
    sys.exit(0 if run_sparrow_scan() else 1)

"""환경변수 로딩 + PROJECT_KEY 자동 생성.

각 모듈이 따로 load_dotenv() 를 부르는 대신 이 모듈을 import 한다.
(import 시점에 1회만 실행 - dotenv 는 override=False 라 여러 번 불려도 안전하지만,
 PROJECT_KEY 파생은 한 곳에서만 일어나야 도구 3개가 같은 키를 본다.)

PROJECT_KEY 를 .env 에 비워두면  <오늘날짜>_<SCAN_PATH 의 마지막 폴더명>  으로 자동 생성.
  SCAN_PATH=C:\\Exception\\분석대상\\airllm-main\\airllm-main
    -> PROJECT_KEY = 20260804_airllm-main

.env 에서 ${SCAN_PATH##*\\} 같은 bash 문법은 쓸 수 없다. python-dotenv 는
${VAR} / ${VAR:-기본값} 만 해석하고, ## 접두사 제거는 변수명의 일부로 먹어버려
조용히 빈 문자열이 된다(= 20260804_ 로 스캔됨). 그래서 여기서 계산한다.
"""

import os
import re
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def _last_folder(path: str) -> str:
    """경로의 마지막 폴더명. 구분자는 \\ / 둘 다, 뒤에 붙은 구분자는 무시."""
    parts = [p for p in re.split(r"[\\/]+", (path or "").strip().strip('"')) if p]
    # 드라이브만 남는 경우(C:) 는 폴더명으로 못 쓴다
    if not parts or re.fullmatch(r"[A-Za-z]:", parts[-1]):
        return ""
    return parts[-1]


def _auto_project_key() -> str:
    folder = _last_folder(os.getenv("SCAN_PATH", "")) or "unknown"
    return f"{datetime.now():%Y%m%d}_{folder}"


# .env 에 값이 있으면 그대로 존중하고, 비어 있을 때만 자동 생성.
if not os.getenv("PROJECT_KEY", "").strip():
    os.environ["PROJECT_KEY"] = _auto_project_key()

# 블랙덕도 기본적으로 같은 키를 쓴다(따로 지정했으면 그대로 둔다).
if not os.getenv("BLACKDUCK_PROJECT_KEY", "").strip():
    os.environ["BLACKDUCK_PROJECT_KEY"] = os.environ["PROJECT_KEY"]

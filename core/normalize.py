"""
패키지명 정규화 모듈.

두 SCA 도구(BlackDuck / Sparrow)가 같은 패키지를 다르게 표기하는 문제를 흡수한다.
  1) 대소문자 / 구분자(_ . -) 차이  -> 표준 정규화
  2) 표시명 != 실제 레지스트리명     -> 수동 alias 테이블(aliases.json)
"""
import json
import os
import re

_ALIAS_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "aliases.json")


def load_aliases(path: str = _ALIAS_PATH) -> dict:
    """
    aliases.json을 읽어 {정규화키: canonical} dict로 반환.
    값은 문자열이거나 {"canonical": ..., "note": ...} 형태 모두 허용.
    '_'로 시작하는 키(_comment 등)는 무시한다.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    aliases = {}
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        canonical = val["canonical"] if isinstance(val, dict) else val
        aliases[_alias_key(key)] = canonical
    return aliases


def _alias_key(name: str) -> str:
    """alias 조회용 느슨한 키: 소문자 + 구분자(/ _ . 공백)를 '-'로 통일."""
    key = name.strip().lower()
    key = re.sub(r"[\s/_.]+", "-", key)
    key = re.sub(r"-+", "-", key).strip("-")
    return key


def normalize_name(name: str, aliases: dict | None = None) -> str:
    """
    패키지명을 비교용 canonical 형태로 정규화.
    순서: alias 매핑 우선 적용 -> 소문자 + 연속 구분자(- _ .)를 '-'로 통일.
    npm scope(@scope/name) 표기는 유지한다.
    """
    if not name:
        return ""
    if aliases is None:
        aliases = load_aliases()

    # 1) alias 우선: 느슨한 키로 조회되면 canonical로 치환
    key = _alias_key(name)
    if key in aliases:
        name = aliases[key]

    # 2) 표준 정규화
    name = name.strip().lower()
    if name.startswith("@") and "/" in name:
        scope, _, pkg = name.partition("/")
        return f"{scope}/{re.sub(r'[-_.]+', '-', pkg).strip('-')}"
    return re.sub(r"[-_.]+", "-", name).strip("-")

"""이름/버전 정규화 테스트.

여기 있는 케이스는 전부 실제 비교에서 나왔던 것들이다.
정규화 규칙을 고칠 때 이 테스트가 깨지면, 예전에 해결했던 문제가
다시 살아난 것이므로 그냥 통과시키지 말고 원인을 확인할 것.
"""
import pytest

from core.compare import normalize_version
from core.normalize import normalize_name


# --------------------------------------------------------------------------
# 이름 정규화
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # 대소문자
    ("SciPy", "scipy"),
    ("ESLint", "eslint"),
    # 구분자(. _ -)는 '-' 하나로 통일
    ("big.js", "big-js"),
    ("lodash.debounce", "lodash-debounce"),
    ("fs.realpath", "fs-realpath"),
    ("@nodelib/fs.scandir", "@nodelib/fs-scandir"),
    # 연속 구분자
    ("foo__bar", "foo-bar"),
    ("foo..bar", "foo-bar"),
    # 앞뒤 공백/구분자
    ("  semver  ", "semver"),
    ("-semver-", "semver"),
])
def test_이름_표준정규화(raw, expected):
    assert normalize_name(raw, aliases={}) == expected


def test_npm_scope_는_유지된다():
    """@scope/name 의 '/' 를 '-' 로 바꿔버리면 서로 다른 패키지가 뭉개진다."""
    assert normalize_name("@babel/Core", aliases={}) == "@babel/core"
    assert normalize_name("@babel/helper-string-parser", aliases={}) == \
        "@babel/helper-string-parser"


def test_빈_이름():
    assert normalize_name("", aliases={}) == ""
    assert normalize_name(None, aliases={}) == ""


def test_alias_가_표준정규화보다_먼저_적용된다():
    """alias 는 '표시명 -> 실제 레지스트리명' 이라 정규화보다 우선해야 한다."""
    aliases = {"d3-js": "d3"}
    assert normalize_name("d3.js", aliases) == "d3"


def test_alias_키는_느슨하게_조회된다():
    """aliases.json 을 사람이 손으로 쓰므로 표기가 흔들려도 걸려야 한다."""
    aliases = {"node-lru-cache": "lru-cache"}
    for variant in ("node-lru-cache", "node_lru_cache", "Node.LRU.Cache", "node/lru/cache"):
        assert normalize_name(variant, aliases) == "lru-cache", variant


# --------------------------------------------------------------------------
# 버전 정규화 (CASE-004: 같은 커밋, 다른 표기)
# --------------------------------------------------------------------------

def test_v접두사_제거():
    assert normalize_version("v1.3.7") == "1.3.7"
    assert normalize_version("1.3.7") == "1.3.7"


def test_go_pseudo_version_은_커밋해시로_통일된다():
    """Sparrow 전체 표기와 Snyk 짧은 표기가 같은 커밋이면 같아야 한다.
    (안 그러면 가짜 '버전 충돌' 이 생긴다 - CASE-004)"""
    sparrow = "0.0.0-20211004153227-1c3628e74d0f"
    snyk = "#1c3628e74d0f"
    assert normalize_version(sparrow) == normalize_version(snyk) == "1c3628e74d0f"


def test_일반_버전은_건드리지_않는다():
    """pseudo-version 규칙이 평범한 버전을 망가뜨리면 안 된다."""
    for v in ("7.0.6", "2.576-SNAPSHOT", "1.0.0-beta.2", "4.0.0-node10",
              "9999.0-empty-to-avoid-conflict-with-guava", "1.1.4c"):
        assert normalize_version(v) == v, v


def test_빈_버전():
    assert normalize_version("") == ""
    assert normalize_version(None) is None

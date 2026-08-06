"""3자 비교 로직 테스트 (버전 집합 판정 / 분류 / 자동병합 안전장치).

컴포넌트는 각 도구의 원본 dict 형태를 흉내낸 최소 구조로 만든다.
"""
import pytest

from core.compare import compare_multi


def C(name, version, paths=None):
    """스패로우/Snyk 형태의 최소 컴포넌트."""
    return {"name": name, "version": version, "file_paths": paths or []}


def BD(name, version, paths=None):
    """블랙덕은 필드명이 다르다 (componentName / componentVersionName).
    이 차이를 흡수하는 게 _extract 의 역할이라 테스트도 실제 필드명을 써야 한다."""
    return {"componentName": name, "componentVersionName": version,
            "file_paths": paths or []}


def run(**by_source):
    """aliases 를 비우고 비교 - 테스트가 aliases.json 내용에 흔들리지 않게."""
    return compare_multi(by_source, aliases={})


def names(rows):
    return {r["name"] for r in rows}


# --------------------------------------------------------------------------
# 커버리지 분류
# --------------------------------------------------------------------------

def test_모든_도구가_같은_버전이면_전체일치():
    r = run(
        blackduck=[BD("lodash", "4.17.21")],
        sparrow=[C("lodash", "4.17.21")],
        snyk=[C("lodash", "4.17.21")],
    )
    assert names(r["buckets"]["full_match"]) == {"lodash"}
    assert r["buckets"]["full_mismatch"] == []
    assert r["combo_counts"] == {"blackduck+sparrow+snyk": 1}


def test_모두_검출했지만_버전이_다르면_버전충돌():
    r = run(
        blackduck=[BD("vite", "7.0.6")],
        sparrow=[C("vite", "7.0.6"), C("vite", "7.3.5")],
        snyk=[C("vite", "7.0.6")],
    )
    assert r["buckets"]["full_match"] == []
    assert names(r["buckets"]["full_mismatch"]) == {"vite"}
    # 충돌 상세에 도구별 버전 집합이 그대로 남아야 검토가 가능하다
    conflict = r["version_conflicts"][0]
    assert sorted(conflict["versions"]["sparrow"]) == ["7.0.6", "7.3.5"]
    assert conflict["versions"]["blackduck"] == ["7.0.6"]


def test_일부_도구에만_있으면_partial():
    r = run(
        blackduck=[BD("only-bd", "1.0.0")],
        sparrow=[C("shared", "1.0.0")],
        snyk=[C("shared", "1.0.0")],
    )
    assert names(r["buckets"]["partial"]) == {"shared"}
    assert names(r["buckets"]["unique"]) == {"only-bd"}
    assert r["combo_counts"]["sparrow+snyk"] == 1
    assert r["combo_counts"]["blackduck"] == 1


def test_한_도구에만_있으면_단독():
    r = run(
        blackduck=[BD("a", "1.0")],
        sparrow=[C("b", "1.0")],
        snyk=[C("c", "1.0")],
    )
    assert names(r["buckets"]["unique"]) == {"a", "b", "c"}
    # only_in 은 '이름 집합' 이 아니라 행 dict 리스트다
    assert names(r["only_in"]["blackduck"]) == {"a"}
    assert names(r["only_in"]["sparrow"]) == {"b"}
    assert names(r["only_in"]["snyk"]) == {"c"}


def test_전체_행수는_고유_패키지_수():
    r = run(
        blackduck=[BD("a", "1.0"), BD("b", "1.0")],
        sparrow=[C("b", "1.0"), C("c", "1.0")],
        snyk=[C("c", "1.0")],
    )
    assert len(r["rows"]) == 3  # a, b, c


# --------------------------------------------------------------------------
# 버전을 '집합' 으로 본다 (문자열 동등 비교가 아님)
# --------------------------------------------------------------------------

def test_같은_패키지의_여러_버전은_하나의_행으로_묶인다():
    r = run(
        blackduck=[BD("@babel/core", "7.0.0"), BD("@babel/core", "8.0.1")],
        sparrow=[C("@babel/core", "8.0.1"), C("@babel/core", "7.0.0")],
    )
    assert len(r["rows"]) == 1
    # 순서가 달라도 집합이 같으면 일치
    assert names(r["buckets"]["full_match"]) == {"@babel/core"}


def test_버전_표기만_다르면_충돌이_아니다():
    """CASE-004: Go pseudo-version. 정규화가 빠지면 여기서 가짜 충돌이 난다."""
    r = run(
        sparrow=[C("github.com/erikgeiser/coninput", "0.0.0-20211004153227-1c3628e74d0f")],
        snyk=[C("github.com/erikgeiser/coninput", "#1c3628e74d0f")],
    )
    assert r["buckets"]["full_mismatch"] == [], "같은 커밋인데 충돌로 분류됨"


# --------------------------------------------------------------------------
# 자동병합 안전장치 (오병합 방지)
# --------------------------------------------------------------------------

def test_이름표기만_다르고_버전이_같으면_자동병합():
    r = run(
        blackduck=[BD("feross/buffer", "6.0.3")],
        sparrow=[C("buffer", "6.0.3")],
        snyk=[C("buffer", "6.0.3")],
    )
    assert len(r["auto_merged"]) == 1
    m = r["auto_merged"][0]
    assert m["from_name"] == "feross/buffer" and m["to_name"] == "buffer"
    # 병합됐으니 3사 모두 검출한 것으로 잡혀야 한다
    assert names(r["buckets"]["full_match"]) == {"buffer"}


def test_이름이_비슷해도_버전이_다르면_자동병합_안_함():
    """버전 완전일치가 유일한 안전장치다. 이게 풀리면 오병합이 발생한다."""
    r = run(
        blackduck=[BD("feross/buffer", "5.0.0")],
        sparrow=[C("buffer", "6.0.3")],
    )
    assert r["auto_merged"] == []
    assert names(r["buckets"]["unique"]) == {"feross/buffer", "buffer"}


def test_버전이_같아도_이름이_무관하면_자동병합_안_함():
    r = run(
        blackduck=[BD("lodash", "1.0.0")],
        sparrow=[C("webpack", "1.0.0")],
    )
    assert r["auto_merged"] == []


# --------------------------------------------------------------------------
# 부가 정보 / 방어
# --------------------------------------------------------------------------

def test_자산경로가_행에_보존된다():
    r = run(
        blackduck=[BD("lodash", "4.0.0", ["a/package.json"])],
        sparrow=[C("lodash", "4.0.0", ["b/yarn.lock"])],
    )
    fp = r["rows"][0]["file_paths"]
    assert fp["blackduck"] == ["a/package.json"]
    assert fp["sparrow"] == ["b/yarn.lock"]


def test_도구_결과가_비어도_죽지_않는다():
    """한 도구가 스캔에 실패하면 빈 리스트가 들어온다(main.py 가 그렇게 처리)."""
    r = run(blackduck=[BD("a", "1.0")], sparrow=[], snyk=[C("a", "1.0")])
    assert names(r["buckets"]["partial"]) == {"a"}
    assert r["only_in"]["sparrow"] == []


def test_대소문자만_다른_이름은_같은_패키지():
    r = run(blackduck=[BD("ESLint", "9.0.0")], sparrow=[C("eslint", "9.0.0")])
    assert len(r["rows"]) == 1
    assert names(r["buckets"]["full_match"]) == {"eslint"}

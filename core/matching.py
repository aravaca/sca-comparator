"""
퍼지(유사도) 매칭 후보 추출.

정규화 + alias로도 매칭되지 않은 이름들에 대해 유사도를 계산한다.
주의: 자동으로 같은 패키지 취급하지 않는다. '사람이 확인할 후보'로만 분리한다.

두 SCA 도구가 같은 패키지를 표기만 다르게 적는 흔한 패턴들을 폭넓게 잡는다:
  - 경로/소유자/호스트 접두    : wooorm/ccount vs ccount, github-com/.../go-colorful vs go-colorful
  - Go 모듈 버전 경로 접미     : .../uax29/v2 vs clipperhouse/uax29, .../ulid/v2 vs oklog/ulid
  - -js / js 장식             : d3-js vs d3, markedjs vs marked, jsdiff vs diff, ms-js vs ms
  - node- / node 접두          : node-semver vs semver, node cookie parser vs cookie
  - 경로를 하이픈으로 평탄화    : spf13-cobra vs spf13/cobra, mattn-go-isatty vs mattn/go-isatty
  - 붙여쓴 접미               : googleuuid vs uuid, microsofttslib vs tslib
  - 공백 vs 하이픈            : strip ansi vs strip-ansi
"""
import re

from rapidfuzz import fuzz

# 점수 구간(밴드)
SCORE_LIKELY = 90   # 이상: "표기 차이 가능성 높음"
SCORE_REVIEW = 70   # 이상: "수동 검토 필요" / 미만: 후보 제외

# 신호별 부여 점수
S_BASENAME = 96.0   # 마지막 경로 세그먼트 일치
S_CORE = 93.0       # 장식/호스트 토큰 제거 후 핵심 토큰 집합 일치
S_SUBSET = 88.0     # 핵심 토큰 집합이 한쪽이 다른쪽의 부분집합
S_SUFFIX = 85.0     # 붙여쓴 접미(googleuuid→uuid 등)

MAX_CANDIDATES_PER_NAME = 3

_VER_SEG = re.compile(r"^v?\d+(?:[.\-]\d+)*$")   # v2, v1.24.4, 2.0 같은 버전형 세그먼트
_SPLIT = re.compile(r"[\s/_.\-]+")

# 경로/호스트/장식 토큰 (비교 시 무시)
_STOP = {
    "github", "gitlab", "com", "org", "gopkg", "in", "www",
    "golang", "js", "node", "nodejs", "dev", "git", "x",
}


def _band(score: float) -> str | None:
    if score >= SCORE_LIKELY:
        return "표기 차이 가능성 높음"
    if score >= SCORE_REVIEW:
        return "수동 검토 필요"
    return None  # 70 미만은 후보에서 제외


def _path_parts(name: str) -> list[str]:
    """'/'로 분할하고 끝쪽의 버전형 세그먼트(/v2 등)를 떼어낸다."""
    parts = [p for p in name.split("/") if p]
    while len(parts) > 1 and _VER_SEG.match(parts[-1]):
        parts.pop()
    return parts or [name]


def _basename(name: str) -> str:
    """마지막 경로 세그먼트(버전 세그먼트 제외)."""
    return _path_parts(name)[-1]


def _strip_js(tok: str) -> str:
    """토큰에 붙은 js 접두/접미 제거 (markedjs→marked, jsdiff→diff)."""
    if len(tok) > 3 and tok.endswith("js"):
        tok = tok[:-2]
    if len(tok) > 3 and tok.startswith("js"):
        tok = tok[2:]
    return tok


def _core_set(name: str) -> set[str]:
    """전체 경로를 토큰화 후 장식/호스트 토큰을 제거한 핵심 토큰 집합."""
    raw = _SPLIT.split("/".join(_path_parts(name)).lower())
    core = set()
    for t in raw:
        t = _strip_js(t)
        if t and t not in _STOP:
            core.add(t)
    return core


def _meaningful(tokens: set[str]) -> bool:
    return any(len(t) >= 3 for t in tokens)


def _concat_suffix(short_base: str, long_base: str) -> bool:
    """long_base가 구분자 없는 단일 토큰이고 short_base로 끝남 (googleuuid→uuid)."""
    return (
        "-" not in long_base
        and len(short_base) >= 4
        and long_base != short_base
        and long_base.endswith(short_base)
    )


def pair_score(a: str, b: str) -> float:
    """두 이름의 유사도 점수(0~100). 여러 신호 중 최댓값."""
    a = a.strip().lower()
    b = b.strip().lower()
    if a == b:
        return 100.0

    ba, bb = _basename(a), _basename(b)
    # 1) 마지막 세그먼트 일치 / 한쪽 전체 == 다른쪽 basename
    if ba == bb or a == bb or b == ba:
        return S_BASENAME

    ca, cb = _core_set(a), _core_set(b)
    # 2) 핵심 토큰 집합 완전 일치 (장식/호스트만 다름)
    if ca and ca == cb:
        return S_CORE
    # 3) 부분집합 (한쪽이 더 많은 경로/토큰을 가짐)
    if ca and cb and (ca <= cb or cb <= ca) and _meaningful(ca & cb):
        return S_SUBSET
    # 4) 붙여쓴 접미 (googleuuid→uuid, microsofttslib→tslib)
    if _concat_suffix(bb, ba) or _concat_suffix(ba, bb):
        return S_SUFFIX

    # 5) 일반 퍼지: 풀네임 / basename / 핵심토큰 문자열 중 최댓값
    sa = " ".join(sorted(ca)) or ba
    sb = " ".join(sorted(cb)) or bb
    return max(
        fuzz.token_sort_ratio(a, b),
        fuzz.token_sort_ratio(ba, bb),
        fuzz.token_sort_ratio(sa, sb),
    )


def find_review_candidates(only_in_blackduck: set[str],
                           only_in_sparrow: set[str],
                           groups: dict | None = None,
                           score_cutoff: int = SCORE_REVIEW) -> list[dict]:
    """
    BD에만 있는 이름 vs SP에만 있는 이름을 교차 비교해 유사 후보를 뽑는다.

    각 후보: {blackduck_name, sparrow_name, score, band, blackduck_versions, sparrow_versions}
    score 내림차순 정렬.
    """
    bd_names = sorted(only_in_blackduck)
    sp_names = sorted(only_in_sparrow)
    if not bd_names or not sp_names:
        return []

    groups = groups or {}
    bd_groups = groups.get("blackduck", {})
    sp_groups = groups.get("sparrow", {})

    candidates = []
    for bd_name in bd_names:
        scored = []
        for sp_name in sp_names:
            score = pair_score(bd_name, sp_name)
            if score >= score_cutoff:
                scored.append((sp_name, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        for sp_name, score in scored[:MAX_CANDIDATES_PER_NAME]:
            band = _band(score)
            if band is None:
                continue
            candidates.append({
                "blackduck_name": bd_name,
                "sparrow_name": sp_name,
                "score": round(score, 1),
                "band": band,
                "blackduck_versions": sorted(bd_groups.get(bd_name, {}).get("versions", [])),
                "sparrow_versions": sorted(sp_groups.get(sp_name, {}).get("versions", [])),
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


def _versions_union(row: dict) -> list[str]:
    """한 패키지가 보유한 모든 도구의 버전 합집합."""
    vs = set()
    for s in row.get("present_in", []):
        vs.update(row.get("versions", {}).get(s, []))
    return sorted(vs)


def find_review_candidates_multi(result: dict,
                                 score_cutoff: int = SCORE_REVIEW) -> list[dict]:
    """3자(N자) 검토 후보 추출.

    핵심: 한 도구에만 있는 '단독' 이름을, **그 도구가 놓친 패키지 전체**
    (다른 도구엔 있는데 이 도구엔 없는 이름)와 비교한다.
    → BlackDuck 단독 `node-lru-cache` 를, Sparrow+Snyk 에 있는 `lru-cache` 와 매칭할 수 있다.
      (기존처럼 only_in 끼리만 비교하면 `lru-cache` 가 2개 도구에 있어 후보로 안 걸림)

    각 후보:
      {source_a, name_a, versions_a,       # 단독으로 잡힌 쪽 (예: BlackDuck / node-lru-cache)
       match_name, match_sources, match_versions,  # 매칭된 쪽 (예: lru-cache / [Sparrow, Snyk])
       score, band}
    score 내림차순 정렬. 자동 병합 X — 사람이 확인할 후보로만 제시.
    """
    rows = result.get("rows", [])
    sources = result.get("sources", [])

    candidates = []
    seen: set[frozenset] = set()  # 같은 이름쌍 중복(대칭) 방지

    for a in sources:
        only_in_a = [r for r in rows if r.get("present_in") == [a]]
        missing_from_a = [r for r in rows if a not in r.get("present_in", [])]
        if not only_in_a or not missing_from_a:
            continue

        for x in only_in_a:
            scored = []
            for y in missing_from_a:
                score = pair_score(x["name"], y["name"])
                if score >= score_cutoff:
                    scored.append((y, score))
            scored.sort(key=lambda t: t[1], reverse=True)

            for y, score in scored[:MAX_CANDIDATES_PER_NAME]:
                band = _band(score)
                if band is None:
                    continue
                key = frozenset((x["name"], y["name"]))
                if key in seen:
                    continue
                seen.add(key)
                candidates.append({
                    "source_a": a,
                    "name_a": x["name"],
                    "versions_a": sorted(x.get("versions", {}).get(a, [])),
                    "match_name": y["name"],
                    "match_sources": list(y.get("present_in", [])),
                    "match_versions": _versions_union(y),
                    "score": round(score, 1),
                    "band": band,
                })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates

"""
BlackDuck / Sparrow 컴포넌트 비교 (이름 정규화 + 버전 그룹화).

핵심: name@version 문자열 1:1 비교가 아니라, 정규화된 '이름' 단위로 그룹화한 뒤
각 이름의 버전 집합(set)을 비교해 exact / partial_overlap / no_overlap 으로 분류한다.
"""
import re
from collections import defaultdict

from core.normalize import normalize_name, load_aliases
from core.matching import pair_score

_V_PREFIX = re.compile(r"^[vV](?=\d)")
# Go pseudo-version 의 커밋 해시 부분: '0.0.0-<타임스탬프>-<해시>' 형태의 끝 해시
_GO_PSEUDO = re.compile(r"-(\d{12,14})-([0-9a-f]{7,40})(?:\+incompatible)?$")
_HEX = re.compile(r"[0-9a-f]{7,40}")


def normalize_version(version: str) -> str:
    """버전 표기를 비교용으로 통일한다.

      - 'v' 접두 제거 (v1.3.7 == 1.3.7)
      - Go pseudo-version 통일: 같은 커밋을 도구마다 다르게 표기하는 문제 흡수
          Snyk    : '#1c3628e74d0f'                       (짧은 커밋 해시)
          Sparrow : '0.0.0-20211004153227-1c3628e74d0f'   (전체 pseudo-version)
        → 둘 다 커밋 해시('1c3628e74d0f')로 통일.
    """
    if not version:
        return version
    v = version.strip()

    # Snyk 짧은 형태: '#<hash>'
    if v.startswith("#") and _HEX.fullmatch(v[1:]):
        return v[1:]

    # Sparrow/Go 전체 pseudo-version: 끝의 '-<타임스탬프>-<hash>'
    m = _GO_PSEUDO.search(v)
    if m:
        return m.group(2)

    return _V_PREFIX.sub("", v)


def _extract(component: dict, source: str) -> dict:
    """각 도구 원본 dict에서 비교에 필요한 이름/버전 + 부가정보(파일경로)를 뽑아낸다.

    비교는 이름/버전으로만 하지만, 파일경로(file_paths)는 참고용으로 함께 수집한다.
    """
    if source == "blackduck":
        name = component.get("componentName")
        version = component.get("componentVersionName")
    else:  # sparrow, snyk 등 {name, version} 형태
        name = component.get("name")
        version = component.get("version")
    return {"raw_name": name, "version": version, "file_paths": component.get("file_paths") or []}


def group_by_name(components: list[dict], source: str, aliases: dict | None = None) -> dict:
    """
    정규화된 이름 -> {versions: set, raw_names: set} 로 그룹화.
    """
    if aliases is None:
        aliases = load_aliases()

    grouped: dict[str, dict] = defaultdict(
        lambda: {"versions": set(), "raw_names": set(), "file_paths": set()}
    )
    for comp in components:
        info = _extract(comp, source)
        if not info["raw_name"] or not info["version"]:
            continue
        canonical = normalize_name(info["raw_name"], aliases)
        g = grouped[canonical]
        g["versions"].add(normalize_version(info["version"]))
        g["raw_names"].add(info["raw_name"])
        g["file_paths"].update(info["file_paths"])
    return grouped


def compare(sparrow_components: list[dict], blackduck_components: list[dict]) -> dict:
    """
    반환 구조:
      exact_match        : [{name, versions, raw_names}]
      version_mismatch   : [{name, raw_names, kind: partial_overlap|no_overlap,
                             blackduck_versions, sparrow_versions, common, bd_only, sp_only}]
      only_in_blackduck  : [{name, versions, raw_names}]
      only_in_sparrow    : [{name, versions, raw_names}]
      unmatched_names    : {blackduck: set, sparrow: set}  # 퍼지 매칭 입력용
    """
    aliases = load_aliases()
    bd = group_by_name(blackduck_components, "blackduck", aliases)
    sp = group_by_name(sparrow_components, "sparrow", aliases)

    exact_match = []
    version_mismatch = []
    only_in_blackduck = []
    only_in_sparrow = []

    for name in sorted(bd.keys() & sp.keys()):
        bd_v = bd[name]["versions"]
        sp_v = sp[name]["versions"]
        raw_names = sorted(bd[name]["raw_names"] | sp[name]["raw_names"])

        if bd_v == sp_v:
            exact_match.append({
                "name": name,
                "versions": sorted(bd_v),
                "raw_names": raw_names,
            })
        else:
            common = bd_v & sp_v
            version_mismatch.append({
                "name": name,
                "raw_names": raw_names,
                "kind": "partial_overlap" if common else "no_overlap",
                "blackduck_versions": sorted(bd_v),
                "sparrow_versions": sorted(sp_v),
                "common": sorted(common),
                "bd_only": sorted(bd_v - sp_v),
                "sp_only": sorted(sp_v - bd_v),
            })

    for name in sorted(bd.keys() - sp.keys()):
        only_in_blackduck.append({
            "name": name,
            "versions": sorted(bd[name]["versions"]),
            "raw_names": sorted(bd[name]["raw_names"]),
        })

    for name in sorted(sp.keys() - bd.keys()):
        only_in_sparrow.append({
            "name": name,
            "versions": sorted(sp[name]["versions"]),
            "raw_names": sorted(sp[name]["raw_names"]),
        })

    # no_overlap을 우선순위 높게: version_mismatch 안에서 정렬
    version_mismatch.sort(key=lambda x: (x["kind"] != "no_overlap", x["name"]))

    return {
        "exact_match": exact_match,
        "version_mismatch": version_mismatch,
        "only_in_blackduck": only_in_blackduck,
        "only_in_sparrow": only_in_sparrow,
        "unmatched_names": {
            "blackduck": {c["name"] for c in only_in_blackduck},
            "sparrow": {c["name"] for c in only_in_sparrow},
        },
        # 퍼지 매칭이 버전까지 보여줄 수 있도록 그룹 원본도 넘김
        "_groups": {"blackduck": bd, "sparrow": sp},
    }


# ─────────────────────────────────────────────────────────────────────────────
# N자(3자 이상) 비교: BlackDuck / Sparrow / Snyk 를 한 번에 이름·버전 기준으로 비교.
# 위의 2자 compare() 는 그대로 두고, 3자 파이프라인은 이쪽을 쓴다.
# ─────────────────────────────────────────────────────────────────────────────

# 소스 표시명 (콘솔/엑셀 헤더용)
DISPLAY_NAMES = {"blackduck": "블랙덕", "sparrow": "스패로우", "snyk": "스닉"}

# 자동 병합 임계: 이름 유사도가 이 이상(basename/코어토큰 일치 수준) '이면서'
# 버전 집합이 완전히 같을 때만 같은 패키지로 자동 병합한다. (버전이 안전장치)
AUTO_MERGE_SCORE = 93.0


def _auto_merge_variants(groups: dict, sources: list[str]) -> list[dict]:
    """이름 표기만 다른(GitHub owner/scope/node- 등) 확실한 동일 패키지를 자동 병합.

    조건: 한 도구에만 있는 이름 X 를, 다른 도구(들)에 있는 이름 Y 와 비교해
          pair_score(X,Y) >= AUTO_MERGE_SCORE  '그리고'  버전 집합이 완전히 동일하면
          X 를 Y 로 합친다. (버전 완전일치가 오검 방지 안전장치)
    groups 를 제자리 수정하고, 병합 기록 리스트를 반환한다.
    """
    # 이름 -> 그 이름을 가진 소스 집합
    name_sources: dict[str, set] = {}
    for s in sources:
        for name in groups[s]:
            name_sources.setdefault(name, set()).add(s)

    merges = []
    for a in sources:
        only_in_a = [n for n, srcs in name_sources.items() if srcs == {a}]
        # a 에 없는 이름들(다른 도구엔 있음) + 그 버전 합집합
        others = {}
        for n, srcs in name_sources.items():
            if a in srcs:
                continue
            vs = set()
            for s2 in srcs:
                vs.update(groups[s2][n]["versions"])
            others[n] = vs

        for x in only_in_a:
            xv = set(groups[a][x]["versions"])
            best = None  # (score, name)
            for y, yv in others.items():
                if xv != yv:  # 버전 완전일치만 (안전장치)
                    continue
                sc = pair_score(x, y)
                if sc >= AUTO_MERGE_SCORE and (best is None or sc > best[0]):
                    best = (sc, y)
            if best:
                merges.append({
                    "from_source": a, "from_name": x, "to_name": best[1],
                    "versions": sorted(xv), "score": round(best[0], 1),
                })

    # 적용: from_name 그룹을 to_name 으로 이동/병합
    for m in merges:
        a, x, y = m["from_source"], m["from_name"], m["to_name"]
        if x not in groups[a]:
            continue
        dst = groups[a].setdefault(y, {"versions": set(), "raw_names": set(), "file_paths": set()})
        src = groups[a].pop(x)
        dst["versions"].update(src["versions"])
        dst["raw_names"].update(src["raw_names"])
        dst["file_paths"].update(src["file_paths"])

    return merges


def compare_multi(components_by_source: dict[str, list[dict]],
                  aliases: dict | None = None) -> dict:
    """
    여러 SCA 도구의 컴포넌트를 정규화된 이름 단위로 묶어 교차 비교한다.

    components_by_source: {"blackduck": [...], "sparrow": [...], "snyk": [...]}
      (dict 삽입 순서가 곧 소스 표시 순서가 됨)

    각 패키지(정규화 이름)마다 어떤 도구에 있는지(present_in)와
    도구별 버전 집합(versions), 그리고 그 도구들 사이 버전 일치 여부(agreement)를 계산한다.
      agreement = "single"   : 한 도구에만 존재 (비교 불가)
                  "all_same" : 보유한 모든 도구의 버전 집합이 동일
                  "mismatch" : 보유한 도구들 사이 버전 집합이 다름
    """
    if aliases is None:
        aliases = load_aliases()

    sources = list(components_by_source.keys())
    groups = {
        src: group_by_name(comps, src, aliases)
        for src, comps in components_by_source.items()
    }

    # 이름 표기만 다르고 버전이 완전히 같은 확실한 동일 패키지는 자동 병합 (검토후보에서 제외)
    auto_merged = _auto_merge_variants(groups, sources)

    all_names = set()
    for g in groups.values():
        all_names |= g.keys()

    rows = []
    for name in sorted(all_names):
        present = [s for s in sources if name in groups[s]]
        versions = {s: sorted(groups[s][name]["versions"]) for s in present}
        file_paths = {s: sorted(groups[s][name]["file_paths"]) for s in present}
        raw_names = sorted(set().union(*(groups[s][name]["raw_names"] for s in present)))

        vsets = [set(versions[s]) for s in present]
        if len(present) == 1:
            agreement = "single"
        elif all(v == vsets[0] for v in vsets):
            agreement = "all_same"
        else:
            agreement = "mismatch"

        rows.append({
            "name": name,
            "present_in": present,
            "coverage": len(present),
            "versions": versions,
            "file_paths": file_paths,  # 참고용 자산경로 (비교엔 미사용)
            "raw_names": raw_names,
            "agreement": agreement,
        })

    n_sources = len(sources)

    # 버킷 분류
    full_match = [r for r in rows if r["coverage"] == n_sources and r["agreement"] == "all_same"]
    full_mismatch = [r for r in rows if r["coverage"] == n_sources and r["agreement"] == "mismatch"]
    partial = [r for r in rows if 1 < r["coverage"] < n_sources]
    unique = [r for r in rows if r["coverage"] == 1]

    # 버전 충돌: 2개 이상 도구가 보유하면서 버전이 어긋난 것 (가장 액션이 필요한 항목)
    version_conflicts = [r for r in rows if r["coverage"] >= 2 and r["agreement"] == "mismatch"]
    version_conflicts.sort(key=lambda r: (-r["coverage"], r["name"]))

    # 존재 조합별 개수 (예: "blackduck+sparrow+snyk", "blackduck+snyk", ...)
    combo_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        combo_counts["+".join(r["present_in"])] += 1

    # 한 도구에만 존재하는 항목 (도구별)
    only_in = {s: [r for r in unique if r["present_in"] == [s]] for s in sources}

    return {
        "sources": sources,
        "rows": rows,
        "buckets": {
            "full_match": full_match,
            "full_mismatch": full_mismatch,
            "partial": partial,
            "unique": unique,
        },
        "version_conflicts": version_conflicts,
        "combo_counts": dict(combo_counts),
        "only_in": only_in,
        "auto_merged": auto_merged,
        "_groups": groups,
    }

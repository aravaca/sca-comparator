"""
비교 결과 출력: 콘솔(사람용 섹션) + JSON(다음 단계 파싱용).
"""
import json

from core.compare import DISPLAY_NAMES


def build_summary(result: dict, candidates: list[dict]) -> dict:
    vm = result["version_mismatch"]
    return {
        "exact_match": len(result["exact_match"]),
        "version_mismatch": len(vm),
        "version_mismatch_partial_overlap": sum(1 for m in vm if m["kind"] == "partial_overlap"),
        "version_mismatch_no_overlap": sum(1 for m in vm if m["kind"] == "no_overlap"),
        "only_in_blackduck": len(result["only_in_blackduck"]),
        "only_in_sparrow": len(result["only_in_sparrow"]),
        "review_candidates": len(candidates),
    }


def print_report(result: dict, candidates: list[dict]) -> None:
    summary = build_summary(result, candidates)

    print("\n" + "=" * 60)
    print(" 비교 요약 (SUMMARY)")
    print("=" * 60)
    print(f"  정확 일치 (exact_match)        : {summary['exact_match']}")
    print(f"  버전 불일치 (version_mismatch) : {summary['version_mismatch']}"
          f"  (부분겹침 {summary['version_mismatch_partial_overlap']} / "
          f"겹침없음 {summary['version_mismatch_no_overlap']})")
    print(f"  BlackDuck에만 (only_in_blackduck): {summary['only_in_blackduck']}")
    print(f"  Sparrow에만   (only_in_sparrow)  : {summary['only_in_sparrow']}")
    print(f"  검토 후보 (review_candidates)  : {summary['review_candidates']}")

    # --- 버전 불일치: no_overlap 우선(이미 정렬됨) ---
    if result["version_mismatch"]:
        print("\n" + "-" * 60)
        print(" [버전 불일치] 이름은 같은데 버전 집합이 다름")
        print("-" * 60)
        for m in result["version_mismatch"]:
            flag = "🔴 겹침없음" if m["kind"] == "no_overlap" else "🟡 부분겹침"
            print(f"  {flag}  {m['name']}")
            print(f"      BlackDuck: {m['blackduck_versions']}")
            print(f"      Sparrow  : {m['sparrow_versions']}")
            print(f"      공통={m['common']}  BD만={m['bd_only']}  SP만={m['sp_only']}")

    # --- 검토 후보(퍼지) ---
    if candidates:
        print("\n" + "-" * 60)
        print(" [검토 후보] 자동 병합 안 함 — 사람이 확인 필요")
        print("-" * 60)
        for c in candidates:
            print(f"  {c['score']:>5}점 [{c['band']}]  "
                  f"BD '{c['blackduck_name']}' {c['blackduck_versions']}  ~  "
                  f"SP '{c['sparrow_name']}' {c['sparrow_versions']}")

    # --- only_in_* 목록 ---
    if result["only_in_blackduck"]:
        print("\n" + "-" * 60)
        print(" [BlackDuck에만 존재]")
        print("-" * 60)
        for m in result["only_in_blackduck"]:
            print(f"  {m['name']} {m['versions']}")

    if result["only_in_sparrow"]:
        print("\n" + "-" * 60)
        print(" [Sparrow에만 존재]")
        print("-" * 60)
        for m in result["only_in_sparrow"]:
            print(f"  {m['name']} {m['versions']}")
    print()


def _disp(src: str) -> str:
    return DISPLAY_NAMES.get(src, src)


def _versions_str(row: dict, src: str) -> str:
    """해당 도구가 보유한 버전 문자열. 미보유면 '-'."""
    if src not in row["present_in"]:
        return "-"
    return ", ".join(row["versions"][src]) or "(버전없음)"


def build_summary_multi(result: dict) -> dict:
    sources = result["sources"]
    b = result["buckets"]
    return {
        "total_packages": len(result["rows"]),
        "in_all_tools": len(b["full_match"]) + len(b["full_mismatch"]),
        "in_all_tools_version_ok": len(b["full_match"]),
        "in_all_tools_version_conflict": len(b["full_mismatch"]),
        "version_conflicts_total": len(result["version_conflicts"]),
        "partial_coverage": len(b["partial"]),
        "unique_total": len(b["unique"]),
        "only_in": {s: len(result["only_in"][s]) for s in sources},
    }


def print_report_multi(result: dict) -> None:
    sources = result["sources"]
    summary = build_summary_multi(result)
    n = len(sources)
    disp = " / ".join(_disp(s) for s in sources)

    print("\n" + "=" * 66)
    print(f" 3자 비교 요약 (SUMMARY)  —  {disp}")
    print("=" * 66)
    print(f"  전체 패키지 수                 : {summary['total_packages']}")
    print(f"  {n}개 도구 모두 존재            : {summary['in_all_tools']}"
          f"  (버전일치 {summary['in_all_tools_version_ok']} / "
          f"버전충돌 {summary['in_all_tools_version_conflict']})")
    print(f"  일부 도구에만 존재 (2~{n-1}개)   : {summary['partial_coverage']}")
    print(f"  단독 존재 (1개 도구)           : {summary['unique_total']}")
    print(f"  버전 충돌 총계(2개 이상 보유)  : {summary['version_conflicts_total']}")
    for s in sources:
        print(f"    └ {_disp(s)}에만 존재          : {summary['only_in'][s]}")

    # --- 존재 조합별 분포 ---
    print("\n" + "-" * 66)
    print(" [존재 조합별 분포]")
    print("-" * 66)
    for combo, cnt in sorted(result["combo_counts"].items(), key=lambda x: (-x[1], x[0])):
        label = " + ".join(_disp(s) for s in combo.split("+"))
        print(f"  {cnt:>4}  {label}")

    # --- 버전 충돌: 여러 도구가 보유하지만 버전이 어긋남 ---
    if result["version_conflicts"]:
        print("\n" + "-" * 66)
        print(" [버전 충돌] 2개 이상 도구가 같은 패키지를 다른 버전으로 봄")
        print("-" * 66)
        for r in result["version_conflicts"]:
            flag = "🔴" if r["coverage"] == n else "🟡"
            print(f"  {flag}  {r['name']}  (보유 {r['coverage']}/{n})")
            for s in sources:
                print(f"      {_disp(s):<10}: {_versions_str(r, s)}")

    # --- 단독 존재 (도구별) ---
    for s in sources:
        items = result["only_in"][s]
        if not items:
            continue
        print("\n" + "-" * 66)
        print(f" [{_disp(s)}에만 존재]")
        print("-" * 66)
        for r in items:
            print(f"  {r['name']}  {', '.join(r['versions'][s])}")
    print()


def save_json_multi(result: dict, path: str = "comparison_multi.json") -> str:
    summary = build_summary_multi(result)
    payload = {
        "summary": summary,
        "sources": result["sources"],
        "combo_counts": result["combo_counts"],
        "version_conflicts": result["version_conflicts"],
        "buckets": result["buckets"],
        "rows": result["rows"],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f" JSON(3자) 저장 완료: {path}")
    return path


def save_json(result: dict, candidates: list[dict], path: str = "comparison_result.json") -> str:
    summary = build_summary(result, candidates)
    payload = {
        "summary": summary,
        "exact_match": result["exact_match"],
        "version_mismatch": result["version_mismatch"],
        "only_in_blackduck": result["only_in_blackduck"],
        "only_in_sparrow": result["only_in_sparrow"],
        "review_candidates": candidates,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f" JSON 저장 완료: {path}")
    return path

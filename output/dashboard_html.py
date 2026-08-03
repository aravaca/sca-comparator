"""비교 결과를 단일 HTML 대시보드로 출력한다.

엑셀과 같은 JSON 에서 만들기 때문에 수치가 항상 일치한다.
외부 라이브러리·서버·인터넷 없이 파일 하나로 열린다(발표 중 사고 방지).

색 배정은 도구 이름에 고정되어 있어 실행마다 색이 뒤바뀌지 않는다.
팔레트는 접근성 검증(색각이상 분리도 ΔE 32.6, 명암비 3:1 이상)을 통과한 값이다.
"""

import json
import os
from datetime import datetime

from core.compare import DISPLAY_NAMES

# 도구 -> 색 (고정 배정. 순위가 아니라 '주체' 를 따라간다)
TOOL_COLORS = {
    "blackduck": "#3B6FF5",
    "sparrow": "#C2410C",
    "snyk": "#7C3AED",
}
_FALLBACK = ["#3B6FF5", "#C2410C", "#7C3AED"]


def _disp(source: str) -> str:
    return DISPLAY_NAMES.get(source, source)


def _build_payload(result: dict, candidates: list[dict] | None) -> dict:
    sources = result["sources"]
    rows = result["rows"]
    buckets = result["buckets"]
    total = len(rows)

    colors = {
        s: TOOL_COLORS.get(s, _FALLBACK[i % len(_FALLBACK)])
        for i, s in enumerate(sources)
    }

    # 단독 검출 수. 실행 중 result 는 only_in 이 '목록', 저장된 JSON 은
    # summary.only_in 이 '개수' 라서 양쪽 형태를 모두 받아준다(저장본으로 재생성 가능).
    def _only_count(s: str) -> int:
        raw = (result.get("only_in") or {}).get(s)
        if raw is None:
            raw = ((result.get("summary") or {}).get("only_in") or {}).get(s, 0)
        return raw if isinstance(raw, int) else len(raw)

    per_tool = []
    for s in sources:
        held = sum(1 for r in rows if s in r["present_in"])
        per_tool.append({
            "key": s,
            "label": _disp(s),
            "color": colors[s],
            "held": held,
            "only": _only_count(s),
        })

    # 조합별 (벤 영역). combo 키는 'a+b' 형태
    combos = []
    for combo, cnt in result["combo_counts"].items():
        parts = combo.split("+")
        combos.append({
            "keys": parts,
            "label": " + ".join(_disp(p) for p in parts),
            "count": cnt,
            "size": len(parts),
        })
    combos.sort(key=lambda c: (-c["count"], c["label"]))

    in_all = len(buckets["full_match"]) + len(buckets["full_mismatch"])

    # 버전충돌 상세 (표로 보여줄 것).
    # 지표 타일도 반드시 이 목록의 길이를 쓴다 - 예전에 타일은 full_mismatch(모든 도구에
    # 존재 + 버전 다름), 표는 version_conflicts(부분 커버리지 포함) 를 써서 5 vs 8 로 어긋났다.
    all_conflicts = result.get("version_conflicts") or []
    conflicts = [
        {"name": c["name"],
         "versions": {s: c["versions"].get(s, []) for s in sources}}
        for c in all_conflicts[:200]
    ]

    return {
        "meta": {
            "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "sparrowProject": os.getenv("PROJECT_KEY") or "-",
            "blackduckProject": os.getenv("BLACKDUCK_PROJECT_KEY") or "-",
            "scanPath": os.getenv("SCAN_PATH") or os.getenv("SNYK_PROJECT_PATH") or "-",
        },
        "sources": [{"key": s, "label": _disp(s), "color": colors[s]} for s in sources],
        "totals": {
            "total": total,
            "inAll": in_all,
            "versionOk": len(buckets["full_match"]),
            "versionConflict": len(all_conflicts),
            "partial": len(buckets["partial"]),
            "unique": len(buckets["unique"]),
            "autoMerged": len(result.get("auto_merged") or []),
            "reviewCandidates": len(candidates or []),
        },
        "perTool": per_tool,
        "combos": combos,
        "conflicts": conflicts,
    }


def export_dashboard_html(result: dict, candidates: list[dict] | None = None,
                          path: str = "dashboard.html") -> str:
    payload = _build_payload(result, candidates)
    html = _TEMPLATE.replace(
        "/*__DATA__*/", json.dumps(payload, ensure_ascii=False)
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f" 대시보드 저장 완료: {path}")
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SCA 3사 비교 결과</title>
<style>
:root{
  --navy:#0A1E3D; --navy2:#123258; --blue:#2E6BF0;
  --ink:#0D1B2E; --muted:#5A6B84; --muted2:#8494AC;
  --ground:#F3F6FC; --surface:#FFFFFF; --border:#DEE7F3; --line:#EAF0F8;
  --good:#1E9D6B; --warn:#B45309; --bad:#C2410C;
  --sans:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Segoe UI",system-ui,sans-serif;
  --mono:"Cascadia Code",Consolas,"D2Coding",ui-monospace,monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    --ink:#E8EEF7; --muted:#9FB0C6; --muted2:#7688A0;
    --ground:#0A1420; --surface:#111E2F; --border:#1F3145; --line:#1A2A3C;
  }
}
:root[data-theme="dark"]{
  --ink:#E8EEF7; --muted:#9FB0C6; --muted2:#7688A0;
  --ground:#0A1420; --surface:#111E2F; --border:#1F3145; --line:#1A2A3C;
}
:root[data-theme="light"]{
  --ink:#0D1B2E; --muted:#5A6B84; --muted2:#8494AC;
  --ground:#F3F6FC; --surface:#FFFFFF; --border:#DEE7F3; --line:#EAF0F8;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--sans);background:var(--ground);color:var(--ink);
  -webkit-font-smoothing:antialiased;line-height:1.5}
.wrap{max-width:1160px;margin:0 auto;padding:32px 24px 64px}

/* 헤더 */
header{background:linear-gradient(140deg,var(--navy),var(--navy2));border-radius:18px;
  padding:30px 34px;color:#fff;margin-bottom:22px}
header h1{font-size:26px;font-weight:800;letter-spacing:-.02em}
header .sub{margin-top:8px;font-size:14px;color:#B9CBE6}
header .meta{margin-top:18px;display:flex;flex-wrap:wrap;gap:8px 26px;font-size:13px;color:#9DB4D6}
header .meta b{color:#fff;font-weight:600}
.tools{margin-top:18px;display:flex;flex-wrap:wrap;gap:10px}
.tool-chip{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.09);
  border:1px solid rgba(255,255,255,.16);border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;flex:none}

/* 지표 타일 */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:14px;margin-bottom:22px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 20px}
.tile .k{font-size:12px;font-weight:700;color:var(--muted);letter-spacing:.02em}
.tile .v{font-size:30px;font-weight:800;margin-top:6px;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums}
.tile .s{font-size:12px;color:var(--muted2);margin-top:2px}
.tile.accent .v{color:var(--blue)}

/* 카드 */
.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;
  padding:24px 26px;margin-bottom:20px}
.card h2{font-size:17px;font-weight:750;letter-spacing:-.01em}
.card .desc{font-size:13px;color:var(--muted);margin-top:5px}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}
@media(max-width:900px){.grid2{grid-template-columns:1fr}}

/* 벤 */
.venn-wrap{display:flex;justify-content:center;padding:6px 0 2px}
svg.venn{width:100%;max-width:470px;height:auto}
svg.venn .region{cursor:default;transition:opacity .15s}
svg.venn.dim .region:not(.on){opacity:.28}
svg.venn text{font-family:var(--sans);pointer-events:none}
.rc{font-size:19px;font-weight:800;fill:var(--ink);font-variant-numeric:tabular-nums}
.rl{font-size:11px;fill:var(--muted);font-weight:600}
.cl{font-size:13px;font-weight:750}

/* 막대 */
.bars{display:flex;flex-direction:column;gap:13px;margin-top:4px}
.bar-row{display:grid;grid-template-columns:88px 1fr auto;gap:12px;align-items:center}
.bar-row .nm{font-size:13px;font-weight:650;display:flex;align-items:center;gap:7px}
/* 조합 이름은 '블랙덕 + 스패로우 + 스닉' 처럼 길어서 폭을 따로 준다 */
.combo-row{grid-template-columns:152px 1fr auto}
.combo-nm{font-size:11.5px;font-weight:650;color:var(--muted);line-height:1.35;
  display:block;word-break:keep-all}
.track{background:var(--line);border-radius:5px;height:11px;overflow:hidden;display:flex}
.fill{height:100%;border-radius:5px}
.fill+.fill{margin-left:2px}
.bar-row .vl{font-size:13px;font-weight:700;font-variant-numeric:tabular-nums;min-width:52px;text-align:right}
.legend{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:14px;font-size:12px;color:var(--muted)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:11px;height:11px;border-radius:3px;flex:none}
.sw.hatch{background-image:repeating-linear-gradient(45deg,currentColor 0 3px,transparent 3px 6px)}

/* 표 */
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11.5px;font-weight:700;color:var(--muted);letter-spacing:.03em;
  border-bottom:1.5px solid var(--border);white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:var(--mono);font-size:12px}
.scroll{overflow-x:auto}
details{margin-top:14px}
summary{cursor:pointer;font-size:13px;color:var(--muted);font-weight:600;
  padding:7px 0;user-select:none}
summary:hover{color:var(--blue)}
summary:focus-visible{outline:2px solid var(--blue);outline-offset:3px;border-radius:4px}
.empty{font-size:13px;color:var(--muted);padding:14px 0}

/* 툴팁 */
#tip{position:fixed;z-index:99;background:var(--navy);color:#fff;border-radius:9px;
  padding:9px 13px;font-size:12.5px;line-height:1.5;pointer-events:none;opacity:0;
  transition:opacity .12s;box-shadow:0 6px 20px rgba(10,30,61,.28);max-width:280px}
#tip b{font-weight:700}
#tip .tv{font-variant-numeric:tabular-nums;font-weight:800;font-size:14px}
footer{margin-top:26px;font-size:12px;color:var(--muted2);text-align:center}
</style>
</head>
<body>
<div class="wrap" id="app"></div>
<div id="tip"></div>
<script>
const DATA = /*__DATA__*/;

const $ = (h) => { const t=document.createElement('template'); t.innerHTML=h.trim(); return t.content.firstChild; };
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = (n,d) => d ? (n/d*100) : 0;
const fmtPct = (n,d) => d ? (n/d*100).toFixed(1)+'%' : '0%';

/* ---------- 툴팁 ---------- */
const tip = document.getElementById('tip');
function showTip(e, html){
  tip.innerHTML = html; tip.style.opacity = '1';
  const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + pad, y = e.clientY + pad;
  if (x + w > innerWidth - 8) x = e.clientX - w - pad;
  if (y + h > innerHeight - 8) y = e.clientY - h - pad;
  tip.style.left = x+'px'; tip.style.top = y+'px';
}
const hideTip = () => tip.style.opacity = '0';

/* ---------- 헤더 ---------- */
function header(){
  const m = DATA.meta, n = DATA.sources.length;
  const chips = DATA.sources.map(s =>
    `<span class="tool-chip"><i class="dot" style="background:${s.color}"></i>${esc(s.label)}</span>`).join('');
  // 값이 없는 항목('-')은 아예 표시하지 않는다(빈 칸이 오히려 지저분해서)
  const metas = [
    ['분석 일시', m.generated],
    ['스캔 경로', m.scanPath],
    ['스패로우 프로젝트', m.sparrowProject],
    ['블랙덕 프로젝트', m.blackduckProject],
  ].filter(([,v]) => v && v !== '-')
   .map(([k,v]) => `<span>${k} <b>${esc(v)}</b></span>`).join('');
  return `<header>
    <h1>SCA ${n}사 컴포넌트 비교 결과</h1>
    <div class="sub">같은 소스코드를 ${n}개 도구로 각각 분석해 검출 결과를 대조한 자동 비교 리포트</div>
    <div class="meta">${metas}</div>
    <div class="tools">${chips}</div>
  </header>`;
}

/* ---------- 지표 타일 ---------- */
function tiles(){
  const t = DATA.totals, n = DATA.sources.length;
  const items = [
    {k:'전체 고유 패키지', v:t.total, s:'같은 패키지는 1개로 계산', accent:false},
    {k:`${n}개 도구 모두 검출`, v:t.inAll, s:fmtPct(t.inAll,t.total)+' · 합의된 패키지', accent:true},
    {k:'버전 충돌', v:t.versionConflict, s:'검출은 됐지만 버전이 다름', accent:false},
    {k:'일부 도구만 검출', v:t.partial, s:'한쪽이 놓쳤거나 이름이 달라 매칭 실패', accent:false},
    {k:'단독 검출', v:t.unique, s:'한 도구에서만 나옴', accent:false},
    {k:'사람이 검토할 건', v:t.reviewCandidates, s:`자동병합 ${t.autoMerged}건 제외 후`, accent:false},
  ];
  return `<div class="tiles">${items.map(i=>`
    <div class="tile ${i.accent?'accent':''}">
      <div class="k">${esc(i.k)}</div>
      <div class="v">${i.v.toLocaleString()}</div>
      <div class="s">${esc(i.s)}</div>
    </div>`).join('')}</div>`;
}

/* ---------- 벤다이어그램 (2~3개 도구) ---------- */
function venn(){
  const S = DATA.sources, n = S.length, total = DATA.totals.total;
  if (n < 2 || n > 3) return '';

  // combo 키 -> count 조회용
  const cnt = {};
  DATA.combos.forEach(c => cnt[[...c.keys].sort().join('|')] = c.count);
  const get = (...keys) => cnt[[...keys].sort().join('|')] || 0;

  let circles, regions, labels;
  if (n === 3){
    const [a,b,c] = S.map(s=>s.key);
    circles = [
      {cx:200, cy:180, r:112, s:S[0]},
      {cx:322, cy:180, r:112, s:S[1]},
      {cx:261, cy:286, r:112, s:S[2]},
    ];
    regions = [
      {keys:[a],      x:132, y:145},
      {keys:[b],      x:390, y:145},
      {keys:[c],      x:261, y:362},
      {keys:[a,b],    x:261, y:132},
      {keys:[a,c],    x:180, y:272},
      {keys:[b,c],    x:342, y:272},
      {keys:[a,b,c],  x:261, y:213},
    ];
    labels = [
      {x:132, y:66,  s:S[0], anchor:'middle'},
      {x:390, y:66,  s:S[1], anchor:'middle'},
      {x:261, y:424, s:S[2], anchor:'middle'},
    ];
  } else {
    const [a,b] = S.map(s=>s.key);
    circles = [
      {cx:200, cy:200, r:125, s:S[0]},
      {cx:322, cy:200, r:125, s:S[1]},
    ];
    regions = [
      {keys:[a],   x:140, y:205},
      {keys:[b],   x:382, y:205},
      {keys:[a,b], x:261, y:205},
    ];
    labels = [
      {x:140, y:56,  s:S[0], anchor:'middle'},
      {x:382, y:56,  s:S[1], anchor:'middle'},
    ];
  }

  const circleSvg = circles.map(c =>
    `<circle cx="${c.cx}" cy="${c.cy}" r="${c.r}" fill="${c.s.color}" fill-opacity="0.16"
             stroke="${c.s.color}" stroke-width="2"/>`).join('');

  const labelSvg = labels.map(l =>
    `<text class="cl" x="${l.x}" y="${l.y}" text-anchor="${l.anchor}" fill="${l.s.color}">${esc(l.s.label)}</text>`).join('');

  const regionSvg = regions.map((r,i) => {
    const v = get(...r.keys);
    const names = r.keys.map(k => S.find(s=>s.key===k).label).join(' + ');
    return `<g class="region" data-i="${i}" data-names="${esc(names)}" data-v="${v}"
               pointer-events="all">
      <circle cx="${r.x}" cy="${r.y}" r="26" fill="transparent"/>
      <text class="rc" x="${r.x}" y="${r.y}" text-anchor="middle" dominant-baseline="central">${v.toLocaleString()}</text>
      <text class="rl" x="${r.x}" y="${r.y+17}" text-anchor="middle">${fmtPct(v,total)}</text>
    </g>`;
  }).join('');

  return `<div class="card">
    <h2>검출 결과 겹침 구조</h2>
    <div class="desc">각 영역의 숫자는 그 조합에만 해당하는 패키지 수입니다. 원의 크기는 수치와 무관합니다 — 정확한 크기 비교는 아래 막대를 보세요.</div>
    <div class="venn-wrap">
      <svg class="venn" viewBox="0 0 522 445" role="img" aria-label="도구별 검출 겹침 벤다이어그램">
        ${circleSvg}${labelSvg}${regionSvg}
      </svg>
    </div>
  </div>`;
}

/* ---------- 조합별 막대 (벤의 정확한 크기 비교) ---------- */
function comboBars(){
  const total = DATA.totals.total;
  const max = Math.max(...DATA.combos.map(c=>c.count), 1);
  // 조합을 이루는 도구들의 색을 2px 간격으로 나눠 칠해 '어느 조합인지' 를 막대에서도 읽히게 한다.
  const rows = DATA.combos.map(c => {
    const segs = c.keys.map(k => (DATA.sources.find(s=>s.key===k)||{}).color).filter(Boolean);
    const w = pct(c.count, max);
    const fills = segs.map(col =>
      `<div class="fill" style="flex:1;background:${col}"></div>`).join('');
    return `<div class="bar-row combo-row" data-tip="${esc(c.label)}|${c.count}|${fmtPct(c.count,total)}">
      <div class="nm combo-nm">${esc(c.label)}</div>
      <div class="track"><div style="width:max(6px,${w}%);display:flex">${fills}</div></div>
      <div class="vl">${c.count.toLocaleString()}</div>
    </div>`;
  }).join('');

  return `<div class="card">
    <h2>조합별 패키지 수</h2>
    <div class="desc">위 벤다이어그램의 각 영역을 크기순으로 정확히 비교한 것입니다. 막대 색은 그 조합에 포함된 도구를 나타냅니다.</div>
    <div class="bars" style="margin-top:16px">${rows}</div>
  </div>`;
}

/* ---------- 도구별 검출량 ---------- */
function toolBars(){
  const total = DATA.totals.total;
  const max = Math.max(...DATA.perTool.map(t=>t.held), 1);
  const rows = DATA.perTool.map(t => {
    const shared = t.held - t.only;
    return `<div class="bar-row" data-tip="${esc(t.label)}|${t.held}|공유 ${shared} · 단독 ${t.only}">
      <div class="nm"><i class="dot" style="background:${t.color}"></i>${esc(t.label)}</div>
      <div class="track">
        <div class="fill" style="width:max(4px,${pct(shared,max)}%);background:${t.color}"></div>
        <div class="fill" style="width:${pct(t.only,max)}%;background:${t.color};opacity:.42"></div>
      </div>
      <div class="vl">${t.held.toLocaleString()}</div>
    </div>`;
  }).join('');
  return `<div class="card">
    <h2>도구별 검출량</h2>
    <div class="desc">진한 부분은 다른 도구와 공유, 흐린 부분은 그 도구만 검출한 패키지입니다.</div>
    <div class="bars" style="margin-top:16px">${rows}</div>
    <div class="legend">
      <span><i class="sw" style="background:var(--muted)"></i>공유 검출</span>
      <span><i class="sw" style="background:var(--muted);opacity:.42"></i>단독 검출</span>
    </div>
  </div>`;
}

/* ---------- 버전 충돌 ---------- */
function conflicts(){
  const c = DATA.conflicts;
  if (!c.length) return `<div class="card"><h2>버전 충돌</h2>
    <div class="empty">버전 충돌이 없습니다 — 공통 검출된 패키지의 버전이 모두 일치합니다.</div></div>`;
  const th = DATA.sources.map(s=>`<th>${esc(s.label)}</th>`).join('');
  const tr = c.map(r => `<tr><td class="mono">${esc(r.name)}</td>${
    DATA.sources.map(s=>{
      const vs = r.versions[s.key] || [];
      return `<td class="mono">${vs.length?esc(vs.join(', ')):'<span style="color:var(--muted2)">-</span>'}</td>`;
    }).join('')}</tr>`).join('');
  return `<div class="card">
    <h2>버전 충돌 <span style="color:var(--muted);font-weight:600;font-size:14px">${c.length}건</span></h2>
    <div class="desc">이름은 같은데 도구마다 버전 집합이 다른 패키지입니다.</div>
    <div class="scroll" style="margin-top:14px"><table>
      <thead><tr><th>패키지</th>${th}</tr></thead><tbody>${tr}</tbody></table></div>
  </div>`;
}

/* ---------- 데이터 표 (접근성 대체 뷰) ---------- */
function dataTable(){
  const total = DATA.totals.total;
  const rows = DATA.combos.map(c =>
    `<tr><td>${esc(c.label)}</td><td class="num">${c.count.toLocaleString()}</td>
     <td class="num">${fmtPct(c.count,total)}</td></tr>`).join('');
  const trows = DATA.perTool.map(t =>
    `<tr><td>${esc(t.label)}</td><td class="num">${t.held.toLocaleString()}</td>
     <td class="num">${t.only.toLocaleString()}</td></tr>`).join('');
  return `<div class="card"><h2>수치 표</h2>
    <div class="desc">위 그래프와 같은 데이터입니다.</div>
    <details open><summary>조합별 분포</summary>
      <div class="scroll"><table><thead><tr><th>조합</th><th class="num">패키지 수</th><th class="num">비율</th></tr></thead>
      <tbody>${rows}</tbody></table></div></details>
    <details><summary>도구별 검출량</summary>
      <div class="scroll"><table><thead><tr><th>도구</th><th class="num">총 검출</th><th class="num">단독 검출</th></tr></thead>
      <tbody>${trows}</tbody></table></div></details>
  </div>`;
}

/* ---------- 렌더 ---------- */
document.getElementById('app').innerHTML =
  header() + tiles() + venn() +
  `<div class="grid2">${comboBars()}${toolBars()}</div>` +
  conflicts() + dataTable() +
  `<footer>SCA 비교 자동화 스크립트가 생성한 리포트입니다 · 엑셀 결과와 동일한 데이터</footer>`;

/* 벤 영역 hover */
const svg = document.querySelector('svg.venn');
if (svg){
  svg.querySelectorAll('.region').forEach(g => {
    g.addEventListener('mouseenter', e => {
      svg.classList.add('dim'); g.classList.add('on');
      showTip(e, `<b>${g.dataset.names}</b><br><span class="tv">${(+g.dataset.v).toLocaleString()}개</span>
                  <span style="color:#9DB4D6"> · 이 조합에만 해당</span>`);
    });
    g.addEventListener('mousemove', e => showTip(e, tip.innerHTML));
    g.addEventListener('mouseleave', () => {
      svg.classList.remove('dim'); g.classList.remove('on'); hideTip();
    });
  });
}
/* 막대 hover */
document.querySelectorAll('[data-tip]').forEach(el => {
  const [a,b,c] = el.dataset.tip.split('|');
  el.addEventListener('mouseenter', e => showTip(e, `<b>${a}</b><br><span class="tv">${(+b).toLocaleString()}개</span><br><span style="color:#9DB4D6">${c}</span>`));
  el.addEventListener('mousemove', e => showTip(e, tip.innerHTML));
  el.addEventListener('mouseleave', hideTip);
});
</script>
</body>
</html>
"""

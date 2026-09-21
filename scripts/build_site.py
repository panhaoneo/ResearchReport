#!/usr/bin/env python3
"""构建 GitHub Pages 站点：读取 data/reports/*.jsonl，生成 docs/ 下的页面与数据分片。

用法：python3 scripts/build_site.py
"""
import hashlib
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CONFIG_DIR, DATA_DIR, DOCS_DIR, PDFS_DIR, build_ranking_index, dump_deterministic,
    infer_industry, load_all_records, load_cfg, load_state, log, match_xcf, month_of,
    normalize_org, now_bj, write_if_changed, write_json_if_changed,
)

PAGES = ["index", "browse", "ranking", "report", "about"]


def enrich(rec, ctx):
    """研报记录 → 站点短键记录（新财富标签/行业映射在构建期计算，配置改动即时生效）。"""
    org_canon = normalize_org(rec.get("org"), ctx["alias"])
    ind_xcf = infer_industry(rec.get("industry_em"), None, rec.get("title", ""), ctx["industry_map"])
    x = match_xcf(org_canon, ind_xcf, ctx["ranking_index"], ctx["t0i"], ctx["t0t"])
    pdf = rec.get("pdf") or {}
    links = rec.get("links") or {}
    out = {
        "i": rec["id"], "t": rec.get("title", ""), "d": rec.get("date", ""),
        "o": ctx["org_disp"].get(org_canon) or rec.get("org", ""),
        "oc": org_canon, "q": rec.get("qtype"), "ti": x["tier"], "x": x["best_rank"],
        "xt": x["tags"], "ps": pdf.get("status") or "unavailable",
    }
    if rec.get("stock"):
        out["s"] = f'{rec["stock"].get("name","")}({rec["stock"].get("code","")})'
    if ind_xcf:
        out["ind"] = ind_xcf
    if rec.get("industry_em"):
        out["ie"] = rec["industry_em"]
    for src_key, dst in (("rating", "r"), ("researcher", "au"), ("pages", "pg"), ("size_kb", "kb"), ("eps", "eps")):
        if rec.get(src_key) not in (None, "", {}):
            out[dst] = rec[src_key]
    if pdf.get("status") == "local" and pdf.get("path"):
        out["p"] = pdf["path"]
    for lk, dst in (("pdf_direct", "u"), ("em_detail", "e"), ("sina", "n")):
        if links.get(lk):
            out[dst] = links[lk]
    return out


def disp_name(name):
    """展示名：仅剥离法律形式后缀（股份有限公司等），保留“证券”字样。"""
    s = (name or "").strip()
    changed = True
    while changed:
        changed = False
        for suf in ("股份有限公司", "有限责任公司", "有限公司", "证券研究所", "研究所", "股份"):
            if s.endswith(suf) and len(s) > len(suf):
                s = s[: -len(suf)]
                changed = True
    return s


def build_site_data(records, cfg, state):
    s = cfg["settings"]
    t0i = int(s["pdf"]["tier0_industry_rank_max"])
    t0t = int(s["pdf"]["tier0_team_rank_max"])
    # 机构规范显示名：上榜机构用榜单原名；其余取最短写法并剥离法律后缀
    ranking_disp = {}
    for ind_rows in (cfg["ranking"].get("industry") or {}).values():
        for row in ind_rows:
            ranking_disp.setdefault(normalize_org(row["org"], cfg["alias"]), row["org"])
    for team_rows in (cfg["ranking"].get("team") or {}).values():
        for row in team_rows:
            ranking_disp.setdefault(normalize_org(row["org"], cfg["alias"]), row["org"])
    org_disp = {}
    for rec in records.values():
        canon = normalize_org(rec.get("org"), cfg["alias"])
        name = (rec.get("org") or "").strip()
        if canon and name and (canon not in org_disp or len(name) < len(org_disp[canon])):
            org_disp[canon] = name
    for canon in list(org_disp):
        org_disp[canon] = ranking_disp.get(canon) or disp_name(org_disp[canon])
    ctx = {
        "alias": cfg["alias"], "industry_map": cfg["industry_map"],
        "ranking_index": build_ranking_index(cfg["ranking"], cfg["alias"]),
        "org_disp": org_disp, "t0i": t0i, "t0t": t0t,
    }
    site_recs = [enrich(r, ctx) for r in records.values()]
    site_recs.sort(key=lambda r: (r["d"] or "", r["i"]))
    today = now_bj().date()
    tstr = today.isoformat()

    # 月度分片
    by_month = {}
    for r in site_recs:
        by_month.setdefault(month_of(r["d"]), []).append(r)
    for m, recs in by_month.items():
        write_json_if_changed(DOCS_DIR / "data" / "months" / f"{m}.json", {"m": m, "records": recs})

    # 首页数据
    d7 = (today - timedelta(days=6)).isoformat()
    d30 = (today - timedelta(days=29)).isoformat()
    recent_floor = (today - timedelta(days=int(s["site"]["latest_days"]) - 1)).isoformat()
    featured = [r for r in site_recs if r["ti"] <= 1 and r["d"] >= d7]
    # 稳定排序两遍：先按日期倒序，再按 (tier, 名次) 升序
    featured.sort(key=lambda r: r["d"], reverse=True)
    featured.sort(key=lambda r: (r["ti"], r["x"] or 99))
    recent = [r for r in site_recs if r["d"] >= recent_floor]
    recent.sort(key=lambda r: (r["d"], r["i"]), reverse=True)
    write_json_if_changed(DOCS_DIR / "data" / "latest.json", {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M"),
        "featured": featured[:60],
        "recent": recent[:600],
    })

    # 统计与索引
    pdf_files = sorted(PDFS_DIR.glob("*.pdf"))
    pdf_bytes = sum(p.stat().st_size for p in pdf_files)
    oldest = None
    for p in pdf_files:
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
        if m and (oldest is None or m.group(1) < oldest):
            oldest = m.group(1)
    counts = {
        "total": len(site_recs),
        "today": sum(1 for r in site_recs if r["d"] == tstr),
        "d7": sum(1 for r in site_recs if r["d"] >= d7),
        "d30": sum(1 for r in site_recs if r["d"] >= d30),
        "by_type": {str(q): sum(1 for r in site_recs if r["q"] == q) for q in (0, 1, 2, 3)},
        "ranked": sum(1 for r in site_recs if r["ti"] <= 1),
        "ranked30": sum(1 for r in site_recs if r["ti"] <= 1 and r["d"] >= d30),
        "local_pdfs": sum(1 for r in site_recs if r["ps"] == "local"),
    }
    months = sorted(((m, len(recs)) for m, recs in by_month.items()), reverse=True)
    x = cfg["ranking"]
    xcf_state = state.get("xcf") or {}
    write_json_if_changed(DOCS_DIR / "data" / "index.json", {
        "generated_at": now_bj().strftime("%Y-%m-%d %H:%M"),
        "counts": counts,
        "months": [list(m) for m in months],
        "pdf_stats": {
            "count": len(pdf_files), "total_mb": round(pdf_bytes / 1048576, 1),
            "oldest": oldest,
            "effective_days": (today - date.fromisoformat(oldest)).days if oldest else 0,
            "retention_target": int(s["pdf"]["retention_days"]),
        },
        "xcf": {
            "edition": x.get("edition"), "year": x.get("year"), "announced": x.get("announced"),
            "updated_at": x.get("updated_at"), "source_url": x.get("source_url"),
            "new_edition_available": bool(xcf_state.get("new_edition_available")),
        },
        "last_run": state.get("last_run") or {},
    })

    # 筛选维度
    orgs, inds, ratings = {}, {}, {}
    for r in site_recs:
        if r["oc"]:
            o = orgs.setdefault(r["oc"], [0, 2])
            o[0] += 1
            o[1] = min(o[1], r["ti"])
        if r.get("ind"):
            inds[r["ind"]] = inds.get(r["ind"], 0) + 1
        if r.get("r"):
            ratings[r["r"]] = ratings.get(r["r"], 0) + 1
    org_list = sorted(((k, v[0], v[1]) for k, v in orgs.items()), key=lambda t: (t[2], -t[1]))[:200]
    ind_list = sorted(inds.items(), key=lambda t: -t[1])
    rat_list = sorted(ratings.items(), key=lambda t: -t[1])
    write_json_if_changed(DOCS_DIR / "data" / "facets.json", {
        "orgs": [list(o) + [org_disp.get(o[0], o[0])] for o in org_list],
        "inds": [list(i) for i in ind_list],
        "ratings": [list(r) for r in rat_list],
        "range": [site_recs[0]["d"] if site_recs else "", site_recs[-1]["d"] if site_recs else ""],
    })

    # 榜单页数据
    stats_by_org = {}
    for r in site_recs:
        if not r["oc"]:
            continue
        st = stats_by_org.setdefault(r["oc"], {"count30": 0, "count7": 0, "local": 0, "latest": ""})
        if r["d"] >= d30:
            st["count30"] += 1
        if r["d"] >= d7:
            st["count7"] += 1
        if r["ps"] == "local":
            st["local"] += 1
        if r["d"] > st["latest"]:
            st["latest"] = r["d"]

    def rows_of(board_rows):
        out = []
        for row in board_rows:
            canon = normalize_org(row["org"], cfg["alias"])
            st = stats_by_org.get(canon) or {}
            out.append({
                "rank": row["rank"], "org": row["org"], "canon": canon,
                "count30": st.get("count30", 0), "count7": st.get("count7", 0),
                "local": st.get("local", 0), "latest": st.get("latest", ""),
            })
        return out

    write_json_if_changed(DOCS_DIR / "data" / "ranking.json", {
        "meta": {k: x.get(k) for k in ("edition", "year", "announced", "updated_at", "source_url", "note")},
        "team_boards": [{"title": t, "rows": rows_of(rows)} for t, rows in (x.get("team") or {}).items()],
        "industries": [{"name": ind, "rows": rows_of(rows)} for ind, rows in (x.get("industry") or {}).items()],
    })

    # 维护清单由 known_orgs_report() 单独生成（不在本函数内写）
    return site_recs, counts


def known_orgs_report(records, cfg, site_recs):
    d30 = (now_bj().date() - timedelta(days=29)).isoformat()
    by_org = {}
    for r in site_recs:
        if r["oc"] and r["d"] >= d30:
            e = by_org.setdefault(r["oc"], [0, r["ti"]])
            e[0] += 1
            e[1] = min(e[1], r["ti"])
    top = sorted(((k, v[0], v[1]) for k, v in by_org.items()), key=lambda t: -t[1])[:40]
    idx = build_ranking_index(cfg["ranking"], cfg["alias"])
    ranked_canons = set(idx.keys())
    present = set(by_org.keys())
    unmapped = {}
    for r in records.values():
        ie = r.get("industry_em")
        if ie and ie not in (cfg["industry_map"].get("name_map") or {}):
            unmapped[ie] = unmapped.get(ie, 0) + 1
    out = {
        "generated_at": now_bj().date().isoformat(),
        "ranked_no_reports_30d": sorted(ranked_canons - present),
        "top_orgs_30d": [list(t) for t in top],
        "unmapped_industries": sorted(unmapped.items(), key=lambda t: -t[1])[:60],
    }
    write_json_if_changed(DATA_DIR / "known_orgs.json", out)
    return out


def render_pages(cfg, counts):
    """写出 6 个 HTML 壳 + robots/sitemap。"""
    v = hashlib.sha1(
        (DOCS_DIR / "assets" / "style.css").read_bytes() + (DOCS_DIR / "assets" / "app.js").read_bytes()
    ).hexdigest()[:8]
    x = cfg["ranking"]
    nav = "".join(
        f'<a href="{p}.html">{label}</a>'
        for p, label in (("index", "首页"), ("browse", "浏览"), ("ranking", "新财富榜单"), ("about", "关于"))
    )

    def shell(page, title, body, desc=""):
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<meta name="description" content="{desc}">
<link rel="stylesheet" href="assets/style.css?v={v}">
</head>
<body data-page="{page}">
<div class="topbar"><div class="inner">
  <h1><a href="index.html">研报数据库</a></h1>
  <div class="nav">{nav}</div>
  <div class="sub">每日自动更新（北京时间 12:30 / 20:30）· 数据来源：东方财富 / 新浪财经 · 榜单：新财富最佳分析师（第 {x.get('edition')} 届）· 站点构建于 <span id="gen-time">-</span></div>
</div></div>
<div class="container">
  <div id="alert-host"></div>
{body}
  <footer>
    数据库收录研报的版权归原研究机构所有，本站仅提供元数据索引与原文链接，供个人学习使用，不构成投资建议。<br>
    数据来源：东方财富研报中心 · 新浪财经研报 · 新财富（榜单）　·　<a href="about.html">关于本站</a>
  </footer>
</div>
<script src="assets/app.js?v={v}"></script>
</body>
</html>"""

    idx_body = """
  <div class="searchbar">
    <form id="search-form" style="display:flex;gap:10px;width:100%">
      <input id="search-input" placeholder="搜索标题 / 机构 / 研究员 / 股票（回车）" autocomplete="off">
      <button type="submit">搜索</button>
    </form>
  </div>
  <div class="stats" id="stats"></div>
  <div class="section">
    <h2>榜单精选<span class="hint">近 7 天 · 新财富上榜机构研报 · 按名次排序</span></h2>
    <div id="featured"></div>
  </div>
  <div class="section">
    <h2>今日最新<span class="hint">全量收录 · 时间倒序</span></h2>
    <div id="recent"></div>
  </div>"""

    browse_body = """
  <div class="filterbar">
    <div class="frow">
      <label>类型</label>
      <div class="chips" id="f-type">
        <span class="chip" data-v="0">个股</span><span class="chip" data-v="1">行业</span>
        <span class="chip" data-v="2">策略</span><span class="chip" data-v="3">宏观</span>
      </div>
      <label>时间</label>
      <div class="chips" id="f-preset">
        <span class="chip" data-v="today">今天</span><span class="chip" data-v="d7">近7天</span>
        <span class="chip" data-v="d30">近30天</span><span class="chip" data-v="d90">近90天</span>
        <span class="chip" data-v="all">全部</span>
      </div>
    </div>
    <div class="frow">
      <select id="f-org"><option value="">全部机构</option></select>
      <select id="f-ind"><option value="">全部行业</option></select>
      <select id="f-rating"><option value="">全部评级</option></select>
      <label class="checkline"><input type="checkbox" id="f-rank">仅上榜机构</label>
      <label class="checkline"><input type="checkbox" id="f-pdf">仅本地PDF</label>
    </div>
    <div class="frow">
      <label>自选</label>
      <input type="date" id="f-from"> <span style="color:#8a9199">~</span> <input type="date" id="f-to">
      <input type="text" id="f-kw" class="kw" placeholder="关键词（标题/机构/研究员）" autocomplete="off">
    </div>
  </div>
  <div class="toolbar">
    <span id="result-count">-</span>
    <span style="margin-left:auto">排序</span>
    <select id="f-sort">
      <option value="rank">榜单优先</option>
      <option value="new">时间最新</option>
      <option value="pages">页数最多</option>
    </select>
  </div>
  <div id="results"></div>"""

    ranking_body = f"""
  <div class="section">
    <h2>第二十三届新财富最佳分析师<span class="hint">榜单展示与收录情况</span></h2>
    <div id="rk-meta" style="font-size:13px;color:#8a9199"></div>
    <p style="font-size:13px;color:#8a9199;margin-top:8px">
      榜单为年度评选（结果发布于 xcf.cn，官方形式为图片，由人工转录进本仓库）。
      点击机构名可查看该机构近期研报；统计为本站已收录范围内的篇数。
      「本地PDF」为本站已保存全文的篇数。
    </p>
  </div>
  <div class="section">
    <h2>团队榜</h2>
    <div class="teamlist" id="team-boards"></div>
  </div>
  <div class="section">
    <h2>行业榜<span class="hint">每个行业前 5 名机构</span></h2>
    <div class="rankboard" id="ind-boards"></div>
  </div>"""

    report_body = """
  <div class="section detail" id="detail"></div>"""

    about_body = f"""
  <div class="section">
    <h2>关于本站</h2>
    <table class="fields">
      <tr><th>收录内容</th><td>券商研报元数据（个股 / 行业 / 策略 / 宏观四类），每日自动抓取</td></tr>
      <tr><th>数据来源</th><td>东方财富研报中心（主）、新浪财经研报（补充）、新财富榜单（xcf.cn）</td></tr>
      <tr><th>榜单版本</th><td><span id="ab-edition">-</span>（人工转录，<a href="ranking.html">查看榜单页</a>）</td></tr>
      <tr><th>更新频率</th><td>每日两次（北京时间 12:30 / 20:30），由 GitHub Actions 自动执行</td></tr>
      <tr><th>当前收录</th><td><span id="ab-total">-</span> 篇 · 站点构建于 <span id="ab-generated">-</span></td></tr>
      <tr><th>本地PDF</th><td><span id="ab-pdf">-</span>（仅上榜机构研报，滚动保留，超期自动清理并保留外链）</td></tr>
      <tr><th>榜单标记规则</th><td>机构进入所在行业前 5 名或总量团队前 3 名标记为最高优先级（金色标签）；上榜机构（前5/前10）标记为一般上榜（灰色标签）</td></tr>
    </table>
  </div>
  <div class="section">
    <h2>免责声明</h2>
    <p style="font-size:13.5px;color:#55606b">
      本站收录研报的全部版权归原研究机构及作者所有，本站仅作个人学习用途的元数据索引与链接聚合，
      不存储、不传播未授权的付费内容；「本地PDF」为抓取时公开可访问的原文快照，将按保留策略定期清理。
      站点内容不构成任何投资建议。
    </p>
  </div>"""

    def notfound(body=""):
        return shell("404", "页面不存在 · 研报数据库", """
  <div class="section"><h2>页面不存在</h2>
  <p style="font-size:14px">请从 <a href="index.html">首页</a> 或 <a href="browse.html">浏览页</a> 进入。</p></div>""")

    for page, title, body, desc in (
        ("index", "研报数据库 · 每日更新", idx_body, "每日自动更新的券商研报数据库，按新财富最佳分析师榜单标记"),
        ("browse", "浏览 · 研报数据库", browse_body, "按类型/时间/机构/行业/评级筛选研报"),
        ("ranking", "新财富榜单 · 研报数据库", ranking_body, "新财富最佳分析师榜单与机构研报收录"),
        ("report", "研报详情 · 研报数据库", report_body, "研报详情"),
        ("about", "关于 · 研报数据库", about_body, "关于本站"),
    ):
        write_if_changed(DOCS_DIR / f"{page}.html", shell(page, title, body, desc))
    write_if_changed(DOCS_DIR / "404.html", notfound())
    write_if_changed(DOCS_DIR / "robots.txt", "User-agent: *\nDisallow: /ResearchReport/pdfs/\nAllow: /\n")
    base = "https://panhaoneo.github.io/ResearchReport/"
    urls = "".join(f"  <url><loc>{base}{p}.html</loc></url>\n" for p in PAGES)
    write_if_changed(DOCS_DIR / "sitemap.xml",
                     f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n')
    return v


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_cfg()
    state = load_state()
    records = load_all_records()
    log.info("加载记录 %d 条", len(records))
    site_recs, counts = build_site_data(records, cfg, state)
    ko = known_orgs_report(records, cfg, site_recs)
    v = render_pages(cfg, counts)
    pdf_mb = round(sum(p.stat().st_size for p in PDFS_DIR.glob("*.pdf")) / 1048576, 1)
    log.info("站点生成完成 (assets v=%s): 总 %d 篇 | 今日 %d | 近7日 %d | 上榜 %d | 本地PDF %d 个 %.1fMB",
             v, counts["total"], counts["today"], counts["d7"], counts["ranked"], counts["local_pdfs"], pdf_mb)
    if pdf_mb > 700:
        log.warning("PDF 目录已达 %.1fMB，接近 850MB 上限，请检查清理策略", pdf_mb)
    if ko["unmapped_industries"]:
        log.info("未映射东财行业 %d 个（见 data/known_orgs.json）: %s",
                 len(ko["unmapped_industries"]), "、".join(k for k, _ in ko["unmapped_industries"][:8]))
    log.info("榜单机构近30日无收录 %d 家；东财高频机构 Top5: %s",
             len(ko["ranked_no_reports_30d"]),
             "、".join(f"{k}({c})" for k, c, _ in ko["top_orgs_30d"][:5]))


if __name__ == "__main__":
    main()

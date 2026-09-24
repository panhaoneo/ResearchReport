"use strict";
/* 研报数据库 · 前端（原生 JS，无框架） */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const TYPE_LABEL = { 0: "个股", 1: "行业", 2: "策略", 3: "宏观" };
const PAGE = document.body.dataset.page || "";

/* ---------- 基础工具 ---------- */

async function getJSON(path) {
  const r = await fetch(path, { cache: "no-cache" });
  if (!r.ok) throw new Error(`加载失败 ${path}: ${r.status}`);
  return r.json();
}

function safeUrl(u) {
  if (typeof u !== "string" || !u) return "#";
  try {
    // 相对链接按当前页面解析（站内 report.html?id=… / browse.html#… 均可通过），
    // 仅拦截 javascript: / data: 等危险协议
    const url = new URL(u, document.baseURI);
    if (url.protocol === "http:" || url.protocol === "https:") return u;
  } catch (e) { /* 非法 URL */ }
  return "#";
}

function el(tag, attrs = {}, children = []) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k === "href") n.setAttribute("href", safeUrl(v));
    else n.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    n.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return n;
}

function fmtMB(kb) { return kb ? (kb / 1024).toFixed(1) + "MB" : ""; }

function typeTag(q) { return el("span", { class: `tag type-${q}`, text: TYPE_LABEL[q] || "其他" }); }

function badges(r) {
  const out = [];
  for (const p of r.pk || []) out.push(el("span", { class: "tag badge-pick", text: "推荐·" + p.n }));
  for (const t of r.xt || []) out.push(el("span", { class: r.ti === 0 ? "tag badge-t0" : "tag badge-t1", text: t }));
  if (r.ps === "local") out.push(el("span", { class: "tag badge-pdf", text: "本地PDF" }));
  return out;
}

function pdfLinks(r, into) {
  if (r.ps === "local" && r.p) into.appendChild(el("a", { href: r.p, class: "local", target: "_blank", text: "本地PDF ↗" }));
  if (r.u) into.appendChild(el("a", { href: r.u, target: "_blank", text: "PDF直链 ↗" }));
  if (r.e) into.appendChild(el("a", { href: r.e, target: "_blank", text: "东财详情 ↗" }));
  if (r.n) into.appendChild(el("a", { href: r.n, target: "_blank", text: "新浪全文 ↗" }));
  return into;
}

function card(r) {
  const c = el("article", { class: "card" });
  c.appendChild(el("div", { class: "row1" }, [typeTag(r.q), ...badges(r)]));
  c.appendChild(el("h3", {}, [el("a", { href: `report.html?id=${encodeURIComponent(r.i)}&d=${r.d}`, text: r.t })]));
  const meta = [r.o, r.s, r.au, r.d].filter(Boolean);
  c.appendChild(el("div", { class: "meta" }, meta.map((m) => el("span", { text: m }))));
  const meta2 = [];
  if (r.ind || r.ie) meta2.push("行业: " + (r.ind || r.ie));
  if (r.r) meta2.push("评级: " + r.r);
  if (r.pg) meta2.push(r.pg + "页");
  if (r.kb) meta2.push(fmtMB(r.kb));
  if (meta2.length) c.appendChild(el("div", { class: "meta" }, meta2.map((m) => el("span", { text: m }))));
  c.appendChild(pdfLinks(r, el("div", { class: "links" })));
  return c;
}

/* ---------- 数据层 ---------- */

const Data = {
  index: null,
  facets: null,
  latest: null,
  months: new Map(),
  async loadIndex() { this.index = this.index || await getJSON("data/index.json"); return this.index; },
  async loadFacets() { this.facets = this.facets || await getJSON("data/facets.json"); return this.facets; },
  async loadLatest() { this.latest = this.latest || await getJSON("data/latest.json"); return this.latest; },
  async loadMonth(m) {
    if (!this.months.has(m)) {
      try {
        const d = await getJSON(`data/months/${m}.json`);
        this.months.set(m, d.records || []);
      } catch (e) {
        this.months.set(m, []);
      }
    }
    return this.months.get(m);
  },
  monthsBetween(from, to) {
    const out = [];
    if (!from || !to) return out;
    // 从 to 往回收集（最多 60 个月），保证"全部时间"优先加载最近的数据
    let y = +to.slice(0, 4), m = +to.slice(5, 7);
    const y1 = +from.slice(0, 4), m1 = +from.slice(5, 7);
    while (y > y1 || (y === y1 && m >= m1)) {
      out.push(`${y}-${String(m).padStart(2, "0")}`);
      m--; if (m < 1) { m = 12; y--; }
      if (out.length >= 60) break;
    }
    return out.reverse();
  },
};

/* ---------- 页头（提示条/统计） ---------- */

async function renderChrome() {
  const idx = await Data.loadIndex();
  const alertHost = $("#alert-host");
  if (alertHost && !alertHost.dataset.done) {
    alertHost.dataset.done = "1";
    if (idx.xcf && idx.xcf.new_edition_available) {
      alertHost.appendChild(el("div", { class: "alert", text: `检测到第 ${idx.xcf.edition + 1} 届新财富榜单已发布（当前收录第 ${idx.xcf.edition} 届）。本站榜单标签将在人工转录更新后生效。` }));
    }
  }
  const gen = $("#gen-time");
  if (gen) gen.textContent = idx.generated_at || "";
  return idx;
}

/* ---------- 首页 ---------- */

async function pageIndex() {
  const idx = await renderChrome();
  const latest = await Data.loadLatest();
  const c = idx.counts || {};
  const stats = $("#stats");
  if (stats) {
    const cells = [
      [c.total || 0, "收录研报"],
      [c.today || 0, "今日新增"],
      [(c.d7) || 0, "近7日"],
      [c.ranked || 0, "新财富上榜机构研报"],
      [c.local_pdfs || 0, "本地PDF"],
      [(idx.pdf_stats && idx.pdf_stats.total_mb || 0) + "MB", "PDF体积"],
    ];
    for (const [num, lab] of cells) stats.appendChild(el("div", { class: "stat" }, [el("div", { class: "num", text: String(num) }), el("div", { class: "lab", text: lab })]));
  }
  const picksHost = $("#picks");
  if (picksHost) {
    try {
      const pd = await getJSON("data/picks.json");
      renderPicks(pd, picksHost);
    } catch (e) {
      picksHost.appendChild(el("div", { class: "empty", text: "推荐名单数据不可用" }));
    }
  }
  const feat = $("#featured");
  if (feat) {
    const rows = (latest.featured || []).slice(0, 40);
    if (!rows.length) feat.appendChild(el("div", { class: "empty", text: "近7天暂无新财富上榜机构研报" }));
    rows.forEach((r) => feat.appendChild(card(r)));
  }
  const recent = $("#recent");
  if (recent) {
    const rows = latest.recent || [];
    let shown = 0;
    const more = el("button", { class: "more" });
    const drawMore = () => {
      rows.slice(shown, shown + 100).forEach((r) => recent.insertBefore(card(r), more));
      shown = Math.min(shown + 100, rows.length);
      more.textContent = shown >= rows.length ? "已显示全部" : `加载更多（共 ${rows.length} 篇）`;
      if (shown >= rows.length) more.disabled = true;
    };
    recent.appendChild(more);
    more.addEventListener("click", drawMore);
    drawMore();
  }
  const sb = $("#search-form");
  if (sb) sb.addEventListener("submit", (e) => {
    e.preventDefault();
    const kw = $("#search-input").value.trim();
    location.href = "browse.html#preset=30" + (kw ? "&kw=" + encodeURIComponent(kw) : "");
  });
}

function renderPicks(pd, host) {
  const grid = el("div", { class: "pickgrid" });
  for (const it of pd.items || []) {
    const c = el("div", { class: "pickcard" });
    const head = el("div", { class: "ph" });
    head.appendChild(el("span", { class: "nm", text: it.name }));
    if (it.team) head.appendChild(el("span", { class: "tag badge-pick", text: "团队" }));
    for (const f of it.fields || []) head.appendChild(el("span", { class: "tag tag-type", text: f }));
    if (it.org) head.appendChild(el("span", { class: "og", text: it.org }));
    c.appendChild(head);
    if (it.note) c.appendChild(el("div", { class: "nt", text: it.note }));
    const st = [];
    if (it.count30) st.push(`近30日 ${it.count30} 篇`);
    else st.push("近30日暂无收录");
    if (it.latest) st.push("最新 " + it.latest.slice(5));
    c.appendChild(el("div", { class: "st", text: st.join(" · ") }));
    const sm = el("div", { class: "sm" });
    for (const r of it.sample || []) {
      sm.appendChild(el("a", { href: `report.html?id=${encodeURIComponent(r.i)}&d=${r.d}`, text: "· " + r.t.slice(0, 34) + (r.t.length > 34 ? "…" : "") }));
    }
    sm.appendChild(el("a", { href: `browse.html#preset=d30&kw=${encodeURIComponent(it.name)}`, text: `· 查看全部（搜索 ${it.name}）→` }));
    c.appendChild(sm);
    grid.appendChild(c);
  }
  host.appendChild(grid);
  const src = pd.source || {};
  if (src.author) {
    host.appendChild(el("div", { class: "st", style: "margin-top:10px" }, [
      el("span", { text: `名单来源：${src.platform || ""} @${src.author}（著作权归原作者所有${src.answer_url ? "，" : ""}）` }),
      src.answer_url ? el("a", { href: src.answer_url, target: "_blank", rel: "noopener", text: "原回答 →" }) : null,
    ].filter(Boolean)));
  }
}

/* ---------- 浏览页 ---------- */

const PRESETS = { today: 1, d7: 7, d30: 30, d90: 90, all: 0 };

function readHash() {
  const p = new URLSearchParams(location.hash.replace(/^#/, ""));
  return {
    q: (p.get("q") || "").split(",").filter((x) => x !== "").map(Number),
    org: p.get("org") || "",
    ind: p.get("ind") || "",
    rating: p.get("rating") || "",
    preset: p.get("preset") || "d7",
    from: p.get("from") || "",
    to: p.get("to") || "",
    rank: p.get("rank") === "1",
    pdf: p.get("pdf") === "1",
    kw: p.get("kw") || "",
    sort: p.get("sort") || "rank",
  };
}

function writeHash(st) {
  const p = new URLSearchParams();
  if (st.q.length) p.set("q", st.q.join(","));
  if (st.org) p.set("org", st.org);
  if (st.ind) p.set("ind", st.ind);
  if (st.rating) p.set("rating", st.rating);
  if (st.preset && st.preset !== "d7") p.set("preset", st.preset);
  if (st.from) p.set("from", st.from);
  if (st.to) p.set("to", st.to);
  if (st.rank) p.set("rank", "1");
  if (st.pdf) p.set("pdf", "1");
  if (st.kw) p.set("kw", st.kw);
  if (st.sort && st.sort !== "rank") p.set("sort", st.sort);
  const h = p.toString();
  history.replaceState(null, "", h ? "#" + h : "#");
}

function ymd(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function dateRange(st) {
  if (st.from || st.to) return { from: st.from || "2000-01-01", to: st.to || ymd(new Date()) };
  const days = PRESETS[st.preset];
  if (!days) return { from: "2000-01-01", to: ymd(new Date()) };
  const to = new Date();
  const from = new Date(to.getTime() - (days - 1) * 86400000);
  return { from: ymd(from), to: ymd(to) };
}

function sortRecs(rows, sort) {
  const byDate = (a, b) => (a.d < b.d ? 1 : a.d > b.d ? -1 : a.i < b.i ? 1 : -1);
  if (sort === "new") return rows.sort(byDate);
  if (sort === "pages") return rows.sort((a, b) => (b.pg || 0) - (a.pg || 0) || byDate(a, b));
  return rows.sort((a, b) => a.ti - b.ti || (a.x || 99) - (b.x || 99) || byDate(a, b));
}

async function pageBrowse() {
  await renderChrome();
  const facets = await Data.loadFacets();
  fillSelects(facets);
  const st = readHash();
  syncControls(st);

  const results = $("#results");
  const countEl = $("#result-count");
  results.textContent = "";
  countEl.textContent = "加载中…";

  const { from, to } = dateRange(st);
  let months = Data.monthsBetween(from, to);
  const avail = (Data.index && Data.index.months || []).map((m) => m[0]);
  if (avail.length) months = months.filter((m) => avail.includes(m));
  let rows = [];
  for (const m of months) rows = rows.concat(await Data.loadMonth(m));

  const kw = st.kw.trim().toLowerCase();
  const kws = kw ? kw.split(/\s+/).filter(Boolean) : [];
  rows = rows.filter((r) => {
    if (r.d < from || r.d > to) return false;
    if (st.q.length && !st.q.includes(r.q)) return false;
    if (st.org && r.oc !== st.org) return false;
    if (st.ind && r.ind !== st.ind) return false;
    if (st.rating && r.r !== st.rating) return false;
    if (st.rank && r.ti > 1) return false;
    if (st.pdf && r.ps !== "local") return false;
    if (kws.length) {
      const hay = [r.t, r.o, r.s, r.au].filter(Boolean).join(" ").toLowerCase();
      if (!kws.every((k) => hay.includes(k))) return false;
    }
    return true;
  });
  sortRecs(rows, st.sort);
  countEl.textContent = `共 ${rows.length} 篇`;

  let shown = 0;
  const more = el("button", { class: "more" });
  const draw = () => {
    rows.slice(shown, shown + 100).forEach((r) => results.insertBefore(card(r), more));
    shown = Math.min(shown + 100, rows.length);
    more.textContent = shown >= rows.length ? "已显示全部" : `加载更多（已显示 ${shown}/${rows.length}）`;
    more.disabled = shown >= rows.length;
  };
  if (!rows.length) results.appendChild(el("div", { class: "empty", text: "没有符合条件的研报，请调整筛选条件" }));
  else { results.appendChild(more); more.addEventListener("click", draw); draw(); }
}

function fillSelects(facets) {
  const orgSel = $("#f-org");
  if (orgSel && orgSel.options.length <= 1) {
    for (const [key, cnt, tier, disp] of facets.orgs || []) {
      const label = (tier === 0 ? "★ " : "") + (disp || key) + `（${cnt}）`;
      orgSel.appendChild(el("option", { value: key, text: label }));
    }
  }
  const indSel = $("#f-ind");
  if (indSel && indSel.options.length <= 1) {
    for (const [name, cnt] of facets.inds || []) indSel.appendChild(el("option", { value: name, text: `${name}（${cnt}）` }));
  }
  const ratSel = $("#f-rating");
  if (ratSel && ratSel.options.length <= 1) {
    for (const [name, cnt] of facets.ratings || []) ratSel.appendChild(el("option", { value: name, text: `${name}（${cnt}）` }));
  }
}

function syncControls(st) {
  $$("#f-type .chip").forEach((c) => c.classList.toggle("on", st.q.includes(Number(c.dataset.v))));
  $("#f-org").value = st.org;
  $("#f-ind").value = st.ind;
  $("#f-rating").value = st.rating;
  $$("#f-preset .chip").forEach((c) => c.classList.toggle("on", c.dataset.v === st.preset));
  $("#f-from").value = st.from;
  $("#f-to").value = st.to;
  $("#f-rank").checked = st.rank;
  $("#f-pdf").checked = st.pdf;
  $("#f-kw").value = st.kw;
  $("#f-sort").value = st.sort;
}

function bindBrowse() {
  const update = (patch) => {
    const st = Object.assign(readHash(), patch);
    writeHash(st);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  };
  $$("#f-type .chip").forEach((c) => c.addEventListener("click", () => {
    const st = readHash();
    const v = Number(c.dataset.v);
    const q = st.q.includes(v) ? st.q.filter((x) => x !== v) : st.q.concat(v);
    update({ q });
  }));
  $$("#f-preset .chip").forEach((c) => c.addEventListener("click", () => update({ preset: c.dataset.v, from: "", to: "" })));
  $("#f-org").addEventListener("change", (e) => update({ org: e.target.value }));
  $("#f-ind").addEventListener("change", (e) => update({ ind: e.target.value }));
  $("#f-rating").addEventListener("change", (e) => update({ rating: e.target.value }));
  $("#f-from").addEventListener("change", (e) => update({ from: e.target.value, preset: "" }));
  $("#f-to").addEventListener("change", (e) => update({ to: e.target.value, preset: "" }));
  $("#f-rank").addEventListener("change", (e) => update({ rank: e.target.checked }));
  $("#f-pdf").addEventListener("change", (e) => update({ pdf: e.target.checked }));
  $("#f-sort").addEventListener("change", (e) => update({ sort: e.target.value }));
  let t = null;
  $("#f-kw").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => update({ kw: e.target.value.trim() }), 350);
  });
}

/* ---------- 榜单页 ---------- */

async function pageRanking() {
  await renderChrome();
  const data = await getJSON("data/ranking.json");
  const meta = data.meta || {};
  $("#rk-meta").textContent = `第 ${meta.edition} 届新财富最佳分析师（${meta.year} 年 ${meta.announced || ""} 公布）· 转录更新于 ${meta.updated_at || "-"} · 来源：xcf.cn（人工转录）`;
  const teams = $("#team-boards");
  for (const board of data.team_boards || []) {
    const block = el("div", { class: "indblock" });
    block.appendChild(el("h3", { text: board.title }));
    for (const row of board.rows || []) block.appendChild(rankRow(row));
    teams.appendChild(block);
  }
  const inds = $("#ind-boards");
  for (const board of data.industries || []) {
    const block = el("div", { class: "indblock" });
    block.appendChild(el("h3", { text: board.name }));
    for (const row of board.rows || []) block.appendChild(rankRow(row));
    inds.appendChild(block);
  }
}

function rankRow(row) {
  const rk = el("span", { class: "rk" + (row.rank <= 3 ? " r" + row.rank : ""), text: String(row.rank) });
  const org = el("a", { class: "org", href: `browse.html#org=${encodeURIComponent(row.canon)}&preset=d30`, text: row.org });
  const st = [];
  if (row.count30) st.push(`30日 ${row.count30} 篇`);
  if (row.local) st.push(`本地PDF ${row.local}`);
  if (row.latest) st.push(row.latest.slice(5));
  return el("div", { class: "indrow" }, [rk, org, el("span", { class: "st", text: st.join(" · ") || "近30日暂无收录" })]);
}

/* ---------- 详情页 ---------- */

async function findRecord(id, d) {
  try {
    const latest = await Data.loadLatest();
    const hit = (latest.featured || []).concat(latest.recent || []).find((x) => x.i === id);
    if (hit) return hit;
  } catch (e) { /* ignore */ }
  if (d) {
    const recs = await Data.loadMonth(d.slice(0, 7));
    const hit = recs.find((x) => x.i === id);
    if (hit) return hit;
  }
  const idx = await Data.loadIndex();
  for (const [m] of idx.months || []) {
    const recs = await Data.loadMonth(m);
    const hit = recs.find((x) => x.i === id);
    if (hit) return hit;
  }
  return null;
}

async function pageReport() {
  const p = new URLSearchParams(location.search);
  const id = p.get("id");
  const host = $("#detail");
  if (!id) { host.appendChild(el("div", { class: "empty", text: "缺少研报参数" })); return; }
  host.appendChild(el("div", { class: "loading", text: "加载中…" }));
  let rec = null;
  try { rec = await findRecord(id, p.get("d")); } catch (e) { /* ignore */ }
  host.textContent = "";
  if (!rec) { host.appendChild(el("div", { class: "empty", text: "未找到该研报（可能尚未收录）" })); return; }

  host.appendChild(el("div", { class: "row1" }, [typeTag(rec.q), ...badges(rec)]));
  host.appendChild(el("h1", { text: rec.t }));
  const tbl = el("table", { class: "fields" });
  const fx = (k, val, node) => {
    if (val === null || val === undefined || val === "") return;
    tbl.appendChild(el("tr", {}, [el("th", { text: k }), el("td", {}, node || [String(val)])]));
  };
  fx("机构", rec.o);
  if (rec.s) fx("标的", rec.s);
  if (rec.au) fx("研究员", rec.au);
  fx("日期", rec.d);
  fx("类型", TYPE_LABEL[rec.q]);
  fx("行业", rec.ind || rec.ie);
  fx("评级", rec.r);
  if (rec.pg) fx("页数", rec.pg + " 页");
  if (rec.kb) fx("大小", fmtMB(rec.kb));
  const eps = rec.eps || {};
  const e2 = ["y1", "y2", "y3"].filter((k) => eps[k] !== undefined && eps[k] !== null);
  if (e2.length) fx("EPS预测", e2.map((k) => `${k.slice(1)}年 ${eps[k]}`).join("　"));
  if ((rec.xt || []).length) fx("新财富", rec.xt.join(" · "));
  host.appendChild(tbl);

  const btnrow = el("div", { class: "btnrow" });
  if (rec.ps === "local" && rec.p) btnrow.appendChild(el("a", { class: "btn", href: rec.p, target: "_blank", text: "本地PDF · 新窗口打开" }));
  if (rec.u) btnrow.appendChild(el("a", { class: "btn" + (rec.ps === "local" ? " ghost" : ""), href: rec.u, target: "_blank", text: "PDF 直链（东财）" }));
  if (rec.e) btnrow.appendChild(el("a", { class: "btn ghost", href: rec.e, target: "_blank", text: "东财详情页" }));
  if (rec.n) btnrow.appendChild(el("a", { class: "btn ghost", href: rec.n, target: "_blank", text: "新浪全文" }));
  host.appendChild(btnrow);

  if (rec.ps === "local" && rec.p) {
    host.appendChild(el("iframe", { class: "pdfview", src: rec.p + "#view=FitH" }));
    host.appendChild(el("div", { class: "meta" }, [el("span", { text: "若上方无法预览 PDF，请点击「新窗口打开」。" })]));
  } else if (rec.ps === "expired") {
    host.appendChild(el("div", { class: "alert", text: "本地 PDF 已超过保留期归档，可通过上方外链在线阅读。" }));
  }
  document.title = rec.t + " · 研报数据库";
}

/* ---------- 关于页 ---------- */

async function pageAbout() {
  try {
    const idx = await Data.loadIndex();
    const x = idx.xcf || {};
    $("#ab-edition").textContent = `第 ${x.edition} 届（${x.year} 年公布）`;
    $("#ab-generated").textContent = idx.generated_at || "-";
    $("#ab-total").textContent = (idx.counts && idx.counts.total) || 0;
    const ps = idx.pdf_stats || {};
    $("#ab-pdf").textContent = `${ps.count || 0} 份 / ${ps.total_mb || 0} MB（最早 ${ps.oldest || "-"}）`;
  } catch (e) { /* ignore */ }
}

/* ---------- 入口 ---------- */

(async function main() {
  try {
    if (PAGE === "index") await pageIndex();
    else if (PAGE === "browse") { await pageBrowse(); bindBrowse(); window.addEventListener("hashchange", () => pageBrowse()); }
    else if (PAGE === "ranking") await pageRanking();
    else if (PAGE === "report") await pageReport();
    else if (PAGE === "about") await pageAbout();
    else await renderChrome();
  } catch (e) {
    const host = $("#results") || $("#detail") || document.body;
    host.appendChild(el("div", { class: "empty", text: "页面加载出错：" + e.message }));
  }
})();

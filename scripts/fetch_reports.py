#!/usr/bin/env python3
"""每日研报抓取：东财双端点 + 新浪列表 → 归一化去重 → PDF 精选下载 → 滚动清理 → 落盘。

用法：
  python3 scripts/fetch_reports.py                          # 增量
  python3 scripts/fetch_reports.py --mode backfill --days 30
  python3 scripts/fetch_reports.py --dry-run --no-download  # 只抓不入库
"""
import argparse
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bs4 import BeautifulSoup  # noqa: E402

from common import (  # noqa: E402
    PDFS_DIR, REPORTS_DIR, EmClient, build_picks_index, build_ranking_index, fingerprint,
    infer_industry, jsonl_path_for, load_all_records, load_cfg, load_jsonl, load_state, log,
    match_picks, match_xcf, month_of, normalize_org, now_bj, save_state, sanitize_filename,
    write_jsonl,
)

SINA_TYPE_MAP = {"公司": 0, "个股": 0, "行业": 1, "策略": 2, "投资策略": 2, "宏观": 3, "宏观经济": 3}
MAX_ATTEMPTS = 3


def parse_args():
    ap = argparse.ArgumentParser(description="研报抓取")
    ap.add_argument("--mode", choices=["incr", "backfill"], default="incr")
    ap.add_argument("--days", type=int, default=30, help="backfill 天数")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--no-prune", action="store_true")
    ap.add_argument("--budget-mb", type=float, default=None, help="覆盖当日 PDF 下载预算")
    ap.add_argument("--dry-run", action="store_true", help="只抓取与统计，不写盘")
    ap.add_argument("--skip-em", action="store_true", help="跳东财只抓新浪（用于补抓重试）")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args()


# ------------------------------------------------------------------ 抓取


def fetch_em(client, cfg, qtype, begin, end, stats):
    sources = cfg["sources"]
    ps = cfg["settings"]["fetch"]["page_size"]
    path_key = "report_list" if qtype <= 1 else "report_jg"
    url = sources["em"][path_key]
    page_size = ps["list"] if qtype <= 1 else ps["jg"]
    max_pages = int(cfg["settings"]["fetch"]["max_pages_per_type"])
    rows, page = [], 1
    ok = False
    while page <= max_pages:
        params = {
            "pageSize": str(page_size), "beginTime": begin, "endTime": end,
            "pageNo": str(page), "fields": "", "qType": str(qtype),
        }
        if qtype <= 1:
            params.update({
                "industryCode": "*", "industry": "*", "rating": "*", "ratingChange": "*",
                "orgCode": "", "code": "", "rcode": "", "p": "1", "pageNum": str(page),
            })
        r = client.get(url, params=params, headers={"Referer": sources["em"]["referer"]}, timeout=30)
        if r is None:
            stats["errors"].append(f"em_q{qtype}_p{page}")
            break
        try:
            d = r.json()
        except ValueError:
            stats["errors"].append(f"em_q{qtype}_json")
            break
        ok = True
        data = d.get("data") or []
        if not data:
            break
        rows.extend(data)
        if page >= (d.get("TotalPage") or 1):
            break
        page += 1
    stats["em_ok"][f"q{qtype}"] = ok
    stats["counts"][f"em_q{qtype}"] = len(rows)
    log.info("东财 qType=%s: %d 条（%s..%s）", qtype, len(rows), begin, end)
    return rows


def parse_sina_page(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    out = []
    for tr in soup.select("table.tb_01 tr"):
        tds = tr.find_all("td")
        if len(tds) < 6 or not tds[0].get_text(strip=True).isdigit():
            continue
        a = tds[1].find("a")
        if not a:
            continue
        m = re.search(r"rptid/(\d+)", a.get("href") or "")
        if not m:
            continue
        out.append({
            "rptid": m.group(1),
            "title": (a.get("title") or a.get_text(strip=True) or "").strip(),
            "type": tds[2].get_text(strip=True),
            "date": tds[3].get_text(strip=True),
            "org": tds[4].get_text(strip=True),
            "author": tds[5].get_text(strip=True),
        })
    return out


def fetch_sina(client, cfg, cutoff, stats, max_pages=120):
    scfg = cfg["settings"]["fetch"].get("sina") or {}
    if not scfg.get("enable"):
        return []
    url_tpl = cfg["sources"]["sina"]["list"]
    enc = cfg["sources"]["sina"].get("encoding", "gbk")
    rows = []
    consecutive = 0
    for p in range(1, max_pages + 1):
        r = client.get(url_tpl.format(page=p), timeout=30)
        if r is None:
            stats["errors"].append(f"sina_p{p}")
            consecutive += 1
            if consecutive >= 3:
                break
            continue
        r.encoding = enc
        page_rows = parse_sina_page(r.text)
        if not page_rows:
            break
        consecutive = 0
        rows.extend(page_rows)
        stats["sina_rows"] += len(page_rows)
        oldest = min(x["date"] for x in page_rows if x["date"])
        if oldest < cutoff:
            break
    return rows


# ------------------------------------------------------------------ 归一化


def make_eps(row):
    eps = {}
    for key, lab in (("predictThisYearEps", "y1"), ("predictNextYearEps", "y2"), ("predictNextTwoYearEps", "y3")):
        v = row.get(key)
        if isinstance(v, (int, float)):
            eps[lab] = round(float(v), 3)
    return eps


def normalize_em(row, qtype, cfg, ts):
    title = (row.get("title") or "").strip()
    d = (row.get("publishDate") or "")[:10]
    org = (row.get("orgSName") or row.get("orgName") or "").strip()
    if not title or not d or not org:
        return None
    info_code = row.get("infoCode")
    jg_id = row.get("id")
    if qtype <= 1 and info_code:
        rid = f"em:{info_code}"
    elif jg_id:
        rid = f"em:{jg_id}"
    else:
        return None
    rec = {
        "id": rid, "src": ["em"], "qtype": qtype, "title": title, "date": d, "org": org,
        "first_seen": ts, "updated_at": ts,
    }
    if row.get("orgCode"):
        rec["org_code"] = str(row["orgCode"])
    if row.get("stockCode") and row.get("stockName"):
        rec["stock"] = {"code": str(row["stockCode"]), "name": row["stockName"]}
    ind = row.get("indvInduName") or row.get("industryName")
    if ind:
        rec["industry_em"] = ind
    if row.get("emRatingName"):
        rec["rating"] = row["emRatingName"]
    if row.get("researcher"):
        rec["researcher"] = row["researcher"]
    if isinstance(row.get("attachPages"), int):
        rec["pages"] = row["attachPages"]
    if isinstance(row.get("attachSize"), (int, float)):
        rec["size_kb"] = int(row["attachSize"])
    eps = make_eps(row)
    if eps:
        rec["eps"] = eps
    links = {}
    details = cfg["sources"]["em"]["detail"]
    if qtype <= 1 and info_code:
        links["em_detail"] = details[f"q{qtype}"].format(info_code=info_code)
        links["pdf_direct"] = cfg["sources"]["em"]["pdf"].format(info_code=info_code)
    elif row.get("encodeUrl") and f"q{qtype}" in details:
        links["em_detail"] = details[f"q{qtype}"].format(encode_url=quote(row["encodeUrl"], safe=""))
    rec["links"] = links
    rec["pdf"] = {"status": "link_only"} if links.get("pdf_direct") else {"status": "unavailable"}
    return rec


def normalize_sina(row, cfg, stats, ts):
    qtype = SINA_TYPE_MAP.get(row.get("type") or "")
    if qtype is None:
        stats["sina_skipped"] += 1
        return None
    if not row.get("title") or not row.get("date") or not row.get("org"):
        return None
    return {
        "id": f"sina:{row['rptid']}", "src": ["sina"], "qtype": qtype,
        "title": row["title"].strip(), "date": row["date"][:10], "org": row["org"].strip(),
        "researcher": (row.get("author") or "").replace("/", ",").strip() or None,
        "links": {"sina": cfg["sources"]["sina"]["detail"].format(rptid=row["rptid"])},
        "pdf": {"status": "unavailable"},
        "first_seen": ts, "updated_at": ts,
    }


def merge_into(old, new, ts):
    """新抓字段合并进旧记录（不覆盖 pdf 状态；links/src 取并集）。返回是否有变化。"""
    changed = False
    debug = bool(os.environ.get("RR_DEBUG_MERGE"))

    def _set(k, a, b):
        nonlocal changed
        changed = True
        if debug:
            log.info("MERGE %s %s: %r -> %r", old.get("id"), k, a, b)

    for k, v in new.items():
        if k in ("id", "pdf", "first_seen", "updated_at", "researcher"):
            continue
        if k == "src":
            merged = sorted(set(old.get("src") or []) | set(v or []))
            if merged != old.get("src"):
                _set("src", old.get("src"), merged)
                old["src"] = merged
        elif k == "links":
            links = dict(old.get("links") or {})
            for lk, lv in (v or {}).items():
                if lv and links.get(lk) != lv:
                    _set(f"links.{lk}", links.get(lk), lv)
                    links[lk] = lv
            old["links"] = links
        elif k == "org":
            # 同一机构两种写法（东财简称 vs 新浪全称）：以 EM 来源为准，保证收敛
            if v and "em" in (new.get("src") or []) and old.get("org") != v:
                _set("org", old.get("org"), v)
                old["org"] = v
        elif k == "title":
            if v and "em" in (new.get("src") or []) and old.get("title") != v:
                _set("title", old.get("title"), v)
                old["title"] = v
        elif k in ("stock", "eps"):
            if v and old.get(k) != v:
                _set(k, old.get(k), v)
                old[k] = v
        elif v not in (None, "") and old.get(k) != v:
            _set(k, old.get(k), v)
            old[k] = v
    # 补充 researcher（旧记录为空时才写）
    if new.get("researcher") and not old.get("researcher"):
        _set("researcher", old.get("researcher"), new["researcher"])
        old["researcher"] = new["researcher"]
    # 新浪记录后续获得 EM 来源 → 状态升级为可下载
    p = old.get("pdf") or {}
    if p.get("status") == "unavailable" and (old.get("links") or {}).get("pdf_direct"):
        old["pdf"] = {"status": "link_only"}
        changed = True
    if changed:
        old["updated_at"] = ts
    return changed


# ------------------------------------------------------------------ 存取


def build_fp_index(records, alias):
    idx = {}
    for rec in records.values():
        fp = fingerprint(normalize_org(rec.get("org"), alias), rec.get("date"), rec.get("title"))
        idx.setdefault(fp, rec["id"])
    return idx


def upsert(records, fp_index, rec, alias, stats, ts):
    rid = rec["id"]
    old = records.get(rid)
    if old is None:
        fp = fingerprint(normalize_org(rec.get("org"), alias), rec.get("date"), rec.get("title"))
        owner = fp_index.get(fp)
        if owner and owner in records:
            if merge_into(records[owner], rec, ts):
                stats["updated"] += 1
            return
        records[rid] = rec
        fp_index[fp] = rid
        stats["new"] += 1
        return
    if merge_into(old, rec, ts):
        stats["updated"] += 1


def write_all(records, stats):
    by_month = {}
    for rec in records.values():
        by_month.setdefault(month_of(rec.get("date")), []).append(rec)
    for month, recs in sorted(by_month.items()):
        if not month:
            continue
        if write_jsonl(jsonl_path_for(month), recs, sort_key=lambda r: (r.get("date") or "", r["id"])):
            stats["shards_written"] = stats.get("shards_written", 0) + 1


# ------------------------------------------------------------------ PDF


def _parse_total(headers):
    cr = headers.get("Content-Range", "")
    m = re.search(r"/(\d+)$", cr)
    if m:
        return int(m.group(1))
    cl = headers.get("Content-Length")
    return int(cl) if cl and cl.isdigit() else None


def _mark_local(rec, fname, size, today, retention_days):
    rec["pdf"] = {
        "status": "local",
        "path": f"pdfs/{fname}",
        "bytes": size,
        "downloaded_at": now_bj().isoformat(timespec="seconds"),
        "expires_at": (today + timedelta(days=retention_days)).isoformat(),
    }


def _mark_failed(rec, reason, today):
    p = rec.get("pdf") or {}
    attempts = int(p.get("attempts") or 0) + 1
    if attempts >= MAX_ATTEMPTS:
        rec["pdf"] = {"status": "unavailable", "note": f"download_failed:{reason}"}
    else:
        rec["pdf"] = {
            "status": "failed", "attempts": attempts, "reason": reason,
            "next_retry": (today + timedelta(days=3)).isoformat(),
        }


def download_pdfs(client, records, cfg, budget_mb, stats):
    s = cfg["settings"]["pdf"]
    per_max_mb = float(s["per_file_max_mb"])
    retention = int(s["retention_days"])
    t0i = int(s["tier0_industry_rank_max"])
    t0t = int(s["tier0_team_rank_max"])
    alias, imap = cfg["alias"], cfg["industry_map"]
    ref = cfg["sources"]["em"]["referer"]
    ranking_index = build_ranking_index(cfg["ranking"], alias)
    picks_index = build_picks_index(cfg.get("picks"), alias)
    today = now_bj().date()

    # 清理上次中断残留
    for part in PDFS_DIR.glob("*.part"):
        part.unlink(missing_ok=True)

    cands = []
    for rec in records.values():
        p = rec.get("pdf") or {}
        st = p.get("status")
        if st in ("local", "unavailable") or p.get("skip_reason") == "too_large":
            continue
        if not (rec.get("links") or {}).get("pdf_direct"):
            continue
        # 超出保留期的记录不再下载（否则会陷入 下载→次日清理 的循环）
        try:
            if (today - date.fromisoformat(rec["date"])).days > retention:
                continue
        except ValueError:
            continue
        if st == "failed" and (p.get("next_retry") or "") > today.isoformat():
            continue
        ind_xcf = infer_industry(rec.get("industry_em"), None, rec.get("title", ""), imap)
        x = match_xcf(normalize_org(rec.get("org"), alias), ind_xcf, ranking_index, t0i, t0t)
        # 推荐分析师的研报视同最高优先级下载
        eff_tier = 0 if match_picks(rec, picks_index, alias) else x["tier"]
        ts = datetime.strptime(rec["date"], "%Y-%m-%d").toordinal()
        cands.append((eff_tier, x["best_rank"] or 99, -ts, rec.get("size_kb") or 99999, rec))
    cands.sort(key=lambda c: c[:4])

    used_mb = 0.0
    for tier, rank, neg_ts, size_kb, rec in cands:
        est_mb = (size_kb / 1024) if size_kb else 2.0
        if used_mb + est_mb > budget_mb:
            stats["pdf_skipped_budget"] += 1
            continue
        info_code = rec["id"].split(":", 1)[1]
        fname = f"{rec['date']}_{sanitize_filename(rec.get('org'))}_{info_code}.pdf"
        path = PDFS_DIR / fname
        if path.exists() and path.stat().st_size >= 1024:
            _mark_local(rec, fname, path.stat().st_size, date.fromisoformat(rec["date"]), retention)
            continue
        url = rec["links"]["pdf_direct"]
        pr = client.get(url, headers={"Referer": ref, "Range": "bytes=0-1023"}, timeout=30)
        if pr is None or pr.status_code not in (200, 206):
            _mark_failed(rec, "probe_http", today)
            stats["pdf_failed"] += 1
            continue
        if pr.status_code == 206 and not pr.content.startswith(b"%PDF"):
            rec["pdf"] = {"status": "unavailable", "note": "not_pdf"}
            continue
        total = _parse_total(pr.headers)
        real_mb = (total / 1048576) if total else est_mb
        if total and total > per_max_mb * 1048576 and tier > 0:
            rec["pdf"] = {"status": "link_only", "skip_reason": "too_large", "size_mb": round(real_mb, 1)}
            stats["pdf_too_large"] += 1
            continue
        if used_mb + real_mb > budget_mb:
            stats["pdf_skipped_budget"] += 1
            continue
        dr = client.get(url, headers={"Referer": ref}, timeout=90)
        if dr is None or len(dr.content) < 1024 or not dr.content.startswith(b"%PDF"):
            _mark_failed(rec, "bad_body", today)
            stats["pdf_failed"] += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(dr.content)
        os.replace(tmp, path)
        _mark_local(rec, fname, len(dr.content), today, retention)
        used_mb += len(dr.content) / 1048576
        stats["pdf_downloaded"] += 1
        stats["pdf_bytes"] += len(dr.content)
        log.info("PDF 已保存: %s (%.1fMB, tier%d rank%s)", fname, len(dr.content) / 1048576, tier, rank)
    stats["pdf_used_mb"] = round(used_mb, 1)


def prune_pdfs(records, cfg, stats):
    ps = cfg["settings"]["pdf"]
    retention = int(ps["retention_days"])
    min_keep = int(ps["min_keep_days"])
    cap_bytes = float(ps["total_cap_mb"]) * 1048576
    today = now_bj().date()
    files = []
    for p in PDFS_DIR.glob("*.pdf"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_", p.name)
        if m:
            files.append((p, m.group(1), p.stat().st_size))
    drop = set()
    for p, d, _ in files:
        if (today - date.fromisoformat(d)).days > retention:
            drop.add(p)
    total = sum(sz for p, _, sz in files if p not in drop)
    for p, d, sz in sorted(files, key=lambda x: x[1]):
        if total <= cap_bytes:
            break
        if (today - date.fromisoformat(d)).days < min_keep:
            break
        if p in drop:
            continue
        drop.add(p)
        total -= sz
    for p in drop:
        p.unlink(missing_ok=True)
    if drop:
        paths = {f"pdfs/{p.name}" for p in drop}
        for rec in records.values():
            p = rec.get("pdf") or {}
            if p.get("status") == "local" and p.get("path") in paths:
                rec["pdf"] = {"status": "expired", "path": p["path"], "expires_at": p.get("expires_at")}
                stats["pruned"] += 1
    stats["pdf_total_mb"] = round(sum(sz for p, _, sz in files if p not in drop) / 1048576, 1)


# ------------------------------------------------------------------ 榜单探测


def probe_new_edition(client, cfg, state, stats):
    edition = int(cfg["ranking"].get("edition") or 0)
    if not edition:
        return
    url = cfg["sources"]["xcf"]["probe"].format(edition=edition + 1)
    r = client.get(url, headers={"Range": "bytes=0-1023"}, timeout=20)
    ok = False
    if r is not None and r.status_code in (200, 206):
        ctype = r.headers.get("Content-Type", "")
        total = _parse_total(r.headers) or len(r.content)
        ok = ctype.startswith("image") and total > 50 * 1024
    state["xcf"] = {
        "edition": edition, "checked_at": now_bj().isoformat(timespec="seconds"),
        "new_edition_available": ok, "probe_url": url,
    }
    if ok:
        log.warning("检测到第 %d 届榜单已发布，请更新 config/xcf_ranking.json（scripts/check_xcf.py --template --edition %d）",
                    edition + 1, edition + 1)
    stats["xcf_new_edition"] = ok


# ------------------------------------------------------------------ main


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_cfg()
    s = cfg["settings"]
    state = load_state()
    client = EmClient(min_interval=float(s["fetch"]["min_interval_s"]))
    today = now_bj().date()
    stats = {
        "errors": [], "sina_rows": 0, "sina_skipped": 0, "em_ok": {}, "counts": {},
        "new": 0, "updated": 0, "pdf_downloaded": 0, "pdf_bytes": 0, "pdf_failed": 0,
        "pdf_too_large": 0, "pdf_skipped_budget": 0, "pruned": 0, "shards_written": 0,
    }
    ts = now_bj().isoformat(timespec="seconds")

    # 时间窗口
    if args.mode == "backfill":
        begin = (today - timedelta(days=args.days)).isoformat()
    else:
        lookback = int(s["fetch"]["incremental_lookback_days"])
        floor = (today - timedelta(days=lookback)).isoformat()
        cursors = state.get("cursors") or {}
        begins = [min(cursors.get(f"em_q{qt}") or floor, floor) for qt in s["fetch"]["qtypes"]]
        begin = min(begins) if begins else floor
    end = today.isoformat()
    log.info("抓取窗口: %s .. %s (%s)", begin, end, args.mode)

    raw_em = {}
    for qt in s["fetch"]["qtypes"]:
        if args.skip_em:
            raw_em[qt] = []
            continue
        raw_em[qt] = fetch_em(client, cfg, int(qt), begin, end, stats)
    sina_cutoff = begin if args.mode == "backfill" else (today - timedelta(days=1)).isoformat()
    sina_max_pages = 120 if args.mode == "backfill" else 12
    raw_sina = fetch_sina(client, cfg, sina_cutoff, stats, max_pages=sina_max_pages)
    log.info("新浪: %d 条（跳过 %d 条非目标类型）", stats["sina_rows"], stats["sina_skipped"])

    if args.skip_em:
        all_failed = stats["sina_rows"] == 0 and bool(stats["errors"])
    else:
        all_failed = (not any(stats["em_ok"].values())) and stats["sina_rows"] == 0
    if all_failed:
        stats["errors"].append("all_sources_failed")

    # 归一化合并
    records = load_all_records()
    fp_index = build_fp_index(records, cfg["alias"])
    for qt, rows in raw_em.items():
        for row in rows:
            rec = normalize_em(row, qt, cfg, ts)
            if rec:
                upsert(records, fp_index, rec, cfg["alias"], stats, ts)
    for row in raw_sina:
        rec = normalize_sina(row, cfg, stats, ts)
        if rec:
            upsert(records, fp_index, rec, cfg["alias"], stats, ts)
    log.info("归一化完成: 新增 %d, 更新 %d, 总记录 %d", stats["new"], stats["updated"], len(records))

    # PDF 下载 / 清理 / 榜单探测
    budget_mb = args.budget_mb if args.budget_mb is not None else float(s["pdf"]["daily_budget_mb"])
    usage = state.get("pdf_usage") or {}
    used_today = float(usage.get("mb") or 0) if usage.get("date") == today.isoformat() else 0.0
    eff_budget = max(0.0, budget_mb - used_today)
    if not args.dry_run:
        if s["pdf"].get("enable", True) and not args.no_download:
            log.info("PDF 预算: 今日已用 %.1fMB / %.0fMB，本次可用 %.1fMB", used_today, budget_mb, eff_budget)
            download_pdfs(client, records, cfg, eff_budget, stats)
            state["pdf_usage"] = {"date": today.isoformat(), "mb": round(used_today + stats["pdf_bytes"] / 1048576, 1)}
        if not args.no_prune:
            prune_pdfs(records, cfg, stats)
        probe_new_edition(client, cfg, state, stats)

        write_all(records, stats)

        cursors = state.setdefault("cursors", {})
        for qt in s["fetch"]["qtypes"]:
            if stats["em_ok"].get(f"q{int(qt)}"):
                cursors[f"em_q{int(qt)}"] = end
        state["last_run"] = {"at": ts, "mode": args.mode, "ok": not all_failed, "counts": stats["counts"]}
        state["last_run"]["new"] = stats["new"]
        state["last_run"]["pdf_new"] = stats["pdf_downloaded"]
        state["last_run"]["pdf_mb"] = round(stats["pdf_bytes"] / 1048576, 1)
        state["last_run"]["pruned"] = stats["pruned"]
        state["errors"] = (state.get("errors") or [])[-20:] + stats["errors"][:20]
        save_state(state)

    log.info(
        "完成: 新增 %d 更新 %d | PDF +%d 个 %.1fMB（预算 %.0fMB）失败 %d 超限 %d | 清理 %d | 写分片 %d",
        stats["new"], stats["updated"], stats["pdf_downloaded"], stats["pdf_bytes"] / 1048576,
        budget_mb, stats["pdf_failed"], stats["pdf_too_large"], stats["pruned"], stats["shards_written"],
    )
    if stats["errors"]:
        log.warning("错误: %s", "; ".join(stats["errors"][:5]))
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())

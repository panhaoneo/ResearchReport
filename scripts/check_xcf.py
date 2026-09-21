#!/usr/bin/env python3
"""新财富榜单维护工具。

用法：
  python3 scripts/check_xcf.py --validate                 # 校验当前榜单 JSON
  python3 scripts/check_xcf.py --probe                    # 探测是否发布新一届
  python3 scripts/check_xcf.py --template --edition 24    # 生成新一届转录骨架
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CONFIG_DIR, EmClient, load_cfg, log, normalize_org, now_bj, write_json_if_changed  # noqa: E402


def validate(cfg):
    ranking = cfg["ranking"]
    alias = cfg["alias"]
    ok = True
    inds = ranking.get("industry") or {}
    teams = ranking.get("team") or {}
    if not inds:
        log.error("industry 为空")
        return False
    n_entries = 0
    canon_seen = set()
    for name, rows in inds.items():
        ranks = [r["rank"] for r in rows]
        if ranks != list(range(1, len(ranks) + 1)):
            log.error("[%s] 名次不连续: %s", name, ranks)
            ok = False
        orgs = [r.get("org", "").strip() for r in rows]
        if any(not o for o in orgs):
            log.error("[%s] 存在空机构名", name)
            ok = False
        if len(set(orgs)) != len(orgs):
            log.error("[%s] 机构重复: %s", name, orgs)
            ok = False
        n_entries += len(rows)
        canon_seen.update(normalize_org(o, alias) for o in orgs)
    for name, rows in teams.items():
        ranks = [r["rank"] for r in rows]
        if ranks != list(range(1, len(ranks) + 1)):
            log.error("[团队·%s] 名次不连续", name)
            ok = False
        n_entries += len(rows)
        canon_seen.update(normalize_org(r.get("org", ""), alias) for r in rows)
    for key in ("edition", "year", "updated_at", "source_url"):
        if not ranking.get(key):
            log.error("缺少字段 %s", key)
            ok = False
    log.info("校验%s：%d 个行业、%d 个团队榜、%d 条记录、归一化后 %d 家机构",
             "通过" if ok else "失败", len(inds), len(teams), n_entries, len(canon_seen))
    return ok


def probe(cfg):
    edition = int(cfg["ranking"].get("edition") or 0)
    if not edition:
        log.error("榜单缺少 edition 字段")
        return False
    url = cfg["sources"]["xcf"]["probe"].format(edition=edition + 1)
    client = EmClient(min_interval=1.0)
    r = client.get(url, headers={"Range": "bytes=0-1023"}, timeout=20)
    if r is None:
        log.error("探测请求失败: %s", url)
        return False
    ctype = r.headers.get("Content-Type", "")
    total = 0
    import re
    m = re.search(r"/(\d+)$", r.headers.get("Content-Range", ""))
    total = int(m.group(1)) if m else int(r.headers.get("Content-Length", "0") or 0)
    new_avail = ctype.startswith("image") and total > 50 * 1024
    log.info("当前第 %d 届；探测第 %d 届图片 %s （Content-Type=%s, 大小=%dB）→ %s",
             edition, edition + 1, url, ctype, total, "已发布！请转录" if new_avail else "未发布")
    return new_avail


def template(cfg, edition):
    ranking = cfg["ranking"]
    skeleton = {
        "edition": edition,
        "year": None,
        "announced": None,
        "updated_at": now_bj().date().isoformat(),
        "source_url": cfg["sources"]["xcf"]["site"],
        "images": [cfg["sources"]["xcf"]["probe"].format(edition=edition).replace("Img1", f"Img{i}") for i in range(1, 7)],
        "note": "第 %d 届新财富最佳分析师评选结果（人工转录）。industry=分行业前5，team=团队榜。" % edition,
        "industry": {name: [{"rank": i + 1, "org": ""} for i in range(len(rows))] for name, rows in (ranking.get("industry") or {}).items()},
        "team": {name: [{"rank": i + 1, "org": ""} for i in range(len(rows))] for name, rows in (ranking.get("team") or {}).items()},
    }
    out = CONFIG_DIR / f"xcf_ranking.{edition}.template.json"
    write_json_if_changed(out, skeleton)
    log.info("已生成转录骨架: %s（将 org 填空后，替换 config/xcf_ranking.json 并运行 --validate）", out)
    return True


def main():
    logging_level = None
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="新财富榜单维护")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--edition", type=int, default=None)
    args = ap.parse_args()
    cfg = load_cfg()
    done = False
    if args.validate:
        done = True
        sys.exit(0 if validate(cfg) else 1)
    if args.probe:
        done = True
        probe(cfg)
    if args.template:
        done = True
        edition = args.edition or (int(cfg["ranking"].get("edition") or 0) + 1)
        template(cfg, edition)
    if not done:
        ap.print_help()


if __name__ == "__main__":
    main()

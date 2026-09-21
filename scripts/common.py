#!/usr/bin/env python3
"""共用工具：配置加载、JSONL 读写、限流请求、机构/行业归一化、榜单匹配。"""
import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
REPORTS_DIR = DATA_DIR / "reports"
DOCS_DIR = ROOT / "docs"
PDFS_DIR = DOCS_DIR / "pdfs"
STATE_PATH = DATA_DIR / "state.json"

TZ = ZoneInfo("Asia/Shanghai")
log = logging.getLogger("rr")

TYPE_LABEL = {0: "个股", 1: "行业", 2: "策略", 3: "宏观"}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def now_bj():
    return datetime.now(TZ)


def today_str():
    return now_bj().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- IO


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_yaml(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_if_changed(path, content):
    """内容未变则不重写，返回是否发生变更（保证 git 无空 diff）。"""
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return True
    return False


def dump_deterministic(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json_if_changed(path, obj):
    return write_if_changed(path, dump_deterministic(obj) + "\n")


def load_jsonl(path):
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def write_jsonl(path, records, sort_key):
    recs = sorted(records, key=sort_key)
    lines = [dump_deterministic(r) for r in recs]
    return write_if_changed(path, "\n".join(lines) + ("\n" if lines else ""))


def load_state():
    return load_json(STATE_PATH, {}) or {}


def save_state(state):
    return write_json_if_changed(STATE_PATH, state)


def load_cfg():
    """一次性加载全部配置。"""
    return {
        "settings": load_yaml(CONFIG_DIR / "settings.yml"),
        "sources": load_yaml(CONFIG_DIR / "sources.yml"),
        "ranking": load_json(CONFIG_DIR / "xcf_ranking.json", {}),
        "alias": load_json(CONFIG_DIR / "org_alias.json", {}),
        "industry_map": load_json(CONFIG_DIR / "industry_map.json", {"name_map": {}, "title_keywords": []}),
    }


# ---------------------------------------------------------------- 归一化


_ORG_SUFFIXES = ["股份有限公司", "有限责任公司", "有限公司", "证券研究所", "研究所", "证券", "股份"]


def normalize_org(name, alias):
    """机构名归一化：先查别名表，再循环剥离通用后缀。"""
    s = (name or "").strip()
    if not s:
        return ""
    s = alias.get(s, s)
    changed = True
    while changed:
        changed = False
        for suf in _ORG_SUFFIXES:
            if s.endswith(suf) and len(s) > len(suf):
                s = alias.get(s[: -len(suf)], s[: -len(suf)])
                changed = True
    return s


def norm_title(t):
    t = (t or "").strip()
    t = re.sub(r"\s+", "", t)
    t = t.replace("：", ":").replace("（", "(").replace("）", ")")
    return t


def fingerprint(org_canon, date, title):
    raw = f"{org_canon}|{date}|{norm_title(title)[:60]}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def sanitize_filename(s, maxlen=40):
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", (s or "").strip())
    return s[:maxlen]


def infer_industry(indv=None, ind_name=None, title="", industry_map=None):
    """推断新财富行业：先精确映射东财行业名，再按标题关键词（有序）。"""
    im = industry_map or {}
    name_map = im.get("name_map") or {}
    for v in (indv, ind_name):
        if v and v in name_map:
            return name_map[v]
    if title:
        for kw, xcf in im.get("title_keywords") or []:
            if kw in title:
                return xcf
    return None


# ---------------------------------------------------------------- 榜单匹配


def build_ranking_index(ranking, alias):
    """榜单机构 → {canon: {"industry": {行业: 名次}, "team": 名次}}"""
    idx = {}
    for ind, rows in (ranking.get("industry") or {}).items():
        for row in rows:
            c = normalize_org(row["org"], alias)
            idx.setdefault(c, {"industry": {}, "team": None})["industry"][ind] = row["rank"]
    for row in (ranking.get("team") or {}).get("最佳总量研究团队", []):
        c = normalize_org(row["org"], alias)
        idx.setdefault(c, {"industry": {}, "team": None})["team"] = row["rank"]
    return idx


def match_xcf(org_canon, industry_xcf, ranking_index, tier0_ind_max=5, tier0_team_max=3):
    """给单条研报打新财富标签。返回 dict（不匹配也返回 matched=False 的结构）。"""
    hit = ranking_index.get(org_canon)
    industry_rank = None
    team_rank = None
    scope = None
    if hit:
        team_rank = hit.get("team")
        if industry_xcf and industry_xcf in hit.get("industry", {}):
            industry_rank = hit["industry"][industry_xcf]
            scope = industry_xcf
    ranks = [r for r in (industry_rank, team_rank) if r]
    matched = bool(ranks)
    best_rank = min(ranks) if ranks else None
    tier = 2
    if matched:
        tier0 = (industry_rank is not None and industry_rank <= tier0_ind_max) or (
            team_rank is not None and team_rank <= tier0_team_max
        )
        tier = 0 if tier0 else 1
    tags = []
    if industry_rank:
        tags.append(f"新财富·{industry_xcf} 第{industry_rank}名")
    if team_rank:
        tags.append(f"新财富·总量 第{team_rank}名")
    return {
        "matched": matched,
        "best_rank": best_rank,
        "tier": tier,
        "industry_rank": industry_rank,
        "team_rank": team_rank,
        "scope": scope,
        "tags": tags,
    }


# ---------------------------------------------------------------- 网络


class EmClient:
    """带全局限流的 HTTP 客户端（东财/新浪/新财富通用）。"""

    def __init__(self, min_interval=1.2, timeout=30):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.min_interval = min_interval
        self.timeout = timeout
        self._last = 0.0

    def _throttle(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.3))
        self._last = time.time()

    def get(self, url, params=None, headers=None, timeout=None, retries=3):
        """返回 requests.Response 或 None（403 不重试；429/5xx 指数退避重试）。"""
        last_err = None
        for attempt in range(retries):
            self._throttle()
            try:
                r = self.session.get(
                    url, params=params, headers=headers, timeout=timeout or self.timeout
                )
                if r.status_code == 403:
                    log.warning("403（不重试）: %s", url)
                    return None
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"HTTP {r.status_code}")
                return r
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(2 ** attempt * 1.5)
        log.warning("请求失败(%s): %s", last_err, url)
        return None


def jsonl_path_for(month):
    """month: '2026-09' -> data/reports/2026-09.jsonl"""
    return REPORTS_DIR / f"{month}.jsonl"


def month_of(date_str):
    return (date_str or "")[:7]


def load_all_records():
    """加载全部月度分片为一个 {id: record} 字典。"""
    records = {}
    for path in sorted(REPORTS_DIR.glob("*.jsonl")):
        for rec in load_jsonl(path):
            records[rec["id"]] = rec
    return records

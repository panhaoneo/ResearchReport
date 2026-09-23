# 研报数据库（ResearchReport）

每日自动抓取券商研报（个股/行业/策略/宏观），按"新财富最佳分析师"榜单标记与排序，GitHub Actions 自动运行，[GitHub Pages 在线浏览](https://panhaoneo.github.io/ResearchReport/)。

## 系统结构

```
config/     settings.yml(调参入口) sources.yml(端点) xcf_ranking.json(新财富榜单)
            org_alias.json(机构名归一化) industry_map.json(东财行业→新财富行业)
            analyst_picks.json(分析师推荐名单，姓名+机构双重匹配)
data/       reports/YYYY-MM.jsonl(主存储，一月一文件)  state.json(游标/PDF额度/榜单检测)
scripts/    fetch_reports.py(抓取)  build_site.py(建站)  check_xcf.py(榜单维护)  common.py
docs/       GitHub Pages 站点（index/browse/ranking/report/about + data/ + pdfs/）
```

数据流：东财双端点(q0/q1=list, q2/q3=jg) + 新浪(次要) → 归一化去重 → 榜单打分(tier/rank/tags)
→ PDF 精选下载(docs/pdfs/, 预算+滚动清理) → 生成站点数据分片 → Actions 提交并部署 Pages。

## 分析师推荐名单（config/analyst_picks.json）

独立于新财富机构榜单的"跟谁看"维度：按 姓名+机构 双重匹配研报的研究员字段（机构为空则仅按姓名），
命中后自动打「推荐·姓名」标签并**视同最高优先级下载 PDF**，首页「推荐关注」板块展示各分析师
近30日收录篇数与最新研报。名单来自知乎 @禾芝 的回答（著作权归原作者，见配置内 source 字段），
可持续追加；机构变动（如刘郁 2026 年由华西→兴业）需同步更新配置中的 org。

## 每日运行

GitHub Actions `daily.yml`：每天北京 12:30 / 20:30 各运行一次（+ 手动 workflow_dispatch 可传回填天数）。
本地运行：

```bash
python3 scripts/fetch_reports.py                 # 增量抓取
python3 scripts/fetch_reports.py --mode backfill --days 30   # 回填
python3 scripts/fetch_reports.py --dry-run --no-download     # 只抓不入库
python3 scripts/build_site.py                    # 重建站点（docs/）
python3 -m http.server 8123 -d docs              # 本地预览
```

## 新财富榜单更新手册

榜单为 xcf.cn 发布的图片（https://www.xcf.cn/zjfxs ），仓库中 `config/xcf_ranking.json` 为人工转录。
每年榜单换届时：

1. 系统自动探测新版（每日运行探测 `{edition+1}fxsResult` 图片），命中后站点首页出现黄条提示，
   并下载最新榜单图片到 `tmp`（见 Actions 日志）。
2. `python3 scripts/check_xcf.py --template --edition 24` 生成空白骨架 JSON。
3. 对照榜单图片转录各行业前 5 机构与总量团队前 10，替换 `config/xcf_ranking.json` 并更新 `edition`。
4. `python3 scripts/check_xcf.py --validate` 通过后 commit，次日运行自动为历史记录重算标签。

机构名对不上时补 `config/org_alias.json`；行业映射缺口看 `data/known_orgs.json` 清单补
`config/industry_map.json`（建议每周看一眼，5 分钟）。

## 容量与运维

- PDF 策略：仅上榜机构研报优先下载（行业榜前5/总量榜前3 最高优先，其余按名次+时间排序），
  每日预算 12MB（12:30/20:30 两次运行共享当日额度），保留 90 天、总容量守护 850MB
  （GitHub Pages 站点上限 1GB）。超限自动清理最旧文件（不少于 30 天），记录降级为外链。
- 监控：站点数据 `docs/data/index.json` 的 `pdf_stats`；workflow 每次打印目录体积与
  `git count-objects -vH`；`data/state.json` 记录当日 PDF 已用额度。
- 维护清单：`data/known_orgs.json`（榜单机构近30日无收录 / 未映射东财行业 / 高频机构）。
- 长期（约一年后）git 历史可能偏大，可做一次压缩重建（git checkout --orphan + 强推，
  操作前先备份），或把 PDF 迁往独立仓库。

## 数据来源与免责

数据来源：东方财富研报中心（reportapi.eastmoney.com）、新浪财经研报（次要）、新财富（榜单）。
所有研报版权归原研究机构所有，本仓库仅作个人学习用途的元数据索引与链接；站点 `robots.txt`
已禁止收录 `pdfs/` 目录。

## License

仅个人学习使用，不构成任何投资建议。

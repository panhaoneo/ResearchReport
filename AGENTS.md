# 研报数据库（ResearchReport）

> 最后更新: 2026-09-23 | 当前分支: main | 当前阶段: v1 已上线并稳定自动运行（每日抓取 + GitHub Pages 展示）

## 是什么
每日自动抓取券商研报（个股/行业/策略/宏观）到 git 仓库并发布到 GitHub Pages 的个人研报数据库。
筛选与排序依据：① 第 23 届「新财富最佳分析师」机构榜单（config/xcf_ranking.json，人工转录）；② 一份个人分析师推荐名单（config/analyst_picks.json，姓名+机构双重匹配）。
线上: https://panhaoneo.github.io/ResearchReport/ ；仓库: `git@github.com:panhaoneo/ResearchReport.git`（public，Pages 已配置为 build_type=workflow）。

## 目录地图
- `scripts/fetch_reports.py` — 抓取唯一入口：东财双端点+新浪 → 归一化去重 → PDF 下载 → 滚动清理 → 写 data/reports/*.jsonl
- `scripts/build_site.py` — 建站唯一入口：读 data/ + config/ → 生成 docs/ 全部页面与数据分片（派生字段全部在构建期计算）
- `scripts/common.py` — 共用库：load_cfg / normalize_org / 榜单与推荐匹配 / EmClient（限流+重试 HTTP）
- `scripts/check_xcf.py` — 榜单维护：`--validate` 校验 / `--probe` 探测新一届 / `--template` 生成转录骨架
- `config/settings.yml` — 唯一调参入口（qtypes、PDF 预算/保留、限流、站点）
- `config/xcf_ranking.json` — 第 23 届新财富榜单（30 行业前 5 + 8 个团队榜）
- `config/analyst_picks.json` — 分析师推荐名单（宏观/策略/金工/固收 8 人，含出处与备注）
- `config/industry_map.json` / `config/org_alias.json` — 东财行业→新财富行业映射 / 机构名归一化
- `data/reports/YYYY-MM.jsonl` — 主存储（**唯一事实源**，一行一条，按月分片）
- `data/state.json` — 游标 / 当日 PDF 已用额度(pdf_usage) / 榜单新版检测
- `data/known_orgs.json` — 维护清单（榜单机构无收录/未映射行业/高频机构），build 生成
- `docs/` — Pages 站点根（index/browse/ranking/report/about + assets/ + data/ + pdfs/），产物但需提交
- `.github/workflows/daily.yml` — 每日两次 cron + push 触发 + 手动 dispatch（可传回填天数）

## 环境与依赖
- 本机 Python 3.10.4 / Node v24（仅用于 `node --check`）/ git 2.27（**老版本，无 `git init -b`**，新建分支用 `git init` + `git symbolic-ref HEAD refs/heads/main`）
- 依赖：`pip install -r requirements.txt`（requests / PyYAML / beautifulsoup4 / lxml）
- Actions 环境 Python 3.11，无需额外配置；**本项目无任何密钥文件**
- gh CLI 已登录 panhaoneo；token 缺 `workflow` scope（要 `gh workflow run` 需先 `gh auth refresh -h github.com -s workflow`）

## 常用命令
- 增量抓取: `python3 scripts/fetch_reports.py`（约 1 分钟；按 state 游标回看 2 天，幂等）
- 30 天回填: `python3 scripts/fetch_reports.py --mode backfill --days 30`（约 5 分钟）
- 只抓不入库: `python3 scripts/fetch_reports.py --dry-run --no-download`
- 仅补抓新浪: `python3 scripts/fetch_reports.py --mode backfill --days 30 --skip-em --no-download`
- 建站: `python3 scripts/build_site.py`（幂等，内容未变不写盘；预期输出"站点生成完成…"）
- 本地预览: `python3 -m http.server 8123 -d docs` → http://localhost:8123/index.html（**必须 http 打开，file:// 会被 CORS 挡**）
- 榜单校验: `python3 scripts/check_xcf.py --validate`（预期"校验通过：30 个行业…"）
- JS 语法检查: `node --check docs/assets/app.js`

## 凭证与数据
- **无密钥**。Actions 用内置 GITHUB_TOKEN；本机推送依赖 `~/.ssh` 密钥（panhaoneo 账号，迁移后新机需自行配置 SSH 与 `gh auth login`）
- 数据来源：`reportapi.eastmoney.com`（q0/q1 走 `/report/list`，q2/q3 走 `/report/jg`，需 `Referer: https://data.eastmoney.com/`）+ `stock.finance.sina.com.cn`（列表页 GBK）；全局限流 1.2s/请求
- 产物去向：`data/reports/*.jsonl`（元数据，永久）+ `docs/pdfs/`（精选 PDF，滚动保留）；两者都提交进 git
- 根目录两个参考 PDF（`一个简单的例子让你了解资产负债表.pdf`、`研报应该怎么看.pdf`）被 .gitignore 忽略 → **git clone 拿不到，需随目录复制**

## 硬约束与约定
- `data/reports/*.jsonl` 是唯一事实源，**不要手工编辑**；榜单标签/行业/机构规范名等派生字段一律在 build 期计算（改 config 即全量生效）
- 所有写盘必须走 `common.write_if_changed` / `write_jsonl`（确定性序列化：sort_keys + 内容未变不落盘），保证重复运行零 diff
- 机构名一律过 `normalize_org`（别名表 → 循环剥离"股份有限公司/证券"等后缀）；同名分析师匹配必须"姓名+机构"双重（"张瑜"在华创与招商都有、"郭磊"在广发/光大/国海都有）
- 站点部署在子路径 `/ResearchReport/`：前端所有资源路径必须**相对**（`data/…`、`assets/…`、`pdfs/…`），禁止以 `/` 开头
- `docs/.nojekyll` 必须保留（否则 Jekyll 会吞掉下划线开头的资源）
- PDF 预算 12MB/日是**当日两次运行共享**（记账在 `state.pdf_usage`），改预算看 `config/settings.yml`

## 已知坑（现象 → 原因 → 规避）
1. 宏观/策略抓不到 → 东财 qType=3 在 `/report/list` 恒为 0 hit；必须走 `report_jg` 端点，且 jg 记录**无 infoCode → 无法下 PDF**，只能贴详情页链接
2. 下载到空文件 → PDF 失效时返回 200+空 body；必须先 `Range: bytes=0-1023` 预检 + 校验 `%PDF` 魔数 + 用 Content-Range 取总长
3. 新浪整批抓取中断 → 单页 5xx 曾中断全批；已改为连续 3 次失败才停。504 偶发，重跑即可
4. 每次运行都"更新 26 条"的假抖动 → merge_into 曾覆盖 id/org/title（新浪是全称"东吴证券股份有限公司" vs 东财简称"东吴证券"）；规则：id 永不合并；org/title 仅 EM 来源可覆盖
5. 过期 PDF 反复下载 → expired 记录会再次成为下载候选，陷入"下载→次日清理"循环；已在候选过滤中排除保留期外记录
6. 新财富新版探测误报 → 不存在的届数返回软 404（200 + text/html + 605B）；判定必须"Content-Type 为 image 且 长度 >50KB"
7. 本地 push 被拒 → bot 每日有 data 提交；先 `git pull --rebase`。冲突几乎只在 `docs/data/*.json` 产物 → 取远端版后重跑 `build_site.py` 再提交即可
8. 曾出现导航显示 `{label}` → f-string 里误写 `{{label}}`（转义成字面量）；写 HTML 模板时注意大括号
9. 数据特征（重要）：**东财以小/中型券商为主**（开源/东吴/国信/国金…），头部券商（广发/华泰/国泰海通/华创…）主要靠**新浪**覆盖；华泰、大摩在两个源均未出现（推荐名单里这几位的计数会是 0，属正常）
10. 新浪历史深度约 13 天（backfill 上限 110 页）；东财无此限制
11. Actions schedule 有数小时延迟属正常（GitHub 免费调度）；push 触发是即时的，且 GITHUB_TOKEN 的 push 不会再触发新 run（无循环风险）
12. 站内链接点击"没反应" → `docs/assets/app.js` 的 `safeUrl()` 曾用前缀白名单（只放行 http(s)/pdfs//./），把 `report.html?id=…`、`browse.html#…` 等相对链接全替换成 `#`；已改为 `new URL(u, document.baseURI)` 协议解析（只拦 javascript:/data:）。**新增任何 href 后用 node 回归 safeUrl**

## 进行中的工作
- 已完成: v1 全链路（双源抓取/跨源去重/榜单打标/分析师推荐/PDF 预算+保留/静态站点/Actions+Pages），线上验证通过；样本量：7,846 篇（8-9 月）、25 份本地 PDF
- 正在做: 无（等用户反馈视觉效果与功能调整）
- 下一步（候选）: ① 24 届榜单发布后按 README 手册转录更新（probe 已自动监测，命中后站点出黄条，逻辑在 `scripts/fetch_reports.py` 的 probe_new_edition）② 可选优化：搜索/筛选增强（`docs/assets/app.js` 的 pageBrowse）、晨会研报 qtype=4 纳入（`config/settings.yml` 的 qtypes）
- 未决问题: 无阻塞项

## 迁移提示（换机交接）
- 会话记录不在工作目录：`~/.qoder-cn/projects/-home-pan-ResearchReport/`（仅 `--resume` 需要；新机绝对路径须同为 `/home/pan/ResearchReport` 才能直接续）
- 需手动带走：根目录两个参考 PDF（被 gitignore）、本机 `~/.ssh` 密钥或在新机重新生成并加到 GitHub
- 新机初始化：`pip install -r requirements.txt` → `gh auth login` → `python3 scripts/build_site.py`（验证环境）→ `git pull`
- 接管时逐条核验本文件（路径/命令/版本可能过期），修正后在此处补变更记录

## 变更记录
- 2026-09-23: 初版，覆盖架构/命令/硬约束/已知坑/迁移要点
- 2026-09-24: 修复 safeUrl 误拦站内相对链接（坑#12）；样本量 8,082 篇 / 32 份 PDF

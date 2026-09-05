# google-map-lead-filter 开发交接

> 换窗口迭代前先读这里（一分钟）。**只记「进度 / 决策 / 待办」，规则和代码一律指向 SKILL.md / references / scripts，不在这里重复**。
> 每天改完：在「改动记录」加一条（标时间戳），更新「明日待办」。

## 明日待办（最近优先，做完划掉）

> **当前状态（2026-09-04 市调模块完成，三大模块全落地）**：数据层 + 获客 + 客户池 + 市调四块落地——SQLite 企业库（7 表）+ MAIN_ID + 三段式比对 + 复刻报告 + 客户池五分类（换池 + `pool_log` 轨迹）+ 市调热度研判（0-100 + 复盘报告 + 缓存 7 天过期），交互全封装成 Flask Web UI（`webapp/app.py`，8766，页面：仪表盘/市调/新建任务/入库/差异审核/客户池/企业库/企业详情）。**下一步：三模块联调 + 真实数据全流程验证**（市调定国家 → 获客入库 → 客户池跟进，下一条）。

- [x] ~~兜底 8 家~~（WebSearch 9/9 成功、kitesurf 超时降级）：50/50 全判档、0 未确认；纠正 Jäger Fischer 误归类 / Solarscouts 真卖 Deye / BOGA 破产风险
- [x] ~~修 Solarscouts 误标~~：WebSearch 证明它是电商真卖 Deye（电池/逆变器有价），无需降级——反而暴露「片段不足以定卖 vs 提及」双向教训（已固化 rule）
- [x] ~~传竞品品牌重跑 backfill~~：三组品牌 + `--deye`（品牌页抓到我方才停）+ `--no-proxy-server`（直连不读系统代理）重跑 50 家成功——4 家卖 Deye 恢复、9 家竞品命中；7 家光伏批发商 brands 空但 body 明确光伏品类 → product_tier 手工判 competitor（增量 24）落地 score_leads.py
- [x] ~~**客户池模块**（第二阶段）~~：五分类操作 UI（`/pool` 总览 + 企业详情 + 行内换池）+ `pool_log` 轨迹时间戳，已落地并验证
- [x] ~~**市调模块**（第三阶段）~~：热度 0-100 研判录入 + 市场洞察复盘报告 + 缓存 7 天过期，已落地并验证
- [ ] **三模块联调 + 真实数据全流程**：市调定国家 → 获客入库 → 客户池跟进，用真实数据端到端跑一遍

## 改动记录（按日倒序）

- **2026-09-05**
  - **波兰获客全流程首跑（真实数据，355 条）**：search_leads(50) → fetch_enf(100) → fetch_gmaps 6 城市(274) → merge(355) → backfill(355) → score → 三段式入库。结果：新增 353、差异 2、重复 0，卖 Deye 35 家。暴露 5 个问题（已修 3、修中 1、待人工 1）：
    - ✅ **fetch_gmaps 广告 URL 未清洗**：Google Maps 广告位 website 是 `/aclk?...` 跳转（真实网址在 `adurl` 参数，无 adurl 即纯广告脏数据）。ARSEM 被抓成 `/aclk?sa=L&...` 入库。修 `extract_real_url`：`/aclk?` 解析 adurl，无 adurl 返回 "" 丢弃。
    - ✅ **merge_leads.py + backfill.py 字段丢失**（根因，最重）：merge 的 `OUT_FIELDS` 只留 7 字段、丢 enf 的 country/email/customer_type/address；backfill 的 `rec` 只输出自己抓的字段、丢 city/phone/rating。→ 353 家入库 `country` 全空、主键全 `LDXX-`（国家码缺失，应为 `LDPL-`）。**已修复**：① merge `OUT_FIELDS` 扩到 13 字段；② backfill `rec = dict(lead)` 继承原始字段；③ 一次性补数脚本（`D:/Agent/tmp/acq_pl/repair_fields.py`）按 website/name 回填 321 家（100 enf + 230 gmaps 域名 + 22 名字回退），主键 `LDXX-`→`LDPL-` 353 条，清 12 条 aclk 脏 website；④ 复查 91 家 enf-only 补 phone。补数后 PL 批次字段完整：country 全 PL、email 100 家、rating/city 230 家、卖 Deye 34 家。
    - ✅ **代理开关**：fetch_gmaps 走代理 `127.0.0.1:33210`（Google 被墙），代理没开 → `ERR_PROXY_CONNECTION_FAILED`。开代理即恢复。
    - ✅ **gbk 编码崩溃**：波兰字符（Łódź 的 ł）让 Windows gbk 控制台崩。修 `sys.stdout.reconfigure(encoding='utf-8', errors='replace')`。
    - ✅ **Rongstar 跨市场撞车**（机制正确，人工审核）：德国库 Rongstar（rongstar.com）被波兰采集再次抓到，5 字段新值全劣化（SEO 标题/电话邮箱空）→ 差异队列正确保护老数据不污染。**教训**：跨国分销商会在多国露出，差异比对正是兜底这种场景，值得保留审核记录。
  - **差异审核 UI 重做（按企业聚合 + 字段级明细）**：原「每字段一行」扁平表太乱 → 改成两级：`/diffs` 按企业聚合（企业名+差异字段数），点进企业详情 `#diffs` 区块看字段级「旧值→新值」逐条审核（覆盖/忽略，pending 黄高亮）。core 加 `list_diff_groups`/`list_company_diffs`，`get_company` 返回 diffs。
  - **获客问题结构化落库（task_issues 表，第 8 张表）**：架构要求「每次获客遇到的问题都要记录到数据库方便迭代」——之前只写 HANDOFF.md 是错的。新增 `db.py` 的 `task_issues` 表（task_id/分类/标题/详情/方案/状态 open|resolved|deferred + created_at/resolved_at）、`core.record_issue`/`list_task_issues`、`render_task_report.py` 第六节从库读问题（去「待补充」占位符）。本次 PL 获客已录 6 个问题（5 resolved + 1 open）。
  - **search_leads 混入 32 条非企业结果**（数据质量，open）：search_leads.py 搜索结果混入博客/YouTube 视频/新闻/目录页/OLX/Facebook 列表/europages 聚合页等 32 条非企业内容，被 merge 误入库。另有子域名差异导致重复（en.pvgroup.pl vs pvgroup.pl 未去重）。**待办**：入库前过滤 search 来源（按 company_name 是否像企业 + 是否有官网/电话/邮箱），去重键归一化处理语言子域（en./pl./www.）。32 条暂留库待人工判定，不自动删。
  - **企业库/客户池复用原版卡片式双模式 UI**（纠正封装第一阶段「自制绿色表格」偏离）：`core.list_companies` 返回完整卡片字段 + JSON 解析 + `wa_url` 按国家映射（修掉 render_report 硬编码法国 +33 债）；新增 `webapp/static/cards.css`/`cards.js`（从 `render_report.py` 原版 `<style>`/`<script>` 提取，`.stats/.stat→.lead-stats/.lead-stat`、`.filter→.lead-filter` 避开 style.css 冲突）；新增 `templates/_cards.html` 卡片宏（白卡片 + 头/长尾双维度条 + 触达 chips + webapp 独有换池操作条）；`companies.html`/`pool.html` 表格改卡片，头部/长尾分数、维度条、排序、A/B/C 筛选、导出 CSV 全按当前模式重算。教训：**封装时 SKILL.md 第八步「UI 报告规格 8 要素」是硬规格，必须对齐 render_report.py，不许自造简化版**
  - **市调改各国热度排名看板 + 国家筛选**：`/research` 改成开发优先级排名（最新市调任务各国热度降序 + 每国「开始获客」按钮跳 `/tasks/new?country=XX` 预填）；`core.list_companies` 加 country 筛选、新增 `list_countries`（国家分布下拉）+ `latest_research_ranking` + `COUNTRY_NAMES`（欧盟27国+乌克兰中文名）；`/companies`、`/pool` 加国家筛选下拉；`/tasks/new` 支持 `?country=` 预填。⚠️ 数据仍是空壳：`country_scores` 0 行、`tasks` 仅 1 空单、`companies` 仅手动塞的 50 家德国——「开始获客」只是开单，抓取/背调/评分/入库（SKILL 第二~九步）是 Agent 的活，尚未真跑。
  - **市调「一键开始市调 + 单国研判详情」+ 取消独立入库页 + 企业库/客户池 deye 筛选**（对齐用户最终确认的闭环）：① 删 `/ingest` 独立页 + `ingest.html`（入库改为「获客自动入库」，Agent 评分后直调 `ingest_leads`，不再人工贴 JSON）；② `/research` 加「开始市调」一键按钮（POST `/research/start`，`start_research(countries=None)` 默认 `EU_UKRAINE` 28 国，无需选国家）；③ 排名卡每国加「详细研判依据」入口（`/research/<mr_id>/country/<country>`）→ 新增 `country_detail.html`（7 维度判断依据表 + 利好/利空/风险/来源 + 开始获客）；④ 前 10 名标「⭐ 优先开发」；⑤ `db.country_scores` 加 `dimensions` 列（7 维度判断依据 JSON）+ 迁移；`core.save_country_score` 加 `dimensions` 参数、新增 `get_country_detail`；⑥ `core.list_companies` 加 `sells_deye` 筛选、`/companies`/`/pool` 加「是否卖 Deye」下拉 + 客户池加检索框（`q`）。⚠️ **7 维度判断依据仍需 Agent 联网调查后手动录入**（市调热度不是脚本自动算的，是 Claude 研判录入）。
- **2026-09-04**
  - **市调模块（第三阶段）落地**：市场趋势洞察——热度 0-100 研判 + 市场洞察复盘报告 + 缓存 7 天过期。`db.py` 加 `market_tasks`/`country_scores` 两表 + `gen_mr_id`；`core.py` 加 `start_research`/`finish_research`/`save_country_score`/`list_research`/`get_research`/`is_expired`；新增 `scripts/render_research_report.py`；webapp 加 `/research`（列表 + 过期标记）/`/research/new`/`/research/<mr_id>`（各国研判录入 + 得分降序）/`/research/<mr_id>/report.md`。热度由 Agent 深度研判后录入（7 维度），非脚本自动算。验证：core（建任务 / 7 天缓存 / UPSERT / 越界拦截 / 过期判断 / 降序）+ Flask（建任务 302 / 详情表单 / 填分 302 / 复盘报告含得分）
  - **客户池模块（第二阶段）落地**：五分类客户池操作 UI + `pool_log` 轨迹时间戳。`core.py` 加 `change_pool`/`list_pool_log`/`pool_stats`/`get_company` 纯函数；webapp 加 `/pool` 总览（五池统计 + 各池列表 + 换池轨迹）、`/companies/<main_id>` 企业详情（全字段 + 轨迹 + 换池备注）、`/companies/<main_id>/pool` 换池 POST；企业库列表行内换池。验证：core 纯函数（换池 / 同池 skip / 非法池拦截 / 轨迹）+ Flask test_client（五池统计 / 换池 302 / 详情轨迹 / 池筛选）
  - **封装第一阶段（数据层 + Web UI）落地**：按架构文档建 SQLite 企业库 + MAIN_ID + 任务时间戳 + 三段式比对 + 复刻报告，交互封装成 Flask Web UI（8766）。新增 `scripts/db.py`（5表）/ `scripts/core.py`（纯函数 start_task/ingest_leads/build_report）/ `scripts/render_task_report.py` / `webapp/`（页面：仪表盘/新建任务/入库/差异审核/企业库）。50 家德国线索验证通过（50新增→幂等全重复→改1条phone进差异）。修正「fetch_enf 已用 SQLite」过时说法（实测全 scripts 零 SQL）
  - 传竞品品牌重跑 backfill 完成（三组品牌 + `--deye` + `--no-proxy-server` 直连）：50 家全成功，4 家卖 Deye（Rongstar/Greenlimon/SolarV/Solarscouts）、9 家竞品命中；7 家光伏批发商 brands 空但 body 明确光伏品类（德国品牌墙 JS 动态/图片，backfill 抓不到品牌名）→ product_tier 手工判 competitor（增量 24）落地 score_leads.py；K.H.Moelle 纯电气淘汰；最终 UI 头部 A13/B35/C2，卖 Deye 4 家
  - 修 backfill 三个缺陷：`--deye`（品牌页抓到我方品牌才停，否则命中竞品就停、漏 Deye）、`--no-proxy-server`（直连不读系统代理，否则梯子关掉 ERR_PROXY_CONNECTION_FAILED 38/50）、德语品牌页路径（marken/hersteller/produkte）
  - 完整版 backfill 跑 50 家 DE seller：规模判档 42/50（84%）、8 家未确认需兜底；Deye 命中 2→4 家（Rongstar / Greenlimon / SolarV / Solarscouts）；生成 UI 报告（头部长尾均 A4 / B46）
  - 固化规则（`qualification-rules.md`）：Großhandel 是渠道不是规模；规模判断正道 = 人工读 body 判档、关键词只做定位辅助；brands_found 卖 vs 提及（score_leads.py 机械判断的局限）
  - 补背调兜底流程：品牌/规模证据缺失 → anysearch（批量）/ WebSearch（零星）；官网抓不动 → kitesurf
  - 改名 NEXT.md → HANDOFF.md，定位「跨窗口迭代衔接 + 明日待办」
  - 兜底 9 家（WebSearch 9/9 成功、kitesurf 超时降级）：50/50 全判档、0 未确认；纠正 3 处——Jäger Fischer 误归类（电气批发商→retail 建议淘汰）、Solarscouts 真卖 Deye（电商非比价站）、BOGA 破产风险标志
  - 固化竞品品牌口径（`brand-mapping.md` 新增竞品清单 + `qualification-rules.md`/`SKILL.md` 改 `--brands` 三组）：根因=评分三元化但背调参数停在二元，竞品增量 24 档形同虚设；传竞品重跑挖 13 家大型批发商
  - 固化 Solarscouts 双向教训：`brands_found` 片段不足以定「卖 vs 提及」，异常公司必须 WebSearch 交叉验证（机械判断把比价站误标卖、人工读片段也可能把电商误判提及）
  - 发现：兜底 9 家**全部触发 WebSearch**（kitesurf 对 Krannich 超时降级、anysearch 一次未用）——兜底主力为何是 WebSearch 待每天探究
- **2026-09-03**
  - 提取 ENF Solar 抓取脚本 `scripts/fetch_enf.py`（seller→distributor、邮箱 JS 解码、429 退避、欧盟 27 国 + 乌克兰）
  - 修品牌关键词 substring 误命中（INGE / Fusion / OHm）：backfill.py + score_leads.py 改词边界匹配，教训见 `references/brand-mapping.md`
  - 市场边界确认：欧盟 27 国 + 乌克兰

## 长期完善方向（技术债 + 迭代项）

- **正则提取规模信号（暂缓）**：曾想给 backfill.py 加正则自动判 `scale_tier`，实测官网措辞参差、误判漏判严重（Großhandel 是渠道不是规模、SolarV/New Power 措辞不同就漏）。**长期方向**：可探索「关键词定位 + LLM 读候选句判档」半自动，纯正则不可行。当前正道 = Claude 人工读 body 判档（见 `qualification-rules.md` 规模判断流程）。
- **评分体系完善**：双模式四维是 v1。可迭代——触达「因地制宜」目前只写 rules、代码未自动判（德国电话降级靠人工）；规模「估」的边界可再收紧。
- **获客渠道完善**：已跑通 Google Maps / 搜索 API / ENF 目录 / 列表页；展会名录、海关数据、品牌官网 find-a-distributor 待接入（见 `multi-source.md`）。
- **brands_found 卖 vs 提及自动区分**：`score_leads.py` 的 `sells_deye` 是机械判断，比价平台列举品牌会误标「卖 Deye」。长期加「上下文语义判断」，当前靠 Claude 读 `brands_context` 手工复核 + 异常公司 WebSearch 交叉验证（双向教训见 `qualification-rules.md`）。
- **兜底分工观察（每天探究）**：德国 50 家实测，兜底 9 家**全部落到 WebSearch**——kitesurf 抓官网超时（Krannich 10s timeout）、anysearch 一次没派上用场。每天兜底时留意记录：① kitesurf 超时是反爬还是 timeout 参数太短（要不要调 `wait_until`/timeout）；② anysearch 是「批量场景没出现」还是「效果不达预期」；③ WebSearch 成兜底主力是否合理（德国公司站大多可搜到）。积累样本后再决定是否调兜底分工。

## 开发注意（铁律，别删）

- 改代码在 `D:/Agent/git/google-map-lead-filter/`（独立 git + 远程），改完 commit + push。
- **改完同步 skills 目录**，否则新窗口触发 skill 跑旧版：
  ```bash
  SRC=/d/Agent/git/google-map-lead-filter
  for DST in ~/.agents/skills/google-map-lead-filter ~/.claude/skills/google-map-lead-filter; do
    rm -rf "$DST"; mkdir -p "$DST"
    cp -r "$SRC/SKILL.md" "$SRC/references" "$SRC/scripts" "$SRC/templates" "$SRC/webapp" "$SRC/HANDOFF.md" "$DST/"
  done
  ```
- 数据文件（`D:/Agent/tmp/*.json`）是会话产物，不入 git。
- **SQLite 库 `data/leads.db` 不入 git**（企业数据 + 频繁变动）；**复刻报告 `reports/` 入 git**（永久存档，作下次 AI 迭代上下文）。
- 口径唯一来源 `references/qualification-rules.md`，SKILL.md 不重复数字。
- **技术债**：`render_report.py` 的 `wa_link()` 硬编码法国 +33，扩展德国(+49)/荷兰(+31)/西班牙(+34)前要按 `country` 映射。

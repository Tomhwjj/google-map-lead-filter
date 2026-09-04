# google-map-lead-filter 开发交接

> 换窗口迭代前先读这里（一分钟）。**只记「进度 / 决策 / 待办」，规则和代码一律指向 SKILL.md / references / scripts，不在这里重复**。
> 每天改完：在「改动记录」加一条（标时间戳），更新「明日待办」。

## 明日待办（最近优先，做完划掉）

> **当前状态（2026-09-04 封装第一阶段完成）**：数据层 + 获客模块改造落地——SQLite 企业库（`data/leads.db`，5 表）+ MAIN_ID + 任务时间戳体系 + 三段式比对 + 复刻报告，交互封装成 Flask Web UI（`webapp/app.py`，8766）。核心逻辑已用 50 家德国线索验证（50 新增 → 幂等全重复 → 改字段进差异队列）。**下一步：客户池模块**（五分类 UI + 状态轨迹，复用 `pool` 字段 + `pool_log` 表，下一条）。

- [x] ~~兜底 8 家~~（WebSearch 9/9 成功、kitesurf 超时降级）：50/50 全判档、0 未确认；纠正 Jäger Fischer 误归类 / Solarscouts 真卖 Deye / BOGA 破产风险
- [x] ~~修 Solarscouts 误标~~：WebSearch 证明它是电商真卖 Deye（电池/逆变器有价），无需降级——反而暴露「片段不足以定卖 vs 提及」双向教训（已固化 rule）
- [x] ~~传竞品品牌重跑 backfill~~：三组品牌 + `--deye`（品牌页抓到我方才停）+ `--no-proxy-server`（直连不读系统代理）重跑 50 家成功——4 家卖 Deye 恢复、9 家竞品命中；7 家光伏批发商 brands 空但 body 明确光伏品类 → product_tier 手工判 competitor（增量 24）落地 score_leads.py
- [ ] **客户池模块**（第二阶段）：五分类（潜在未联系/已联系/强意向/重点关注/老客户）操作 UI + `pool_log` 轨迹时间戳；复用本轮 `companies.pool` 字段 + `pool_log` 表。Web 后台加「客户池」页面

## 改动记录（按日倒序）

- **2026-09-04**
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

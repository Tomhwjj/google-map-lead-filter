# google-map-lead-filter 开发交接

> 换窗口迭代前先读这里（一分钟）。**只记「进度 / 决策 / 待办」，规则和代码一律指向 SKILL.md / references / scripts，不在这里重复**。
> 每天改完：在「改动记录」加一条（标时间戳），更新「明日待办」。

## 明日待办（最近优先，做完划掉）

- [ ] **兜底 8 家**（Krannich / pvXchange / Jäger Fischer / BOGA / ARK / RoofTech / Fa.Solar-qqq / Solarscouts / Sinotunity）：kitesurf + anysearch 补规模+品牌，验证兜底有效性
- [ ] **传竞品品牌重跑 backfill**：`--brands` 加 Huawei/Sungrow/GoodWe/Fronius/SMA/Solax，把 13 家大型批发商的竞品命中挖出（产品匹配 0→24）
- [ ] **修 Solarscouts 误标**：比价平台，Deye 是品牌列表提及非销售，手工降级
- [ ] **客户跟踪池子**（获客途径完善）：按钮分池——潜在客户（未联系 → 已联系）、强意向、老客户；本地保存 + 检索（电话 / 企业名）。存储方案待定：SQLite（`fetch_enf.py` 已用 SQLite WAL，可复用）vs markdown vs csv

## 改动记录（按日倒序）

- **2026-09-04**
  - 完整版 backfill 跑 50 家 DE seller：规模判档 42/50（84%）、8 家未确认需兜底；Deye 命中 2→4 家（Rongstar / Greenlimon / SolarV / Solarscouts）；生成 UI 报告（头部长尾均 A4 / B46）
  - 固化规则（`qualification-rules.md`）：Großhandel 是渠道不是规模；规模判断正道 = 人工读 body 判档、关键词只做定位辅助；brands_found 卖 vs 提及（score_leads.py 机械判断的局限）
  - 补背调兜底流程：品牌/规模证据缺失 → anysearch（批量）/ WebSearch（零星）；官网抓不动 → kitesurf
  - 改名 NEXT.md → HANDOFF.md，定位「跨窗口迭代衔接 + 明日待办」
- **2026-09-03**
  - 提取 ENF Solar 抓取脚本 `scripts/fetch_enf.py`（seller→distributor、邮箱 JS 解码、429 退避、欧盟 27 国 + 乌克兰）
  - 修品牌关键词 substring 误命中（INGE / Fusion / OHm）：backfill.py + score_leads.py 改词边界匹配，教训见 `references/brand-mapping.md`
  - 市场边界确认：欧盟 27 国 + 乌克兰

## 长期完善方向（技术债 + 迭代项）

- **正则提取规模信号（暂缓）**：曾想给 backfill.py 加正则自动判 `scale_tier`，实测官网措辞参差、误判漏判严重（Großhandel 是渠道不是规模、SolarV/New Power 措辞不同就漏）。**长期方向**：可探索「关键词定位 + LLM 读候选句判档」半自动，纯正则不可行。当前正道 = Claude 人工读 body 判档（见 `qualification-rules.md` 规模判断流程）。
- **评分体系完善**：双模式四维是 v1。可迭代——触达「因地制宜」目前只写 rules、代码未自动判（德国电话降级靠人工）；规模「估」的边界可再收紧。
- **获客渠道完善**：已跑通 Google Maps / 搜索 API / ENF 目录 / 列表页；展会名录、海关数据、品牌官网 find-a-distributor 待接入（见 `multi-source.md`）。
- **brands_found 卖 vs 提及自动区分**：`score_leads.py` 的 `sells_deye` 是机械判断，比价平台列举品牌会误标「卖 Deye」（实测 Solarscouts）。长期加「上下文语义判断」，当前靠 Claude 读 `brands_context` 手工降级。

## 开发注意（铁律，别删）

- 改代码在 `D:/Agent/git/google-map-lead-filter/`（独立 git + 远程），改完 commit + push。
- **改完同步 skills 目录**，否则新窗口触发 skill 跑旧版：
  ```bash
  SRC=/d/Agent/git/google-map-lead-filter
  for DST in ~/.agents/skills/google-map-lead-filter ~/.claude/skills/google-map-lead-filter; do
    rm -rf "$DST"; mkdir -p "$DST"
    cp -r "$SRC/SKILL.md" "$SRC/references" "$SRC/scripts" "$SRC/templates" "$SRC/HANDOFF.md" "$DST/"
  done
  ```
- 数据文件（`D:/Agent/tmp/*.json`）是会话产物，不入 git。
- 口径唯一来源 `references/qualification-rules.md`，SKILL.md 不重复数字。
- **技术债**：`render_report.py` 的 `wa_link()` 硬编码法国 +33，扩展德国(+49)/荷兰(+31)/西班牙(+34)前要按 `country` 映射。

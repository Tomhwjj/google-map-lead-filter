---
name: google-map-lead-filter
description: 多渠道外贸经销商线索挖掘与分级（Google Maps + Google Search + 品牌官网 + 展会名录 + 海关数据）。当用户要「找某产品在某国家/城市的经销商/批发商/进口商」、批量挖掘海外 B2B 客户线索、或把一批潜在客户按优先级分 A/B/C 级时触发。输入产品类目 + 目标国家/城市 + 客户类型（+ 我方合作品牌），输出 A/B/C 级线索表格 + 可交互 HTML 报告。脚本批量抓取为主（Google Maps 批量 + 搜索 API 批量 + 列表页抓取），Claude 负责初筛背调评分分级。
---

# 外贸经销商线索挖掘与分级（多源）

多渠道挖掘海外 B2B 经销商线索：**脚本批量抓取为主**（Google Maps 批量 + 搜索 API 批量 + 列表页抓取），然后初筛 → 背调 → 五维评分 → A/B/C 分级。

> ⚠️ **为什么脚本批量为主**：手工 WebSearch 的天花板就是十几条——法国光伏公司实际 1377 家，手工只覆盖 1.3%。批量脚本一次出几百条：**Google Maps 批量抓安装商长尾**（法国 8 城市实测 301 条）+ **搜索 API 批量挖批发商大鱼**（分销渠道被少数大批发商垄断，这类大鱼在 Google Maps 标签是「能源设备供应商」而非「distributor」，靠搜索 API 命中）。两者互补，缺一不可。

## 输入

- **产品类目**（如「光伏组件」「solar panel」）——**这是核心输入，技能目标是「找某品类的海外渠道」，不是「找某品牌的经销商」**
- **目标国家/城市**（如「德国 汉堡」「Netherlands」）——**仅欧盟国家，英国不在范围**（见 `references/deye-ecosystem.md`）
- **客户类型**（distributor / wholesaler / importer）
- **我方合作品牌（可选）**（如「Deye 德业」，用于背调判断官网是否在销售我方产品；含贴牌品牌，见 `references/brand-mapping.md`）。**品牌不是必需的**：未指定品牌时，按「增量口径」挖竞品品牌 + 不限品牌的品类批发商，这批本身就是品类渠道大鱼。

## 执行流程

### 第一步：解析需求，生成搜索词

按 `references/search-keywords.md` 生成搜索词组合（产品 × 客户类型 × 城市，含本地语言词），同时生成多源搜索词（见 `references/multi-source.md`）。

### 第二步：多源批量挖掘

**① 搜索 API 批量**（挖批发商大鱼）——用 `scripts/search_leads.py`（走 AnySearch 聚合搜索 API，零额外成本）：

```bash
python scripts/search_leads.py "grossiste photovoltaïque France" "Sungrow distributeur France" \
    "installateur photovoltaïque Lyon" --language fr --out search.csv
# 或从文件读几十个关键词
python scripts/search_leads.py --queries-file keywords.txt --language fr --out search.csv
```

**② 列表页抓取**（品牌 Find-a-Distributor 名单 + 展会名录）——用 `scripts/list_scraper.py`：

```bash
python scripts/list_scraper.py "https://brand.com/where-to-buy" "https://show.com/exhibitors" --out list.csv
```

搜索词按 `references/multi-source.md` 生成（增量口径：竞品品牌 + 不限品牌品类词）。⚠️ 列表页仅对静态 `<a>` 列表有效；JS 动态页（华为 find-distributor、tecsol 名录）用 **anysearch extract** 抓全文兜底（已验证 tecsol 名录 223 家供应商）。

### 第三步：Google Maps 批量抓取（主力出量层，铺安装商长尾）

对「产品 + 城市」关键词批量跑 `scripts/fetch_gmaps.py`，一次一个城市，铺开本地安装商/小批发商长尾：

```bash
for city in Lyon Marseille Bordeaux Toulouse; do
  python scripts/fetch_gmaps.py "installateur photovoltaïque $city" --max 40 --out "fr_$city.csv"
done
```

脚本用 Playwright headless + 代理抓取，滚动加载，解析公司名/评分/电话/官网/Google Maps 链接，输出 CSV。**内置限速（2-3 秒延迟），勿改快。** 这是出量主力：安装商在 Google Maps 是海量长尾，一个城市几十条、几个城市几百条。

### 第四步：合并去重

用 `scripts/merge_leads.py` 按官网域名去重合并（目录模式下文件名当城市标签）：

```bash
python scripts/merge_leads.py D:/tmp/fr_gmaps/ search.csv list.csv --out merged.csv
```

> ⚠️ **保留高分版**：多源（手工背调 / gmaps 批量 / 搜索 API）合并时，同一公司（同域名）会出多条记录。手工背调的高分版（带邮箱/联系人/品牌命中）常被 gmaps 低分版覆盖——法国实测 BayWa 95A→68B、POwR 90A→68B 就是教训。合并后必须按 domain 对照，**同域名保留 score 最高（或字段最全）的那条**，低分重复删掉，别让精调成果被批量版冲掉。

### 第五步：初筛

读 CSV，按 `references/qualification-rules.md` 的初筛规则淘汰：广告(Sponsored)、纯零售/建材超市、非目标行业、无官网无电话、黄页伪官网。

### 第六步：背调

1. 跑背调脚本抓官网，**用 `--brands` 传入我方品牌 + 贴牌品牌**（贴牌映射见 `references/brand-mapping.md`），在正文里搜品牌命中：

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk"
```

2. 读 `backfill.json`，逐条判断：**品牌匹配（核心，`brands_found` 是否命中我方品牌）+ 上下文确认在销售而非仅提及**、渠道类型、公司规模、近期动态。官网抓不动时用 **kitesurf** 兜底抓该站；仍失败用 WebSearch 搜「公司名 + 品牌名 + distributor」找产品页/行业新闻证据（大鱼官网常 JS 重渲染，backfill 抓不到品牌，但 WebSearch 能确认命中）。
3. 用 WebSearch 搜「公司名 + linkedin」补 LinkedIn 链接。

### 第七步：双模式评分 + 分级

跑 `scripts/score_leads.py` 自动算两套评分（头部/长尾，各 100 分），生成 `sells_deye`、`score`/`grade`（头部）、`score_lt`/`grade_lt`（长尾）及每维度的评分依据：

```bash
python scripts/score_leads.py leads_final.json --out leads_scored.json
```

评分口径、每维权重、打分标准、三态防幻觉规则**全部以 `references/qualification-rules.md` 为唯一来源**（SKILL.md 不重复具体数字，避免两处漂移）。A级 80-100 / B级 50-79 / C级 0-49。

### 第八步：输出表格 + UI 报告

1. 按 `templates/lead-table.md` 的 13 字段表格输出 markdown 表格（对话里给用户看）。
2. 把评分结果写成 `leads_final.json`（字段见 `scripts/render_report.py` 顶部注释），生成可交互的 HTML 报告：

```bash
python scripts/render_report.py leads_final.json --out report.html --title "英国光伏经销商线索 · Deye/Sunsynk 命中"
```

**UI 报告规格**（`render_report.py` 已实现，以下 8 要素必须全部具备，缺一不可）：

1. **双评分模式切换** —— 顶部两按钮「头部模式（啃大客户）/ 长尾模式（铺小客户）」，切换后卡片分数/分级/维度条/排序/统计栏全部按当前模式重算。
2. **评分规则图例** —— 可折叠 `<details>`，列 4 维度给分依据（产品匹配 / 渠道 / 规模 / 触达，头部与长尾权重不同），数字以 `references/qualification-rules.md` 为准。
3. **每卡评分依据** —— 每个维度条后跟「依据」小字，三态标注：`证据`（经营痕迹：仓库/多品牌/评分数）→ 按档位；`估`（背调过无硬证据）→ 档位+「估」；`未确认`（未背调）→ 中性分 +「未确认·未背调」。
4. **卖 Deye 标志** —— `sells_deye=true` 卡片顶部绿色「✓ Deye」badge。
5. **联系人触达排序** —— 电话（📞 前置，`tel:` 链接）→ WhatsApp（`wa.me` 绿标）→ 官网 → 邮箱（`mailto:`）→ LinkedIn → Google Maps。
6. **导出 CSV 按钮** —— 客户端 Blob 下载，UTF-8 BOM（Excel 兼容），文件名 `leads_{当前模式}.csv`，导出当前筛选可见的卡片。
7. **A/B/C 筛选** —— 全部/A/B/C 按钮，按当前模式分级过滤。
8. **统计栏** —— 线索总数 / A级数 / B级数 / 卖Deye数（随模式切换更新）。

**电话转 WhatsApp**（落地「电话是可加 WhatsApp 的获客主路径」）：`wa_link()` 把法国本地 10 位号去前导 0 补 `33`（其它 10 位号也补 33），生成 `wa.me` 链接。

3. 用本地 HTTP 服务打开报告，**不要 file:// 双击**——Chrome 会把 `file:` 页面当独立安全源，拦截官网/Google Maps 外链跳转（console 报 "file: URLs are treated as unique security origins"）：

```bash
python scripts/serve_report.py report.html
```

## 反幻觉铁律

- 不编造公司、邮箱、联系人、代理品牌、规模 —— 只写来源能验证的事实。
- 每行必须带来源 URL（Google Maps 链接 / 官网 / 行业新闻）。
- 判断不了标「未确认」，不脑补。
- **不把 C 级标 A 级**：分数是算出来的。
- **不自动发邮件**：只产出开发建议，发送由人工确认。
- 多源线索同样要背调验证品牌命中，不能凭搜索结果标题就认定是经销商。

## 引用

- `references/deye-ecosystem.md` — **行业认知底座**：Deye 生态 / 贴牌 / 市场边界 / 欧盟经销商种子，动手前先读
- `references/search-keywords.md` — 关键词生成
- `references/multi-source.md` — 多源挖掘方法（Google Search / 品牌官网 / 展会 / 海关数据）
- `references/qualification-rules.md` — 初筛 / 背调 / 评分 / 分级
- `references/brand-mapping.md` — 品牌贴牌 / 代工映射（背调 --brands 依据）
- `references/compliance-rules.md` — 合规边界 / 限速 / 禁止行为
- `templates/lead-table.md` — 输出表格模板
- `scripts/fetch_gmaps.py` — Google Maps 批量抓取脚本（铺安装商长尾，出量主力）
- `scripts/search_leads.py` — 搜索 API 批量挖掘脚本（AnySearch，挖批发商大鱼）
- `scripts/list_scraper.py` — 列表页抓取脚本（品牌经销商名单 / 展会名录）
- `scripts/merge_leads.py` — 多源 CSV 合并去重脚本
- `scripts/backfill.py` — 批量背调脚本（抓官网提取邮箱/品牌）
- `scripts/render_report.py` — 线索 HTML 报告生成器（读 `leads_final.json` → 自包含 HTML）
- `scripts/serve_report.py` — 本地 HTTP 服务器打开报告（解决 file:// 拦截外链）

---
name: google-map-lead-filter
description: 多渠道外贸经销商线索挖掘与分级（Google Maps + Google Search + 品牌官网 + 展会名录 + 海关数据）。当用户要「找某产品在某国家/城市的经销商/批发商/进口商」、批量挖掘海外 B2B 客户线索、或把一批潜在客户按优先级分 A/B/C 级时触发。输入产品类目 + 目标国家/城市 + 客户类型（+ 我方合作品牌），输出 A/B/C 级线索表格 + 可交互 HTML 报告。默认多源为主，Google Maps 降级为补充源。
---

# 外贸经销商线索挖掘与分级（多源）

多渠道挖掘海外 B2B 经销商线索：**Google Search / 品牌官网 / 展会名录 / 海关数据为主，Google Maps 补充**，然后抓取 → 初筛 → 背调 → 五维评分 → A/B/C 分级。

> ⚠️ **为什么多源为主**：Google Maps 对纯 B2B 批发商覆盖不足。英国实测：Google Maps 只挖到 8 条（4 经销商 + 3 安装商 + 1 竞品），而 Google Search 多挖出 8 条 Google Maps 漏掉的真实批发商（Segen UK、Menlo Electric、Powerland、E-Tech、GL-E、Phase、LAMPS、SolarVoxGreen）。分销渠道常被少数大批发商垄断，这些大鱼往往官网规模大、在 Google Maps 上标签是「能源设备供应商」而非「distributor」，靠单一地图源根本捞不到。

## 输入

- **产品类目**（如「光伏组件」「solar panel」）——**这是核心输入，技能目标是「找某品类的海外渠道」，不是「找某品牌的经销商」**
- **目标国家/城市**（如「德国 汉堡」「Netherlands」）
- **客户类型**（distributor / wholesaler / importer）
- **我方合作品牌（可选）**（如「Deye 德业」，用于背调判断官网是否在销售我方产品；含贴牌品牌，见 `references/brand-mapping.md`）。**品牌不是必需的**：未指定品牌时，按「增量口径」挖竞品品牌 + 不限品牌的品类批发商，这批本身就是品类渠道大鱼。

## 执行流程

### 第一步：解析需求，生成搜索词

按 `references/search-keywords.md` 生成搜索词组合（产品 × 客户类型 × 城市，含本地语言词），同时生成多源搜索词（见 `references/multi-source.md`）。

### 第二步：多源挖掘（主）

按 `references/multi-source.md` 逐源挖线索，合并成 `leads_multisource.csv`：

1. **Google Search 关键词挖掘**（零 API 成本，最有效）——用 WebSearch 搜品牌 + 国家 + distributor/wholesale/stockist 组合，命中即出线索。
2. **品牌官网 Find-a-Distributor 名单**——Sunsynk/Deye 等品牌官网常有官方分销商查询页，直接列地区授权经销商。
3. **展会名录**——行业展会（如 Solar & Storage Live UK）参展商名单，全是真实贸易商。
4. **海关数据**（ImportYeti 等，免费额度）——查谁在向目标国进口我方品牌/品类。

每源记录 `company_name / website / country / city / customer_type / source_url`，标注来源。

### 第三步：Google Maps 补充（补充源）

对搜索词跑抓取脚本，补 Google Maps 上遗漏的本地小批发商/安装商：

```bash
python scripts/fetch_gmaps.py "solar panel distributor Hamburg" --max 50 --out leads.csv
```

脚本用 Playwright headless + 代理抓取，滚动加载，解析公司名/评分/电话/官网/Google Maps 链接，输出 CSV。**内置限速（2-3 秒延迟），勿改快。**

### 第四步：合并去重

多源 CSV + Google Maps CSV 按官网域名去重合并，得到 `leads_all.csv`。

### 第五步：初筛

读 CSV，按 `references/qualification-rules.md` 的初筛规则淘汰：广告(Sponsored)、纯零售/建材超市、非目标行业、无官网无电话、黄页伪官网。

### 第六步：背调

1. 跑背调脚本抓官网，**用 `--brands` 传入我方品牌 + 贴牌品牌**（贴牌映射见 `references/brand-mapping.md`），在正文里搜品牌命中：

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk"
```

2. 读 `backfill.json`，逐条判断：**品牌匹配（核心，`brands_found` 是否命中我方品牌）+ 上下文确认在销售而非仅提及**、渠道类型、公司规模、近期动态。官网抓不动时用 **kitesurf** 兜底抓该站；仍失败用 WebSearch 搜「公司名 + 品牌名 + distributor」找产品页/行业新闻证据（大鱼官网常 JS 重渲染，backfill 抓不到品牌，但 WebSearch 能确认命中）。
3. 用 WebSearch 搜「公司名 + linkedin」补 LinkedIn 链接。

### 第七步：五维评分 + 分级

按 `references/qualification-rules.md` 的评分表打分（产品30/渠道25/规模20/联系人15/活跃10），A级 80-100 / B级 50-79 / C级 0-49。

### 第八步：输出表格 + UI 报告

1. 按 `templates/lead-table.md` 的 13 字段表格输出 markdown 表格（对话里给用户看）。
2. 把评分结果写成 `leads_final.json`（字段见 `scripts/render_report.py` 顶部注释），生成可交互的 HTML 报告（A/B/C 筛选 + 评分条 + 官网/电话/邮箱/Google Maps 直达）：

```bash
python scripts/render_report.py leads_final.json --out report.html --title "英国光伏经销商线索 · Deye/Sunsynk 命中"
```

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

- `references/search-keywords.md` — 关键词生成
- `references/multi-source.md` — 多源挖掘方法（Google Search / 品牌官网 / 展会 / 海关数据）
- `references/qualification-rules.md` — 初筛 / 背调 / 评分 / 分级
- `references/brand-mapping.md` — 品牌贴牌 / 代工映射（背调 --brands 依据）
- `references/compliance-rules.md` — 合规边界 / 限速 / 禁止行为
- `templates/lead-table.md` — 输出表格模板
- `scripts/fetch_gmaps.py` — Google Maps 抓取脚本
- `scripts/backfill.py` — 背调脚本
- `scripts/render_report.py` — 线索 HTML 报告生成器（读 `leads_final.json` → 自包含 HTML）
- `scripts/serve_report.py` — 本地 HTTP 服务器打开报告（解决 file:// 拦截外链）

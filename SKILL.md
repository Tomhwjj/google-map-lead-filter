---
name: google-map-lead-filter
description: 基于 Google Maps 的外贸经销商线索挖掘与分级。当用户要「找某产品在某国家/城市的经销商/批发商/进口商」、批量挖掘海外 B2B 客户线索、或把一批潜在客户按优先级分 A/B/C 级时触发。输入产品类目 + 目标国家/城市 + 客户类型，输出 A/B/C 级线索表格。
---

# Google Maps 外贸经销商线索挖掘与分级

基于 Google Maps 批量挖掘海外 B2B 经销商线索，抓取 → 初筛 → 背调 → 五维评分 → A/B/C 分级。

## 输入

- **产品类目**（如「光伏组件」「solar panel」）
- **目标国家/城市**（如「德国 汉堡」「Netherlands」）
- **客户类型**（distributor / wholesaler / importer）
- **我方合作品牌**（如「Deye 德业」，用于背调判断官网是否在销售我方产品）

## 执行流程

### 第一步：解析需求，生成搜索词

按 `references/search-keywords.md` 生成搜索词组合（产品 × 客户类型 × 城市，含本地语言词）。

### 第二步：抓取 Google Maps

对每个搜索词跑抓取脚本，结果合并去重：

```bash
python scripts/fetch_gmaps.py "solar panel distributor Hamburg" --max 50 --out leads.csv
```

脚本用 Playwright headless + 代理抓取，滚动加载，解析公司名/评分/电话/官网/Google Maps 链接，输出 CSV。**内置限速（2-3 秒延迟），勿改快。**

### 第三步：初筛

读 CSV，按 `references/qualification-rules.md` 的初筛规则淘汰：广告(Sponsored)、纯零售/建材超市、非目标行业、无官网无电话、黄页伪官网。

### 第四步：背调

1. 跑背调脚本抓官网，**用 `--brands` 传入我方品牌 + 贴牌品牌**（贴牌映射见 `references/brand-mapping.md`），在正文里搜品牌命中：

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk"
```

2. 读 `backfill.json`，逐条判断：**品牌匹配（核心，`brands_found` 是否命中我方品牌）+ 上下文确认在销售而非仅提及**、渠道类型、公司规模、近期动态。官网抓不动时用 **kitesurf** 兜底抓该站。
3. 用 WebSearch 搜「公司名 + linkedin」补 LinkedIn 链接。

### 第五步：五维评分 + 分级

按 `references/qualification-rules.md` 的评分表打分（产品30/渠道25/规模20/联系人15/活跃10），A级 80-100 / B级 50-79 / C级 0-49。

### 第六步：输出表格

按 `templates/lead-table.md` 的 13 字段表格输出。

## 反幻觉铁律

- 不编造公司、邮箱、联系人、代理品牌、规模 —— 只写来源能验证的事实。
- 每行必须带来源 URL（Google Maps 链接 + 官网）。
- 判断不了标「未确认」，不脑补。
- **不把 C 级标 A 级**：分数是算出来的。
- **不自动发邮件**：只产出开发建议，发送由人工确认。

## 引用

- `references/search-keywords.md` — 关键词生成
- `references/qualification-rules.md` — 初筛 / 背调 / 评分 / 分级
- `references/brand-mapping.md` — 品牌贴牌 / 代工映射（背调 --brands 依据）
- `references/compliance-rules.md` — 合规边界 / 限速 / 禁止行为
- `templates/lead-table.md` — 输出表格模板
- `scripts/fetch_gmaps.py` — 抓取脚本
- `scripts/backfill.py` — 背调脚本

# 初筛 + 背调 + 评分规则

线索从「Google Maps 抓取」到「A/B/C 分级」经过三关：**初筛 → 背调 → 评分**。

## 一、初筛规则（读 CSV 后先过滤）

`fetch_gmaps.py` 输出的 CSV 含噪声，先按以下规则淘汰，不进入背调：

| 淘汰类型 | 判据 |
|---------|------|
| 广告/赞助结果 | `raw_text` 含 "Sponsored" |
| 纯零售/超市 | 品类是 `Home improvement store`、`Retail store` 等（如 OBI 建材超市） |
| 非目标行业 | 品类与产品无关（如搜光伏混入的装修公司、物业） |
| 信息不全 | 无官网且无电话（无法背调） |
| 黄页伪官网 | `website` 指向 firmenfreund / yell / 黄页类站点而非官网 |

初筛后的线索进入背调。

## 二、背调规则（两层：脚本抓 + Claude 判）

背调的核心目标：**判断该企业官网是否在销售我方合作品牌的产品**（如 Deye 德业）。这直接决定「产品匹配度 30%」的分数。

### 抓取层（脚本 backfill.py）

对每个初筛通过的线索，脚本抓官网首页 + `contact` / `kontakt` / `impressum` / `about` 页 + 品牌页（`/brands`、`/products`、`/inverters` 等），提取：
- 页面标题、meta description、正文文本
- 邮箱、电话
- **品牌命中**：用 `--brands` 传入我方品牌列表（含贴牌品牌，见 `brand-mapping.md`），在正文里搜品牌关键词，输出命中的品牌 + 上下文片段

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk"
```

> ⚠️ 脚本已用 `networkidle` 等待 JS 渲染——电商站（Shopify/Magento）的品牌列表常靠 JS 动态加载，若用 `domcontentloaded` 会漏抓（实测 HDM Solar 首页：domcontentloaded 时 Sunsynk=0，networkidle 后=5）。

### 判断层（Claude 现场）

Claude 读 `backfill.json`，按优先级判断：

| 判断项 | 判据 |
|-------|------|
| **品牌匹配（核心）** | `brands_found` 命中我方品牌 = 在卖我们的货；命中同类竞品 = 有替换可能；都没命中 = 不相关 |
| 渠道类型 | 官网自述 distributor / wholesaler / Großhandel / 有批发板块；或只是 installer / 安装商 |
| 公司规模 | about 页员工数、成立年份、仓储/物流描述 |
| 近期动态 | news 有 6 个月内的更新、招聘信息 |

> ⚠️ `brands_found` 命中只是「提到该品牌」，要结合 `brands_context` 上下文判断是「作为经销商在销售」还是「作为竞品被提及」。判断不了标「未确认」。

### 兜底：kitesurf

个别官网 playwright 抓不动（JS 渲染/反爬）时，Claude 现场用 **kitesurf** 抓该官网转 Markdown，再判断。

### LinkedIn

用 WebSearch 搜「公司名 + linkedin」补 LinkedIn 链接（脚本抓 LinkedIn 易撞登录墙，不划算）。

## 三、五维评分（总分 100）

| 维度 | 权重 | 评分标准 |
|------|------|---------|
| 产品匹配度 | 30 | 官网销售我方品牌（如 Deye）30；销售同类竞品品牌 20；完全不相关 0 |
| 渠道匹配度 | 25 | 明确标注 distributor/importer 25；有批发业务板块 15；纯零售 0 |
| 公司规模 | 20 | 员工 >50 人或年营业额 >500 万美金 20；10-50 人 10；<10 人 0 |
| 联系人质量 | 15 | 采购经理/供应链负责人邮箱 15；仅前台邮箱 5；无联系人 0 |
| 近期活跃度 | 10 | 近 6 个月有新品/展会/招聘 10；近 1 年有更新 5；无更新 0 |

## 四、分级标准

| 分级 | 分数 | 处理 |
|------|------|------|
| A 级 | 80-100 | 优先跟进 |
| B 级 | 50-79 | 培育跟进 |
| C 级 | 0-49 | 暂存 |

## 五、反幻觉铁律

- **不编造**公司、邮箱、联系人、代理品牌、规模——只写官网/来源里能验证的事实。
- 每行线索必须带**来源 URL**（Google Maps 链接 + 官网）。
- 判断不了就标「未确认」，不要脑补。
- **不把 C 级标 A 级**：分数是算出来的，不是估的。
- 不自动发邮件（只产出开发建议，发送由人工确认）。

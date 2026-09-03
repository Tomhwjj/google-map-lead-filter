# 多源获客途径（数据来源）

> **所有途径平等并列、不分主次、不分先后**。目标是每个途径都尽可能挖到线索，多源并行、叠加去重——一条线索可能从多个源撞出来，合并时保留字段最全/评分最高的那条。

## 市场边界（铁律）

- **只做欧洲（欧盟 27 国）**，英国不在范围内（英国已脱欧，非欧盟）。
- 美国（Sol-Ark 市场）不做，Sol-Ark 贴牌仅用于识别同源产品。
- 英国、南非、北美等非欧盟市场一律不挖。

## 核心口径：存量 vs 增量（先想清楚再搜）

两条搜索路线，价值有高低：

| 口径 | 搜什么 | 挖到的客户 | 价值 |
|------|--------|-----------|------|
| **存量** | `{我方品牌} distributor` | 已经在卖 Deye 的经销商 | **最高优先**——卖 Deye 为主，精准获客 |
| **增量** | `{竞品品牌} distributor` + 储能批发词（不限品牌） | 卖竞品储能/逆变器但没卖 Deye 的批发商 | 次之——现成渠道，pitch 引 Deye 替换 |

**存量优先、增量也挖**：存量（卖 Deye）产品匹配直接满分，是最高价值；增量（卖竞品）同样要挖——对方需求大、我们价格好就能抢下来（广撒网）。两条线都搜，存量优先。

**品牌是评分加分项、不是抓取筛选标准**：抓取按品类广撒网（储能/逆变器），不按品牌过滤；背调后 Deye（含贴牌）命中 = 存量满分，卖竞品 = 增量次之。

## 获客途径全景（平等并列，不分主次）

### A. Google Maps 批量（脚本 `fetch_gmaps.py`）

搜「品类/客户类型/品牌 × 城市」，**全客户类型都搜**：

- ⚠️ **纠正「Google Maps 只铺安装商」的旧认知**：Google Maps 搜批发商词（`wholesaler` / `Großhändler` / `distributor`）命中的是「Solar energy equipment supplier」分类的**批发商/设备供应商**，一样能挖批发商（2026-09 实测：搜 `solar wholesaler Hamburg` 20 条全命中 equipment supplier，如 solarspeicher24 / SEH SolarEnergie）。
- 客户类型词全铺：distributor / wholesaler / importer / installer（见 `search-keywords.md`），不要只搜 installer。
- 批发商有实体仓库/展厅，Google Maps 上通常有 listing，是挖批发商的完整途径，不是"安装商专属"。

### B. 搜索 API 批量（脚本 `search_leads.py`，AnySearch）

搜「品牌/品类 + 国家 + 渠道词」批量跑，挖官网/搜索结果。品牌词模板见下。

### C. ENF Solar 企业目录（`enf.com`）★ 核心目录源

全球光伏企业目录（2005 年起，16000+ 制造商/销售商/安装商，8 语言）：

- 企业分类：**经销商 / 批发商 / 安装商 / 制造商 / 系统集成商**，按**国家**筛。
- 产品分类：组件 / **逆变器** / **储能系统**——直接对应 Deye 品类。
- **能直接挖到经销 Deye 的商户**：企业页列「已知销售商数量」+ 代理品牌（实测有商户页面标注经销 Deye / Sunsynk / Lux Power / Dyness 等）。
- 免费，企业页含联系方式，是比 Google Maps 更"批发商友好"的目录源。

### D. 品牌官网 Find-a-Distributor

品牌官网的渠道查询页，直接列地区授权经销商：

- **存量线**：Deye 官网 `deyeinverter.com` → where-to-buy / 合作新闻稿；贴牌官网（Sunsynk 等）→ find-an-installer。
- **增量线**：竞品品牌（Sungrow / Huawei / GoodWe / Growatt / FoxESS / Sigenergy）官网的经销商名单 = 卖竞品的增量目标。
- 找 `find-a-distributor` / `where-to-buy` / `partners` / `dealers` 页。
- **优点**：官方认证、渠道类型明确。**缺点**：只列官方授权，漏未授权但在卖的批发商，需配合其它源补。

### E. 展会参展商名录

行业展会参展商名单全是真实贸易商：

- 光伏/储能：Intersolar Europe（德国）、Genera（西班牙）、Key Energy（意大利）、Be Positive（法国）、Solar Solutions（荷兰）。
- 搜 `{行业} trade show {国家} exhibitor list 2026` 找名录页，筛「distributor / wholesaler / supplier」标签。

### F. 各国光伏协会会员名录

协会官网的会员/企业数据库 = 官方认证的经销商/安装商：

- 德国 BSW（`solarwirtschaft.de`）、法国 Enerplan（`enerplan.asso.fr`）、西班牙 UNEF（`unef.es`）、荷兰 Holland Solar、欧洲级 SolarPower Europe。
- 会员名录质量高（真实运营、合规），是 Google Maps 之外的干净渠道源。

### G. 认证/并网数据库

通过认证/并网的安装商/经销商 = 真实运营的渠道：

- 德国 VDE / TÜV 认证安装商清单、各国并网企业清单。
- 搜 `{国家} certified solar installer list` / `{国家} grid-connected installer registry`。

### H. 本地黄页 / 商业目录

Google Maps 之外的批发商补充：

- 德国 `gelbeseiten.de`（黄页）、`wlw.de`（Wer liefert was）；法国 `Kompass.fr`、`PagesJaunes`。
- 按企业分类搜批发商（Großhandel / grossiste / mayorista），能补 Google Maps 漏掉的黄页入驻批发商。

### I. 海关数据（验证进口实锤）

查谁在向目标国进口我方品牌/品类，直接定位真实进口商。

⚠️ **ImportYeti 实测结论（2026-09 验证）**：
- **数据是美国海关（U.S. Imports），不是欧洲**。搜 Deye 出的是宁波德业工厂出口到美国的提单 + 美国进口商，对欧洲帮助有限。
- **Cloudflare Turnstile 反爬**：自动化浏览器被卡「正在验证」，只能人工在真实浏览器查。
- 结论：ImportYeti 适合**北美市场**，不适合欧洲；只能人工操作。

**欧洲海关替代**：欧盟 TARIC/Access2Markets（只给关税，不给进口商）、各国海关公开数据（部分开放）、国内平台（网易外贸通/52wmb/外贸邦，欧洲覆盖有限）。

### J. LinkedIn（决策人触达，半自动）

- Sales Navigator 搜 `solar distributor` + 国家 → 经销商公司 + 决策人。
- ⚠️ 撞登录墙，脚本难抓，手动/半自动，作为触达决策人的补充，不做批量主力。

## 多源并行原则

- **不分主次、不分先后**：所有途径一起上，每个源都尽可能挖，不因为"某源命中少"就跳过。
- **叠加不替代**：不同源覆盖不同盲区（Google Maps 覆盖有实体仓库的、ENF 覆盖目录入驻的、协会覆盖合规认证的、搜索 API 覆盖线上批发商），一条渠道漏掉的另一条补上。
- **交叉去重**：同一公司多源撞出时，按官网域名合并，保留字段最全/评分最高那条（见 `SKILL.md` 第四步）。
- **反幻觉铁律贯穿**：每个源的线索都要进官网背调验证，不能凭目录/搜索标题就认定是经销商。

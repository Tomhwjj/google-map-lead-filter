# 多源挖掘方法

Google Maps 覆盖不足时的补充/主力数据源。核心原则：**用品牌名 + 国家 + 渠道词去搜「谁在卖我方产品」，而不是搜「谁在卖这个品类」**。分销渠道被少数大批发商垄断，直接搜品牌名命中率远高于搜品类名。

## 1. Google Search 关键词挖掘（零 API，最有效）

用 WebSearch 逐组搜，命中即出线索。关键词模板（`{品牌}` 含贴牌品牌，如 Deye → Sunsynk/Sol-Ark/INGE，见 `references/brand-mapping.md`）：

| 模式 | 示例 | 命中类型 |
|------|------|----------|
| `{品牌} distributor {国家}` | `Sunsynk distributor UK` | 官方/授权分销商 |
| `{品牌} authorized distributor {国家}` | `Deye authorized distributor UK` | 授权分销商 |
| `{品牌} wholesale {国家}` | `Sunsynk wholesale UK` | 批发商 |
| `{品牌} stockist {国家}` | `Sunsynk stockist UK` | 存货商/零售商 |
| `{品牌} {国家} {城市}` | `Sunsynk London` | 本地渠道 |
| `{品牌} trade supplier` | `Sunsynk trade supplier` | 贸易供应商 |
| 竞品品牌替换（找增量机会） | `Growatt distributor UK` | 卖竞品的批发商，可 pitch 引 Deye 替换 |

**产出**：每命中一条记 `company_name / website / country / city / customer_type / source_url`。

**背调要求**：搜索结果标题说「distributor」不等于真分销商，必须进官网确认（跑 backfill 或 WebSearch 产品页），证据写进 reason。

## 2. 品牌官网 Find-a-Distributor 名单

品牌官网常有官方渠道查询页，直接列地区授权经销商，是最干净的线索源：

- Sunsynk 官网 `sunsynk.org` → "Find an installer" / "Where to buy" / distributor locator
- Deye 官网 `deyeinverter.com` → "Where to buy" / 合作新闻稿（「Deye 与 XX 签约分销」）
- 其它品牌同理：找 `find-a-distributor` / `where-to-buy` / `partners` / `dealers` 页

**优点**：官方认证，渠道类型明确。**缺点**：只列官方授权，漏掉未授权但实际在卖的批发商，需配合 Google Search 补。

## 3. 展会名录

行业展会参展商名单全是真实贸易商，直接抓名录：

- 光伏：Solar & Storage Live（UK）、Intersolar（欧洲）、Genera（西班牙）、Key Energy（意大利）
- 搜 `{行业} trade show {国家} exhibitor list 2026` 找名录页
- 从名录里筛「distributor / wholesaler / supplier」标签的公司

**产出**：公司名 + 官网 + 展位，渠道类型较准。

## 4. 海关数据（免费额度）

查谁在向目标国进口我方品牌/品类，直接定位真实进口商：

- **ImportYeti**（importyeti.com）：免费额度查「谁进口 X」，搜品牌名/HS code，出供应商+进口商名单
- 网易外贸通、52wmb、外贸邦等国内平台：免费查询额度，可查 HS code 对应进口商
- 搜 `{国家} customs import data {品类}` 或 `{品牌} importer of record {国家}`

**注意**：海关数据只给公司名 + 货值 + 品类，联系方式/官网要 WebSearch 补；免费额度有限，优先查已确定的重点目标。

## 优先级建议

1. **品牌官网 Find-a-Distributor**（最准，先做）
2. **Google Search 品牌+国家+渠道词**（最全，主力）
3. **展会名录**（补充真实贸易商）
4. **海关数据**（验证进口实锤，重点目标才查）
5. **Google Maps**（兜底本地小批发商/安装商，降级为补充源）

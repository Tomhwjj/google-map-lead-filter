# 搜索关键词生成规则

根据用户输入（产品类目、目标国家/城市、客户类型），生成 Google Maps 搜索词组合。

## 通用组合模板

```
{产品} {客户类型词} {城市}
{产品} {客户类型词} {国家}
{产品} {客户类型词} {城市} {国家}
```

## 客户类型词（按目标客户类型选）

| 中文 | 英文 | 德语 | 荷兰语 | 法语 | 西语 |
|------|------|------|--------|------|------|
| 经销商 | distributor | Distributor / Händler | distributeur | distributeur | distribuidor |
| 批发商 | wholesaler | Großhändler | groothandel | grossiste | mayorista |
| 进口商 | importer | Importeur | importeur | importateur | importador |

## 产品线拆分（以光伏为例）

不同产品线的买家不同，应拆开搜：

- 组件：`solar panel` / `PV module` / `Photovoltaik`
- 逆变器：`solar inverter` / `Wechselrichter`
- 支架：`mounting system` / `Montagesystem`
- 储能：`battery storage` / `Speicher`
- 系统集成：`solar system` / `Solaranlage`

## 本地语言关键词（欧洲重点市场）

| 语言 | 光伏批发商 | 光伏进口商 |
|------|-----------|-----------|
| 德语 | `Solaranlagen Großhändler` / `Photovoltaik Großhandel` | `PV Importeur` |
| 荷兰语 | `zonnepanelen groothandel` | `zonnepanelen importeur` |
| 法语 | `grossiste panneaux solaires` | `importateur panneaux solaires` |
| 西语 | `mayorista paneles solares` | `importador paneles solares` |

## 生成策略

1. **优先本地语言**：找德国客户用德语关键词命中率更高（德国公司官网/Google 分类用德语）。
2. **产品线 × 客户类型 × 城市** 交叉组合，每次搜索聚焦一个城市/区域。
3. 一个搜索词抓完再换下一个，脚本逐个执行，避免高频触发反爬。
4. 同一城市可用多个客户类型词（distributor / wholesaler / importer）分别搜，结果合并去重。

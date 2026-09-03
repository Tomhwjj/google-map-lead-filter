# 品牌贴牌 / 代工映射

背调判断「官网是否销售我方品牌」时，**不能只搜品牌字面名**——很多中国品牌在海外有贴牌（OEM/ODM），硬件同源但品牌名不同。搜错品牌名会导致永远 0 命中（实测：南非市场搜 "Deye" 0 命中，搜 "Sunsynk" 命中 4 家）。

## 光伏 / 储能逆变器（案例：Deye 德业）

| 我方品牌 | 海外贴牌 | 主要市场 |
|---------|---------|---------|
| Deye（宁波德业） | **Sunsynk** | 南非 |
| Deye | **Sol-Ark** | 北美（非目标市场） |
| Deye | **INGE** | 南非 |
| Deye | **Fusion** | 南非（Solaradvice 卖） |
| Deye | **OHm** | 南非 |
| Deye | **Noark** | 澳大利亚（正泰 Chint 旗下） |

> Sunsynk 逆变器由 Ningbo Deye Inverter Technology 代工，硬件与 Deye 一致，仅固件 / 监控 App 不同。Sunsynk 主要在英国、南非销售。**欧盟市场 Deye 以本牌为主**，贴牌在欧盟命中少属正常。

## 用法

背调时 `--brands` 传入我方品牌 + 所有贴牌品牌：

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark"
```

命中任何一个贴牌品牌，都视为「销售我方产品」（产品匹配度给满分）。

## 如何发现贴牌关系

- WebSearch 搜「我方品牌 + rebrand / OEM / same manufacturer / vs 贴牌名」
- 观察同源产品在不同市场的品牌名（Deye ↔ Sunsynk ↔ Sol-Ark ↔ INGE）

> ⚠️ 命中贴牌品牌时，开发理由里要写清楚「Sunsynk = Deye 贴牌，同源产品」，避免自己误判为竞品。

## ⚠️ 品牌关键词匹配必须用词边界（血泪教训）

短品牌名做 substring 匹配会灾难性误命中本地语言普通词（2026-09 德国 ENF 50 家实测）：

| 品牌 | substring 误命中 |
|------|-----------------|
| INGE | 德语 `springen`(跳转，每个网站导航都有)、`Ingenieur`(工程师)、`Dinge`(东西) → 23/50 假命中 |
| Fusion | `FusionSolar`(华为产品线) → 假命中 |
| OHm | 电阻单位 `Ohm`(电气官网常见) → 潜在误命中 |

**规则**：`backfill.py` 的 `find_brands` 和 `score_leads.py` 的 `sells_deye` 必须用词边界正则 `(?<![a-z0-9])品牌名(?![a-z0-9])`，**禁止 `in` substring 匹配**。已修，勿回退。

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

## 竞品品牌清单（增量口径，判「卖同类竞品储能/逆变器」）

「产品匹配 30 分」除了存量（卖 Deye/贴牌=30），还有**增量**（卖同类竞品储能逆变器=24，可替换）。要触发增量档，`--brands` 必须**同时传竞品品牌**——否则 `brands_found` 要么命中 Deye、要么空，竞品增量 24 永远触发不了（2026-09 德国 50 家实测：13 家大型批发商卖华为/阳光/固德威，但没传竞品 → 产品匹配全 0 分）。

| 竞品品牌 | 厂商 | 品类 |
|---------|------|------|
| Huawei | 华为 | 储能逆变器 |
| Sungrow | 阳光电源 | 储能逆变器 |
| GoodWe | 固德威 | 储能逆变器 |
| Fronius | 奥地利 | 逆变器 |
| SMA | 德国 SMA Solar | 逆变器 |
| Solax | 首航新能源 | 储能逆变器 |
| Sofar | 首航 Solar | 储能逆变器 |
| Growatt | 古瑞瓦特 | 储能逆变器 |
| Kostal | 德国 | 逆变器 |
| SolarEdge | 以色列 | 逆变器 |
| Enphase | 美国 | 微逆 |
| Hoymiles | 禾迈 | 微逆 |
| FoxESS | 麦田能源 | 储能 |
| Solis | 锦浪 | 逆变器 |

> ⚠️ **只含储能逆变器/微逆/储能竞品，不含组件品牌**（Jinko / Longi / Trina / JA Solar 是组件品类，命中组件 ≠ 可替换储能逆变器，会误加分）。
>
> ⚠️ **电池竞品暂缓**（BYD / LG / Tesla / Sonnen / Varta）：Sonnen 是德语「太阳」词根（sonnenenergie 官网常见）会词边界误命中，短词 BYD/LG 也易误命中。等有实测需要再加。

## 用法

背调时 `--brands` 传入**三组品牌**：我方品牌 + 贴牌 + 竞品品牌：

```bash
python scripts/backfill.py leads.csv --out backfill.json \
  --brands "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark,Huawei,Sungrow,GoodWe,Fronius,SMA,Solax,Sofar,Growatt,Kostal,SolarEdge,Enphase,Hoymiles,FoxESS,Solis"
```

- 命中**贴牌品牌** → 视为「销售我方产品」（存量，产品匹配 30）
- 命中**竞品品牌** → 「卖同类竞品储能/逆变器」（增量，产品匹配 24，可替换）
- 都没命中 → 不相关（0）

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

# 品牌贴牌 / 代工映射

背调判断「官网是否销售我方品牌」时，**不能只搜品牌字面名**——很多中国品牌在海外有贴牌（OEM/ODM），硬件同源但品牌名不同。搜错品牌名会导致永远 0 命中（实测：英国光伏批发商搜 "Deye" 0 命中，搜 "Sunsynk" 命中 4 家）。

## 光伏 / 储能逆变器（案例：Deye 德业）

| 我方品牌 | 海外贴牌 | 主要市场 |
|---------|---------|---------|
| Deye（宁波德业） | **Sunsynk** | 英国、南非 |
| Deye | **Sol-Ark** | 北美 |
| Deye | **INGE** | 南非等 |

> Sunsynk 逆变器由 Ningbo Deye Inverter Technology 代工，硬件与 Deye 一致，仅固件 / 监控 App 不同。英国市场主要用 Sunsynk 品牌名销售。

## 用法

背调时 `--brands` 传入我方品牌 + 所有贴牌品牌：

```bash
python scripts/backfill.py leads.csv --out backfill.json --brands "Deye,Sunsynk,Sol-Ark,INGE"
```

命中任何一个贴牌品牌，都视为「销售我方产品」（产品匹配度给满分）。

## 如何发现贴牌关系

- WebSearch 搜「我方品牌 + rebrand / OEM / same manufacturer / vs 贴牌名」
- 观察同源产品在不同市场的品牌名（Deye ↔ Sunsynk ↔ Sol-Ark ↔ INGE）

> ⚠️ 命中贴牌品牌时，开发理由里要写清楚「Sunsynk = Deye 贴牌，同源产品」，避免自己误判为竞品。

# Deye 生态背调（光伏产业认知底座）

> 本文件是 skill 的**行业认知底座**。改评分、改搜索词、改背调逻辑之前，先读这里，避免力气用错方向。
>
> **教训来源**：早期把 Deye 误判成「并网逆变器后进者」，按「组件」品类（solar panel）抓取，导致背调命中 Deye 极少、白费力气。根因是**没先摸清行业生态和 Deye 这家公司**。2026-09 核实后固化本文件。

## 一、Deye 公司基本盘（宁波德业 / 德业股份 605117.SH）

- **主营**：户用储能逆变器 + 储能电池包（占营收约 71%），另有除湿机等家电
- **营收**：2024 年 112 亿 → 2025 年 122 亿
- **海外占比**：2023 58% → 2024 70.9% → 2025 79.7% → 2026 前4月 87.6%
- **欧洲是最大市场**：占营收约 78%（欧洲营收约 62 亿），2026 前4月欧洲占比约 45.3%
- **行业地位**：
  - 全球第一大户用储能逆变器厂商（2024 市占 24.4%，2025 年 20.6% 仍第一）
  - 工商业储能逆变器全球第二（20.4%）
  - 欧洲 hybrid 逆变器榜单 2025 上半年第一，9 月被华为反超至第二

## 二、Deye 的生态位：储能逆变器，不是组件（关键认知）

光伏产业三条产品线，Deye 只在「储能逆变器 + 电池」这一条：

| 产品线 | 头部玩家 | 说明 |
|--------|---------|------|
| 组件（solar panel） | LONGi / JA / Trina / Jinko / Qcells / Tongwei | 搜 solar panel 捞到的全是这条 |
| 并网逆变器（string/on-grid） | Huawei / SMA / Sungrow / SolarEdge / Fronius | Deye 不在此列 |
| **储能逆变器 + 电池（hybrid + battery）** | **Deye（龙头）/ Huawei / BYD / GoodWe** | **Deye 主战场** |

> ⚠️ **核心教训**：Deye 的经销商是「储能/电池/混合逆变器/户用光伏」这一拨人，不是「组件经销商」。
> 搜 `solar panel` 是「用组件的网捞储能的鱼」，天然错位。
> → 搜索词必须走储能品类词（`battery storage` / `hybrid inverter` / 德语 `Speicher` 等），见 `search-keywords.md`。

## 三、贴牌 / OEM 关系（全表，已核实）

Deye 是 OEM，多家海外品牌是它的贴牌。背调 `--brands` **必须带全**，漏一个漏一片：

| 贴牌 | 关系 | 主要市场 |
|------|------|---------|
| Sunsynk | Deye OEM，硬件同源，加定制固件/UI，贵 15-25% | 南非 |
| Sol-Ark | Deye OEM，美国独家合作伙伴（2018 起） | 美国（非目标市场） |
| INGE | Deye 制造、INGE 品牌 | 南非 |
| Fusion | Deye OEM（Solaradvice 卖） | 南非 |
| OHm | Deye OEM | 南非 |
| Noark | Deye OEM（正泰 Chint 旗下） | 澳大利亚 |

> ⚠️ **欧盟市场 Deye 以本牌为主**，贴牌（Sunsynk/Sol-Ark/INGE/Fusion/OHm/Noark）主要分布在南非、澳大利亚、北美等非欧盟市场。欧盟背调重点搜 "Deye"，贴牌命中少属正常（用户实测已印证）。

## 四、市场边界（铁律）

- **只做欧洲：欧盟 27 国 + 乌克兰**。英国不在范围（已脱欧）；瑞士/挪威/塞尔维亚等非欧盟也不做。
- 乌克兰：候选国未入盟，但**用户指定追加**（战后储能/重建需求大），纳入目标市场。
- 美国（Sol-Ark 市场）不做——Sol-Ark 贴牌仅用于识别「同源产品」，不作为目标市场。
- 英国、瑞士、挪威、塞尔维亚、南非、北美等一律不挖。

## 五、光伏产业生态版图

- **上游**：组件（LONGi/JA/Trina/Jinko/Qcells/Tongwei）
- **中游**：逆变器分两拨——并网（Huawei/SMA/Sungrow）和储能 hybrid（Deye 龙头 + Huawei + GoodWe）
- **下游渠道**：批发商/分销商 → 安装商/EPC → 终端户用/工商业业主

Deye 卡在「储能 hybrid 逆变器 + 电池」位，其欧洲渠道是**储能导向的批发商 + 户用安装商**，与组件经销商重叠但不完全重合。

## 六、Deye 欧盟经销商（种子线索，已深挖 2026-09）

> 这些是「已在卖 Deye」的存量线索，产品匹配直接满分，是最高价值种子。持续补充。

### 德国
- KAEFER Batteriesysteme GmbH（Brilon）— Deye 德国分销伙伴
- Greenlimon Technologies GmbH（Sinzig）— Deye 黄金分销商
- Everwind GmbH（Düsseldorf）— 批发商，Deye hybrid + 电池

### 法国
- Solu'Sun — Deye 法国分销/批发商
- H2O Energy — Deye 官方经销商（安装 + SAV）
- Yunfan（yfpowerzone）— Deye 欧洲授权分销

### 荷兰
- E2C BV — Deye「Golden authorized distributor」（荷兰/比利时/乌克兰）
- Uni Z International B.V.（海牙）— 分销商，代理 Deye/Longi/Huawei
- 7SUN — 荷兰 PV 系统分销商

### 西班牙
- Eastech Electric SAU — Deye 西班牙官方进口分销商
- Wccsolar / PlusEnergy Solar（塞维利亚）— Deye 官方独家分销（VIP/黄金伙伴）
- DSPSOLAR（Asturias）— 官方分销 + 认证售后
- REBACAS SLU（Castellón）— Deye 分销

### 欧洲级（跨国）
- Menlo Electric — 欧盟最大 Deye 分销商之一（多国仓库）
- SOLARKIT（匈牙利）— Deye 铂金分销，10+ 欧洲国家
- Z-ECOENERGY（波兰）— 批发/进口，B2B 平台
- Solarity — 欧洲 PV 分销商，与 Deye 官方合作
- DeyeStore（deyestore.com）— Deye 官方欧洲在线店（德国 Sinzig 发货，覆盖 30 国）

## 来源

- [sun.store Battery Index Sep 2025](https://sun.store/blog/battery-index-september-2025/)
- [JA Solar, Deye i Huawei liderami rynku PV – PV Index](https://enerad.pl/ja-solar-deye-i-huawei-liderami-rynku-pv-sprawdz-najnowszy-pv-index/)
- [德业股份港交所再递表：海外营收近九成](https://finance.sina.cn/2026-07-31/detail-iniksxpf4469144.d.html)
- [数字储能网：德业递表港交所](https://www.desn.com.cn/news/show-2153030.html)
- [Sunsynk vs Deye 2026](https://www.ourpower.co.za/solar/compare/sunsynk-vs-deye)
- [Sol-Ark response to Deye shutdown](https://diysolarforum.com/threads/sol-ark-response-to-reports-of-deye-inverters-shutting-down.94550/)
- [Is there a link between the Inge & Sunsynk?](https://powerforum.co.za/topic/5991-is-there-a-link-between-the-inge-sunsynk/)
- [Menlo Electric Deye EU distributor](https://www.pvtime.org/menlo-electric-becomes-one-of-the-largest-deye-inverters-distributor-in-eu/)
- [Eastech Electric Deye España](https://www.solarnews.es/2022/10/11/eastech-electric-sau-importador-distribuidor-oficial-de-deye-inverter-en-espana/)
- [Deye Distributor Directory](https://deyeinverters.net/)
- [Noark inverters are rebadged Deye units](https://www.solarquotes.com.au/inverters/noark-review.html)
- [Deye rebrand variants: INGE, Sunsynk, OHm, Sol-Ark](https://powerforum.co.za/topic/5991-is-there-a-link-between-the-inge-sunsynk/)
- [Fusion is a rebranded Deye sold by Solaradvice](https://powerforum.co.za/topic/9244-sunsynk-who-is-the-oem/)

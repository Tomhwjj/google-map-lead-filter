# 搜索关键词生成规则

根据用户输入（产品类目、目标国家/城市、客户类型），生成 Google Maps / 搜索 API 关键词组合。

## ⚠️ 品类铁律：储能优先，不是组件

Deye 是**储能逆变器 + 电池**玩家（户用储能逆变器全球第一，见 `deye-ecosystem.md`），不是组件厂。它的经销商是「储能/电池/混合逆变器/户用光伏」这一拨人，与组件经销商**不重叠**。

> **搜 `solar panel`（组件）是「用组件的网捞储能的鱼」，天然错位**——早期按组件抓取导致 Deye 命中极少、白费力气，这是固化本铁律的原因。

→ 默认品类词走**储能**；组件词只在「增量扩品类」时用，不做主力。

## 存量 vs 增量（先想清楚再搜）

| 口径 | 搜什么 | 命中 | 价值 |
|------|--------|------|------|
| **存量** | `Deye` + 贴牌 + 储能品类词 | 已在卖 Deye 的经销商 | 产品匹配满分，最高优先 |
| **增量** | 竞品储能品牌 + 不限品牌储能词 | 卖同品类但没卖 Deye | 现成渠道，pitch 引 Deye 替换 |

**默认两条都搜**：Deye 是评分加分项、不是抓取筛选标准，抓取按品类广撒网。品牌词的详细模板见 `multi-source.md`（存量 `Deye distributor FR` / 增量 `Sungrow distributor DE` / 不限品牌 `Speicher Großhändler`），本文件只管品类词。

## 产品线拆分（储能优先）

| 产品线 | 英文 | 德语 | 法语 | 荷兰语 | 西语 | 定位 |
|--------|------|------|------|--------|------|------|
| **储能逆变器** | hybrid inverter / storage inverter | Hybridwechselrichter / Wechselrichter | onduleur hybride | hybride omvormer | inversor híbrido | **Deye 核心，主力搜** |
| **储能电池** | battery storage / home battery / energy storage | Speicher / Batteriespeicher / Heimspeicher | stockage batterie / batterie domestique | thuisbatterij / batterij opslag | almacenamiento batería / batería solar | **Deye 核心，主力搜** |
| 并网逆变器 | string inverter / on-grid inverter | Wechselrichter | onduleur | omvormer | inversor | 竞品增量（Huawei/SMA/Sungrow） |
| 组件 | solar panel / PV module | Photovoltaik / Solarmodul | panneau solaire | zonnepaneel | panel solar | 仅增量扩品类（LONGi/JA/Trina） |

> 主搜前两行（储能逆变器 + 储能电池）；并网逆变器、组件只在「增量」时用——卖这些的经销商可以 pitch 引 Deye 储能线补产品。

## 客户类型词（按目标客户类型选）

| 中文 | 英文 | 德语 | 荷兰语 | 法语 | 西语 |
|------|------|------|--------|------|------|
| 经销商 | distributor | Distributor / Händler | distributeur | distributeur | distribuidor |
| 批发商 | wholesaler | Großhändler | groothandel | grossiste | mayorista |
| 进口商 | importer | Importeur | importeur | importateur | importador |
| 安装商 | installer | Installateur | installateur | installateur | instalador |

> 安装商是 Google Maps 长尾主力（一个城市几十家），头部批发商/进口商靠搜索 API 挖（见 `multi-source.md`）。

## 本地语言关键词（欧盟 27 国 + 乌克兰全覆盖，储能导向）

> v2（2026-09-12）：从 4 语言扩到全市场覆盖——此前缺波兰语等小语种列，跑 PL 只能临时凑 3 个窄词，
> 是波兰只挖到 327 家（实际千级）的根因之一。**可执行版本在 `runner.py` 的 `COUNTRY_KEYWORDS`**
> （获客流水线直接消费），本表是同源的规则文档；两处改动须同步。
> 词形组合 = 品类（储能/电池/混合逆变器）× 角色（批发/经销/安装）。

| 语言 (locale) | 覆盖国家 | 储能批发商词 | 储能经销商/安装商词 |
|------|------|-----------|-----------|
| 德语 (de-DE) | DE、AT | `Speicher Großhändler` / `Batteriespeicher Großhandel` | `Hybridwechselrichter Distributor` / `Photovoltaik Speicher Installateur` |
| 法语 (fr-FR) | FR、LU | `grossiste stockage batterie` / `distributeur onduleur hybride` | `installateur batterie solaire` |
| 荷兰语 (nl-NL) | NL、BE | `thuisbatterij groothandel` / `batterij opslag distributeur` | `thuisbatterij installateur` |
| 西语 (es-ES) | ES | `mayorista almacenamiento batería` / `distribuidor inversor híbrido` | `instalador batería solar` |
| **波兰语 (pl-PL)** | PL | `hurtownik magazynów energii` / `dystrybutor magazynu energii` | `instalator magazynów energii` / `fotowoltaika hurtownia` |
| 意语 (it-IT) | IT | `grossista sistemi di accumulo` / `distributore inverter ibrido` | `installatore batteria solare` |
| 葡语 (pt-PT) | PT | `grossista armazenamento bateria` | `distribuidor inversor híbrido` / `instalador bateria solar` |
| 瑞典语 (sv-SE) | SE | `batterilager grossist` | `solcellsbatteri distributör` / `solcellsinstallatör` |
| 丹麦语 (da-DK) | DK | `batterilager grossist` | `solcellebatteri distributør` / `solcelleinstallatør` |
| 芬兰语 (fi-FI) | FI | `energiavarasto tukku` | `hybridivaihtosuuntaaja jälleenmyyjä` / `aurinkopaneeli akku asentaja` |
| 英语 (en) | IE、MT | `battery storage wholesaler` | `hybrid inverter distributor` / `solar battery installer` |
| 捷克语 (cs-CZ) | CZ | `velkoobchod bateriová úložiště` | `distributor hybridních střídačů` / `instalace fotovoltaiky s baterií` |
| 匈牙利语 (hu-HU) | HU | `energiatároló nagykereskedő` | `hibrid inverter forgalmazó` / `napelem akkumulátor telepítő` |
| 罗马尼亚语 (ro-RO) | RO | `angrosist sisteme de stocare` | `distribuitor invertor hibrid` / `instalator panouri solare baterii` |
| 斯洛伐克语 (sk-SK) | SK | `veľkoobchod batériové úložiská` | `distributor hybridných meničov` / `inštalácia fotovoltaiky s batériou` |
| 斯洛文尼亚语 (sl-SI) | SI | `trgovina na veliko baterijska skladišča` | `distributer hibridnih pretvornikov` / `namestitev fotovoltaike z baterijo` |
| 克罗地亚语 (hr-HR) | HR | `veleprodaja baterijskih sustava` | `distributer hibridnih invertera` / `instalater solarnih sustava` |
| 希腊语 (el-GR) | GR、CY | `χονδρικό εμπόριο συστημάτων αποθήκευσης` | `διανομέας υβριδικών αντιστροφέων` / `εγκαταστάτης φωτοβολταϊκών` |
| 保加利亚语 (bg-BG) | BG | `едро на акумулаторни системи` | `дистрибутор на хибридни инвертори` / `монтажник на соларни батерии` |
| 立陶宛语 (lt-LT) | LT | `didmeninė prekyba energijos kaupikliais` | `platintojas hibridiniai keitikliai` / `montuotojas saulės baterijų` |
| 拉脱维亚语 (lv-LV) | LV | `vairumtirdzniecība enerģijas uzglabāšana` | `izplatītājs hibrīdie invertori` / `uzstādītājs saules bateriju` |
| 爱沙尼亚语 (et-EE) | EE | `hulgimüük akudesüsteemid` | `edasimüüja hübriidinverterid` / `paigaldaja päikesepaneelide akud` |
| 乌克兰语 (uk-UA) | UA | `оптовий продавець накопичувачів енергії` | `дистриб'ютор гібридних інверторів` / `монтажник сонячних станцій` |

> 旧表（仅德/荷/法/西）见 git 历史。新增语言翻译系模型生成，**主要市场（PL/IT/RO/CZ/GR/UA）背调时建议人工抽查校准一次**；小市场（MT/CY/LU/IE）英语覆盖度高，本地语可缓。

## 生成策略

1. **储能词优先**：主力组合 = 储能逆变器/储能电池 × 客户类型 × 城市，组件词只做增量补充。
2. **优先本地语言**：德国客户用德语（`Speicher` 比 `battery storage` 命中率高），本地公司官网/Google 分类用本地语。
3. **存量+增量双线**：存量线带 Deye/贴牌品牌词，增量线带竞品品牌 + 不限品牌储能词（品牌模板见 `multi-source.md`）。
4. **产品线 × 客户类型 × 城市** 交叉组合，每次搜索聚焦一个城市/区域。
5. 一个搜索词抓完再换下一个，脚本逐个执行，避免高频触发反爬。
6. 同一城市可用多个客户类型词（distributor / wholesaler / importer / installer）分别搜，结果合并去重。

# google-map-lead-filter 开发交接

> 换窗口迭代前先读这里（一分钟）。**只记「进度 / 决策 / 待办」，规则和代码一律指向 SKILL.md / references / scripts，不在这里重复**。
> 每天改完：在「改动记录」加一条（标时间戳），更新「明日待办」。

## 明日待办（最近优先，做完划掉）

- [ ] **客户跟踪池子**（获客途径完善）：加按钮把客户分池跟踪——潜在客户（未联系 → 已联系）、强意向客户、老客户；本地保存 + 检索（按电话 / 企业名）。
  - 存储方案**待定**：SQLite（`fetch_enf.py` 已用 SQLite WAL，可复用）vs markdown vs csv，明日先讨论定。
- [ ] **规模判断落地**：完整版 backfill（不加 --fast）跑 50 家 DE seller 后，读 body 判规模看能拿几成确定档位；拿不到的 anysearch / WebSearch 兜底（分工见 `references/qualification-rules.md` 兜底表）。

## 改动记录（按日倒序）

- **2026-09-04**
  - 补背调兜底流程：品牌/规模证据缺失 → anysearch（批量）/ WebSearch（零星）；官网抓不动 → kitesurf（`references/qualification-rules.md` 兜底表 + SKILL.md 第六步）
  - 改名 NEXT.md → HANDOFF.md，定位「跨窗口迭代衔接 + 明日待办」，砍掉与 references 重复的内容
- **2026-09-03**
  - 提取 ENF Solar 抓取脚本 `scripts/fetch_enf.py`（源自 pv-company-scraper）：seller→distributor 映射、邮箱 JS 解码、429 退避、只抓欧盟 27 国 + 乌克兰
  - 修品牌关键词 substring 误命中（INGE→springen/Ingenieur、Fusion→FusionSolar、OHm→电阻）：backfill.py + score_leads.py 改词边界匹配，教训见 `references/brand-mapping.md`
  - 市场边界确认：欧盟 27 国 + 乌克兰（乌克兰是候选国，非欧盟成员）

## 开发注意（铁律，别删）

- 改代码在 `D:/Agent/git/google-map-lead-filter/`（独立 git + 远程），改完 commit + push。
- **改完同步 skills 目录**，否则新窗口触发 skill 跑旧版：
  ```bash
  SRC=/d/Agent/git/google-map-lead-filter
  for DST in ~/.agents/skills/google-map-lead-filter ~/.claude/skills/google-map-lead-filter; do
    rm -rf "$DST"; mkdir -p "$DST"
    cp -r "$SRC/SKILL.md" "$SRC/references" "$SRC/scripts" "$SRC/templates" "$SRC/HANDOFF.md" "$DST/"
  done
  ```
- 数据文件（`D:/Agent/tmp/*.json`）是会话产物，不入 git。
- 口径唯一来源 `references/qualification-rules.md`，SKILL.md 不重复数字。
- **技术债**：`render_report.py` 的 `wa_link()` 硬编码法国 +33，扩展德国(+49)/荷兰(+31)/西班牙(+34)前要按 `country` 映射。

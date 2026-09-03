# google-map-lead-filter 开发交接

> 换窗口开发前先读这里。这是项目的"接下来做什么"，git 管理，随仓库走。

## 当前状态（截至 2026-09-03）

- **评分改四维**（产品匹配/渠道/规模/触达）：砍掉活跃度、开发难度、教育档，规模改「经营痕迹三档」，Deye 是评分加分项不是筛选标准（见 `references/qualification-rules.md`）
- **Deye 生态已固化**（`references/deye-ecosystem.md`）：公司基本盘、储能生态位、贴牌全表、欧盟市场边界、欧盟经销商种子名单
- **市场边界**：只做欧盟（不含英国），已写入 SKILL.md / multi-source.md / deye-ecosystem.md
- **双模式评分 + UI 报告规格**（8 要素）已实现：`score_leads.py` / `render_report.py`（⚠️ 代码仍硬编码旧五维权重，待同步）
- 法国 334 条线索已跑通（数据在 `D:/Agent/tmp/leads_fr_*.json`，**不入 git**）

## 接下来可做（按优先级）

1. **代码层同步评分四维**（最重要）：`score_leads.py` / `render_report.py` 仍硬编码旧五维权重（含活跃/开发难度），必须改成与 `qualification-rules.md` 新四维一致。
2. **搜索词转储能**（方向已定，待深挖完统一改 `search-keywords.md`）：品类词从 solar panel 转向 battery storage / hybrid inverter / Speicher 等储能词。
3. **欧盟经销商种子线索落地**：`deye-ecosystem.md` 第六节已列德国/法国/荷兰/西班牙 + 欧洲级的 Deye 官方分销商，可先对这批跑 backfill 补邮箱/电话，作为第一批 A 级存量线索。
4. **扩展到其他欧盟国家**：目前只做了法国，可跑德国/荷兰/西班牙/意大利等（英国不做）。

## 开发注意（重要）

- **改代码在开发目录** `D:/Agent/git/google-map-lead-filter/`（有独立 git + 远程），改完 `git commit` + `git push`。
- **改完要同步到 skills 目录**，否则新对话窗口触发 skill 会跑旧版：
  ```bash
  SRC=/d/Agent/git/google-map-lead-filter
  for DST in ~/.agents/skills/google-map-lead-filter ~/.claude/skills/google-map-lead-filter; do
    rm -rf "$DST"; mkdir -p "$DST"
    cp -r "$SRC/SKILL.md" "$SRC/references" "$SRC/scripts" "$SRC/templates" "$DST/"
  done
  ```
- **数据文件**（`D:/Agent/tmp/leads_fr_*.json`）是会话产物，不入 git，新窗口跑会重新生成。
- 评分/UI/三态口径**唯一来源是 `references/qualification-rules.md`**，SKILL.md 不重复具体数字；改口径时改 rules + 代码（`score_leads.py` / `render_report.py`）两处即可。

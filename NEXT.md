# google-map-lead-filter 开发交接

> 换窗口开发前先读这里。这是项目的"接下来做什么"，git 管理，随仓库走。

## 当前状态（截至 2026-09-03）

- **双模式评分**（头部/长尾）已实现：`scripts/score_leads.py`
- **UI 报告规格**（8 要素：双模式切换/图例/每卡依据/Deye标志/触达排序/CSV导出/A-B-C筛选/统计栏）已实现：`scripts/render_report.py`
- **三态防幻觉**（规模/活跃：证据 > 估 > 未确认中性分5）已实现并写进 SKILL.md
- 法国 334 条线索已跑通（数据在 `D:/Agent/tmp/leads_fr_*.json`，**不入 git**）
- 最新提交：`6a856bb 固化UI报告规格到SKILL.md + 规模/活跃三态防幻觉`

## 接下来可做（按优先级）

1. **批量补背调**（最重要）：310 条 Google Maps 安装商的规模/活跃还是"未确认中性5"占位值。若要真按长尾模式铺客户，需对排前的候选补背调（`backfill.py` 抓官网 about/news/招聘），把 `employees`（员工数）/`active_signals`（活跃信号）硬证据填进 JSON，否则这两维分数是虚的。
2. **Facebook 链接补抓**：触达维度里 Facebook 占 4/5 分，但当前数据基本没有 `facebook` 字段，可补抓（之前用户说"先不做，后续补"）。
3. **A 级 wholesaler 联系方式补全**：ESTG / SINES 等 12 家搜索 API 挖出的批发商，`email`/`phone` 还是空（reason 里标了"联系方式待官网补"）。
4. **扩展到其他国家**：目前只做了法国。SKILL.md 流程通用，可跑德国/英国/荷兰等。

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
- 评分/UI/三态口径见 `SKILL.md` 第七、八步 + `references/qualification-rules.md`，改口径时三处（代码 + SKILL.md + rules）要同步对齐。

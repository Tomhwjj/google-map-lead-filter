#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
市场洞察复盘报告生成器：把某次市调任务沉淀成可复刻的 markdown 报告。

对应架构文档第一节《市场趋势洞察（市调模块）》强制字段：
  市调任务 ID / 起止时间戳 / 覆盖国家 / 各国热度得分 / 利好利空摘要 /
  风险点清单 / 信息来源快照 / 执行人 / 缓存有效期（7 天）

热度得分由 Agent 深度全网研判后录入，本脚本只做结构化渲染 + 排序。
用法:
    python render_research_report.py --mr-id <id>                 # 默认 reports/<id>/市场洞察复盘报告.md
    python render_research_report.py --mr-id <id> --out path.md
"""
import argparse
import os
import sys

from core import get_research
from db import DEFAULT_DB, get_conn

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fmt_duration(sec):
    if sec is None:
        return "（未结束）"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分{s}秒"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _lines(v):
    """把换行分隔的多行文本拆成列表（去空）。"""
    return [x.strip() for x in (v or "").splitlines() if x.strip()]


def render_md(data):
    task = data["task"]
    scores = data["scores"]
    countries = task.get("countries_list") or []

    lines = []
    lines.append("# 市场洞察复盘报告\n")
    lines.append("> 由 `render_research_report.py` 自动生成，作后续获客与国家优先级决策依据。\n")

    lines.append("## 一、市调任务基本信息\n")
    lines.append(f"- **市调任务 ID**：`{task['mr_id']}`")
    lines.append(f"- **执行人**：{task.get('executor') or '（未记录）'}")
    lines.append(f"- **覆盖国家**：{'、'.join(countries) if countries else '（未指定）'}")
    lines.append(f"- **开始时间**：{task.get('started_at') or '—'}")
    lines.append(f"- **结束时间**：{task.get('finished_at') or '（进行中）'}")
    lines.append(f"- **运行时长**：{fmt_duration(task.get('duration_sec'))}")
    expired = "⚠️ 已过期（建议重搜）" if task.get("expired") else "有效"
    lines.append(f"- **缓存有效期**：{task.get('cache_expires_at') or '—'}（{expired}）\n")

    lines.append("## 二、各国热度得分（0–100，降序）\n")
    if scores:
        lines.append("| 排名 | 国家 | 热度得分 | 核心利好 | 核心利空 |")
        lines.append("|---|---|---|---|---|")
        for i, s in enumerate(scores, 1):
            lines.append(
                f"| {i} | {s['country']} | **{s['score']}** | "
                f"{s.get('positives') or '—'} | {s.get('negatives') or '—'} |")
    else:
        lines.append("（尚未录入任何国家的热度研判）")
    lines.append("")

    lines.append("## 三、各国利好 / 利空摘要\n")
    if scores:
        for s in scores:
            lines.append(f"### {s['country']}（{s['score']} 分）\n")
            lines.append(f"**利好**：{s.get('positives') or '（未记录）'}\n")
            lines.append(f"**利空**：{s.get('negatives') or '（未记录）'}\n")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 四、风险点清单\n")
    risk_lines = []
    for s in scores:
        for r in _lines(s.get("risks")):
            risk_lines.append(f"- [{s['country']}] {r}")
    if risk_lines:
        lines.extend(risk_lines)
    else:
        lines.append("（未记录）")
    lines.append("")

    lines.append("## 五、信息来源快照\n")
    src_lines = []
    for s in scores:
        for src in _lines(s.get("sources")):
            src_lines.append(f"- [{s['country']}] {src}")
    if src_lines:
        lines.extend(src_lines)
    else:
        lines.append("（未记录）")
    lines.append("")

    lines.append("## 六、获客与国家优先级建议\n")
    lines.append("> 待补充：按热度从高到低建议获客国家顺序、重点突破的国家/城市、风险规避提示。\n")

    return "\n".join(lines)


def render_report(mr_id, out=None, db_path=None):
    """生成市场洞察复盘报告 md，返回 (md 文本, 输出路径)。"""
    data = get_research(mr_id, db_path)
    md = render_md(data)

    if out is None:
        out = os.path.join(PROJECT_ROOT, "reports", mr_id, "市场洞察复盘报告.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    conn = get_conn(db_path)
    conn.execute("UPDATE market_tasks SET report_path=? WHERE mr_id=?", (out, mr_id))
    conn.commit()
    conn.close()
    return md, out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成市场洞察复盘报告 md")
    ap.add_argument("--mr-id", required=True, help="市调任务 ID")
    ap.add_argument("--out", default=None, help="输出路径（默认 reports/<mr_id>/市场洞察复盘报告.md）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    args = ap.parse_args()

    md, out = render_report(args.mr_id, args.out, args.db)
    print(f"市场洞察复盘报告已生成: {out}")
    print(md)


if __name__ == "__main__":
    main()

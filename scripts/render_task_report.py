#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
复刻报告生成器：把某次获客任务沉淀成可复刻的 markdown 报告。

对应架构文档第三节《本次获客复刻报告》固定结构：
  任务基本信息 / 搜索策略与关键词快照 / 数据统计 / 新增企业清单 /
  差异待核验清单 / 背调问题清单 / 本次获客结论 / 下一轮优化方向 / 原始数据快照链接

自动填充：任务信息、关键词/数据源快照、new/dup/diff 统计、新增与差异清单。
需 Claude 补充：背调问题清单、获客结论、下一轮优化方向（生成后留占位）。

用法:
    python render_task_report.py --task-id <id>                 # 输出到默认 reports/<id>/复刻报告.md
    python render_task_report.py --task-id <id> --out path.md   # 指定路径
"""
import argparse
import json
import os
import sys

from core import build_report
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


def _parse_list(v):
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except Exception:
        return []


def render_md(data):
    task = data["task"]
    stats = data["stats"]
    diffs = data["diffs"]
    new_companies = data["new_companies"]
    keywords = _parse_list(task.get("keywords"))
    sources = _parse_list(task.get("sources"))
    total = stats["new"] + stats["dup"] + stats["diff"]

    lines = []
    lines.append("# 本次获客复刻报告\n")
    lines.append("> 由 `render_task_report.py` 自动生成，可作下次 AI 迭代的上下文（跨窗口/跨重启无缝接续）。\n")

    lines.append("## 一、任务基本信息\n")
    lines.append(f"- **任务 ID**：`{task['task_id']}`")
    lines.append(f"- **目标国家**：{task.get('country') or '（未指定）'}")
    lines.append(f"- **开始时间**：{task.get('started_at') or '—'}")
    lines.append(f"- **结束时间**：{task.get('finished_at') or '（进行中）'}")
    lines.append(f"- **运行时长**：{fmt_duration(task.get('duration_sec'))}\n")

    lines.append("## 二、搜索策略与关键词快照\n")
    if keywords:
        lines.append("**关键词**：")
        for k in keywords:
            lines.append(f"- {k}")
    else:
        lines.append("**关键词**：（未记录）")
    lines.append("")
    lines.append("**数据源**：" + ("、".join(sources) if sources else "（未记录）") + "\n")

    lines.append("## 三、数据统计\n")
    lines.append("| 指标 | 数量 |")
    lines.append("|---|---|")
    lines.append(f"| 本次筛查企业总数 | {total} |")
    lines.append(f"| 新增入库 | {stats['new']} |")
    lines.append(f"| 存量重复无变化 | {stats['dup']} |")
    lines.append(f"| 信息差异·待核验 | {stats['diff']} |\n")

    lines.append("## 四、新增企业清单\n")
    if new_companies:
        lines.append("| 企业 | 国家/城市 | 渠道 | 电话 | 邮箱 | 官网 | 头部档 | 长尾档 | 卖Deye |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for c in new_companies:
            deye = "✓" if c.get("sells_deye") else ""
            lines.append(
                f"| {c.get('company_name') or ''} | {c.get('country') or ''}/{c.get('city') or ''} "
                f"| {c.get('customer_type') or ''} | {c.get('phone') or ''} | {c.get('email') or ''} "
                f"| {c.get('website') or ''} | {c.get('grade') or ''} | {c.get('grade_lt') or ''} | {deye} |")
    else:
        lines.append("（本次无新增）")
    lines.append("")

    lines.append("## 五、差异待核验清单\n")
    if diffs:
        lines.append("| 企业 | 字段 | 旧值 | 新值 | 状态 |")
        lines.append("|---|---|---|---|---|")
        for d in diffs:
            lines.append(f"| {d.get('company_name') or d.get('main_id')} | {d['field']} "
                         f"| {d.get('old_value') or ''} | {d.get('new_value') or ''} | {d['status']} |")
    else:
        lines.append("（本次无差异）")
    lines.append("")

    lines.append("## 六、背调问题清单\n")
    issues = data.get("issues") or []
    if issues:
        lines.append("| 分类 | 问题 | 状态 | 解决方案 |")
        lines.append("|---|---|---|---|")
        for it in issues:
            cat = it.get("category") or ""
            title = it.get("title") or ""
            st = it.get("status") or ""
            sol = it.get("solution") or ""
            lines.append(f"| {cat} | {title} | {st} | {sol} |")
        for it in issues:
            if it.get("detail"):
                lines.append(f"\n- **{it.get('title') or ''}**：{it.get('detail')}")
    else:
        lines.append("（本次无记录问题）")
    lines.append("")

    lines.append("## 七、本次获客结论\n")
    lines.append("> 待补充。\n")

    lines.append("## 八、下一轮优先优化方向\n")
    lines.append("> 待补充。\n")

    lines.append("## 九、原始数据快照链接\n")
    lines.append(f"> 评分产物 / 原始 CSV 等快照路径，见任务 `{task['task_id']}` 的入库记录。\n")

    return "\n".join(lines)


def render_report(task_id, out=None, db_path=None):
    """生成复刻报告 md，返回 (md 文本, 输出路径)。"""
    data = build_report(task_id, db_path)
    md = render_md(data)

    if out is None:
        out = os.path.join(PROJECT_ROOT, "reports", task_id, "复刻报告.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)

    # 写回 tasks.report_path，便于 UI 跳转
    conn = get_conn(db_path)
    conn.execute("UPDATE tasks SET report_path=? WHERE task_id=?", (out, task_id))
    conn.commit()
    conn.close()
    return md, out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成获客复刻报告 md")
    ap.add_argument("--task-id", required=True, help="任务 ID")
    ap.add_argument("--out", default=None, help="输出路径（默认 reports/<task_id>/复刻报告.md）")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    args = ap.parse_args()

    md, out = render_report(args.task_id, args.out, args.db)
    print(f"复刻报告已生成: {out}")
    print(md)


if __name__ == "__main__":
    main()

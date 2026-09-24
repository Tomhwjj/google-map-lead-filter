#!/usr/bin/env python3
"""wb_data_health_check.py — 数据健康巡检（WorkBuddy 2026-09-21，只读）

背景：2026-09-21 确诊系统性失误——A 管线 ingest_backfill_wb.py 只写 email，
品牌/类目证据入库丢弃（backfilled=1 的 1273 家 94% brands_found 空），导致
57/100 家 Deye 存量卖家被压分至 58/73 基线，直至用户质疑才暴露（issue #13 前科 #14）。

本脚本对 leads.db 做只读巡检，任何一项超出阈值即报 ⚠。
用法: python scripts/wb_data_health_check.py [--db PATH]
退出码: 0=健康 1=有告警（便于自动化判断）
"""
import argparse
import sys

sys.path.insert(0, "scripts")
import db  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    conn = db.get_conn(args.db)
    conn.row_factory = None
    cur = conn.cursor()
    q = lambda s: cur.execute(s).fetchone()[0]

    warns = []
    total = q("SELECT COUNT(*) FROM companies")

    def pct(n):
        return round(n * 100 / total, 1) if total else 0

    # 1. 关键字段空值率（证据完整性）
    # 工单5（Claude 2026-09-22）：统一空值口径——NULL/空串/'[]'/'{}' 占位一律算空，
    # 否则 brands_context 这类「假填满」字段（87.4% 是 '{}'）永远不告警。
    # 并把 product_tier / scale_tier / maps_category / brands_context 纳入巡检。
    def empty_cond(col):
        return f"{col} IS NULL OR TRIM({col})='' OR TRIM({col})='[]' OR TRIM({col})='{{}}'"

    checks = [
        ("brands_found 空", empty_cond("brands_found"), 40),
        ("brands_context 空(含{}占位)", empty_cond("brands_context"), 40),
        ("customer_type 空", empty_cond("customer_type"), 60),
        ("product_tier 空", empty_cond("product_tier"), 80),
        ("scale_tier 空", empty_cond("scale_tier"), 80),
        ("maps_category 空", empty_cond("maps_category"), 60),
        ("email 空", "email IS NULL OR email=''", 35),
    ]
    backfilled = q("SELECT COUNT(*) FROM companies WHERE backfilled=1")
    for label, cond, thresh in checks:
        n = q(f"SELECT COUNT(*) FROM companies WHERE {cond}")
        p = pct(n)
        line = f"{label}: {n}/{total} ({p}%)"
        if label.startswith("brands") and backfilled:
            nb = q(f"SELECT COUNT(*) FROM companies WHERE backfilled=1 AND ({cond})")
            line += f" | backfilled=1 中: {nb}/{backfilled} ({round(nb*100/max(backfilled,1),1)}%)"
        if p > thresh:
            warns.append(line)
        print(("⚠ " if p > thresh else "  ") + line)

    # 2. Deye 存量卖家分数健康度（<80 视为疑似压分）
    deye = q("SELECT COUNT(*) FROM companies WHERE sells_deye=1")
    deye_low = q("SELECT COUNT(*) FROM companies WHERE sells_deye=1 AND score<80")
    print(f"  sells_deye=1: {deye} 家，其中 score<80: {deye_low} 家")
    if deye and deye_low * 100 / deye > 20:
        warns.append(f"Deye 存量卖家压分率 {round(deye_low*100/deye,1)}% (>20%)")

    # 3. score 分布（分布突变检测：同一模板分数扎堆）
    print("  score 分布:", cur.execute(
        "SELECT score, COUNT(*) FROM companies GROUP BY score ORDER BY COUNT(*) DESC LIMIT 5"
    ).fetchall())

    # 4. 同步队列卡死（pending 超过 7 天）
    pend = q("SELECT COUNT(*) FROM gmail_contacts WHERE status='pending' AND contact_resource_name IS NULL")
    print(f"  gmail_contacts 卡 pending: {pend}")
    if pend > 10:
        warns.append(f"同步队列卡 pending {pend} 条")

    # 5. 未关闭的问题单
    issues = cur.execute(
        "SELECT id, category, title FROM task_issues WHERE status='open' ORDER BY id"
    ).fetchall()
    print(f"  task_issues open: {len(issues)}")
    for r in issues:
        print(f"    #{r[0]} [{r[1]}] {r[2][:60]}")

    conn.close()
    print()
    if warns:
        print("⚠ 健康告警:")
        for w in warns:
            print("  -", w)
        return 1
    print("✓ 数据健康巡检通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

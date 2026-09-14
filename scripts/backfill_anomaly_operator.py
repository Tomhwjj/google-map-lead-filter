#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性回填：email_anomalies 历史无效邮箱记录的 operator。

背景：operator/updated_at 列上线（2026-09-12）前，bounce 管线自动标记的记录
没记操作者。历史无效邮箱（email 非空）全部来自每日拉信管线（人工「⛔ 标无效」
按钮当晚才上线），统一回填 operator='自动(bounce管线)'。
无邮箱异常（email 空）来源混合（扫描按钮/手动），不回填，页面显示 —。

用法:
    python scripts/backfill_anomaly_operator.py            # 实际回填
    python scripts/backfill_anomaly_operator.py --dry-run  # 只看影响行
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db


def main():
    dry = "--dry-run" in sys.argv
    conn = db.init_db()
    rows = conn.execute(
        "SELECT id, email, created_at FROM email_anomalies "
        "WHERE email IS NOT NULL AND email != '' AND operator IS NULL").fetchall()
    print(f"待回填 {len(rows)} 条历史无效邮箱记录")
    for r in rows[:10]:
        print(f"  #{r['id']} {r['email']} @ {r['created_at']}")
    if len(rows) > 10:
        print(f"  ... 共 {len(rows)} 条")
    if dry or not rows:
        conn.close()
        print("dry-run 结束，未写库" if dry else "无可回填记录")
        return
    cur = conn.execute(
        "UPDATE email_anomalies SET operator='自动(bounce管线)' "
        "WHERE email IS NOT NULL AND email != '' AND operator IS NULL")
    conn.commit()
    print(f"已回填 {cur.rowcount} 条 → operator='自动(bounce管线)'")
    conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重算派生分数：按库内现字段重跑 score_lead，写回 score/grade/score_detail/reason 等。

为什么需要它（task_issues #14 item5）：
  score 是**派生值** —— 由 customer_type / brands_found / scale_tier / phone / email
  现算出来的。任何证据字段被回填（backfill_company_evidence / backfill_maps_category
  / rerun_*）之后，旧分数就**过期**了：证据进了库，分数却还停在「证据缺失」时的兜底值。
  此前仓库没有通用重算工具，只能临时写一次性脚本（tmp/acq_pl/rescore.py 等），
  于是「回填了但没重算」反复发生。本脚本把这一步固化成可复用的标准动作。

口径：与 rerun_brands / rerun_emails 的写回完全一致（同一 score_lead，同一批列）。

用法:
    # 按审计轨迹挑：重算「被某类回填写过」的企业
    python scripts/rescore_companies.py --reviewer-like "%历史证据回填%"
    python scripts/rescore_companies.py --reviewer-like "%Maps 类目回收%" --apply
    # 指定企业
    python scripts/rescore_companies.py --mains LDPL-xxxx LDPL-yyyy --apply
    # 全库
    python scripts/rescore_companies.py --all --apply
"""
import argparse
import collections
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import init_db, now_iso  # noqa: E402
from score_leads import score_lead  # noqa: E402


def _brands(v):
    if isinstance(v, list):
        return v
    try:
        return json.loads(v or "[]")
    except Exception:
        return []


def rescore_one(conn, row, dry=True):
    """按库内现字段重算并写回。返回 (out, 变化描述)；dry 时 desc 也照常生成。"""
    out = score_lead({
        "customer_type": row.get("customer_type"),
        "phone": row.get("phone"),
        "email": row.get("email"),
        "website": row.get("website"),
        "scale_tier": row.get("scale_tier"),
        "scale_estimated": row.get("scale_estimated"),
        "product_tier": row.get("product_tier"),
        "brands_found": _brands(row.get("brands_found")),
    })
    desc = (f"{row.get('grade')}({row.get('score')}) -> {out['grade']}({out['score']})")
    if dry:
        return out, desc
    conn.execute(
        "UPDATE companies SET sells_deye=?, score=?, grade=?, score_detail=?, "
        "score_basis=?, score_lt=?, grade_lt=?, score_detail_lt=?, score_basis_lt=?, "
        "reason=?, updated_at=? WHERE main_id=?",
        (int(out["sells_deye"]), out["score"], out["grade"],
         json.dumps(out["score_detail"], ensure_ascii=False),
         json.dumps(out["score_basis"], ensure_ascii=False),
         out["score_lt"], out["grade_lt"],
         json.dumps(out["score_detail_lt"], ensure_ascii=False),
         json.dumps(out["score_basis_lt"], ensure_ascii=False),
         out["reason"], now_iso(), row["main_id"]))
    return out, f"{row.get('grade')}({row.get('score')}) -> {out['grade']}({out['score']})"


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="按库内现字段重算派生分数（score/grade）")
    ap.add_argument("--db", default="data/leads.db")
    ap.add_argument("--mains", nargs="*", default=[], help="指定 main_id 列表")
    ap.add_argument("--reviewer-like", default="",
                    help="按 diffs.reviewer LIKE 挑企业（如 '%%历史证据回填%%'）")
    ap.add_argument("--all", action="store_true", help="全库重算")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry 只打印）")
    args = ap.parse_args()

    if not (args.mains or args.reviewer_like or args.all):
        ap.error("至少给一个范围：--mains / --reviewer-like / --all")

    conn = init_db(args.db)
    conn.row_factory = sqlite3.Row
    sql = "SELECT * FROM companies"
    params = []
    if args.mains:
        sql += " WHERE main_id IN (%s)" % ",".join("?" for _ in args.mains)
        params = list(args.mains)
    elif args.reviewer_like:
        sql += (" WHERE main_id IN (SELECT DISTINCT main_id FROM diffs "
                "WHERE reviewer LIKE ?)")
        params = [args.reviewer_like]
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    rows = [dict(r) for r in conn.execute(sql, params)]

    print(f"范围: mains={len(args.mains)} reviewer_like={args.reviewer_like!r} "
          f"all={args.all} -> 命中 {len(rows)} 家   apply={args.apply}")
    before = collections.Counter(r.get("grade") for r in rows)
    after = collections.Counter()
    changes = []
    for r in rows:
        out, desc = rescore_one(conn, r, dry=not args.apply)
        after[out["grade"]] += 1
        if f"{r.get('grade')}({r.get('score')})" != f"{out['grade']}({out['score']})":
            changes.append(f"{r.get('company_name', '')[:32]}: {desc}")
    if args.apply:
        conn.commit()
    conn.close()

    print(f"等级分布 {dict(before)} -> {dict(after)}")
    print(f"{'[apply] 已重算' if args.apply else '[dry] 待重算'} {len(rows)} 家，"
          f"其中等级/分数变化 {len(changes)} 家")
    for c in changes[:25]:
        print("  ", c)
    if len(changes) > 25:
        print(f"   ... 共 {len(changes)} 家")
    if not args.apply:
        print("\n[dry] 未写库。确认后加 --apply")


if __name__ == "__main__":
    main()

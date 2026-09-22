#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A 管线收尾入库：把 backfill.py 产出的 backfill_lh4wfq_rerun.json 中新邮箱写回 companies。

原则（与 rerun_emails.py 同款写入范式，禁裸 SQL 越界）：
  - 只处理 main_id 在 companies 中存在、且 email 为空的行（补邮箱不覆盖已有邮箱）
  - 邮箱清洗/过滤复用 rerun_emails.rank_emails（junk 表 + 模板域 + invalid 集合 + 免费域排后）
  - 写回 email + backfilled + 重算 score/grade（score_lead）
  - 每次写入落 diffs 审计轨迹（reviewer=WorkBuddy(存量补邮箱回填)）
  - 禁碰 pool 字段与 pool_log

用法:
    python scripts/ingest_backfill_wb.py                # dry：只统计打印，不写库
    python scripts/ingest_backfill_wb.py --apply        # 写库
"""
import argparse
import json
import sqlite3
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "scripts")

from core import (EVIDENCE_FIELDS, fill_company_evidence,  # noqa: E402
                  init_db, now_iso)
from rerun_emails import apply_email  # noqa: E402
from rerun_emails import _TEMPLATE_DOMAINS, clean_email, rank_emails  # noqa: E402
from backfill import is_junk_email  # noqa: E402

REVIEWER = "WorkBuddy(存量补邮箱回填)"


def load_companies(db):
    conn = init_db(db)
    conn.row_factory = sqlite3.Row
    rows = {r["main_id"]: dict(r) for r in conn.execute("SELECT * FROM companies").fetchall()}
    conn.close()
    return rows


def invalid_set(db):
    conn = init_db(db)
    conn.row_factory = sqlite3.Row
    bad = {r["email"].strip().lower() for r in conn.execute(
        "SELECT DISTINCT email FROM email_anomalies WHERE status='open' AND email != ''"
    ).fetchall() if r["email"]}
    conn.close()
    return bad


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="backfill json -> companies.email 回填入库")
    ap.add_argument("--db", default="data/leads.db")
    ap.add_argument("--json", default="data/acq_work/backfill_lh4wfq_rerun.json")
    ap.add_argument("--expect-total", type=int, default=1169,
                    help="预期记录总数，未达到则拒绝 --apply（防半截数据入库）")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry 只打印）")
    args = ap.parse_args()

    recs = json.load(open(args.json, encoding="utf-8"))
    if args.apply and len(recs) < args.expect_total:
        print(f"[abort] json 仅 {len(recs)}/{args.expect_total} 条，背调未完成，拒绝入库")
        sys.exit(1)

    companies = load_companies(args.db)
    inv = invalid_set(args.db)

    # ---- 第一遍：证据字段回填（2026-09-22 加，task_issues #14 根因修复）----
    # 独立于 email 路径：原逻辑「已有邮箱 → continue」把绝大多数企业的背调证据
    # （brands_found/brands_context/customer_type/scale_tier）挡在库外 —— 这才是
    # 1199 家 brands_found 空的真因。此遍对**每条记录**都走，只补空不覆盖。
    n_ev_fill = n_ev_skip_empty = 0
    ev_written = []
    for rec in recs:
        mid = (rec.get("main_id") or "").strip()
        if not mid or mid not in companies:
            continue
        evidence = {k: rec.get(k) for k in EVIDENCE_FIELDS if rec.get(k)}
        if not evidence:
            n_ev_skip_empty += 1
            continue
        if args.apply:
            r = fill_company_evidence(
                mid, evidence, reviewer="WorkBuddy(存量背调证据回填)",
                db_path=args.db)
            if r["filled"]:
                n_ev_fill += 1
                ev_written.append(f"{mid} {companies[mid].get('company_name','')}: "
                                  f"{list(r['filled'])}")
        else:
            n_ev_fill += 1
            ev_written.append(f"[dry] {mid} {companies[mid].get('company_name','')}: "
                              f"{list(evidence)}")
    print(f"[证据回填] json={len(recs)} 有证据={n_ev_fill} 无证据={n_ev_skip_empty} "
          f"apply={args.apply}")
    for w in ev_written[:20]:
        print("  ", w)
    if len(ev_written) > 20:
        print(f"   ... 共 {len(ev_written)} 条")

    # 证据已回填，重载库内数据供第二遍评分用（brands 已进库，union 结果一致）
    if args.apply:
        companies = load_companies(args.db)

    # ---- 第二遍：邮箱回填 ----
    n_match = n_skip_hasemail = n_skip_missing = n_nomail = n_write = 0
    written = []
    for rec in recs:
        mid = (rec.get("main_id") or "").strip()
        lead = companies.get(mid)
        if not lead:
            n_skip_missing += 1
            continue
        n_match += 1
        if (lead.get("email") or "").strip():
            n_skip_hasemail += 1
            continue
        ranked = rank_emails(rec.get("emails") or [], inv)
        if not ranked:
            n_nomail += 1
            continue
        # 把背调发现的品牌并入评分输入
        # 2026-09-16 WorkBuddy 修：backfill json 里 brands_found 是 list 而非 JSON 字符串，
        # 直接 json.loads(list) 会 TypeError 崩溃（91 条写到第 3 条即中断）；统一规整为 str
        if rec.get("brands_found"):
            lead = dict(lead)
            old_bf = lead.get("brands_found")
            known = json.loads(old_bf) if isinstance(old_bf, str) else (old_bf or [])
            lead["brands_found"] = json.dumps(
                sorted(set(known) | set(rec["brands_found"])), ensure_ascii=False)
        if args.apply:
            merged, out = apply_email(args.db, lead, ranked)
            n_write += 1
            written.append(f"{mid} {lead.get('company_name','')}: {merged} "
                           f"(grade={out['grade']} score={out['score']})")
        else:
            n_write += 1
            written.append(f"[dry] {mid} {lead.get('company_name','')}: {', '.join(ranked[:3])}")

    print(f"json={len(recs)} 匹配={n_match} 已有邮箱跳过={n_skip_hasemail} "
          f"main_id缺失={n_skip_missing} 无有效邮箱={n_nomail} 待写入={n_write} "
          f"apply={args.apply}")
    for w in written[:40]:
        print(" ", w)
    if len(written) > 40:
        print(f"  ... 共 {len(written)} 条")


if __name__ == "__main__":
    main()

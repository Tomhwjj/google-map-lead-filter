#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
历史背调证据回填：把 6 个历史背调 JSON 里完好的证据字段补进 companies。

背景（task_issues #14）：A 管线补邮箱入库时只写了 email，背调 JSON 里完好的
brands_found / brands_context / customer_type / scale_tier 全部没进 companies，
导致评分器渠道/规模/竞品增量三档大面积走兜底（58 分基线）、竞品卖家被压到 0-28。

**只补空，绝不覆盖** —— 已有值可能是人工判的更准。每次写入落 diffs 审计轨迹
（reviewer=自动(历史证据回填)）。禁碰 pool / email（各走各的路径）。

用法:
    python scripts/backfill_company_evidence.py                 # dry：只统计打印
    python scripts/backfill_company_evidence.py --apply         # 写库
    python scripts/backfill_company_evidence.py --json a.json b.json --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (EVIDENCE_FIELDS, fill_company_evidence,  # noqa: E402
                  init_db)
from core import _evidence_empty as evidence_empty  # noqa: E402

# 默认 6 个历史背调 JSON（顺序 = 合并优先级：先出现的非空值胜出）
DEFAULT_JSONS = [
    "data/acq_work/backfill_lh4wfq_rerun.json",
    "data/acq_work/backfill_lh4wfq_retry.json",
    "data/acq_work/backfill_lh4wfq_retry2.json",
    "data/acq_work/backfill_lh4wfq_retry3.json",
    "data/acq_work/pl_round3_retry.json",
    "data/acq_work/backfill_58fix_20260921.json",
]


def nonempty(v):
    """背调 JSON 侧的空值判定（list/dict 看长度，字符串去 '[]'/'{}' 那类）。"""
    if v is None:
        return False
    if isinstance(v, (list, dict)):
        return len(v) > 0
    s = str(v).strip()
    return bool(s) and s not in ("[]", "{}", "null", "None")


def merge_evidence(files):
    """多 JSON 合并 main_id -> 证据 dict（先出现的非空值胜出）。"""
    pool = {}
    per_file = []
    for fp in files:
        if not os.path.exists(fp):
            print(f"[warn] 跳过不存在的文件: {fp}")
            continue
        recs = json.load(open(fp, encoding="utf-8"))
        added = 0
        for r in recs:
            mid = (r.get("main_id") or "").strip()
            if not mid:
                continue
            e = pool.setdefault(mid, {})
            for k in EVIDENCE_FIELDS:
                if k not in e and nonempty(r.get(k)):
                    e[k] = r[k]
                    added += 1
        per_file.append((os.path.basename(fp), len(recs), added))
    return pool, per_file


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="历史背调证据回填（只补空 + diffs 审计）")
    ap.add_argument("--db", default="data/leads.db")
    ap.add_argument("--json", nargs="*", default=DEFAULT_JSONS, help="背调 JSON 路径（可多个）")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry 只打印）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 家（调试用）")
    args = ap.parse_args()

    pool, per_file = merge_evidence(args.json)
    for name, n_rec, n_ev in per_file:
        print(f"  {name:40} 记录={n_rec:5} 贡献证据={n_ev:4}")
    n_bf = sum(1 for v in pool.values() if "brands_found" in v)
    print(f"\n合并后: main_id={len(pool)}  含 brands_found={n_bf}  "
          f"含 customer_type={sum(1 for v in pool.values() if 'customer_type' in v)}  "
          f"含 brands_context={sum(1 for v in pool.values() if 'brands_context' in v)}  "
          f"含 scale_tier={sum(1 for v in pool.values() if 'scale_tier' in v)}")

    conn = init_db(args.db)
    cur = {r["main_id"]: dict(r) for r in conn.execute(
        f"SELECT main_id, {', '.join(EVIDENCE_FIELDS)} FROM companies")}
    conn.close()
    targets = [m for m in pool if m in cur]
    print(f"其中在 companies 存在的 = {len(targets)}（库外 {len(pool) - len(targets)} 跳过）")
    if args.limit:
        targets = targets[:args.limit]

    stat = {"filled_companies": 0, "fields": {}, "all_skipped": 0}
    samples = []
    for mid in targets:
        # 只送本企业真正有值、且 DB 侧为空的字段（dry 与 apply 判定口径一致）
        ev = {k: v for k, v in pool[mid].items()
              if nonempty(v) and evidence_empty(cur[mid].get(k))}
        if not ev:
            stat["all_skipped"] += 1
            continue
        if args.apply:
            r = fill_company_evidence(
                mid, ev, reviewer="自动(历史证据回填)", task_id="", db_path=args.db)
            if not r["filled"]:
                stat["all_skipped"] += 1
                continue
            got = list(r["filled"])
        else:
            got = list(ev)
        stat["filled_companies"] += 1
        for f in got:
            stat["fields"][f] = stat["fields"].get(f, 0) + 1
        if len(samples) < 25:
            samples.append(f"{'[dry] ' if not args.apply else ''}{mid}: {got}")

    print(f"\n=== {'APPLY' if args.apply else 'DRY'} 结果 ===")
    print(f"企业数={stat['filled_companies']}  字段分布={stat['fields']}  "
          f"整家跳过(库已有值/无新证据)={stat['all_skipped']}")
    for s in samples:
        print("  ", s)
    if len(samples) >= 25:
        print("   ...")
    if not args.apply:
        print("\n[dry] 未写库。确认无误后加 --apply")


if __name__ == "__main__":
    main()

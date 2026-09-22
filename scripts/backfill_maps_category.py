#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
存量回填：从历史 gmaps CSV 的 raw_text 里回收 Google Maps 类目，补 maps_category +
customer_type，并（可选）重算分数。

背景（task_issues #14 item3「customer_type 源头修复」）：
  Google Maps 卡片自带类目（"Solar energy equipment supplier · 地址"），但 fetch_gmaps
  历史版本只把它塞进 raw_text，入库后 companies 里没有 —— 全库 896 家 customer_type
  为空，评分器 classify_channel 兜底 retail，渠道档白丢 25 分。
  修复分两半：fetch_gmaps（新抓，已改）+ 本脚本（存量，从历史 CSV 回收）。

数据源：历史 gmaps CSV 的 raw_text 列（56 个 CSV / 7350 张卡片）。**不重抓** ——
  类目原文就在 raw_text 里，重抓等于白烧代理和配额。

写入纪律（与 backfill_company_evidence 同款）：
  · **只补空，绝不覆盖**：maps_category / customer_type 已有值一律不动
    （已有值可能是人工判的，更准）；全部落 diffs 审计轨迹。
  · 类目原文入库（maps_category）→ 映射表日后修订可重跑，不必重抓。
  · customer_type 只写白名单命中的角色；表外留空给「手工判」环节，**不猜**。
  · 禁碰 pool（人工铁律）。
  · 默认 dry，--apply 才写库。

用法:
    python scripts/backfill_maps_category.py                 # dry：统计 + 打印类目全表
    python scripts/backfill_maps_category.py --apply         # 写库
    python scripts/backfill_maps_category.py --apply --rescore   # 写库 + 同批重算分数
    python scripts/backfill_maps_category.py --dirs data/acq_work D:/Agent/tmp --apply
"""
import argparse
import collections
import csv
import glob
import json
import os
import sqlite3
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import (extract_maps_category, fill_company_evidence,  # noqa: E402
                  init_db, map_maps_category, now_iso)
from score_leads import score_lead  # noqa: E402

# 历史 CSV 所在目录（data/acq_work = 库里留档的批次；D:/Agent/tmp = 各轮抓取的落盘中转）
DEFAULT_DIRS = ["data/acq_work", "D:/Agent/tmp"]
REVIEWER = "自动(Maps 类目回收·customer_type 源头修复)"

# 库内已有值也当证据源：这些列的现值不足以判断渠道时，才用类目补
TARGET_FIELDS = ("maps_category", "customer_type")


def load_csv_index(dirs):
    """扫目录下所有含 raw_text 的 CSV，建 maps_url -> 类目 与 domain -> 类目 索引。

    同一 url 多条时先出现的胜出（后出现的多为去重前的重复抓取，内容一致）。
    """
    files = []
    for d in dirs:
        if os.path.isdir(d):
            files.extend(sorted(glob.glob(os.path.join(d, "**", "*.csv"), recursive=True)))
        elif os.path.exists(d):
            files.append(d)
    by_url, by_dom = {}, {}
    n_files = n_rows = n_cat = 0
    for fp in files:
        try:
            with open(fp, encoding="utf-8-sig") as f:
                rd = csv.DictReader(f)
                if not rd.fieldnames or "raw_text" not in rd.fieldnames:
                    continue
                n_files += 1
        except Exception:
            continue
        try:
            with open(fp, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    n_rows += 1
                    cat = extract_maps_category(row.get("raw_text"))
                    if not cat:
                        continue
                    n_cat += 1
                    url = (row.get("google_maps_url") or "").strip()
                    if url:
                        by_url.setdefault(url, cat)
                    # 二级键：同域名（部分库里行无 maps_url，如 enf/search 源带进来的同企业）
                    host = urlparse(row.get("website") or "").netloc.lower().split(":")[0]
                    host = host[4:] if host.startswith("www.") else host
                    if host:
                        by_dom.setdefault(host, cat)
        except Exception as e:
            print(f"  [warn] 读取失败 {fp}: {type(e).__name__}: {e}")
    return by_url, by_dom, {"files": n_files, "rows": n_rows, "cats": n_cat}


def rescore_and_update(conn, lead, now):
    """按库内现字段重算分数写回（映射后 customer_type 变了，渠道档必须重算）。"""
    out = score_lead({
        "customer_type": lead.get("customer_type"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "website": lead.get("website"),
        "scale_tier": lead.get("scale_tier"),
        "scale_estimated": lead.get("scale_estimated"),
        "product_tier": lead.get("product_tier"),
        "brands_found": json.loads(lead.get("brands_found") or "[]")
        if isinstance(lead.get("brands_found"), str) else (lead.get("brands_found") or []),
    })
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
         out["reason"], now, lead["main_id"]))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="存量回填 Maps 类目 -> maps_category/customer_type")
    ap.add_argument("--db", default="data/leads.db")
    ap.add_argument("--dirs", nargs="*", default=DEFAULT_DIRS, help="历史 CSV 目录")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry 只统计打印）")
    ap.add_argument("--rescore", action="store_true",
                    help="写库后同批重算分数（customer_type 变了，渠道档必须重算）")
    ap.add_argument("--show-unmapped", type=int, default=25,
                    help="打印未映射类目条数（补表用，0=不打印）")
    ap.add_argument("--conflict-out", default="data/acq_work/maps_category_conflicts.csv",
                    help="「库内 customer_type ≠ 类目映射」的矛盾清单输出路径")
    args = ap.parse_args()

    by_url, by_dom, st = load_csv_index(args.dirs)
    print(f"[源] CSV {st['files']} 个 / 记录 {st['rows']} 条 / 抽出类目 {st['cats']} 条 "
          f"/ 唯一 maps_url {len(by_url)}")

    conn = init_db(args.db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM companies")]

    # 类目分布（全量，供映射表复核）
    cat_cnt = collections.Counter(by_url.values())
    print(f"\n[类目分布] 唯一类目 {len(cat_cnt)} 个，前 12：")
    for c, n in cat_cnt.most_common(12):
        role = map_maps_category(c) or "—"
        print(f"   {n:5}  {role:12} {c}")

    # 匹配 + 只补空判定（dry 与 apply 同口径）
    plan = []          # (row, cat, role)
    conflicts = []     # 已有 customer_type 与类目映射矛盾 → 不覆盖，导报告供人工判
    n_unmatched = n_skip_full = n_manual = 0
    for r in rows:
        url = (r.get("google_maps_url") or "").strip()
        dom = (r.get("domain") or "").strip()
        cat = by_url.get(url) or by_dom.get(dom) or ""
        if not cat:
            n_unmatched += 1
            continue
        role = map_maps_category(cat)
        cur_type = (r.get("customer_type") or "").strip()
        if role and cur_type and role != cur_type:
            # 矛盾：映射说 A，库里写的 B。**不覆盖**（库里可能是人工判的更准），
            # 但也**不静默丢**——导出报告交人工定夺（不直接灌 diffs 审核队列，
            # 免得几百条冲垮人工队列）。
            conflicts.append((r, cat, role, cur_type))
        need_cat = not (r.get("maps_category") or "").strip()
        need_type = role and not cur_type
        if need_cat or need_type:
            plan.append((r, cat, role))
            if not role and not cur_type:
                n_manual += 1          # 类目在表外 → customer_type 仍留空，等手工判
        else:
            n_skip_full += 1           # 已有值（只补空）或无需补

    # 只数「真的要补 customer_type」的（plan 里还有一批仅需补 maps_category 的行）
    n_type = sum(1 for r, _, role in plan
                 if role and not (r.get("customer_type") or "").strip())
    print(f"\n[匹配] 库内 {len(rows)} 家 / 命中类目 {len(rows) - n_unmatched} / "
          f"未命中(无 url 也无域名) {n_unmatched} / 已满跳过 {n_skip_full}")
    print(f"[待补] {len(plan)} 家：maps_category "
          f"{sum(1 for r, _, _ in plan if not (r.get('maps_category') or '').strip())} 家、"
          f"customer_type {n_type} 家（其中 {n_manual} 家类目在映射表外 → 仍留空待手工判）")
    conf_cnt = collections.Counter(f"{cur}→{role}" for _, _, role, cur in conflicts)
    print(f"[矛盾] {len(conflicts)} 家「库内 customer_type ≠ 类目映射」——不覆盖、不静默丢，"
          f"导报告供人工判：{dict(conf_cnt)}")
    role_cnt = collections.Counter(role for _, _, role in plan if role)
    print(f"[角色分布] {dict(role_cnt)}")

    unmapped = collections.Counter(c for c in cat_cnt if not map_maps_category(c))
    print(f"\n[未映射类目] {len(unmapped)} 个（按唯一 maps_url 计 {sum(unmapped.values())} 条）——"
          f"留空交手工判，如属常见类目请补 core.MAPS_CATEGORY_ROLE：")
    for c, n in unmapped.most_common(args.show_unmapped or 0):
        print(f"   {n:5}  {c}")

    # 矛盾清单落文件（dry 也写，便于先审后 apply）
    if conflicts:
        os.makedirs(os.path.dirname(os.path.abspath(args.conflict_out)) or ".", exist_ok=True)
        with open(args.conflict_out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["main_id", "company_name", "domain", "maps_category",
                        "类别映射", "库内现值", "website"])
            for r, cat, role, cur in conflicts:
                w.writerow([r["main_id"], r["company_name"], r.get("domain") or "",
                            cat, role, cur, r.get("website") or ""])
        print(f"→ 矛盾清单已写 {args.conflict_out}（{len(conflicts)} 行），人工核对后再决定是否改判")

    if not args.apply:
        print("\n[dry] 未写库。确认映射表无误后加 --apply（--rescore 一并重算分数）")
        conn.close()
        return

    now = now_iso()
    n_written = n_rescored = 0
    up_grade = []
    for r, cat, role in plan:
        fields = {}
        if not (r.get("maps_category") or "").strip():
            fields["maps_category"] = cat
        if role and not (r.get("customer_type") or "").strip():
            fields["customer_type"] = role
        if not fields:
            continue
        res = fill_company_evidence(r["main_id"], fields, reviewer=REVIEWER,
                                    task_id="", db_path=args.db)
        if not res["filled"]:
            continue
        n_written += 1
        if args.rescore:
            # 用库内现字段重算（maps_category/customer_type 已落库）
            fresh = dict(conn.execute("SELECT * FROM companies WHERE main_id=?",
                                      (r["main_id"],)).fetchone())
            before = r.get("score")
            out = rescore_and_update(conn, fresh, now)
            # 立刻提交：下一轮的 fill_company_evidence 用**另一条连接**写同库，
            # 本连接若攥着未提交的写锁，两边会互等到 busy_timeout 报错。
            conn.commit()
            n_rescored += 1
            if (r.get("grade") or "") != out["grade"]:
                up_grade.append(f"{r['company_name'][:30]}: {r.get('grade')}"
                                f"({before}) -> {out['grade']}({out['score']})")
    conn.commit()
    conn.close()

    print(f"\n=== APPLY 结果 ===")
    print(f"写入 {n_written} 家（diffs 审计同数）；重算分数 {n_rescored} 家")
    if up_grade:
        print(f"等级变化 {len(up_grade)} 家：")
        for s in up_grade[:25]:
            print("  ", s)
        if len(up_grade) > 25:
            print(f"   ... 共 {len(up_grade)} 家")


if __name__ == "__main__":
    main()

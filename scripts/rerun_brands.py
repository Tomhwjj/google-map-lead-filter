#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
重跑品牌背调：针对 brands_found 为空（或历史漏判）的企业，用改好的 backfill 逻辑
（从首页自动提取产品分类链接，多语言通用）重新抓官网品牌，命中的写回 companies + 重算分数。

背景：2026-09-11 发现 backfill.py 品牌页路径硬编码英文/德语，波兰语 WooCommerce 站
（如 Oze-Ekoshop 的 /kategoria/.../falowniki/）全部 404，竞品品牌漏判。本脚本复用
backfill 的 extract_product_links / find_brands + score_leads 的 score_lead，只重跑候选。

用法:
    python scripts/rerun_brands.py --country PL --channel distributor --min-score 50 --limit 20
    python scripts/rerun_brands.py --dry-run   # 只抓不写库
"""
import argparse
import json
import random
import re
import sqlite3
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "scripts")

from backfill import extract_product_links, find_brands  # noqa: E402
from score_leads import score_lead  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 库路径跟随仓库根（同 db.py），别硬编码 D:\Agent\...——换机器/目录跑错，
# 且从别处跑会静默直写共有库（2026-09-24 改进 #2）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "leads.db")

# 品牌清单（与入库时一致）：我方 Deye+贴牌 / 竞品
DEYE_BRANDS = "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark"
COMPETITOR_BRANDS = ("Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark,Huawei,Sungrow,"
                     "GoodWe,Fronius,SMA,Solax,Sofar,Growatt,Kostal,SolarEdge,"
                     "Enphase,Hoymiles,FoxESS,Solis")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def load_candidates(db, country=None, channel=None, min_score=None, limit=None):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT * FROM companies WHERE backfilled=1 "
           "AND (brands_found IS NULL OR brands_found='' OR brands_found='[]') "
           "AND website IS NOT NULL AND website LIKE 'http%'")
    conds, params = [], []
    if country:
        conds.append("country=?")
        params.append(country)
    if channel == "distributor":
        conds.append("(customer_type LIKE '%distribut%' OR customer_type LIKE '%hurtownia%' "
                     "OR customer_type LIKE '%wholesale%' OR customer_type LIKE '%import%' "
                     "OR customer_type LIKE '%sklep%' OR customer_type LIKE '%supplier%')")
    elif channel == "installer":
        conds.append("(customer_type LIKE '%install%' OR customer_type LIKE '%reseller%')")
    if min_score is not None:
        conds.append("score>=?")
        params.append(min_score)
    if conds:
        sql += " AND " + " AND ".join(conds)
    sql += " ORDER BY score DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def rescore_and_update(db, lead, brands_found, dry_run=False):
    """用 score_lead 重算，写回 brands_found + 分数。返回新分数摘要。"""
    ld = {
        "customer_type": lead.get("customer_type"),
        "phone": lead.get("phone"),
        "email": lead.get("email"),
        "website": lead.get("website"),
        "scale_tier": lead.get("scale_tier"),
        "scale_estimated": lead.get("scale_estimated"),
        "backfilled": lead.get("backfilled"),
        "product_tier": lead.get("product_tier"),
        "brands_found": brands_found,
    }
    out = score_lead(ld)
    if dry_run:
        return out
    conn = sqlite3.connect(db)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    # 2026-09-22 Claude 修（task_issues #14 同族 bug）：原 SET 里带
    # `brands_context=?` 且传死值 "{}"，等于**每次重跑都把背调证据清空**——与
    # record_email_review 覆盖 ai_analysis 是同一类「无关字段被顺手覆盖」的错误。
    # 本函数的职责只有 brands_found + 派生分数，brands_context 不归它管，移出 SET。
    conn.execute(
        "UPDATE companies SET brands_found=?, sells_deye=?, score=?, grade=?, "
        "score_detail=?, score_basis=?, score_lt=?, grade_lt=?, score_detail_lt=?, "
        "score_basis_lt=?, reason=?, updated_at=? WHERE main_id=?",
        (json.dumps(brands_found, ensure_ascii=False), int(out["sells_deye"]),
         out["score"], out["grade"], json.dumps(out["score_detail"], ensure_ascii=False),
         json.dumps(out["score_basis"], ensure_ascii=False),
         out["score_lt"], out["grade_lt"], json.dumps(out["score_detail_lt"], ensure_ascii=False),
         json.dumps(out["score_basis_lt"], ensure_ascii=False), out["reason"], now,
         lead["main_id"]))
    conn.commit()
    conn.close()
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="重跑品牌背调 + 写回")
    ap.add_argument("--country", default=None)
    ap.add_argument("--channel", choices=["distributor", "installer"], default=None)
    ap.add_argument("--min-score", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="只抓不写库")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    brands = [b.strip() for b in COMPETITOR_BRANDS.split(",") if b.strip()]
    candidates = load_candidates(args.db, args.country, args.channel,
                                 args.min_score, args.limit)
    print(f"候选 {len(candidates)} 家（country={args.country} channel={args.channel} "
          f"min_score={args.min_score} dry_run={args.dry_run}）", flush=True)

    changed = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1280, "height": 800})
        page = ctx.new_page()
        for i, lead in enumerate(candidates):
            name = lead.get("company_name", "")[:40]
            website = (lead.get("website") or "").strip()
            if not website.startswith("http"):
                continue
            brands_found = []
            texts = []
            try:
                page.goto(website, timeout=20000, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                time.sleep(random.uniform(0.4, 0.8))
                home_html = page.content()
                texts.append((page.inner_text("body") or "")[:5000])
                ctx_brands = find_brands(" ".join(texts), brands)
                brands_found = list(ctx_brands.keys())
                # 首页没命中 → 抓自动提取的产品分类链接（最多 6 个）
                if not brands_found:
                    for url in extract_product_links(home_html, website)[:6]:
                        if brands_found:
                            break
                        try:
                            page.goto(url, timeout=10000, wait_until="domcontentloaded")
                            try:
                                page.wait_for_load_state("networkidle", timeout=4000)
                            except Exception:
                                pass
                            time.sleep(random.uniform(0.2, 0.5))
                            texts.append((page.inner_text("body") or "")[:3000])
                            brands_found = list(find_brands(" ".join(texts), brands).keys())
                        except Exception:
                            pass
            except Exception as e:
                print(f"[{i+1}/{len(candidates)}] {name}: 抓取失败 {str(e)[:60]}", flush=True)
                continue

            if brands_found:
                out = rescore_and_update(args.db, lead, brands_found, dry_run=args.dry_run)
                changed += 1
                flag = "(dry)" if args.dry_run else ""
                print(f"[{i+1}/{len(candidates)}] {name}: 命中 {brands_found} → "
                      f"{out['score']}{out['grade']}/{out['score_lt']}{out['grade_lt']} {flag}",
                      flush=True)
            else:
                print(f"[{i+1}/{len(candidates)}] {name}: 仍无品牌", flush=True)
        browser.close()

    print(f"\n完成：{len(candidates)} 家，命中品牌 {changed} 家", flush=True)


if __name__ == "__main__":
    main()

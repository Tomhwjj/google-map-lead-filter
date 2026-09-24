#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
补邮箱：针对「A级分销商但 email 为空」的已有企业，重抓官网首页 + 全量联系页
（contact/kontakt/impressum/bok 等），提取邮箱写回 companies.email + 重算分数。

与 rerun_brands.py 同款写入范式（直接参数化 UPDATE + diffs 审计轨迹 + score_lead 重算）。
筛选纪律：复用 backfill 的垃圾邮箱过滤 + 本脚本补充模板域过滤；剔除 email_anomalies
        已判无效邮箱；清洗页码粘连前缀；企业自有域排前，免费域（gmail/wp.pl 等）排后。

用法:
    python scripts/rerun_emails.py                        # dry：只抓打印不写库
    python scripts/rerun_emails.py --apply                # 抓完直接写库
    python scripts/rerun_emails.py --from-json dry.json --apply   # 复用已抓 JSON 写库
    python scripts/rerun_emails.py --main-id LDDE-xxxx --apply    # 指定单家
"""
import argparse
import json
import os
import random
import re
import sqlite3
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else "scripts")

from backfill import CONTACT_PATHS, extract_emails, is_junk_email  # noqa: E402
from core import (fill_company_evidence, get_invalid_email_set,  # noqa: E402
                  init_db, now_iso)
from score_leads import score_lead  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 库路径跟随仓库根（同 db.py），别硬编码 D:\Agent\...（2026-09-24 改进 #2）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(_PROJECT_ROOT, "data", "leads.db")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 免费域排后（企业自有域优先展示）；不剔除——info@gmail 仍是有效触达
FREE_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "wp.pl", "o2.pl", "interia.pl", "onet.pl", "poczta.onet.pl", "tlen.pl",
    "gmx.de", "gmx.net", "web.de", "t-online.de", "freenet.de", "yahoo.com",
}

# 模板占位邮箱域（backfill 的 junk 表之外补充，2026-09-13 Solarscouts 'name@domain.de' 教训）
_TEMPLATE_DOMAINS = {
    "domain.de", "domain.pl", "domain.com", "email.pl", "twojemail.pl",
    "adres.pl", "twojadres.com", "example.de", "test.de", "nazwa.pl",
}


def clean_email(e):
    """小写化 + 清洗粘连垃圾前缀（'720-50info@x.de' -> 'info@x.de'）。"""
    e = (e or "").strip().lower()
    if "@" not in e:
        return ""
    local, dom = e.rsplit("@", 1)
    # local part 开头的页码/分页粘连：纯数字、数字-数字、数字. 串
    local = re.sub(r"^(?:\d{1,4}[-._]?)+", "", local)
    # mailto 链接 URL 编码残留前缀（%20 空格 / %09 tab / %0a %0d 换行）
    local = re.sub(r"^(?:%20|%09|%0a|%0d)+", "", local)
    return local + "@" + dom if local and dom else ""


def rank_emails(emails, invalid_set):
    """清洗 + 过滤模板/无效，自有域在前、免费域在后。"""
    ok = set()
    for raw in emails:
        e = clean_email(raw)
        if not e or is_junk_email(e) or e.rpartition("@")[2] in _TEMPLATE_DOMAINS:
            continue
        if e in invalid_set:
            continue
        ok.add(e)
    own = sorted(e for e in ok if e.rpartition("@")[2] not in FREE_DOMAINS)
    free = sorted(e for e in ok if e.rpartition("@")[2] in FREE_DOMAINS)
    return own + free


def load_candidates(db, main_id=None):
    """取 A级分销商、邮箱为空、有官网的候选（未联系池）。"""
    conn = init_db(db)
    conn.row_factory = sqlite3.Row
    sql = ("SELECT * FROM companies WHERE grade='A' AND (email IS NULL OR email='') "
           "AND website LIKE 'http%' AND pool='潜在客户(未联系)'")
    params = []
    if main_id:
        sql += " AND main_id=?"
        params.append(main_id)
    else:
        sql += (" AND (customer_type LIKE '%distribut%' OR customer_type LIKE '%hurtownia%' "
                "OR customer_type LIKE '%wholesale%' OR customer_type LIKE '%import%' "
                "OR customer_type LIKE '%sklep%' OR customer_type LIKE '%supplier%')")
    sql += " ORDER BY score DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def scrape(page, website):
    """首页 + 全量联系页抓邮箱（直连，命中即提前收工但联系页至少试 3 个）。"""
    found = set()
    page.goto(website, timeout=25000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    time.sleep(random.uniform(1, 2))
    found |= set(extract_emails(page.content()))
    tried = 0
    for path in CONTACT_PATHS:
        if found and tried >= 3:
            break
        try:
            page.goto(website.rstrip("/") + "/" + path,
                      timeout=15000, wait_until="domcontentloaded")
            time.sleep(random.uniform(1, 2))
            found |= set(extract_emails(page.content()))
            tried += 1
        except Exception:
            pass
    return found


def apply_email(db, lead, emails, evidence=None):
    """写回 email + 重算分数 + diffs 审计轨迹。

    evidence（2026-09-22 Claude 加，task_issues #14）：背调证据字段 dict
    {brands_found/brands_context/customer_type/scale_tier}，走 core.fill_company_evidence
    **只补空**（不覆盖已有值，含 diffs 审计）。此前只写 email，背调证据全丢 —— 这是
    1199 家 brands_found 空的根因。
    """
    merged = ", ".join(dict.fromkeys(e.strip() for e in emails if e and e.strip()))
    ld = {
        "customer_type": lead.get("customer_type"),
        "phone": lead.get("phone"),
        "email": merged,
        "website": lead.get("website"),
        "scale_tier": lead.get("scale_tier"),
        "scale_estimated": lead.get("scale_estimated"),
        "backfilled": 1,
        "product_tier": lead.get("product_tier"),
        "brands_found": json.loads(lead.get("brands_found") or "[]"),
    }
    out = score_lead(ld)
    conn = init_db(db)
    now = now_iso()
    old = lead.get("email") or ""
    conn.execute(
        "UPDATE companies SET email=?, backfilled=?, sells_deye=?, score=?, grade=?, "
        "score_detail=?, score_basis=?, score_lt=?, grade_lt=?, score_detail_lt=?, "
        "score_basis_lt=?, reason=?, updated_at=? WHERE main_id=?",
        (merged, 1, int(out["sells_deye"]), out["score"], out["grade"],
         json.dumps(out["score_detail"], ensure_ascii=False),
         json.dumps(out["score_basis"], ensure_ascii=False),
         out["score_lt"], out["grade_lt"],
         json.dumps(out["score_detail_lt"], ensure_ascii=False),
         json.dumps(out["score_basis_lt"], ensure_ascii=False),
         out["reason"], now, lead["main_id"]))
    conn.execute(
        "INSERT INTO diffs (main_id, task_id, field, old_value, new_value, status, "
        "detected_at, reviewer) VALUES (?,?,?,?,?,'approved',?,?)",
        (lead["main_id"], "", "email", old, merged, now, "WorkBuddy(官网补邮箱)"))
    conn.commit()
    conn.close()
    # 证据字段只补空（在 email 连接关闭后单独走，避免同库两连接交错写）
    if evidence:
        fill_company_evidence(
            lead["main_id"], evidence,
            reviewer="WorkBuddy(存量补邮箱回填·证据字段)",
            db_path=db)
    return merged, out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="补邮箱：重抓官网联系页写回 companies.email")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--main-id", default="", help="只处理指定企业")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry 只打印）")
    ap.add_argument("--from-json", default="", help="从已抓 JSON 直接 apply（不重抓）")
    ap.add_argument("--out", default=os.path.join(_PROJECT_ROOT, "data", "email_backfill_wb.json"))
    args = ap.parse_args()

    if args.from_json:
        # 复用已抓结果：按 main_id 对上候选，重过清洗/过滤后写库
        with open(args.from_json, encoding="utf-8") as f:
            scraped = json.load(f)
        cands = {c["main_id"]: c for c in load_candidates(args.db)}
        invalid_set = get_invalid_email_set(db_path=args.db)
        n_ok = 0
        for rec in scraped:
            lead = cands.get(rec["main_id"])
            if not lead or not rec.get("emails"):
                continue
            emails = rank_emails(rec["emails"], invalid_set)
            if not emails:
                continue
            merged, sc = apply_email(args.db, lead, emails)
            n_ok += 1
            print(f"[已写] {lead['company_name'][:32]}: {merged} "
                  f"(score {lead.get('score')}->{sc['score']} grade->{sc['grade']})")
        print(f"apply 完成: {n_ok} 家写库")
        return

    cands = load_candidates(args.db, main_id=args.main_id or None)
    invalid_set = get_invalid_email_set(db_path=args.db)
    print(f"候选 {len(cands)} 家（apply={args.apply}），已判无效邮箱 {len(invalid_set)} 个")

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-proxy-server"])
        page = browser.new_context(user_agent=UA, locale="en-US",
                                   viewport={"width": 1280, "height": 800}).new_page()
        for i, lead in enumerate(cands):
            emails, err = [], ""
            try:
                emails = rank_emails(scrape(page, lead["website"]), invalid_set)
            except Exception as e:
                err = str(e)[:150]
            rec = {"main_id": lead["main_id"], "company_name": lead["company_name"],
                   "website": lead["website"], "emails": emails, "error": err}
            if args.apply and emails:
                merged, sc = apply_email(args.db, lead, emails)
                rec["written"] = merged
                rec["new_score"] = sc["score"]
                rec["new_grade"] = sc["grade"]
            results.append(rec)
            tag = "已写" if rec.get("written") else ("dry" if not args.apply else "未抓到")
            print(f"[{i + 1}/{len(cands)}] {lead['company_name'][:32]}: "
                  f"{len(emails)} 个 ({', '.join(emails[:2]) or '无'}) {err and '| ERR: ' + err}"
                  f"  [{tag}]", flush=True)
        browser.close()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    n_ok = sum(1 for r in results if r["emails"])
    print(f"完成: {n_ok}/{len(results)} 家抓到邮箱 -> {args.out}")


if __name__ == "__main__":
    main()

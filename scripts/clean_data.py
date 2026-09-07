#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据清洗：清垃圾邮箱 + 删抓取噪声 + 修 title 污染的公司名。

三件事（都可 --dry-run 预览，正式跑前自动备份库）：
  1. 垃圾邮箱 —— 用 is_syncable_email 过滤掉 sentry/home.com/company.com/prestashop 等
     「采集器误抓的占位/日志邮箱」，同时清掉 gmail_contacts 里对应的垃圾行
  2. 抓取噪声 —— customer_type 为空 且 官网域名命中新闻/目录/聚合/比价/分类信息/视频/搜索跳转
     的「非企业」条目，删除前整行导出 JSON 备份（可追溯、可恢复），并级联清子表
  3. 公司名污染 —— 网页 <title> 混进企业名：按「|」取第一段、剥 "About us - " 前缀、
     7 家真实企业显式映射回干净公司名

铁律遵守：只清「垃圾邮箱 + 明确非企业噪声 + title 污染」，绝不改客户池状态、绝不自动判客户。
删除 = 先备份 + 先导出，100% 可恢复。
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import DEFAULT_DB, init_db, normalize_domain, normalize_name, now_iso
from core import is_syncable_email, split_emails

# 抓取噪声域名（新闻/目录/聚合/比价/分类信息/视频/搜索跳转，非企业，不含 www）
JUNK_HOSTS = {
    "anengjienergy.com",       # 内容农场 "Top 10 ..."
    "boostess.energy",         # 聚合落地页
    "ensun.io",                # 聚合搜索
    "pvkalkulator.pl",         # 比价/排名站
    "renewablesnow.com",       # 新闻
    "sg.finance.yahoo.com",    # 新闻
    "bignewsnetwork.com",      # 新闻
    "enfsolar.com",            # 目录
    "europages.co.uk",         # 目录
    "facebook.com",            # 视频帖子
    "google.com",              # 搜索跳转
    "gramwzielone.pl",         # 绿色能源新闻门户
    "lindab-polska.pl",        # 通风厂商博客文
    "novasolarhome.com",       # 目录
    "okorder.com",             # B2B 市场
    "olx.pl",                  # 分类信息
    "pfnexus.com",             # 聚合
    "poweryde.com",            # 目录
    "sklepfalowniki.pl",       # 商店分类页
    "sma-solar.pl",            # SMA 产品页
    "youtube.com",             # 视频
    "energa.pl",               # ENERGA 产品页
    "enerad.pl",               # 博客文
    "akademia-fotowoltaiki.pl",# 博客文
    "installenergy.pl",        # 分类页
}

# 真实企业 title 污染 → 干净公司名（7 家 ctype 为空的真实企业，域名非噪声）
NAME_FIX = {
    "LDPL-600db4336d": "Baterie.com.pl",
    "LDPL-e4a5621438": "Bluesun Solar Polska",
    "LDPL-976daf5ae6": "PVGroup.pl",
    "LDPL-ec66b5bd03": "SolaX Power Poland",
    "LDPL-a368951460": "JGSTech",
    "LDPL-3fd564266d": "Procarte",
    "LDPL-ad172a60dc": "Prology",
}

# 参与级联清理的子表（都以 main_id 关联）
CHILD_TABLES = ["task_companies", "diffs", "pool_log", "gmail_contacts", "email_anomalies"]


def backup_db(db_path):
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    bak = f"{db_path}.bak-{ts}"
    shutil.copy2(db_path, bak)
    return bak


def export_rows(conn, main_ids):
    """导出待删企业的完整行到 JSON（可追溯、可恢复）。"""
    out = []
    for mid in main_ids:
        r = conn.execute("SELECT * FROM companies WHERE main_id=?", (mid,)).fetchone()
        if r:
            out.append(dict(r))
    return out


def clean_junk_emails(conn, dry_run):
    """过滤垃圾邮箱：更新 companies.email，清 gmail_contacts 垃圾行。"""
    rows = conn.execute(
        "SELECT main_id, email FROM companies WHERE email IS NOT NULL AND email != ''").fetchall()
    changed = 0
    removed_contacts = 0
    for r in rows:
        emails = split_emails(r["email"])
        clean = [e for e in emails if is_syncable_email(e)]
        if clean == emails:
            continue
        changed += 1
        new_email = ", ".join(clean) if clean else None
        junk = [e for e in emails if e not in clean]
        if not dry_run:
            conn.execute("UPDATE companies SET email=?, updated_at=? WHERE main_id=?",
                         (new_email, now_iso(), r["main_id"]))
            for e in junk:
                cur = conn.execute("DELETE FROM gmail_contacts WHERE main_id=? AND lower(email)=lower(?)",
                                   (r["main_id"], e))
                removed_contacts += cur.rowcount
        print(f"  [邮箱] {r['main_id']} 清掉 {len(junk)} 条垃圾 -> 保留 {len(clean)} 条")
    if not dry_run:
        conn.commit()
    print(f"垃圾邮箱：{changed} 家企业，删 gmail_contacts {removed_contacts} 行")
    return changed


def delete_noise(conn, dry_run, export_path):
    """删除抓取噪声企业（ctype 空 且 域名命中噪声），先导出 JSON。"""
    rows = conn.execute("SELECT main_id, company_name, website, customer_type FROM companies").fetchall()
    noise_ids = []
    for r in rows:
        host = normalize_domain(r["website"] or "")
        if (r["customer_type"] in (None, "")) and (host in JUNK_HOSTS):
            noise_ids.append(r["main_id"])
            print(f"  [删除] {r['main_id']}  {r['company_name'][:48]}  ({host})")

    if not noise_ids:
        print("无噪声条目。")
        return 0

    if not dry_run:
        export = export_rows(conn, noise_ids)
        os.makedirs(os.path.dirname(export_path), exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        for mid in noise_ids:
            for tbl in CHILD_TABLES:
                conn.execute(f"DELETE FROM {tbl} WHERE main_id=?", (mid,))
            conn.execute("DELETE FROM companies WHERE main_id=?", (mid,))
        conn.commit()
    print(f"噪声企业：{len(noise_ids)} 家" + ("" if dry_run else f"，已导出 {export_path}"))
    return len(noise_ids)


def clean_names(conn, dry_run):
    """修 title 污染的公司名：显式映射 + 「|」取第一段 + 剥 About us 前缀。"""
    rows = conn.execute("SELECT main_id, company_name FROM companies").fetchall()
    changed = 0
    for r in rows:
        mid, name = r["main_id"], r["company_name"]
        new = None
        if mid in NAME_FIX:
            new = NAME_FIX[mid]
        elif "|" in name:
            first = name.split("|")[0].strip()
            if first:
                new = first
        elif re.match(r"^about\s+us\s*-\s*", name, re.I):
            new = re.sub(r"^about\s+us\s*-\s*", "", name, flags=re.I).strip()
        if new and new != name:
            changed += 1
            if not dry_run:
                conn.execute("UPDATE companies SET company_name=?, name_key=?, updated_at=? WHERE main_id=?",
                             (new, normalize_name(new), now_iso(), mid))
            print(f"  [改名] {mid}  {name[:44]!r} -> {new!r}")
    if not dry_run:
        conn.commit()
    print(f"公司名清洗：{changed} 家")
    return changed


def main():
    ap = argparse.ArgumentParser(description="数据清洗：垃圾邮箱 + 抓取噪声 + 公司名污染")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览不改库")
    args = ap.parse_args()

    conn = init_db(args.db)
    print(f"库: {args.db}   {'（DRY-RUN 预览）' if args.dry_run else '（正式执行）'}\n")

    # 正式执行前备份
    bak = None
    if not args.dry_run:
        bak = backup_db(args.db)
        print(f"已备份库 -> {bak}\n")

    print("== 1. 垃圾邮箱 ==")
    clean_junk_emails(conn, args.dry_run)
    print("\n== 2. 抓取噪声删除 ==")
    export_path = os.path.join(os.path.dirname(args.db),
                               f"deleted_noise_{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
    delete_noise(conn, args.dry_run, export_path)
    print("\n== 3. 公司名清洗 ==")
    clean_names(conn, args.dry_run)

    total = conn.execute("SELECT COUNT(*) c FROM companies").fetchone()["c"]
    with_email = conn.execute(
        "SELECT COUNT(*) c FROM companies WHERE email IS NOT NULL AND email != ''").fetchone()["c"]
    no_email = conn.execute(
        "SELECT COUNT(*) c FROM companies WHERE email IS NULL OR email=''").fetchone()["c"]
    print(f"\n=== 清洗后企业库：共 {total} 家（有邮箱 {with_email} · 无邮箱 {no_email}）===")
    conn.close()


if __name__ == "__main__":
    main()

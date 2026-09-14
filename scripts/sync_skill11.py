#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
组批次 skill11：把指定企业的「主邮箱」建进 Google 联系人 skill11 分组 + 回写 gmail_contacts。

与 gmail_sync.py sync 的区别：只取每企业 1 个最对口的部门邮箱（orders/b2b/anfrage/biuro 等），
不把 companies.email 里的全部邮箱灌进组（群发同企业 8 个地址会重复 8 封）。
分组固定 skill11（不使用自动编号，避免撞上历史 skill9/10 的空号段）。

用法:
    python scripts/sync_skill11.py --dry-run   # 预览
    python scripts/sync_skill11.py             # 真正建联系人 + 归组
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from gmail_sync import (add_contact, add_contacts_to_group, build_people,
                        get_or_create_group, get_credentials)  # noqa: E402
from core import contact_note, get_invalid_email_set, is_syncable_email  # noqa: E402
from db import init_db, now_iso  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_ROOT, "data", "leads.db")

# 每企业主邮箱（部门箱优先：orders/b2b/anfrage/vertrieb > 通用 info/kontakt/hello/biuro/office）
PICKS = {
    "LDPL-e8559b150e": "orders@7sun.eu",            # 7SUN（补货盘）
    "LDDE-3fb8f82aad": "office@greenlimon.solar",   # Greenlimon（补货盘）
    "LDDE-97f6ab2f6a": "kontakt@solarscouts.de",    # Solarscouts（补货盘）
    "LDDE-2973e89093": "info@solarv.de",            # SolarV（补货盘）
    "LDDE-a9997945b3": "hello@schmitzsolar.de",     # SchmitzSolar
    "LDDE-b125c2825c": "info@md-enrgy.de",          # MD Enrgy
    "LDDE-82a6bdd550": "info@veh-solar.de",         # VEH Solar
    "LDDE-5368096143": "anfrage@wiemann.de",        # Wiemann（销售询价箱）
    "LDDE-c78d70c1d5": "info@solar-depot.de",       # Solar Depot
    "LDPL-6a418882d3": "b2b@elementum.pl",          # Elementum（B2B 部门箱）
    "LDPL-aad04e3647": "kartuzy@elus.pl",           # Elus
    "LDPL-d34a6de078": "biuro@free-energyshop.pl",  # Free Energy（唯一原本就有邮箱的补货盘）
}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    creds = get_credentials()
    session = build_people(creds)
    # WorkBuddy 沙箱 env 有 HTTPS_PROXY=127.0.0.1:2585（对 Google 域 502），gmail_sync
    # 模块顶部的 setdefault 压不过它。此处强制显式代理（WB_GMAIL_PROXY 可覆盖）。
    _px = os.environ.get("WB_GMAIL_PROXY") or "http://127.0.0.1:33210"
    session.trust_env = False
    session.proxies = {"https": _px, "http": _px}
    conn = init_db(DB)
    done = {(r["main_id"], (r["email"] or "").lower()) for r in conn.execute(
        "SELECT main_id, email FROM gmail_contacts").fetchall()}
    invalid = get_invalid_email_set(db_path=DB)

    # 取企业信息
    rows = {}
    for mid in PICKS:
        r = conn.execute("SELECT main_id, company_name, country, email FROM companies "
                         "WHERE main_id=?", (mid,)).fetchone()
        if r is None:
            print(f"✗ {mid} 不在 companies 表，跳过")
            continue
        rows[mid] = dict(r)
    conn.close()

    plan, problems = [], []
    for mid, email in PICKS.items():
        c = rows.get(mid)
        if not c:
            continue
        if email.lower() in invalid:
            problems.append(f"{c['company_name']}: {email} 已判无效，跳过")
            continue
        if not is_syncable_email(email):
            problems.append(f"{c['company_name']}: {email} 不可同步，跳过")
            continue
        if (mid, email.lower()) in done:
            problems.append(f"{c['company_name']}: {email} 已在联系人，跳过")
            continue
        plan.append((mid, c, email))

    print(f"计划建 {len(plan)} 个联系人 → skill11（dry-run={args.dry_run}）")
    for p in problems:
        print("  ·", p)

    if args.dry_run:
        for mid, c, email in plan:
            print(f"[dry-run] {c['company_name']}: {email} → "
                  f"{contact_note(c['country'], mid, c['company_name'], 1)}")
        return

    group_res = get_or_create_group(session, "skill11")
    print(f"skill11 组: {group_res}")
    resources = []
    added = failed = 0
    for mid, c, email in plan:
        note = contact_note(c["country"], mid, c["company_name"], 1)
        try:
            resource = add_contact(session, c["company_name"], email, note)
            from core import mark_gmail_contact
            mark_gmail_contact(mid, email, note, resource_name=resource,
                               status="synced", skill_group="skill11", db_path=DB)
            resources.append(resource)
            added += 1
            print(f"✓ {c['company_name']}: {email} → {resource}")
        except Exception as e:
            from core import mark_gmail_contact
            mark_gmail_contact(mid, email, note, status="failed", error=str(e)[:200],
                               skill_group="skill11", db_path=DB)
            failed += 1
            print(f"✗ {c['company_name']}: {email} 失败 {str(e)[:120]}")
    if resources:
        n = add_contacts_to_group(session, group_res, resources)
        print(f"✓ {n} 个联系人已加入 skill11 组")
    print(f"完成：新增 {added} · 失败 {failed}")


if __name__ == "__main__":
    main()

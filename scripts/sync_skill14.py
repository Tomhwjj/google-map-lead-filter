#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
组批次 skill14（优质）：候选池收敛批——10 家 73 分 B 级 Deye 卖家
（skill11/12/13 从未圈过，历史欠账；用户 2026-09-22 拍板直接进优质组）
+ 4 家 83 分 A 级（skill12 建组漏圈，用户同日拍板同进 skill14）。
进 Google 联系人 skill14（优质）分组 + 回写 gmail_contacts。

与 sync_skill13.py 同款流程。三种模式:
    python scripts/sync_skill14.py --audit     # 只查: Google 侧组列表 + 既有 skill（优质）组对照
    python scripts/sync_skill14.py --dry-run   # 预览计划（不写 Google 不写库）
    python scripts/sync_skill14.py             # 真正建联系人 + 归组 + 落库
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from gmail_sync import (add_contact, add_contacts_to_group, build_people,
                        get_or_create_group, get_credentials)  # noqa: E402
from core import contact_note, get_invalid_email_set, is_syncable_email, mark_gmail_contact  # noqa: E402
from db import init_db  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_ROOT, "data", "leads.db")
GROUP_NAME = "skill14（优质）"
PRIOR_GROUPS = ["skill11（优质）", "skill12（优质）", "skill13（优质）",
                "skill11", "skill12", "skill13"]

# 10 家主箱（主箱序: 角色/销售箱 > 通用箱 > 个人箱 > 功能箱；取 companies.email 队首）
PICKS = {
    "LDPL-788779d717": "biuro@solar-tech.pl",               # Solar-Tech 73
    "LDXX-f3eaed888a": "kontakt@evermar.net",               # EVERMAR 73
    "LDXX-9fa5cca89e": "biuro@zeneco.pl",                   # Zeneco 73
    "LDXX-0a857de757": "kontakt@solar-group.pl",            # Solar Group 73
    "LDXX-80cade9fa2": "biuro@domenergy.pl",                # DOM Energy 73(3箱)
    "LDXX-3131e112b4": "kontakt@zycienaprad.pl",            # Życie na Prąd 73
    "LDPL-db5293ef98": "biuro@eco-constans.pl",             # Eco-Constans 73
    "LDPL-d24cfaacac": "office@bbsolar.eu",                 # BBsolar 73
    "LDPL-868c79b1bb": "kontakt@sicaev.pl",                 # Sica 73
    "LDPL-31255ccde0": "zleceniaboltenergy@gmail.com",      # BoltEnergy 73(gmail 个人箱)
    # ---- 4 家 83A（skill12 建组漏圈，用户 2026-09-22 拍板同进 skill14）----
    "LDPL-9cd1c2c032": "biuro@z-ecoenergy.com",             # Z-Ecoenergy 83
    "LDXX-8c2b2b4031": "biuro@ecosystemprojekt.pl",         # ECO SYSTEM Group 83
    "LDXX-ae501f359c": "biuro@ogrzejmyto.pl",               # OGRZEJMY TO 83
    "LDXX-d5bf60bced": "g4freeenergy@gmail.com",            # G4 FREE ENERGY 83(gmail 个人箱)
}


def connect():
    # 沙箱环境 HTTPS_PROXY 已存在（=2585），gmail_sync 的 setdefault 压不过它，
    # token 续期会走 2585 对 Google 502。必须在 get_credentials 前强制覆盖（同 sync_skill13）。
    _px = os.environ.get("WB_GMAIL_PROXY") or "http://127.0.0.1:33210"
    for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        os.environ[_k] = _px
    creds = get_credentials()
    assert creds and creds.valid, "OAuth 凭据不可用（先跑 gmail_fetch_wb 拉信续期）"
    session = build_people(creds)
    session.trust_env = False
    session.proxies = {"https": _px, "http": _px}
    return session


def list_groups(session):
    resp = session.get("https://people.googleapis.com/v1/contactGroups",
                       params={"pageSize": 1000})
    resp.raise_for_status()
    return {g["name"]: g["resourceName"] for g in resp.json().get("contactGroups", [])}


def group_member_emails(session, group_res):
    resp = session.get("https://people.googleapis.com/v1/" + group_res,
                       params={"maxMembers": 1000})
    resp.raise_for_status()
    members = resp.json().get("memberResourceNames", [])
    emails = set()
    for i in range(0, len(members), 50):
        batch = members[i:i + 50]
        r = session.get("https://people.googleapis.com/v1/people:batchGet",
                        params={"resourceNames": batch,
                                "personFields": "emailAddresses"})
        r.raise_for_status()
        for p in r.json().get("responses", []):
            for e in p.get("person", {}).get("emailAddresses", []) or []:
                if e.get("value"):
                    emails.add(e["value"].lower())
    return emails


def audit(session):
    groups = list_groups(session)
    print("== Google 联系人组 ==")
    for name in sorted(groups):
        print("  -", name)
    for gname in PRIOR_GROUPS + [GROUP_NAME]:
        if gname not in groups:
            print(f"\n[{gname}] 不存在")
            continue
        emails = group_member_emails(session, groups[gname])
        print(f"\n[{gname}] 成员 {len(emails)} 个邮箱")
        hits = [(mid, em) for mid, em in PICKS.items() if em.lower() in emails]
        for mid, em in hits:
            print("   ✓ 候选已在组: %s %s" % (mid, em))
        if not hits:
            print("   （候选无一在组）")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    session = connect()
    if args.audit:
        audit(session)
        return

    conn = init_db(DB)
    rows = {}
    for mid in PICKS:
        r = conn.execute("SELECT main_id, company_name, country, email FROM companies "
                         "WHERE main_id=?", (mid,)).fetchone()
        if r is None:
            print(f"✗ {mid} 不在 companies 表，跳过")
            continue
        rows[mid] = dict(r)
    gc = {}
    for r in conn.execute("SELECT main_id, email, status, contact_resource_name "
                          "FROM gmail_contacts WHERE main_id IN (%s)"
                          % ",".join("?" * len(PICKS)), list(PICKS)).fetchall():
        gc[(r[0], (r[1] or "").lower())] = dict(
            status=r[2], resource=r[3] or None)
    invalid = get_invalid_email_set(db_path=DB)

    groups = list_groups(session)
    prior_emails = set()
    for gname in PRIOR_GROUPS:
        if gname in groups:
            prior_emails |= group_member_emails(session, groups[gname])
    print("prior 组（skill11/12/13 优质）成员邮箱 %d 个" % len(prior_emails))

    plan_create, plan_group_only, skip = [], [], []
    for mid, email in PICKS.items():
        c = rows.get(mid)
        if not c:
            continue
        if email.lower() in invalid:
            skip.append(f"{c['company_name']}: {email} 已判无效")
            continue
        if not is_syncable_email(email):
            skip.append(f"{c['company_name']}: {email} 不可同步")
            continue
        if email.lower() in prior_emails:
            skip.append(f"{c['company_name']}: {email} 已在 skill11/12/13 优质组，不重复进组")
            continue
        existing = gc.get((mid, email.lower()))
        if existing and existing["resource"]:
            plan_group_only.append((mid, c, email, existing["resource"]))
        else:
            plan_create.append((mid, c, email))

    print(f"计划: 新建联系人 {len(plan_create)} · 已有条目仅加组 {len(plan_group_only)} · 跳过 {len(skip)} → {GROUP_NAME} (dry-run={args.dry_run})")
    for s in skip:
        print("  ·", s)
    if args.dry_run:
        for mid, c, email in plan_create:
            print(f"[create] {c['company_name']}: {email} → {contact_note(c['country'], mid, c['company_name'], 1)}")
        for mid, c, email, res in plan_group_only:
            print(f"[group]  {c['company_name']}: {email} ({res})")
        return

    group_res = get_or_create_group(session, GROUP_NAME)
    print(f"{GROUP_NAME} 组: {group_res}")

    resources, added, failed = [], 0, 0
    for mid, c, email in plan_create:
        note = contact_note(c["country"], mid, c["company_name"], 1)
        try:
            resource = add_contact(session, c["company_name"], email, note)
            mark_gmail_contact(mid, email, note, resource_name=resource,
                               status="synced", skill_group=GROUP_NAME, db_path=DB)
            resources.append(resource)
            added += 1
            print(f"✓ {c['company_name']}: {email} → {resource}")
        except Exception as e:
            mark_gmail_contact(mid, email, note, status="failed", error=str(e)[:200],
                               skill_group=GROUP_NAME, db_path=DB)
            failed += 1
            print(f"✗ {c['company_name']}: {email} 失败 {str(e)[:120]}")
    for mid, c, email, res in plan_group_only:
        note = contact_note(c["country"], mid, c["company_name"], 1)
        resources.append(res)
        mark_gmail_contact(mid, email, note, resource_name=res,
                           status="synced", skill_group=GROUP_NAME, db_path=DB)
        print(f"⟳ {c['company_name']}: {email} 已有条目 → 加组+落库")
    if resources:
        n = add_contacts_to_group(session, group_res, resources)
        print(f"✓ {n} 个联系人已加入 {GROUP_NAME}")
    print(f"完成：新建 {added} · 失败 {failed} · 仅加组 {len(plan_group_only)} · 跳过 {len(skip)}")


if __name__ == "__main__":
    main()

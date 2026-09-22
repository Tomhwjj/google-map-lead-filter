#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
组批次 skill13（优质）：评分修复后新跳 A 的 31 家 + 16 家 backfilled 批（并集 39 家）
进 Google 联系人 skill13（优质）分组 + 回写 gmail_contacts。

与 sync_skill11.py 同款流程（主箱 1 邮箱/企业）。三种模式:
    python scripts/sync_skill13.py --audit     # 只查: Google 侧组列表 + skill11/12（优质）成员对照
    python scripts/sync_skill13.py --dry-run   # 预览计划（不写 Google 不写库）
    python scripts/sync_skill13.py             # 真正建联系人 + 归组 + 落库
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))

from gmail_sync import (add_contact, add_contacts_to_group, build_people,
                        get_or_create_group, get_credentials)  # noqa: E402
from core import contact_note, get_invalid_email_set, is_syncable_email, mark_gmail_contact  # noqa: E402
from db import init_db, now_iso  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(PROJECT_ROOT, "data", "leads.db")
GROUP_NAME = "skill13（优质）"
PRIOR_GROUPS = ["skill11（优质）", "skill12（优质）", "skill11", "skill12"]

# 39 家主箱（主箱序: 角色/销售箱 > 通用箱 > 个人箱 > 功能箱；多箱企业已按规则选）
PICKS = {
    # ---- 新跳 A（31 家）----
    "LDXX-ed55efe5f3": "sprzedaz.solarsystemspl@baywa-re.com",  # BayWa 100 synced
    "LDPL-3fd564266d": "biuro@procarte.pl",                     # Procarte 100 synced(skill3)
    "LDPL-d6a4ab9df2": "bok@fega.pl",                           # FEGA 92 zero
    "LDXX-1cdb592451": "zamowienia@coreenergy.pl",              # Core Energy 92 zero(15箱)
    "LDXX-367417e522": "kamil.andruszkiewicz@ecoabm.pl",        # ecoABM 92 synced
    "LDXX-8e7bff69f7": "biuro@eco-sol.pl",                      # CellX 92 zero(2箱)
    "LDXX-5dcefa93ab": "biuro@dominion-pv.pl",                  # Dominion 90 synced
    "LDXX-8fd0247b42": "zamowienia@sunkraft.pl",                # Sunkraft 86 zero(9箱)
    "LDPL-74c950e054": "sklep@el-corte.pl",                     # el-corte 83 pending
    "LDPL-cd0e7d94cb": "biuro@solar-em.pl",                     # solar-em 83 zero
    "LDPL-d24cc0475b": "biuro@jpsenergy.pl",                    # JPSenergy 83 pending
    "LDXX-233dfe1ae0": "sklep@alians-oze.pl",                   # Alians-Shop 83 pending
    "LDXX-2ce0d28cf0": "biuro@heat-energy.com.pl",              # Heat Energy 83 zero(2箱)
    "LDXX-4a696ebdc6": "biuro@twojafotowoltaika.com.pl",        # TWOJA FV 83 pending
    "LDXX-4daf023afe": "biuro@buyenergy.pl",                    # BuyEnergy 83 pending
    "LDXX-546ae4b813": "biuro@valueimpex.pl",                   # VALUEIMPEX 83 zero
    "LDXX-645b7b701e": "biuro@ekowolt.pl",                      # Ekowolt 83 zero
    "LDXX-b0fbc17a38": "biuro@solar-energia.com.pl",            # solar-energia 83 pending
    "LDPL-e4a2171200": "biuro@smartekodom.pl",                  # SmartEkoDom 82 pending(新主箱)
    "LDXX-1217cbb993": "biuro@mam-power.pl",                    # MAM Power 82 synced
    "LDXX-27b32392ab": "zapytania@budotom.pl",                  # Budotom 82 synced
    "LDXX-2a1885f983": "office@eurosenergy.com",                # Euros Energy 82 zero
    "LDXX-2c9103f2e5": "biuro@ozebiznes.pl",                    # ozebiznes 82 synced
    "LDXX-2e64fb3713": "arkadiusz.planeta@widar.net",           # Widar 82 zero
    "LDXX-314866bfb5": "biuro@eco-team.net",                    # ECO-TEAM 82 pending
    "LDXX-617f93091c": "kontakt@serwis-fotowoltaiczny.pl",      # SERWIS 82 zero
    "LDXX-6e60129566": "kontakt@nexuss.com.pl",                 # Nexuss 82 synced
    "LDXX-924e4aef22": "biuro@gregbudpompyciepla.pl",           # GREGBUD 82 zero
    "LDXX-d6bb8bb2a1": "biuro@nrg4.pl",                         # NRG4 82 zero(3箱)
    "LDXX-f056c95edf": "biuro@technit.pl",                      # Technit 82 synced
    "LDXX-f8f7f5bf57": "biuro@enkam.pl",                        # Enkam 82 synced
    # ---- 16 家 backfilled 批新增的 B 级（8 家；其余 8 家已在上面 31 内）----
    "LDPL-0ca2e77c6d": "fortek@fortek.com.pl",                  # Fortek 73 pending
    "LDPL-1f74880d4d": "banach@g-wat.pl",                       # G-WAT 73 pending
    "LDPL-7778954e06": "biuro@sundek-energia.pl",               # SUNDEK 73 pending
    "LDPL-8886bdc65e": "zamowienia@pvmp.pl",                    # PVMP 73 pending(主箱序)
    "LDPL-c69e72aff6": "biuro@husarenergia.pl",                 # Husar 73 pending
    "LDXX-3e7c4f3927": "kontakt@e-pcf.pl",                      # Polskie Centrum 73 pending
    "LDXX-44e6c709a3": "biuro@wisesolution.pl",                 # WiseSolution 73 pending
    "LDXX-27d5b3de82": "biuro@energianowejery.pl",              # EnergiaNowejEry 58 pending
}


def connect():
    # 沙箱环境 HTTPS_PROXY 已存在（=2585），gmail_sync 的 setdefault 压不过它，
    # token 续期会走 2585 对 Google 502。必须在 get_credentials 前强制覆盖（同 gmail_fetch_wb）。
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
    print("prior 组（skill11/12 优质）成员邮箱 %d 个" % len(prior_emails))

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
            skip.append(f"{c['company_name']}: {email} 已在 skill11/12 优质组，不重复进组")
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

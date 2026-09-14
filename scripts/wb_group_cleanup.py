#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wb_group_cleanup.py —— 无效/冗余邮箱的 Google 联系人清理（每日拉信工作流第 2 步）。

工作流（用户 2026-09-14 定）：
  每日 19:00 拉信分类 → 标记 invalid → 本脚本自动跑：
    ① 清组：把 open 无效邮箱的联系人从所有 skill 分组移除（contactGroups.members.modify）
    ② 删条目：batchDeleteContacts 删除 Google 联系人条目（防止自动补全/手选再带出）
    ③ 复核：全量 contacts 重扫无效集合 = 0 残留才算完成
  同步时过滤兜底：email_quality_filter L0-L3 + core.get_invalid_email_set 常驻闸门。

本脚本只对付「已确认无效」（bounce 铁证 / 假坏）。
⑤⑥⑧类（模板变体/同箱别名/离职工箱）非明显假坏，波次验证有铁证后用
  --evict 邮箱1,邮箱2   手动清出组（只移出分组，不删联系人条目）。

用法：
  python scripts/wb_group_cleanup.py              # 清理全部 open 无效邮箱（幂等，可反复跑）
  python scripts/wb_group_cleanup.py --dry-run    # 只看将做什么，不动 Google
  python scripts/wb_group_cleanup.py --evict a@x.pl,b@y.pl  # 波次验证后清出组（保留条目）

只写 Google 通讯录 + core.mark_contact_invalid（幂等），不碰 companies/其他表。
"""
import os
import sys
import json
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

PX = os.environ.get("WB_GMAIL_PROXY") or "http://127.0.0.1:33210"
for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ[_k] = PX

import requests  # noqa: E402
import core  # noqa: E402


def get_session():
    from gmail_sync import get_credentials
    from google.auth.transport.requests import Request as GRequest
    creds = get_credentials()
    if not creds.valid:
        creds.refresh(GRequest())
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"https": PX, "http": PX}
    s.headers["Authorization"] = f"Bearer {creds.token}"
    return s


def list_contacts(s, with_groups=True):
    contacts, tok = [], None
    fields = "emailAddresses,memberships" if with_groups else "emailAddresses"
    while True:
        r = s.get("https://people.googleapis.com/v1/people/me/connections",
                  params={"pageSize": 200, "pageToken": tok or "",
                          "personFields": fields}, timeout=30)
        r.raise_for_status()
        d = r.json()
        contacts.extend(d.get("connections", []))
        tok = d.get("nextPageToken")
        if not tok:
            return contacts


def group_names(s):
    r = s.get("https://people.googleapis.com/v1/contactGroups",
              params={"pageSize": 100}, timeout=30)
    r.raise_for_status()
    return {g["resourceName"]: g.get("name", "")
            for g in r.json().get("contactGroups", [])}


def remove_from_groups(s, contact_res, group_res_list):
    """把联系人从指定组移除（members.modify）。"""
    for g in group_res_list:
        r = s.post(f"https://people.googleapis.com/v1/{g}/members:modify",
                   json={"resourceNamesToRemove": [contact_res]}, timeout=30)
        print(f"    组移除 {contact_res} <- {g}: {r.status_code}")
        if not r.ok:
            print(f"      {r.text[:150]}")


def cleanup_invalid(dry_run=False):
    """每日自动清理：open 无效邮箱 → 清组 + 删条目 + 复核。"""
    inv = set()
    for x in core.get_invalid_email_set():
        em = x if isinstance(x, str) else x.get("email", "")
        if em:
            inv.add(em.lower())
    print(f"open 无效邮箱 {len(inv)} 个")

    s = get_session()
    contacts = list_contacts(s)
    groups = group_names(s)
    targets = []
    for c in contacts:
        emails = [(e.get("value") or "").lower() for e in c.get("emailAddresses", [])]
        bad = [e for e in emails if e in inv]
        if not bad:
            continue
        gres = [m["contactGroupResourceName"] for m in c.get("memberships", [])
                if m.get("contactGroupResourceName", "").startswith("contactGroups/")]
        targets.append({"res": c["resourceName"], "bad": bad, "groups": gres,
                        "all_emails": emails})
    print(f"命中 Google 联系人条目 {len(targets)} 个")

    to_delete, done_groups = [], 0
    for t in targets:
        if t["groups"]:
            if dry_run:
                print(f"  [dry] {t['res']} {t['bad']} 将从组 {t['groups']} 移除")
            else:
                remove_from_groups(s, t["res"], t["groups"])
                done_groups += len(t["groups"])
        to_delete.append(t["res"])

    if to_delete:
        if dry_run:
            print(f"  [dry] 将 batchDeleteContacts {len(to_delete)} 条")
        else:
            for i in range(0, len(to_delete), 200):
                chunk = to_delete[i:i + 200]
                r = s.post("https://people.googleapis.com/v1/people:batchDeleteContacts",
                           json={"resourceNames": chunk}, timeout=60)
                print(f"  batchDelete {len(chunk)} 条: {r.status_code}")
                if not r.ok:
                    print(f"    {r.text[:200]}")

    # DB 侧幂等标记（gmail_contacts.status=invalid）
    if not dry_run:
        for t in targets:
            for em in t["bad"]:
                core.mark_contact_invalid(em, error="wb_group_cleanup: 已从 Google 删除条目")

    # 复核
    if not dry_run and to_delete:
        time.sleep(2)
        residual = []
        for c in list_contacts(s, with_groups=False):
            for e in c.get("emailAddresses", []):
                if (e.get("value") or "").lower() in inv:
                    residual.append(e["value"])
        print(f"复核：残留无效邮箱 {len(residual)} {residual or '（清零 ✓）'}")
        if residual:
            print("⚠ 有残留，请重跑本脚本（删除有传播延迟）")
    elif not targets:
        print("Google 侧已干净，无需动作 ✓")
    print(f"完成（dry_run={dry_run}）：命中 {len(targets)}，删条目 {len(to_delete)}，清组 {done_groups}")


def evict_from_groups(emails, dry_run=False):
    """波次验证后的清出组：只移出 skill 组，保留联系人条目（⑤⑥⑧类冗余）。"""
    want = {e.strip().lower() for e in emails if "@" in e}
    s = get_session()
    contacts = list_contacts(s)
    groups = group_names(s)
    n = 0
    for c in contacts:
        emails_c = [(e.get("value") or "").lower() for e in c.get("emailAddresses", [])]
        if not (want & set(emails_c)):
            continue
        gres = [m["contactGroupResourceName"] for m in c.get("memberships", [])
                if m.get("contactGroupResourceName", "").startswith("contactGroups/")]
        if not gres:
            print(f"  {c['resourceName']} {sorted(set(emails_c) & want)} 不在任何组，跳过")
            continue
        print(f"  {c['resourceName']} {sorted(set(emails_c) & want)} <- 组 "
              f"{[groups.get(g, g) for g in gres]}")
        if not dry_run:
            remove_from_groups(s, c["resourceName"], gres)
        n += 1
    print(f"完成（dry_run={dry_run}）：清出组 {n} 个联系人")
    return n


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--evict":
        evict_from_groups(args[1].split(",") if len(args) > 1 else [],
                          dry_run="--dry-run" in args)
    else:
        cleanup_invalid(dry_run="--dry-run" in args)

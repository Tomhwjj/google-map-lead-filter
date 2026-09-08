#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
企业邮箱（Gmail / Google Workspace）集成：绑定账号 + 保持登录态 + 自动加客户为联系人。

依赖 Google Cloud：
  1. 控制台 https://console.cloud.google.com 建项目 → 启用 People API + Gmail API
  2. OAuth 同意屏（内部/外部均可，测试期把账号加进「测试用户」）
  3. 凭证 → 创建 OAuth 客户端 ID（桌面应用）→ 下载 JSON 存为 data/credentials.json

流程：
  python scripts/gmail_sync.py authorize        # 首次：浏览器授权，存 refresh token（此后自动续期保持登录态）
  python scripts/gmail_sync.py status           # 查看绑定 + 同步概览
  python scripts/gmail_sync.py sync             # 把所有「有邮箱」企业自动加进联系人（自动跳过已标记无效的邮箱）
  python scripts/gmail_sync.py sync <main_id>   # 只同步某一家企业
  python scripts/gmail_sync.py sync --dry-run   # 预览不落库/不调 API
  python scripts/gmail_sync.py mark-invalid --email przetargi@kdpinvest.com --reason "bounce 550 User doesn't exist"
                                                # 把退信邮箱标记为无效（同步时过滤）
  python scripts/gmail_sync.py clean-invalid    # 从 Gmail 硬删「已同步但已标记无效」的联系人
  python scripts/gmail_sync.py clean-invalid --dry-run  # 预览将删哪些，不真删

安全/合规铁律：
  - 只【加联系人 + 备注】，绝不自动发邮件（发送 100% 人工确认）
  - 备注格式：{国家} {企业主码} {企业名} #{n}（n = 该企业第 n 个邮箱）
  - refresh_token 存本地 data/gmail_token.json（data/ 已 gitignore，不泄漏）
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (contact_note, get_email_account, get_invalid_email_set,
                  is_syncable_email, list_companies, mark_email_invalid,
                  mark_gmail_contact, save_email_account, split_emails,
                  update_email_account_sync)
from db import init_db, now_iso

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
TOKEN_FILE = os.path.join(DATA_DIR, "gmail_token.json")

# Google API 直连（googleapis.com）在国内会超时（WinError 10060），走本地代理 127.0.0.1:33210（与 git 同一代理）。
# 仅本进程生效：命令行脚本自身，或 import 本模块的 Flask 进程。用 setdefault 不覆盖已有配置，不影响全局 shell。
_GOOGLE_PROXY = os.environ.get("GOOGLE_HTTPS_PROXY") or "http://127.0.0.1:33210"
for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.setdefault(_k, _GOOGLE_PROXY)

SCOPES = [
    "https://www.googleapis.com/auth/contacts",   # 管理联系人（加客户）
    "https://www.googleapis.com/auth/gmail.send",  # 备将来人工确认后发邮件（本脚本不自动发）
]


def _import_google():
    """延迟导入 google 库，没装时给清晰报错（不阻塞纯数据层）。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        return Request, Credentials, InstalledAppFlow, build
    except ImportError as e:
        print("缺少 Google 依赖，先安装：", file=sys.stderr)
        print("  pip install --upgrade google-api-python-client google-auth-oauthlib "
              "-i https://mirrors.aliyun.com/pypi/simple/", file=sys.stderr)
        raise SystemExit(f"ImportError: {e}")


def load_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_token(creds):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(json.loads(creds.to_json()), f, indent=2)


def get_credentials(authorize_if_missing=False):
    """取有效凭据（有 token 自动刷新，保持登录态）。无 token 返回 None（除非 authorize_if_missing）。"""
    Request, Credentials, InstalledAppFlow, build = _import_google()
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_token(creds)
        except Exception as e:
            print(f"⚠️ refresh token 续期失败（可能已撤销，需重新 authorize）: {e}", file=sys.stderr)
    if creds and creds.valid:
        return creds
    if authorize_if_missing and os.path.exists(CREDENTIALS_FILE):
        return authorize()
    return None


def authorize():
    """首次 OAuth：本地起临时服务，浏览器授权后存 refresh token。"""
    Request, Credentials, InstalledAppFlow, build = _import_google()
    if not os.path.exists(CREDENTIALS_FILE):
        raise SystemExit(
            f"缺 OAuth 客户端凭证 {CREDENTIALS_FILE}\n"
            "请到 Google Cloud Console 建项目 → 启用 People API + Gmail API → "
            "创建桌面应用 OAuth 客户端 → 下载 JSON 存到该路径。")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    save_token(creds)
    return creds


def build_people(creds):
    """构建 People API 客户端：用 requests AuthorizedSession（走本地代理）。

    不用 googleapiclient 默认的 httplib2——httplib2 0.32 对 HTTP 代理的 https 隧道有 bug
    （直连/代理都会 WinError 10060 超时），改用 requests，代理走模块顶部设好的环境变量。
    """
    _import_google()
    from google.auth.transport.requests import AuthorizedSession
    session = AuthorizedSession(creds)
    session.trust_env = True  # 读环境变量代理（127.0.0.1:33210，模块顶部已 setdefault）
    return session


def add_contact(session, name, email, note):
    """People API 建一个联系人（公司名 + 邮箱 + 备注）。返回 resourceName。"""
    body = {
        "names": [{"unstructuredName": name}],
        "emailAddresses": [{"value": email}],
        "biographies": [{"value": note, "contentType": "TEXT_PLAIN"}],
    }
    resp = session.post(
        "https://people.googleapis.com/v1/people:createContact",
        json=body,
    )
    resp.raise_for_status()
    return resp.json().get("resourceName", "")


# 联系人分组：所有由本 skill 同步的企业都归到这个分组
GROUP_NAME = "由skill同步的企业"


def get_or_create_group(session, name=GROUP_NAME):
    """获取（或创建）联系人分组，返回 group resourceName（contactGroups/xxx）。"""
    resp = session.get("https://people.googleapis.com/v1/contactGroups",
                       params={"pageSize": 1000})
    resp.raise_for_status()
    for g in resp.json().get("contactGroups", []):
        if g.get("name") == name:
            return g["resourceName"]
    resp = session.post("https://people.googleapis.com/v1/contactGroups",
                        json={"contactGroup": {"name": name}})
    resp.raise_for_status()
    return resp.json()["resourceName"]


def add_contacts_to_group(session, group_resource, person_resources):
    """把一批联系人加进分组（members:modify 单次最多 100，自动分批）。

    Google 对「已在分组的成员」重复添加会返回 409，这里降级为逐个添加、跳过已存在的，
    保证幂等可重试。返回本次实际新加入分组的条数。"""
    person_resources = [p for p in person_resources if p]
    if not person_resources:
        return 0
    url = f"https://people.googleapis.com/v1/{group_resource}/members:modify"
    added = 0
    for i in range(0, len(person_resources), 100):
        batch = person_resources[i:i + 100]
        try:
            resp = session.post(url, json={"resourceNamesToAdd": batch})
            resp.raise_for_status()
            added += len(batch)
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status != 409:
                raise
            # 409：批次里混了已在分组的成员 → 逐个重试，跳过已存在的
            for p in batch:
                try:
                    r = session.post(url, json={"resourceNamesToAdd": [p]})
                    r.raise_for_status()
                    added += 1
                except Exception:
                    # 单个也失败（已在组/无效），跳过不计
                    pass
    return added


# ---------------------------------------------------------------------------
# 联系人分组：按「批次 + 每组最多50」拆到 skill1~skillN（替代原来的单一总组）
# ---------------------------------------------------------------------------

def _max_skill_group_num(session):
    """遍历现有分组，返回 skill{编号} 的最大编号（没有则 0）。"""
    resp = session.get("https://people.googleapis.com/v1/contactGroups",
                       params={"pageSize": 1000})
    resp.raise_for_status()
    mx = 0
    for g in resp.json().get("contactGroups", []):
        m = re.match(r"^skill(\d+)$", g.get("name", ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def get_or_create_skill_group(session, num):
    """获取（或创建）名为 skill{num} 的分组，返回 resourceName。"""
    return get_or_create_group(session, name=f"skill{num}")


def delete_group_by_name(session, name):
    """按名字删除联系人分组（删分组不删联系人，成员只是移出该组）。返回是否删到。"""
    resp = session.get("https://people.googleapis.com/v1/contactGroups",
                       params={"pageSize": 1000})
    resp.raise_for_status()
    for g in resp.json().get("contactGroups", []):
        if g.get("name") == name:
            d = session.delete(f"https://people.googleapis.com/v1/{g['resourceName']}")
            d.raise_for_status()
            return True
    return False


def _skill_group_names(session):
    """拉全部 contactGroups，返回 {resourceName: name}（用于反查联系人的 skill 分组）。"""
    resp = session.get("https://people.googleapis.com/v1/contactGroups",
                       params={"pageSize": 1000})
    resp.raise_for_status()
    return {g["resourceName"]: g.get("name", "") for g in resp.json().get("contactGroups", [])}


def _contact_skill_group(session, resource, group_names=None):
    """People API 反查某联系人所属的 skill 分组（名字匹配 skill1/skill2…），返回 resourceName 或 None。"""
    if not resource:
        return None
    try:
        resp = session.get(
            f"https://people.googleapis.com/v1/{resource}",
            params={"personFields": "memberships"})
        resp.raise_for_status()
    except Exception:
        return None
    grs = [m["contactGroupMembership"]["contactGroupResourceName"]
           for m in resp.json().get("memberships", [])
           if m.get("contactGroupMembership", {}).get("contactGroupResourceName")]
    if not grs:
        return None
    if group_names is None:
        group_names = _skill_group_names(session)
    for gr in grs:
        if re.match(r"^skill\d+$", group_names.get(gr, "")):
            return gr
    return None


def assign_by_company(session, conn, company_resources):
    """按企业归组：同一企业的联系人必须进同一 skill 分组（组容量≤50，不拆企业）。

    company_resources: {main_id: [resourceName, ...]}（本次新增的联系人）。
      本地已有分组的企业 → 新邮箱补进原组；
      本地无记录但已有联系人的企业 → People API 反查历史分组补进（跨次 sync 不拆）；
      全新企业 → 装进当前组，装满 50 开新组。
    写回 gmail_contacts.skill_group。返回归入条数。
    """
    company_resources = {k: [r for r in v if r] for k, v in company_resources.items()}
    company_resources = {k: v for k, v in company_resources.items() if v}
    if not company_resources:
        return 0

    # 本地已知分组：main_id -> group resourceName
    known = {}
    for r in conn.execute(
        "SELECT main_id, skill_group FROM gmail_contacts "
        "WHERE skill_group IS NOT NULL AND skill_group != ''").fetchall():
        known.setdefault(r["main_id"], r["skill_group"])

    group_names = None  # 懒加载 contactGroups 名字映射
    cur_group = None
    cur_used = 0
    next_num = _max_skill_group_num(session) + 1
    total = 0

    for main_id, resources in company_resources.items():
        group = known.get(main_id)
        if not group:
            row = conn.execute(
                "SELECT contact_resource_name FROM gmail_contacts "
                "WHERE main_id=? AND status='synced' AND contact_resource_name IS NOT NULL "
                "AND contact_resource_name != '' LIMIT 1", (main_id,)).fetchone()
            if row:
                if group_names is None:
                    group_names = _skill_group_names(session)
                group = _contact_skill_group(session, row["contact_resource_name"], group_names)
        if not group:
            # 全新企业：装进当前组，装不下开新组
            if cur_group is None or cur_used + len(resources) > 50:
                cur_group = get_or_create_skill_group(session, next_num)
                next_num += 1
                cur_used = 0
            group = cur_group
            cur_used += len(resources)

        cnt = add_contacts_to_group(session, group, resources)
        total += cnt
        for res in resources:
            conn.execute("UPDATE gmail_contacts SET skill_group=? WHERE contact_resource_name=?",
                         (group, res))
    conn.commit()
    return total


def _pending_emails(conn):
    """返回已同步的 (main_id, email) 集合（已 synced 的跳过，避免重复加联系人）。"""
    done = set()
    for r in conn.execute("SELECT main_id, email FROM gmail_contacts WHERE status='synced'"):
        done.add((r["main_id"], (r["email"] or "").lower()))
    return done


def sync_all(service, dry_run=False, db_path=None):
    """把所有「有邮箱」企业的可同步邮箱加进联系人，逐条落 gmail_contacts 轨迹。"""
    conn = init_db(db_path)
    done = _pending_emails(conn)
    invalid = get_invalid_email_set(db_path=db_path)
    companies = list_companies(has_email=True, db_path=db_path)
    account = get_email_account(db_path=db_path)

    added = skipped = failed = 0
    company_resources = {}   # main_id -> [resourceName, ...] 本次新增
    for c in companies:
        emails = c.get("email_list") or []
        for i, email in enumerate(emails, start=1):
            key = (c["main_id"], email.lower())
            if key in done:
                skipped += 1
                continue
            if email.lower() in invalid:
                skipped += 1
                continue
            if not is_syncable_email(email):
                skipped += 1
                continue
            note = contact_note(c["country"], c["main_id"], c["company_name"], i)
            if dry_run:
                print(f"[dry-run] {c['company_name']}: {email} → {note}")
                added += 1
                continue
            try:
                resource = add_contact(service, c["company_name"], email, note)
                mark_gmail_contact(c["main_id"], email, note, resource_name=resource,
                                   status="synced", db_path=db_path)
                company_resources.setdefault(c["main_id"], []).append(resource)
                print(f"✓ {c['company_name']}: {email} → {resource}")
                added += 1
            except Exception as e:
                mark_gmail_contact(c["main_id"], email, note, status="failed",
                                   error=str(e), db_path=db_path)
                print(f"✗ {c['company_name']}: {email} 失败 {e}")
                failed += 1
    if company_resources and not dry_run:
        try:
            n = assign_by_company(service, conn, company_resources)
            print(f"✓ 已把 {n} 条联系人按企业归入 skill 分组（同一企业不拆组）")
        except Exception as e:
            print(f"⚠️ 归组失败：{e}")
    if account and not dry_run:
        update_email_account_sync(account["account_email"], db_path=db_path)
    conn.close()
    print(f"\n完成：新增 {added} · 跳过 {skipped} · 失败 {failed}")
    return {"added": added, "skipped": skipped, "failed": failed}


def sync_company(service, main_id, dry_run=False, db_path=None):
    """只同步某一家企业的所有可同步邮箱。"""
    conn = init_db(db_path)
    done = _pending_emails(conn)
    invalid = get_invalid_email_set(db_path=db_path)
    companies = list_companies(db_path=db_path)
    company = next((c for c in companies if c["main_id"] == main_id), None)
    if not company:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    added = skipped = failed = 0
    company_resources = {}   # main_id -> [resourceName, ...] 本次新增
    for i, email in enumerate(company.get("email_list") or [], start=1):
        key = (main_id, email.lower())
        if key in done:
            skipped += 1
            continue
        if email.lower() in invalid:
            skipped += 1
            continue
        if not is_syncable_email(email):
            skipped += 1
            continue
        note = contact_note(company["country"], main_id, company["company_name"], i)
        if dry_run:
            print(f"[dry-run] {email} → {note}")
            added += 1
            continue
        try:
            resource = add_contact(service, company["company_name"], email, note)
            mark_gmail_contact(main_id, email, note, resource_name=resource,
                               status="synced", db_path=db_path)
            company_resources.setdefault(main_id, []).append(resource)
            print(f"✓ {email} → {resource}")
            added += 1
        except Exception as e:
            mark_gmail_contact(main_id, email, note, status="failed", error=str(e), db_path=db_path)
            print(f"✗ {email} 失败 {e}")
            failed += 1
    if company_resources and not dry_run:
        try:
            n = assign_by_company(service, conn, company_resources)
            print(f"✓ 已把 {n} 条联系人按企业归入 skill 分组（同一企业不拆组）")
        except Exception as e:
            print(f"⚠️ 归组失败：{e}")
    conn.close()
    return {"added": added, "skipped": skipped, "failed": failed}


def remove_invalid_contacts(session, dry_run=False, db_path=None):
    """硬删「已同步但已被标记无效」的 Gmail 联系人，并把 gmail_contacts 状态标 invalid。

    解决 bounce 邮箱（如 przetargi@kdpinvest.com）已同步进 skill 分组、群发踩雷的问题：
    先用 mark-invalid 标记无效 → 再 clean-invalid 把该联系人从 Gmail 删掉 + 轨迹标 invalid。
    显式命令，绝不自动执行（删除不可逆）。返回删除条数。
    """
    conn = init_db(db_path)
    invalid = get_invalid_email_set(db_path=db_path)
    if not invalid:
        conn.close()
        print("没有 open 状态的无效邮箱，无需清理。")
        return 0
    rows = [dict(r) for r in conn.execute(
        "SELECT id, main_id, email, contact_resource_name FROM gmail_contacts "
        "WHERE status='synced' AND contact_resource_name IS NOT NULL "
        "AND contact_resource_name != ''").fetchall()]
    targets = [r for r in rows if (r["email"] or "").lower() in invalid]
    if not targets:
        conn.close()
        print("无效邮箱均未同步成联系人，无需删除。")
        return 0

    deleted = 0
    for r in targets:
        email = r["email"]
        resource = r["contact_resource_name"]
        if dry_run:
            print(f"[dry-run] 将删除 {email} ({resource})")
            deleted += 1
            continue
        try:
            resp = session.delete(
                f"https://people.googleapis.com/v1/{resource}:deleteContact")
            resp.raise_for_status()
            conn.execute(
                "UPDATE gmail_contacts SET status='invalid', synced_at=NULL, "
                "error='标记无效，已从 Gmail 删除' WHERE id=?", (r["id"],))
            print(f"✓ 已删除无效联系人 {email} ({resource})")
            deleted += 1
        except Exception as e:
            print(f"✗ 删除失败 {email}: {e}")
    conn.commit()
    conn.close()
    print(f"\n完成：删除 {deleted} 条（目标 {len(targets)} 条）")
    return deleted


def split_skill_groups(db_path=None, dry_run=False):
    """把历史已同步联系人按「批次 + 每组最多50」拆到 skill1~skillN，并删除原总组。

    批次识别：相邻 synced_at 间隔 > 10 分钟视为新一批（每次点同步 = 一批）。
    历史数据只做一次拆分；后续同步由 sync_all/sync_company 自动「满50换组」。
    """
    conn = init_db(db_path)
    rows = conn.execute(
        "SELECT contact_resource_name, synced_at FROM gmail_contacts "
        "WHERE status='synced' AND contact_resource_name IS NOT NULL "
        "AND contact_resource_name != '' AND synced_at IS NOT NULL "
        "ORDER BY synced_at").fetchall()
    conn.close()
    if not rows:
        print("没有已同步的联系人，无需拆分。")
        return 0

    # 批次切分：间隔 > 10 分钟算新一批
    batches = []
    cur = []
    prev = None
    for r in rows:
        t = datetime.fromisoformat(r["synced_at"])
        if prev is not None and (t - prev).total_seconds() > 600:
            batches.append(cur)
            cur = []
        cur.append(r["contact_resource_name"])
        prev = t
    if cur:
        batches.append(cur)

    # 每批内每 50 切一组，从 skill1 连续编号
    plan = []
    num = 1
    for b in batches:
        for i in range(0, len(b), 50):
            plan.append((f"skill{num}", b[i:i + 50]))
            num += 1

    if dry_run:
        print(f"[dry-run] 会把 {len(rows)} 条按 {len(batches)} 个批次拆成 {len(plan)} 组：")
        for name, res in plan:
            print(f"  {name}: {len(res)} 条")
        print(f"[dry-run] 拆分后删除原分组「{GROUP_NAME}」")
        return len(rows)

    creds = get_credentials()
    if not (creds and creds.valid):
        raise SystemExit("尚未授权，先跑：python scripts/gmail_sync.py authorize")
    session = build_people(creds)
    conn2 = init_db(db_path)
    total = 0
    for name, res in plan:
        grp = get_or_create_group(session, name)
        n = add_contacts_to_group(session, grp, res)
        total += n
        for rname in res:
            conn2.execute("UPDATE gmail_contacts SET skill_group=? WHERE contact_resource_name=?",
                          (grp, rname))
        print(f"✓ {name}: 归入 {n} 条")
    conn2.commit()
    conn2.close()
    if delete_group_by_name(session, GROUP_NAME):
        print(f"✓ 已删除原分组「{GROUP_NAME}」")
    else:
        print(f"⚠️ 未找到原分组「{GROUP_NAME}」（可能已删或改名）")
    print(f"✓ 完成：{total} 条拆入 {len(plan)} 组")
    return total


def status(db_path=None):
    """绑定状态 + 同步概览。"""
    account = get_email_account(db_path=db_path)
    token = load_token()
    creds = get_credentials()
    print("=== 企业邮箱绑定状态 ===")
    print("账号:", account["account_email"] if account else "（未绑定）")
    print("类型:", account["account_type"] if account else "—")
    print("OAuth token:", "已存（登录态保持中）" if token else "未授权")
    print("凭据有效:", "是" if (creds and creds.valid) else "否（需 authorize）")
    print("credentials.json:", "存在" if os.path.exists(CREDENTIALS_FILE) else "缺失（先建 Google Cloud 项目）")
    from core import gmail_sync_stats
    s = gmail_sync_stats(db_path=db_path)
    print("=== 联系人同步概览 ===")
    print(f"待同步 {s['pending']} · 已同步 {s['synced']} · 失败 {s['failed']} · 覆盖企业 {s['companies_synced']}")


def main():
    ap = argparse.ArgumentParser(description="企业邮箱(Gmail)绑定 + 联系人自动同步")
    ap.add_argument("cmd", choices=["authorize", "status", "sync", "skill", "mark-invalid", "clean-invalid"],
                    help="authorize=首次授权 / status=状态 / sync=同步联系人 / skill=历史已同步按批次拆组(每组≤50) / mark-invalid=标记退信无效邮箱 / clean-invalid=硬删已同步的无效邮箱联系人")
    ap.add_argument("main_id", nargs="?", help="sync 时可选：只同步某企业 main_id")
    ap.add_argument("--dry-run", action="store_true", help="预览不落库/不调 API")
    ap.add_argument("--account", default="", help="绑定账号邮箱（authorize 后落 email_accounts）")
    ap.add_argument("--email", default="", help="mark-invalid: 退信无效邮箱地址")
    ap.add_argument("--reason", default="", help="mark-invalid: 无效原因（默认 bounce: User doesn't exist）")
    ap.add_argument("--company-id", default="", help="mark-invalid: 可选关联企业 main_id（自动补公司名/国家）")
    args = ap.parse_args()

    init_db()
    if args.cmd == "authorize":
        creds = authorize()
        print("授权成功，refresh token 已存", TOKEN_FILE)
        if args.account:
            save_email_account(args.account, account_type="workspace" if "@" in args.account else "gmail")
            print("已绑定账号:", args.account)
        else:
            print("提示：用 --account <邮箱> 可绑定账号；或稍后 sync 时自动绑定。")
    elif args.cmd == "status":
        status()
    elif args.cmd == "sync":
        creds = get_credentials(authorize_if_missing=True)
        if not creds:
            raise SystemExit("尚未授权，先跑：python scripts/gmail_sync.py authorize")
        service = build_people(creds)
        if args.main_id:
            sync_company(service, args.main_id, dry_run=args.dry_run)
        else:
            sync_all(service, dry_run=args.dry_run)
    elif args.cmd == "skill":
        split_skill_groups(dry_run=args.dry_run)
    elif args.cmd == "mark-invalid":
        if not args.email:
            raise SystemExit("mark-invalid 需要 --email <退信邮箱>，例如："
                             "--email przetargi@kdpinvest.com --reason \"bounce 550 User doesn't exist\"")
        aid = mark_email_invalid(args.email, reason=args.reason or None,
                                 main_id=args.company_id or None)
        print(f"已标记无效邮箱: {args.email} (anomaly_id={aid})")
        print("提示：同步时已自动跳过该邮箱；如它已同步成 Gmail 联系人，再跑 clean-invalid 删除。")
    elif args.cmd == "clean-invalid":
        creds = get_credentials(authorize_if_missing=True)
        if not creds:
            raise SystemExit("尚未授权，先跑：python scripts/gmail_sync.py authorize")
        service = build_people(creds)
        remove_invalid_contacts(service, dry_run=args.dry_run)


if __name__ == "__main__":
    main()

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
  python scripts/gmail_sync.py sync             # 把所有「有邮箱」企业自动加进联系人
  python scripts/gmail_sync.py sync <main_id>   # 只同步某一家企业
  python scripts/gmail_sync.py sync --dry-run   # 预览不落库/不调 API

安全/合规铁律：
  - 只【加联系人 + 备注】，绝不自动发邮件（发送 100% 人工确认）
  - 备注格式：{国家} {企业主码} {企业名} #{n}（n = 该企业第 n 个邮箱）
  - refresh_token 存本地 data/gmail_token.json（data/ 已 gitignore，不泄漏）
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import (contact_note, get_email_account, is_syncable_email,
                  list_companies, mark_gmail_contact, save_email_account,
                  split_emails, update_email_account_sync)
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
    companies = list_companies(has_email=True, db_path=db_path)
    account = get_email_account(db_path=db_path)
    group_resource = None if dry_run else get_or_create_group(service)

    added = skipped = failed = 0
    added_resources = []
    for c in companies:
        emails = c.get("email_list") or []
        for i, email in enumerate(emails, start=1):
            key = (c["main_id"], email.lower())
            if key in done:
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
                added_resources.append(resource)
                print(f"✓ {c['company_name']}: {email} → {resource}")
                added += 1
            except Exception as e:
                mark_gmail_contact(c["main_id"], email, note, status="failed",
                                   error=str(e), db_path=db_path)
                print(f"✗ {c['company_name']}: {email} 失败 {e}")
                failed += 1
    if group_resource and added_resources and not dry_run:
        try:
            n = add_contacts_to_group(service, group_resource, added_resources)
            print(f"✓ 已把 {n} 条联系人归入分组「{GROUP_NAME}」")
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
    companies = list_companies(db_path=db_path)
    company = next((c for c in companies if c["main_id"] == main_id), None)
    if not company:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    group_resource = None if dry_run else get_or_create_group(service)
    added = skipped = failed = 0
    added_resources = []
    for i, email in enumerate(company.get("email_list") or [], start=1):
        key = (main_id, email.lower())
        if key in done:
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
            added_resources.append(resource)
            print(f"✓ {email} → {resource}")
            added += 1
        except Exception as e:
            mark_gmail_contact(main_id, email, note, status="failed", error=str(e), db_path=db_path)
            print(f"✗ {email} 失败 {e}")
            failed += 1
    if group_resource and added_resources and not dry_run:
        try:
            n = add_contacts_to_group(service, group_resource, added_resources)
            print(f"✓ 已把 {n} 条联系人归入分组「{GROUP_NAME}」")
        except Exception as e:
            print(f"⚠️ 归组失败：{e}")
    conn.close()
    return {"added": added, "skipped": skipped, "failed": failed}


def group_synced(db_path=None, dry_run=False):
    """把 DB 里所有 status='synced' 的联系人批量归入分组（处理历史已同步的）。"""
    conn = init_db(db_path)
    rows = [r["contact_resource_name"] for r in conn.execute(
        "SELECT contact_resource_name FROM gmail_contacts "
        "WHERE status='synced' AND contact_resource_name IS NOT NULL "
        "AND contact_resource_name != ''")]
    conn.close()
    if not rows:
        print("没有已同步的联系人，无需归组。")
        return 0
    creds = get_credentials()
    if not (creds and creds.valid):
        raise SystemExit("尚未授权，先跑：python scripts/gmail_sync.py authorize")
    session = build_people(creds)
    group_resource = get_or_create_group(session)
    if dry_run:
        print(f"[dry-run] 会把 {len(rows)} 条已同步联系人归入分组「{GROUP_NAME}」")
        return len(rows)
    n = add_contacts_to_group(session, group_resource, rows)
    print(f"✓ 已把 {n} 条已同步联系人归入分组「{GROUP_NAME}」")
    return n


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
    ap.add_argument("cmd", choices=["authorize", "status", "sync", "group"], help="authorize=首次授权 / status=状态 / sync=同步联系人 / group=已同步联系人批量归组")
    ap.add_argument("main_id", nargs="?", help="sync 时可选：只同步某企业 main_id")
    ap.add_argument("--dry-run", action="store_true", help="预览不落库/不调 API")
    ap.add_argument("--account", default="", help="绑定账号邮箱（authorize 后落 email_accounts）")
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
    elif args.cmd == "group":
        group_synced(dry_run=args.dry_run)


if __name__ == "__main__":
    main()

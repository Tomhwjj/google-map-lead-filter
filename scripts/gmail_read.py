#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查群发开发信回复（只读，绝不写/发）。

与 gmail_sync.py（加联系人 + 备发邮件）分离：本脚本只请求 gmail.readonly 最小权限，
独立 token 存 data/gmail_read_token.json，不碰发邮件/联系人权限。

流程：
  python scripts/gmail_read.py authorize            # 首次：浏览器授权（只读），存 data/gmail_read_token.json
  python scripts/gmail_read.py replies              # 查昨天群发 → 今天回复（默认昨天群发）
  python scripts/gmail_read.py replies --sent-date 2026/9/7   # 指定群发日
  python scripts/gmail_read.py read --query "from:xxx@xx.com" --max 5   # 读邮件正文（Gmail 搜索语法）

安全铁律：只读收件箱/已发送，绝不发邮件、不改任何邮件状态。
"""
import argparse
import json
import os
import re
import sys
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Google API 直连（googleapis.com）国内超时，走本地代理（与 gmail_sync 一致）。setdefault 不污染 shell。
_GOOGLE_PROXY = os.environ.get("GOOGLE_HTTPS_PROXY") or "http://127.0.0.1:33210"
for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.setdefault(_k, _GOOGLE_PROXY)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
TOKEN_FILE = os.path.join(DATA_DIR, "gmail_read_token.json")

# 只读：仅查邮件，不碰发邮件/联系人（gmail_sync.py 管那两个）
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        return Request, Credentials, InstalledAppFlow
    except ImportError as e:
        print("缺少 Google 依赖：", file=sys.stderr)
        print("  pip install --upgrade google-api-python-client google-auth-oauthlib "
              "-i https://mirrors.aliyun.com/pypi/simple/", file=sys.stderr)
        raise SystemExit(f"ImportError: {e}")


def authorize():
    Request, Credentials, InstalledAppFlow = _import_google()
    if not os.path.exists(CREDENTIALS_FILE):
        raise SystemExit(f"缺 OAuth 客户端凭证 {CREDENTIALS_FILE}")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(json.loads(creds.to_json()), f, indent=2)
    return creds


def get_credentials(authorize_if_missing=False):
    Request, Credentials, InstalledAppFlow = _import_google()
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(json.loads(creds.to_json()), f, indent=2)
        except Exception as e:
            print(f"⚠️ refresh 续期失败（需重新 authorize）: {e}", file=sys.stderr)
    if creds and creds.valid:
        return creds
    if authorize_if_missing and os.path.exists(CREDENTIALS_FILE):
        return authorize()
    return None


def build_session(creds):
    """AuthorizedSession（requests，走代理）——绕开 httplib2 代理 bug（同 gmail_sync）。"""
    from google.auth.transport.requests import AuthorizedSession
    session = AuthorizedSession(creds)
    session.trust_env = True
    return session


def list_message_ids(session, query="", max_results=500):
    ids = []
    page_token = None
    while True:
        params = {"q": query, "maxResults": 100}
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        ids.extend(m.get("id") for m in data.get("messages", []))
        page_token = data.get("nextPageToken")
        if not page_token or len(ids) >= max_results:
            break
    return ids


def get_message_meta(session, mid):
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}"
    headers = ["From", "To", "Subject", "Date", "In-Reply-To", "References"]
    resp = session.get(url, params={"format": "metadata", "metadataHeaders": headers})
    resp.raise_for_status()
    data = resp.json()
    meta = {"threadId": data.get("threadId")}
    for h in data.get("payload", {}).get("headers", []):
        meta[h["name"]] = h["value"]
    meta["snippet"] = (data.get("snippet") or "")[:200]
    return meta


def extract_emails(s):
    if not s:
        return []
    return re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", s)


def replies(sent_after, sent_before, inbox_after):
    creds = get_credentials(authorize_if_missing=True)
    if not creds:
        raise SystemExit("尚未授权或 token 失效，先跑：python scripts/gmail_read.py authorize")
    session = build_session(creds)

    # 1. 群发窗内 SENT
    sent_ids = list_message_ids(session, query=f"after:{sent_after} before:{sent_before} in:sent")
    sent_threads = set()
    recipients = set()
    for mid in sent_ids:
        m = get_message_meta(session, mid)
        sent_threads.add(m.get("threadId"))
        recipients.update(extract_emails(m.get("To", "")))
    recipients_l = {e.lower() for e in recipients}

    # 2. 从群发日（含当天）起 INBOX
    inbox_ids = list_message_ids(session, query=f"after:{inbox_after} in:inbox")
    inbox_records = []
    for mid in inbox_ids:
        m = get_message_meta(session, mid)
        froms = extract_emails(m.get("From", ""))
        subj = m.get("Subject") or ""
        hit = (
            any(f.lower() in recipients_l for f in froms)
            or m.get("threadId") in sent_threads
            or subj.strip().lower().startswith("re:")
        )
        inbox_records.append({
            "from": m.get("From"),
            "subject": subj,
            "date": m.get("Date"),
            "snippet": m.get("snippet"),
            "hit": hit,
        })

    replies_hit = [r for r in inbox_records if r["hit"]]

    print(f"=== 群发回复查询 ===")
    print(f"群发窗({sent_after} ~ {sent_before}) SENT 发信: {len(sent_ids)} 封")
    print(f"自 {inbox_after} 起 INBOX 收信: {len(inbox_ids)} 封")
    print(f"其中判定为回复: {len(replies_hit)} 封\n")
    for i, r in enumerate(replies_hit, 1):
        print(f"[{i}] 来自: {r['from']}")
        print(f"    主题: {r['subject']}")
        print(f"    时间: {r['date']}")
        print(f"    摘要: {r['snippet']}\n")
    return replies_hit


def _b64url_decode(s):
    """Gmail message body 的 base64url 解码（补 = 到 4 的倍数）。"""
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="replace")


def _extract_body(payload):
    """递归提取 Gmail message payload 的 text/plain 正文（无纯文本时回退 html）。"""
    if not payload:
        return ""
    mt = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if mt == "text/plain" and data:
        return _b64url_decode(data)
    for p in payload.get("parts", []):
        if p.get("mimeType") == "text/plain" and (p.get("body") or {}).get("data"):
            return _b64url_decode(p["body"]["data"])
    for p in payload.get("parts", []):
        if p.get("parts") or p.get("mimeType", "").startswith("multipart/"):
            txt = _extract_body(p)
            if txt:
                return txt
    if mt == "text/html" and data:
        return _b64url_decode(data)
    return ""


def read_mail(query="", max_results=10, show_body=True):
    """读往来邮件正文（只读）。query 用 Gmail 搜索语法（from:/to:/subject:/newer_than: 等）。"""
    creds = get_credentials(authorize_if_missing=True)
    if not creds:
        raise SystemExit("尚未授权，先跑：python scripts/gmail_read.py authorize")
    session = build_session(creds)
    base = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    params = {"maxResults": max_results}
    if query:
        params["q"] = query
    resp = session.get(base, params=params)
    resp.raise_for_status()
    msgs = resp.json().get("messages", [])
    if not msgs:
        print(f"没有匹配的邮件（query={query or '最近收件箱'}）")
        return

    def hdr(headers, name):
        for h in headers:
            if h.get("name", "").lower() == name.lower():
                return h.get("value", "")
        return ""

    print(f"共 {len(msgs)} 封" + (f"（query: {query}）" if query else "（最近收件箱）"))
    for m in msgs:
        mid = m["id"]
        r = session.get(f"{base}/{mid}", params={"format": "full"})
        r.raise_for_status()
        d = r.json()
        payload = d.get("payload", {})
        headers = payload.get("headers", [])
        print("\n" + "=" * 64)
        print(f"发件人: {hdr(headers, 'From')}")
        print(f"收件人: {hdr(headers, 'To')}")
        print(f"主题:   {hdr(headers, 'Subject')}")
        print(f"时间:   {hdr(headers, 'Date')}")
        if show_body:
            body = _extract_body(payload)
            print("-" * 64)
            print(body if body.strip() else f"（无纯文本正文，摘要：{d.get('snippet', '')}）")
        else:
            print(f"摘要: {d.get('snippet', '')}")


def main():
    ap = argparse.ArgumentParser(description="查群发开发信回复 / 读邮件正文（只读）")
    ap.add_argument("cmd", choices=["authorize", "replies", "read"],
                    help="authorize=只读授权 / replies=查群发回复 / read=读邮件正文（--query 搜索）")
    ap.add_argument("--sent-date", default="", help="replies: 群发日（YYYY/M/D 或 YYYY-M-D），默认昨天")
    ap.add_argument("--query", default="", help="read: Gmail 搜索语法，如 from:xxx@xx.com / subject:inverter / newer_than:7d")
    ap.add_argument("--max", dest="max_results", type=int, default=10, help="read: 最多读几封（默认 10）")
    ap.add_argument("--no-body", action="store_true", help="read: 只看头/摘要，不读正文")
    args = ap.parse_args()

    if args.cmd == "authorize":
        creds = authorize()
        print("只读授权成功，token 已存", TOKEN_FILE)
    elif args.cmd == "replies":
        if args.sent_date:
            parts = re.split(r"[/-]", args.sent_date)
            sent = date(int(parts[0]), int(parts[1]), int(parts[2]))
        else:
            sent = date.today() - timedelta(days=1)

        def _dstr(d):
            return f"{d.year}/{d.month}/{d.day}"

        replies(_dstr(sent), _dstr(sent + timedelta(days=1)), _dstr(sent))
    elif args.cmd == "read":
        read_mail(query=args.query, max_results=args.max_results, show_body=not args.no_body)


if __name__ == "__main__":
    main()

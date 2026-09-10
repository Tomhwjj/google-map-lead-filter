#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WorkBuddy 拉信模块（只读，绝不写/发）——邮件回复分类管线的取数前端。

与将退役的 gmail_read.py 同款最小权限设计：仅 gmail.readonly，独立 token 存
data/wb_gmail_read_token.json（WorkBuddy 专用，不与其余 token 混用）。

流程：
  python scripts/gmail_fetch_wb.py authorize          # 首次：只读授权（浏览器点一次）
  python scripts/gmail_fetch_wb.py fetch              # 拉收件箱近 7 天 → data/wb_inbox/*.json
  python scripts/gmail_fetch_wb.py run                # fetch + 直接过分类管线写 email_review
  python scripts/gmail_fetch_wb.py run --days 2       # 指定回溯天数（默认读 data/wb_last_sync.json）

安全铁律：只读收件箱，绝不发邮件、绝不改邮件状态（no modify scope）。
幂等：message_id 已在 email_review 的自动跳过；管线 UPSERT + 异常标记均幂等。
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Google API 国内直连超时，走本地代理（与 gmail_read 同款，setdefault 不污染 shell）
_GOOGLE_PROXY = os.environ.get("GOOGLE_HTTPS_PROXY") or "http://127.0.0.1:33210"
for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ.setdefault(_k, _GOOGLE_PROXY)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
CREDENTIALS_FILE = os.path.join(DATA_DIR, "credentials.json")
TOKEN_FILE = os.path.join(DATA_DIR, "wb_gmail_read_token.json")
LAST_SYNC_FILE = os.path.join(DATA_DIR, "wb_last_sync.json")
INBOX_DIR = os.path.join(DATA_DIR, "wb_inbox")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]  # 只读，绝不加 modify/send
BODY_MAX = 20000          # 正文截断（管线只需 500 字摘要 + 退信收件人提取）

RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _import_google():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        return Request, Credentials, InstalledAppFlow
    except ImportError as e:
        raise SystemExit(
            f"缺少 Google 依赖: {e}\n"
            "venv 里装: pip install google-api-python-client google-auth-oauthlib "
            "-i https://mirrors.aliyun.com/pypi/simple/")


def authorize():
    """首次授权：用 google_auth_oauthlib 自带 run_local_server 回环（Claude 验证过的姿势）。

    ⚠ 别手写 HTTPServer 回环——localhost 可能被浏览器解析成 ::1(IPv6) 或走系统代理，
    回调到不了监听端口，且残留孤儿进程会接走 code 导致作废。run_local_server
    内部已处理端口绑定/回调捕获/token 交换，最稳。
    """
    Request, Credentials, InstalledAppFlow = _import_google()
    if not os.path.exists(CREDENTIALS_FILE):
        raise SystemExit(f"缺 OAuth 客户端凭证 {CREDENTIALS_FILE}")
    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(json.loads(creds.to_json()), f, indent=2)
    print(f"授权成功，token 已存 {TOKEN_FILE}", flush=True)
    return creds


_AUTH_CAPTURED = {}


class _AuthHandler(BaseHTTPRequestHandler):
    """回环回调：接住 Google 重定向的 ?code=...，给浏览器一句确认。"""

    def do_GET(self):
        _AUTH_CAPTURED["path"] = self.path
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h2>✅ 授权成功，可关闭此页面回到 WorkBuddy。</h2>".encode("utf-8"))

    def log_message(self, *a):
        pass


def get_credentials():
    Request, Credentials, _ = _import_google()
    if not os.path.exists(TOKEN_FILE):
        raise SystemExit(f"尚未授权，先跑: python scripts/gmail_fetch_wb.py authorize")
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(json.loads(creds.to_json()), f, indent=2)
    if not creds.valid:
        raise SystemExit("token 失效且无 refresh_token，重新 authorize")
    return creds


def _session(creds):
    from google.auth.transport.requests import AuthorizedSession
    s = AuthorizedSession(creds)
    # 沙箱环境变量的 HTTPS_PROXY 指向 WorkBuddy 内部代理(:2585)，对 Google 域 502——
    # 必须 trust_env=False + 显式代理。用户 VPN 本地端口默认 33210，可用 WB_GMAIL_PROXY 覆盖。
    s.trust_env = False
    proxy = os.environ.get("WB_GMAIL_PROXY", "http://127.0.0.1:33210")
    if proxy:
        s.proxies = {"https": proxy, "http": proxy}
    return s


def _get_retry(session, url, params=None, tries=6):
    """API GET 重试（代理瞬时 502 抖动兜底；总窗口约 75s，覆盖节点切换）。"""
    import time
    last = None
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            wait = 5 * (i + 1)
            print(f"  GET 失败({i+1}/{tries})，{wait}s 后重试: {type(e).__name__}",
                  file=sys.stderr, flush=True)
            time.sleep(wait)
    raise last


def _hdr(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _b64url(s):
    import base64
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", errors="replace")


def _extract_body(payload):
    """递归取 text/plain，回退 html（与 gmail_read 同款）。"""
    if not payload:
        return ""
    mt = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if mt == "text/plain" and data:
        return _b64url(data)
    for p in payload.get("parts", []):
        if p.get("mimeType") == "text/plain" and (p.get("body") or {}).get("data"):
            return _b64url(p["body"]["data"])
    for p in payload.get("parts", []):
        if p.get("parts") or p.get("mimeType", "").startswith("multipart/"):
            txt = _extract_body(p)
            if txt:
                return txt
    if mt == "text/html" and data:
        return _b64url(data)
    return ""


def _list_ids(session, query):
    ids, token = [], None
    while True:
        params = {"q": query, "maxResults": 100}
        if token:
            params["pageToken"] = token
        r = _get_retry(session, "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                       params=params)
        r.raise_for_status()
        d = r.json()
        ids += [m["id"] for m in d.get("messages", [])]
        token = d.get("nextPageToken")
        if not token or len(ids) >= 300:
            break
    return ids


def _read_last_sync():
    try:
        with open(LAST_SYNC_FILE, encoding="utf-8") as f:
            return json.load(f).get("last_sync", "")
    except Exception:
        return ""


def _write_last_sync():
    with open(LAST_SYNC_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_sync": datetime.now().strftime("%Y/%m/%d %H:%M")}, f,
                  ensure_ascii=False, indent=1)


def fetch(days=None, dry_run=False):
    """拉收件箱 → data/wb_inbox/{ts}.json（管线输入格式），返回 (文件路径, n)。"""
    creds = get_credentials()
    session = _session(creds)
    if not days:
        days = 7 if not _read_last_sync() else 2   # 首跑回看 7 天，之后增量 2 天兜底
    query = f"in:inbox newer_than:{days}d"
    ids = _list_ids(session, query)
    if not ids:
        print(f"收件箱近 {days} 天无新邮件")
        return None, 0

    # 幂等：已在 email_review 的 message_id 跳过
    import core
    seen = {r["message_id"] for r in core.list_email_review(limit=100000)}
    msgs = []
    for mid in ids:
        r = _get_retry(session, f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                       params={"format": "full"})
        r.raise_for_status()
        d = r.json()
        headers = d.get("payload", {}).get("headers", [])
        m = {
            "message_id": _hdr(headers, "Message-ID") or f"gmail:{mid}",
            "from": _hdr(headers, "From"),
            "to": _hdr(headers, "To"),
            "subject": _hdr(headers, "Subject"),
            "date": _hdr(headers, "Date"),
            "in_reply_to": _hdr(headers, "In-Reply-To"),
            "references": _hdr(headers, "References"),
            "snippet": (d.get("snippet") or "")[:200],
            "body": _extract_body(d.get("payload", {}))[:BODY_MAX],
            "_gmail_id": mid,
        }
        if m["message_id"] in seen:
            continue
        msgs.append(m)

    os.makedirs(INBOX_DIR, exist_ok=True)
    out = os.path.join(INBOX_DIR, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(msgs, f, ensure_ascii=False, indent=1)
    print(f"拉到 {len(msgs)} 封新邮件（近 {days} 天，去重后）→ {out}")
    return out, len(msgs)


def run(days=None):
    """fetch + 过管线（复用 email_pipeline_wb 的分类/写入函数，只走 core）。"""
    out, n = fetch(days=days)
    if not out or not n:
        return
    import email_pipeline_wb as ep
    db_path = ep.load_spec()["db_path"]
    msgs = ep.load_messages(out)
    stats = {}
    for msg in msgs:
        res = ep.classify(msg)
        self_addr = ""
        import core
        acc = core.get_email_account(db_path=db_path)
        self_addr = (acc or {}).get("account_email", "").strip().lower()
        if self_addr and res["from_address"] == self_addr:
            res["classification"], res["rule_id"], res["confidence"] = "unrelated", "R7", 0.90
            res["action_detail"] = f"自发邮件（出站原件，from={self_addr}），非客户回复"
            res["proposed_action"] = "ignored"
        if res["classification"] == "bounce":
            res["bounced_recipient"] = ep.extract_bounced_recipient(
                msg.get("body") or "", self_addr=self_addr)
            match_addr = res["bounced_recipient"]
        else:
            match_addr = res["from_address"]
        mid, mem, mby = ep.match_sender(match_addr, db_path=db_path)
        res["matched_main_id"], res["matched_email"], res["matched_by"] = mid, mem, mby
        res, actions = ep.write_result(res, db_path, dry_run=False)
        label = res["classification"]
        stats[label] = stats.get(label, 0) + 1
        print(f"[{res['rule_id']}] {label:<20} conf={res['confidence']:<5} "
              f"status={res['status']:<8} from={res['from_address']}"
              + (f" → {mid}" if mid else " → 陌生邮箱"))
        for a in actions:
            print(f"    {a}")
    _write_last_sync()
    print(f"\n=== 拉信处理完成 {len(msgs)} 封 ===")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print(f"  {k:<22} {stats[k]}")


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 拉信（只读）+ 分类入库")
    ap.add_argument("cmd", choices=["authorize", "fetch", "run"])
    ap.add_argument("--days", type=int, default=None, help="回溯天数（默认首跑 7 天/之后 2 天）")
    args = ap.parse_args()
    if args.cmd == "authorize":
        authorize()
    elif args.cmd == "fetch":
        fetch(days=args.days)
    elif args.cmd == "run":
        run(days=args.days)


if __name__ == "__main__":
    main()

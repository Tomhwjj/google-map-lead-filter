#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wb_sent_times_backfill.py —— 无效邮箱的「真实发送时间」补全。

从 Gmail 已发邮件（in:sent）逐个查询异常表里的无效邮箱，
取最近一次发给该地址的已发邮件的 Date 头作为真实发送时间，
落盘 data/wb_sent_times.json（email -> {sent_at, subject, message_id}）。

只读操作：gmail.readonly，不写 Gmail，不写 leads.db。
webapp /email-anomalies/invalid 页读取该 JSON 展示，杜绝估算。

用法：
    python scripts/wb_sent_times_backfill.py            # 全量补
    python scripts/wb_sent_times_backfill.py --refresh  # 忽略缓存强制重查
"""
import os
import sys
import json
import time
import datetime as _d

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

# 沙箱内部代理(2585)对 Google 返回 502，OAuth 续期也走 env —— 全部改指 VPN 33210
PX = os.environ.get("WB_GMAIL_PROXY") or "http://127.0.0.1:33210"
for _k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
    os.environ[_k] = PX

from gmail_fetch_wb import get_credentials, _session, _get_retry  # noqa: E402
import core  # noqa: E402

OUT = os.path.join(PROJECT_ROOT, "data", "wb_sent_times.json")
REFRESH = "--refresh" in sys.argv


def load_cache():
    if os.path.exists(OUT) and not REFRESH:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    return {}


def sent_query(session, email):
    """返回发给该邮箱最近一次已发邮件的 {sent_at, subject, message_id} 或 None。"""
    q = f"in:sent {email}"
    lst = _get_retry(session, "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                     params={"q": q, "maxResults": 5}).json()
    ids = [m["id"] for m in (lst.get("messages") or [])]
    if not ids:
        return None
    # 逐个校验：地址必须真实出现在 To/Cc/Bcc 头（避免正文提及误匹配）
    for mid in ids:
        msg = _get_retry(session, f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                         params={"format": "metadata",
                                 "metadataHeaders": ["Date", "Subject", "To", "Cc", "Bcc"]}).json()
        heads = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        rcpt = " ".join(heads.get(k, "") for k in ("to", "cc", "bcc")).lower()
        if email.lower() not in rcpt:
            continue
        raw_date = heads.get("date", "")
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(raw_date).astimezone(
                _d.timezone(_d.timedelta(hours=8)))
            sent_at = dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            sent_at = raw_date
        return {"sent_at": sent_at, "subject": heads.get("subject", ""),
                "message_id": mid}
    return None


def main():
    cache = load_cache()
    anomalies = core.list_email_anomalies(status=None)
    emails = sorted({a["email"].strip().lower() for a in anomalies if a.get("email")})
    print(f"待查无效邮箱 {len(emails)} 个（缓存命中 {len([e for e in emails if e in cache and cache[e]])}）")

    creds = get_credentials()
    session = _session(creds)
    session.proxies = {"https": PX, "http": PX}

    found = 0
    for i, em in enumerate(emails, 1):
        if cache.get(em):
            continue
        try:
            r = sent_query(session, em)
        except Exception as e:
            print(f"  [{i}/{len(emails)}] {em} 查询失败: {type(e).__name__} {e}")
            time.sleep(2)
            continue
        cache[em] = r or {}
        if r:
            found += 1
            print(f"  [{i}/{len(emails)}] {em} -> {r['sent_at']} | {r['subject'][:40]}")
        else:
            print(f"  [{i}/{len(emails)}] {em} -> 无已发记录")
        if i % 5 == 0:
            with open(OUT, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
        time.sleep(0.4)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    print(f"完成：真实发送记录 {found}/{len(emails)}，已写 {OUT}")


if __name__ == "__main__":
    main()

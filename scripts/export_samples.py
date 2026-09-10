#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导出样本邮件供 WorkBuddy 对接（只读，绝不写/发）。
用法：
  python scripts/export_samples.py list                    # 列最近收件箱邮件元数据（快，format=metadata）
  python scripts/export_samples.py dump <id1> <id2> ...    # 导出指定 message_id 全文到 data/email_samples/
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gmail_read import (get_credentials, build_session, list_message_ids,
                        get_message_meta, _extract_body)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(PROJECT_ROOT, "data", "email_samples")
BASE = "https://gmail.googleapis.com/gmail/v1/users/me/messages"


def get_full(session, mid):
    r = session.get(f"{BASE}/{mid}", params={"format": "full"})
    r.raise_for_status()
    return r.json()


def hdr(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def cmd_list(session):
    ids = list_message_ids(session, query="in:inbox", max_results=60)
    print(f"收件箱最近 {len(ids)} 封：\n")
    for i, mid in enumerate(ids, 1):
        m = get_message_meta(session, mid)
        print(f"[{i}] id={mid}")
        print(f"    发件: {m.get('From', '')}")
        print(f"    收件: {m.get('To', '')}")
        print(f"    主题: {m.get('Subject', '')}")
        print(f"    时间: {m.get('Date', '')}")
        print(f"    摘要: {m.get('snippet', '')}")
        print()


def cmd_dump(session, mids):
    os.makedirs(OUT_DIR, exist_ok=True)
    for mid in mids:
        d = get_full(session, mid)
        headers = d.get("payload", {}).get("headers", [])
        body = _extract_body(d.get("payload"))
        meta = {
            "message_id": mid,
            "from": hdr(headers, "From"),
            "to": hdr(headers, "To"),
            "subject": hdr(headers, "Subject"),
            "date": hdr(headers, "Date"),
            "in_reply_to": hdr(headers, "In-Reply-To"),
            "references": hdr(headers, "References"),
            "snippet": d.get("snippet", ""),
        }
        subj_safe = re.sub(r"[^\w一-鿿-]+", "_", meta["subject"] or "no_subject")[:40].strip("_")
        fname = f"{subj_safe}__{mid[:12]}.json"
        out = os.path.join(OUT_DIR, fname)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({**meta, "body": body}, f, ensure_ascii=False, indent=2)
        print(f"✓ {fname}")
    print(f"\n已导出 {len(mids)} 封到 {OUT_DIR}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "dump"])
    ap.add_argument("ids", nargs="*", help="dump: message_id 列表")
    args = ap.parse_args()

    creds = get_credentials(authorize_if_missing=True)
    if not creds:
        raise SystemExit("先授权：python scripts/gmail_read.py authorize")
    session = build_session(creds)

    if args.cmd == "list":
        cmd_list(session)
    else:
        if not args.ids:
            raise SystemExit("dump 需要 message_id")
        cmd_dump(session, args.ids)


if __name__ == "__main__":
    main()

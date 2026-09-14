#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wb_social_scan.py — 扫 A/B 级企业官网首页，抓 Facebook/Instagram/LinkedIn 主页链接。
只读产出 data/wb_social_links.json，不写库。直连（不走代理），UA 伪装，10 线程。
用法:
    python scripts/wb_social_scan.py            # 扫全部 A/B 有邮箱企业
    python scripts/wb_social_scan.py --apply    # 扫完把 facebook 链接回写 companies.facebook（参数化 UPDATE，先自动备份）
"""
import os, sys, json, re, time, sqlite3, shutil, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
import core  # noqa: E402

# 禁用系统代理：官网背调一律直连（项目约定）
for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

import requests
requests.packages.urllib3.disable_warnings()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
OUT = os.path.join(PROJECT_ROOT, "data", "wb_social_links.json")
FB_RE = re.compile(r"https?://(?:www\.|m\.|web\.)?facebook\.com/[^\"'<> \)\]]+", re.I)
IG_RE = re.compile(r"https?://(?:www\.)?instagram\.com/[^\"'<> \)\]]+", re.I)
LI_RE = re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[^\"'<> \)\]]+", re.I)

JUNK = ("/sharer", "/share.php", "/plugins", "/tr?", "facebook.com/dialog",
        "/hashtag", "/events", "/groups/", "/watch", "/policies", "/help",
        "/legal", "/privacy", "/business/", "facebook.com/2008")


def clean_link(url, host_domain):
    u = url.split("&")[0].rstrip("/\\\"',")
    if any(j in u.lower() for j in JUNK):
        return None
    # 过滤分享链接：目标域名是公司自己的不算主页
    try:
        p = urlparse(u)
        if p.path in ("", "/"):
            return None
        if host_domain and host_domain in (p.netloc or ""):
            return None
    except Exception:
        return None
    return u


def fetch(website):
    headers = {"User-Agent": UA, "Accept-Language": "pl,en;q=0.8"}
    for url in (website, website.rstrip("/") + "/kontakt"):
        try:
            r = requests.get(url, headers=headers, timeout=10, verify=False, allow_redirects=True)
            if r.status_code == 200 and len(r.text or "") > 200:
                return r.text
        except Exception:
            pass
    return None


def scan_one(item):
    main_id, name, website = item
    host_domain = urlparse(website if website.startswith("http") else "http://" + website).netloc.lower()
    html = fetch(website if website.startswith("http") else "http://" + website)
    res = {"main_id": main_id, "company": name, "website": website,
           "facebook": None, "instagram": None, "linkedin": None, "ok": False}
    if not html:
        return res
    res["ok"] = True
    for m in FB_RE.findall(html):
        c = clean_link(m, host_domain)
        if c:
            res["facebook"] = c
            break
    for m in IG_RE.findall(html):
        c = clean_link(m, host_domain)
        if c:
            res["instagram"] = c
            break
    for m in LI_RE.findall(html):
        c = clean_link(m, host_domain)
        if c:
            res["linkedin"] = c
            break
    return res


def main():
    apply = "--apply" in sys.argv
    rows = core.list_companies(has_email=True)
    targets = []
    for r in rows:
        if r.get("grade") not in ("A", "B"):
            continue
        w = (r.get("website") or "").strip()
        if not w:
            continue
        targets.append((r["main_id"], r.get("company_name") or "", w))
    print(f"待扫 A/B 企业: {len(targets)}")

    results, done = [], 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(scan_one, t): t for t in targets}
        for f in as_completed(futs):
            try:
                results.append(f.result())
            except Exception:
                pass
            done += 1
            if done % 40 == 0:
                print(f"  进度 {done}/{len(targets)}")

    nfb = sum(1 for r in results if r["facebook"])
    nig = sum(1 for r in results if r["instagram"])
    nli = sum(1 for r in results if r["linkedin"])
    print(f"完成: {len(results)} | FB: {nfb} | IG: {nig} | LinkedIn: {nli} | 官网不可达: {sum(1 for r in results if not r['ok'])}")
    json.dump(results, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"结果已落 {OUT}")

    if apply and nfb:
        db = os.path.join(PROJECT_ROOT, "data", "leads.db")
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
        bak = db + f".bak_{stamp}_wbsocial"
        shutil.copy2(db, bak)
        print(f"备份: {bak}")
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA busy_timeout=5000")
        n = 0
        for r in results:
            if r["facebook"]:
                conn.execute("UPDATE companies SET facebook=?, updated_at=? WHERE main_id=?",
                             (r["facebook"], datetime.datetime.now().isoformat(timespec="seconds"), r["main_id"]))
                n += 1
        conn.commit()
        conn.close()
        print(f"已回写 companies.facebook: {n} 家")


if __name__ == "__main__":
    main()

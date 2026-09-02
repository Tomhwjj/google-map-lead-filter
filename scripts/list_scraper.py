#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
列表页抓取：抓「品牌官网经销商名单页 / 展会参展商名录页」，提取公司名 + 官网链接。

用于「多源挖掘」的第 2/3 源：
  - 品牌官网 Find-a-Distributor 名单（官方授权经销商，最准）
  - 展会 exhibitor list（真实贸易商）

原理：这类页面都是「公司名 = <a> 链接文本，官网 = <a href>」结构，
抓页面后提取指向外部域名的链接，过滤导航/社交/JS 链接，输出线索 CSV。

用法:
    python list_scraper.py "https://brand.com/where-to-buy" "https://show.com/exhibitors" --out list.csv

输出 CSV 字段: company_name, website, source_url
"""
import argparse
import csv
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

DEFAULT_PROXY = ""  # 品牌/展会官网一般可直连
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

CSV_FIELDS = ["company_name", "website", "source_url"]

# 噪声域名：社交/通用/导航链接，不是公司官网
NOISE_DOMAINS = {
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "youtube.com",
    "instagram.com", "whatsapp.com", "t.me", "google.com", "google.fr",
    "apple.com", "maps.google.com", "play.google.com", "wa.me",
    "pinterest.com", "tiktok.com", "weixin.qq.com", "wechat.com",
}


def host_of(url):
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return ""


def is_noise(url):
    h = host_of(url)
    return any(h == d or h.endswith("." + d) for d in NOISE_DOMAINS)


def scrape_page(page, url, max_links):
    """抓一个列表页，返回 [(公司名, 官网URL), ...]。"""
    page.goto(url, timeout=45000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    time.sleep(random.uniform(1, 2))

    page_host = host_of(page.url or url)
    results = []
    seen = set()

    for a in page.query_selector_all("a"):
        href = (a.get_attribute("href") or "").strip()
        text = (a.inner_text() or "").strip()
        if not href or not text:
            continue
        # 跳过 js/mailto/tel/锚点
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        abs_url = urljoin(url, href)
        if not abs_url.startswith(("http://", "https://")):
            continue
        # 只要外部域名（公司官网），排除当前站点自身的导航
        if host_of(abs_url) == page_host:
            continue
        if is_noise(abs_url):
            continue
        # 文本清洗：去掉 emoji/换行，限制长度
        name = re.sub(r"\s+", " ", text).strip()
        if not (2 <= len(name) <= 90):
            continue
        if abs_url in seen:
            continue
        seen.add(abs_url)
        results.append((name, abs_url))
        if len(results) >= max_links:
            break
    return results


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="抓列表页提取公司名+官网（品牌经销商名单/展会名录）")
    ap.add_argument("urls", nargs="+", help="列表页 URL（可多个）")
    ap.add_argument("--out", default="list_leads.csv", help="输出 CSV")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="代理地址")
    ap.add_argument("--max", type=int, default=300, help="每个页面最多提取链接数")
    args = ap.parse_args()

    rows = []
    seen_url = set()
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if args.proxy:
            launch_kwargs["proxy"] = {"server": args.proxy}
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        for u in args.urls:
            try:
                items = scrape_page(page, u, args.max)
            except Exception as e:
                print(f"[列表页] {u}: 失败 {e}", file=sys.stderr)
                items = []
            for name, w in items:
                if w in seen_url:
                    continue
                seen_url.add(w)
                rows.append({"company_name": name, "website": w, "source_url": u})
            print(f"[列表页] {u}: {len(items)} 条", flush=True)

        browser.close()

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"列表抓取完成: {len(args.urls)} 页 -> {len(rows)} 条（去重后） -> {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
抓取 Google Maps 搜索结果，解析商家卡片，输出 CSV。

用法:
    python fetch_gmaps.py "solar panel distributor Hamburg" --max 50 --out leads.csv

依赖: playwright (pip install playwright && playwright install chromium)
"""
import argparse
import csv
import random
import re
import sys
import time
import urllib.parse
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

# 默认走本机代理（访问 Google 需要），可用 --proxy 覆盖
DEFAULT_PROXY = "http://127.0.0.1:33210"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

CSV_FIELDS = ["company_name", "rating", "phone", "website",
              "google_maps_url", "raw_text"]


def extract_real_url(href):
    """Google Maps 的 Website 链接是重定向 URL，解析出真实网址。"""
    if not href:
        return ""
    if "url?q=" in href or "/url?" in href:
        qs = parse_qs(urlparse(href).query)
        if "q" in qs:
            return unquote(qs["q"][0])
    return href


def parse_article(article):
    """解析单个结果卡片，返回字段 dict。"""
    # 公司名 + Google Maps 入口（a[href*="/maps/place/"]）
    name = ""
    maps_url = ""
    a = article.query_selector('a[href*="/maps/place/"]')
    if a:
        name = (a.get_attribute("aria-label") or "").strip()
        maps_url = a.get_attribute("href") or ""

    text = (article.inner_text() or "").strip()

    # 评分：优先 "4.7" 数字，否则 "No reviews"
    rating = ""
    m = re.search(r"\b(\d\.\d)\b", text)
    if m:
        rating = m.group(1)
    elif "No reviews" in text:
        rating = "No reviews"

    # 电话：+49 / +31 等国际格式
    phone = ""
    m = re.search(r"(\+[\d\s\-()]{7,})", text)
    if m:
        phone = re.sub(r"\s+", " ", m.group(1)).strip()

    # 官网：Website 链接是外链重定向（href 含 /url? 或 url?q=），语言无关
    website = ""
    for wa in article.query_selector_all("a"):
        href = wa.get_attribute("href") or ""
        if "/url?" in href or "url?q=" in href:
            website = extract_real_url(href)
            break
    # 兜底：按界面文本标签找（多语言）
    if not website:
        for wa in article.query_selector_all("a"):
            lab = (wa.get_attribute("aria-label") or "") + " " + (wa.inner_text() or "")
            if any(k in lab for k in ("Website", "网站", "網站", "ウェブ")):
                website = extract_real_url(wa.get_attribute("href") or "")
                break

    return {
        "company_name": name,
        "rating": rating,
        "phone": phone,
        "website": website,
        "google_maps_url": maps_url,
        "raw_text": text,  # 整块文本，含品类/地址/营业状态，供后续精判
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="抓取 Google Maps 搜索结果 -> CSV")
    ap.add_argument("query", help="搜索词，如 'solar panel distributor Hamburg'")
    ap.add_argument("--max", type=int, default=50, help="最多抓取条数 (默认 50)")
    ap.add_argument("--out", default="leads.csv", help="输出 CSV 路径")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="代理地址")
    args = ap.parse_args()

    url = "https://www.google.com/maps/search/" + urllib.parse.quote(args.query)

    leads = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": args.proxy})
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(url, timeout=60000, wait_until="domcontentloaded")
        time.sleep(random.uniform(5, 8))  # 等首屏渲染

        feed = page.query_selector('[role="feed"]')
        prev_count = -1
        stall = 0
        while len(leads) < args.max and stall < 4:
            for a in page.query_selector_all('div[role="article"]'):
                data = parse_article(a)
                key = data["google_maps_url"] or data["company_name"]
                if key and key not in seen:
                    seen.add(key)
                    leads.append(data)
                    if len(leads) >= args.max:
                        break

            if len(leads) == prev_count:
                stall += 1
            else:
                stall = 0
            prev_count = len(leads)

            if len(leads) >= args.max:
                break
            if feed:
                feed.evaluate("el => el.scrollTop = el.scrollHeight")
                time.sleep(random.uniform(2, 3))  # 限速，防反爬
            else:
                break

        browser.close()

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for lead in leads:
            w.writerow(lead)

    print(f"抓取完成: {len(leads)} 条 -> {args.out}")


if __name__ == "__main__":
    main()

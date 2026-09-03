#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
抓取 ENF Solar（enf.com）光伏企业目录 -> 经销商/安装商线索 CSV。

ENF 是「批发商友好度最高的目录源」：企业页列代理品牌，能直接挖到经销
Deye/Sunsynk 的商户。本脚本把 pv-company-scraper（D:\\Agent\\git\\pv-company-scraper）
沉淀的 ENF 抓取知识抽成 google-map-lead-filter 的一个数据源脚本：

  - 列表页 URL: /directory/{category}/{country-slug}，分页 total-pages-N
  - 详情页字段: itemprop 提取 name/address/phone/email/website
  - 邮箱 JS 混淆解码（let x='...' + .replace() 链）
  - Cloudflare 用 curl-cffi impersonate=chrome124 绕过

用法:
    python fetch_enf.py --country DE --category seller --max 50 --out enf_de.csv
    python fetch_enf.py --country DE,FR,NL --category seller,installer --max 200 --out enf.csv

输出 CSV 字段: company_name, country, customer_type, phone, email, website,
    address, profile_url, source_url
  - customer_type: ENF seller -> distributor（经销商），installer 保持 installer
    （⚠️ 评分脚本的渠道关键词不含 "seller"，不映射会被判成 retail 零分）
  - 只抓欧盟 27 国；英国/瑞士/挪威/乌克兰/塞尔维亚等非欧盟自动拒绝
"""
import argparse
import csv
import random
import re
import sys
import time

from curl_cffi import requests as cffi_requests
from selectolax.parser import HTMLParser

BASE_URL = "https://www.enfsolar.com"

# 欧盟 27 国 -> ENF 目录 slug（不含英国 GB/瑞士 CH/挪威 NO/乌克兰 UA/塞尔维亚 RS）
EU27_SLUGS = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia",
    "CZ": "Czech", "DK": "Denmark", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "DE": "Germany", "GR": "Greece", "HU": "Hungary",
    "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LT": "Lithuania",
    "LU": "Luxembourg", "NL": "Netherlands", "PL": "Poland", "PT": "Portugal",
    "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
}

# ENF 分类 -> 我方 customer_type（seller 是 ENF 对「经销商/销售商」的叫法）
CATEGORY_MAP = {"seller": "distributor", "installer": "installer"}
CATEGORY_PATHS = {"seller": "/directory/seller", "installer": "/directory/installer"}

CSV_FIELDS = ["company_name", "country", "customer_type", "phone", "email",
              "website", "address", "profile_url", "source_url"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def make_session(proxy=""):
    s = cffi_requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def fetch(session, url):
    """抓页面，403/503 视为 Cloudflare 拦截。"""
    resp = session.get(url, impersonate="chrome124", timeout=30)
    if resp.status_code in (403, 503):
        raise RuntimeError(f"Cloudflare 拦截 {resp.status_code}: {url}")
    resp.raise_for_status()
    return resp.text


def parse_total_pages(tree):
    div = tree.css_first("[class*='enf-company-list-total-pages']")
    if not div:
        return 1
    cls = " ".join(div.attributes.get("class", "").split())
    m = re.search(r"total-pages-(\d+)", cls)
    return int(m.group(1)) if m else 1


def parse_listing(html):
    """列表页 -> [(公司名, profile_url), ...]，返回 (列表, 总页数)。"""
    tree = HTMLParser(html)
    total_pages = parse_total_pages(tree)
    out = []
    for row in tree.css("tr.mkjs-el"):
        link = row.css_first("a.mkjs-a")
        if not link:
            continue
        name = link.text(strip=True)
        href = link.attributes.get("href", "")
        if not name or not href or "directory=" not in href:
            continue
        if href.startswith("/"):
            href = BASE_URL + href
        elif not href.startswith("http"):
            href = BASE_URL + "/" + href
        out.append((name, href))
    return out, total_pages


def decode_email_js(tree, fallback=""):
    """ENF Solar 风格邮箱 JS 混淆解码（复用 pv-company-scraper parser._decode_email_js）。"""
    container = tree.css_first('[itemprop="email"]')
    if not container:
        return fallback
    script = container.css_first("script")
    if not script:
        return fallback
    js = (script.html or "").replace("&#39;", "'").replace("&quot;", '"')
    m = re.search(r"let\s+\w+\s*=\s*'([^']+)'", js)
    if not m:
        return fallback
    encoded = m.group(1)
    replaces = re.findall(r"\.replace\(/([^/]+)/g\s*,\s*'([^']*)'\)", js)
    result = encoded
    for pattern, replacement in replaces:
        result = result.replace(pattern, replacement)
    return result if "@" in result else fallback


def by_itemprop(tree, prop):
    """itemprop 提取，优先 tel:/mailto: 链接，否则文本。"""
    el = tree.css_first(f'[itemprop="{prop}"]')
    if not el:
        return ""
    for tag, prefix in (("a[href^='tel:']", "tel:"), ("a[href^='mailto:']", "mailto:")):
        link = el.css_first(tag)
        if link:
            href = link.attributes.get("href", "")
            if href:
                return href.replace(prefix, "").strip()
    return el.text(strip=True) or ""


def extract_website(tree):
    """官网：itemprop=url 里的 <a href>（完整 URL），否则文本。"""
    el = tree.css_first('[itemprop="url"]')
    if not el:
        return ""
    a = el if el.tag == "a" else el.css_first("a[href]")
    if a is not None:
        href = a.attributes.get("href", "")
        if href and href.startswith("http"):
            return href.strip()
    return el.text(strip=True) or ""


def parse_profile(html, url, country, customer_type):
    """详情页 -> 字段 dict（或 None）。"""
    tree = HTMLParser(html)
    title_el = tree.css_first("title")
    name = ""
    if title_el:
        m = re.match(r"^([^|]+)", title_el.text(strip=True))
        if m:
            name = m.group(1).strip()
    if not name:
        return None
    email = decode_email_js(tree, by_itemprop(tree, "email"))
    return {
        "company_name": name,
        "country": country,
        "customer_type": customer_type,
        "phone": by_itemprop(tree, "telephone"),
        "email": email,
        "website": extract_website(tree),
        "address": by_itemprop(tree, "address"),
        "profile_url": url,
        "source_url": url,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="抓取 ENF Solar 企业目录 -> 线索 CSV")
    ap.add_argument("--country", default="DE", help="欧盟 2 字母代码，逗号分隔，如 DE,FR,NL")
    ap.add_argument("--category", default="seller", help="seller/installer，逗号分隔")
    ap.add_argument("--max", type=int, default=50, help="最多抓详情页条数 (0=全部)")
    ap.add_argument("--max-pages", type=int, default=0, help="每个组合最多翻页数 (0=全部)")
    ap.add_argument("--out", default="enf_leads.csv", help="输出 CSV 路径")
    ap.add_argument("--proxy", default="", help="代理地址（默认直连）")
    ap.add_argument("--delay", type=float, default=0.8, help="详情页间延迟秒数基准")
    args = ap.parse_args()

    countries = [c.strip().upper() for c in args.country.split(",") if c.strip()]
    categories = [c.strip().lower() for c in args.category.split(",") if c.strip()]

    for c in countries:
        if c not in EU27_SLUGS:
            print(f"❌ 非欧盟国家 {c} 拒绝（只做欧盟 27 国）", file=sys.stderr)
            sys.exit(1)
    for cat in categories:
        if cat not in CATEGORY_MAP:
            print(f"❌ 未知分类 {cat}（仅 seller/installer）", file=sys.stderr)
            sys.exit(1)

    session = make_session(args.proxy)
    leads = []
    seen_url = set()

    for cat in categories:
        ctype = CATEGORY_MAP[cat]
        path = CATEGORY_PATHS[cat]
        for code in countries:
            slug = EU27_SLUGS[code]
            # 收集列表页的公司链接（翻页）
            company_urls = []
            page = 1
            total_pages = 1
            while True:
                url = f"{BASE_URL}{path}/{slug}" if page == 1 else f"{BASE_URL}{path}/{slug}/{page}"
                try:
                    html = fetch(session, url)
                except Exception as e:
                    print(f"[列表] {code}/{cat} 第{page}页失败: {e}", file=sys.stderr)
                    break
                items, total_pages = parse_listing(html)
                company_urls.extend(items)
                print(f"[列表] {code}/{cat} 第{page}/{total_pages}页: +{len(items)} 家（累计 {len(company_urls)}）", flush=True)
                if args.max and len(company_urls) >= args.max:
                    break
                if page >= total_pages:
                    break
                if args.max_pages and page >= args.max_pages:
                    break
                page += 1
                time.sleep(random.uniform(0.5, 1.5))

            # 逐个详情页抓字段
            for i, (name, purl) in enumerate(company_urls):
                if args.max and len(leads) >= args.max:
                    break
                if purl in seen_url:
                    continue
                seen_url.add(purl)
                try:
                    html = fetch(session, purl)
                    rec = parse_profile(html, purl, code, ctype)
                    if rec:
                        leads.append(rec)
                        print(f"[详情 {len(leads)}] {rec['company_name']}: "
                              f"email={'有' if rec['email'] else '无'} phone={'有' if rec['phone'] else '无'} "
                              f"web={'有' if rec['website'] else '无'}", flush=True)
                except Exception as e:
                    print(f"[详情] {name}: 失败 {str(e)[:80]}", file=sys.stderr)
                time.sleep(random.uniform(args.delay, args.delay * 2.5))

            if args.max and len(leads) >= args.max:
                break
        if args.max and len(leads) >= args.max:
            break

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in leads:
            w.writerow(r)

    n_email = sum(1 for r in leads if r["email"])
    n_phone = sum(1 for r in leads if r["phone"])
    n_web = sum(1 for r in leads if r["website"])
    print(f"抓取完成: {len(leads)} 条 -> {args.out}")
    print(f"  邮箱 {n_email}/{len(leads)}  电话 {n_phone}/{len(leads)}  官网 {n_web}/{len(leads)}")


if __name__ == "__main__":
    main()

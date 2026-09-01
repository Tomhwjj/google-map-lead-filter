#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
背调脚本：读 fetch_gmaps.py 输出的 CSV，抓每个线索的官网首页 + contact 页，
提取标题 / meta 描述 / 邮箱 / 正文，输出 JSON 供 Claude 判断。

用法:
    python backfill.py leads.csv --out backfill.json
"""
import argparse
import csv
import json
import random
import re
import sys
import time

from playwright.sync_api import sync_playwright

DEFAULT_PROXY = ""  # 官网一般可直连，默认不走代理；需要时用 --proxy 指定
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 常见联系方式页路径（按优先级，找到邮箱即停）
CONTACT_PATHS = ["contact", "kontakt", "impressum", "about", "about-us",
                 "ueber-uns", "contact-us", "en/contact", "de/kontakt"]

# 品牌/产品页路径（品牌未命中时抓，判断官网代理哪些品牌）
BRAND_PATHS = ["brands", "products", "inverters", "solar-panels", "manufacturers"]


def extract_emails(text):
    return sorted(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)))


def find_brands(text, brands):
    """在文本中搜索品牌关键词，返回 {品牌: 上下文片段}。"""
    found = {}
    low = text.lower()
    for b in brands:
        bidx = low.find(b.lower())
        if bidx >= 0:
            start = max(0, bidx - 100)
            end = min(len(text), bidx + 100)
            found[b] = text[start:end].strip()
    return found


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="背调：抓官网提取邮箱/正文")
    ap.add_argument("csv", help="fetch_gmaps.py 输出的 CSV")
    ap.add_argument("--out", default="backfill.json", help="输出 JSON 路径")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="代理地址（默认直连）")
    ap.add_argument("--max", type=int, default=0, help="最多背调条数 (0=全部)")
    ap.add_argument("--brands", default="", help="我方合作品牌，逗号分隔，如 'Deye,Sungrow'")
    args = ap.parse_args()
    brands = [b.strip() for b in (args.brands or "").split(",") if b.strip()]

    leads = []
    with open(args.csv, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            leads.append(row)

    results = []
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if args.proxy:
            launch_kwargs["proxy"] = {"server": args.proxy}
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1280, "height": 800})
        page = ctx.new_page()

        for i, lead in enumerate(leads):
            if args.max and i >= args.max:
                break
            name = lead.get("company_name", "").strip()
            website = (lead.get("website") or "").strip()
            rec = {
                "company_name": name,
                "website": website,
                "title": "",
                "meta": "",
                "emails": [],
                "brands_found": [],
                "brands_context": {},
                "body": "",
                "error": "",
            }
            if website.startswith("http"):
                texts = []
                try:
                    page.goto(website, timeout=20000, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    time.sleep(random.uniform(1, 2))
                    rec["title"] = page.title()
                    rec["meta"] = page.evaluate(
                        "() => document.querySelector('meta[name=\"description\"]')?.content || ''"
                    )
                    rec["emails"] = extract_emails(page.content())
                    texts.append((page.inner_text("body") or "")[:5000])
                except Exception as e:
                    rec["error"] = str(e)[:200]

                # 联系方式页（找到邮箱即停）
                for path in CONTACT_PATHS:
                    if rec["emails"]:
                        break
                    try:
                        url = website.rstrip("/") + "/" + path
                        page.goto(url, timeout=15000, wait_until="domcontentloaded")
                        time.sleep(random.uniform(1, 2))
                        c = page.content()
                        rec["emails"] = sorted(set(rec["emails"] + extract_emails(c)))
                        texts.append((page.inner_text("body") or "")[:3000])
                    except Exception:
                        pass

                rec["body"] = " ".join(texts)[:8000]
                if brands and rec["body"]:
                    ctx = find_brands(rec["body"], brands)
                    rec["brands_found"] = list(ctx.keys())
                    rec["brands_context"] = ctx

                # 品牌页（品牌未命中时再抓，命中即停）
                if brands and not rec["brands_found"]:
                    for path in BRAND_PATHS:
                        if rec["brands_found"]:
                            break
                        try:
                            url = website.rstrip("/") + "/" + path
                            page.goto(url, timeout=10000, wait_until="domcontentloaded")
                            try:
                                page.wait_for_load_state("networkidle", timeout=4000)
                            except Exception:
                                pass
                            time.sleep(random.uniform(0.3, 0.6))
                            texts.append((page.inner_text("body") or "")[:3000])
                            ctx = find_brands(" ".join(texts), brands)
                            rec["brands_found"] = list(ctx.keys())
                            rec["brands_context"] = ctx
                        except Exception:
                            pass
            else:
                rec["error"] = "no website"

            results.append(rec)
            print(f"[{i + 1}/{len(leads)}] {name}: {len(rec['emails'])} emails, "
                  f"brands={rec['brands_found']}", flush=True)

        browser.close()

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"背调完成: {len(results)} 条 -> {args.out}")


if __name__ == "__main__":
    main()

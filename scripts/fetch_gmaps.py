#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
抓取 Google Maps 搜索结果，解析商家卡片，输出 CSV。

v2（2026-09-12 WorkBuddy）：多查询批处理 + locale + 跨查询去重 + 断点续跑。
  背景：Google Maps 单查询 feed 硬上限 ~120 条（实际稳定 50-100），波兰全国只跑
  3 个大词只挖到 327 家——市调口径下认证安装商就有 1500+。突破靠「关键词矩阵」：
  品类 × 客户类型 × 城市，一次跑几十上百个精准词，每词抓 50 条（合规上限）。

用法:
    # 单查询（向后兼容，等价旧版）
    python fetch_gmaps.py "battery storage distributor Hamburg" --out leads.csv
    # 多查询一次跑（同一浏览器实例，查询间随机延迟 5-9 秒）
    python fetch_gmaps.py "hurtownik magazyn energii Warszawa" "instalator fotowoltaiki Kraków" --out pl.csv
    # 从文件读关键词矩阵（每行一个），本地化界面+结果语言
    python fetch_gmaps.py --queries-file kw_pl.txt --locale pl-PL --out pl_gmaps.csv
    # 断点续跑（中断后重跑同命令加 --resume，已完成的查询自动跳过）
    python fetch_gmaps.py --queries-file kw_pl.txt --resume --out pl_gmaps.csv

依赖: playwright (pip install playwright && playwright install chromium)
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.parse
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import extract_maps_category, map_maps_category  # noqa: E402

# 默认走本机代理（访问 Google 需要），可用 --proxy 覆盖
DEFAULT_PROXY = "http://127.0.0.1:33210"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# query 列 = 产出该条的搜索词（获客源效果分析用：哪个词/哪类词命中多）
# maps_category / customer_type（2026-09-22 加，task_issues #14 item3 源头修复）：
#   卡片自带类目（div.W4Efsd 首块，如 "Solar energy equipment supplier · 地址"）。
#   maps_category 存**原文**（抓取证据），customer_type 存白名单映射后的渠道角色
#   （core.MAPS_CATEGORY_ROLE，表外留空交手工判，不猜）。此前类目只烂在 raw_text 里，
#   入库后 customer_type 全空 → 渠道档 0 分。
CSV_FIELDS = ["company_name", "rating", "phone", "website",
              "google_maps_url", "raw_text", "query", "country",
              "maps_category", "customer_type"]


def extract_real_url(href):
    """Google Maps 的 Website 链接是重定向 URL，解析出真实网址。

    三种跳转形态：
      1. 广告跳转 /aclk?...         真实网址藏在 adurl 参数；无 adurl 即纯广告脏数据，返回 ""（丢弃）
      2. 普通外链 /url?q=... 或 url?q= 真实网址在 q 参数
      3. 其他直链                 原样返回
    """
    if not href:
        return ""
    if "/aclk?" in href or "aclk?" in href:
        qs = parse_qs(urlparse(href).query)
        if "adurl" in qs:
            return unquote(qs["adurl"][0])
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

    # 官网：两种形态（v2 教训：hl 本地化模式下是直链，不是 /url? 重定向）
    #   a. /url?q=... 重定向（默认 en 界面）
    #   b. 外部直链 http(s)（hl 本地化界面）——排除 Google 内部链接与广告跳转
    website = ""
    for wa in article.query_selector_all("a"):
        href = wa.get_attribute("href") or ""
        if not href:
            continue
        if "/url?" in href or "url?q=" in href:
            website = extract_real_url(href)
            break
        if href.startswith("http") and "google.com" not in href \
                and "/aclk?" not in href and "maps.google" not in href:
            website = href
            break

    # 类目：卡片里紧跟评分行的 "类目 · 地址"，取 '·' 前段。
    # 解析走 core.extract_maps_category —— 与存量回填（scan 历史 CSV 的 raw_text）
    # **同一套逻辑**，避免线上抓取与历史回填两套口径漂移。
    maps_category = extract_maps_category(text)

    return {
        "company_name": name,
        "rating": rating,
        "phone": phone,
        "website": website,
        "google_maps_url": maps_url,
        "raw_text": text,  # 整块文本，含品类/地址/营业状态，供后续精判
        "maps_category": maps_category,          # 类目原文（证据，可复核）
        "customer_type": map_maps_category(maps_category),  # 白名单映射，表外为空
    }


def _dedup_key(data):
    """跨查询去重键：maps_url 优先，退而求公司名。"""
    return data["google_maps_url"] or data["company_name"]


def _load_queries(args):
    """合并 --queries-file 与位置参数，去空去重保序。"""
    queries = []
    if args.queries_file:
        with open(args.queries_file, encoding="utf-8-sig") as f:
            for line in f:
                q = line.strip()
                if q and not q.startswith("#"):
                    queries.append(q)
    queries.extend(q.strip() for q in args.queries if q.strip())
    seen = set()
    out = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _ckpt_path(out):
    return os.path.splitext(out)[0] + ".ckpt.json"


def _load_checkpoint(args, n_queries):
    """断点续跑：恢复已完成的查询集合与跨查询去重 seen。

    ckpt 结构：{"done_queries": [...], "seen_keys": [...]}"""
    if not args.resume:
        return set(), set()
    p = _ckpt_path(args.out)
    if not os.path.exists(p):
        return set(), set()
    try:
        with open(p, encoding="utf-8") as f:
            ck = json.load(f)
        done = {q for q in ck.get("done_queries", [])}
        seen = set(ck.get("seen_keys", []))
        print(f"[resume] 载入断点：已完成 {len(done)} 个查询，已见 {len(seen)} 条")
        return done, seen
    except Exception as e:
        print(f"[resume] 断点文件损坏（{e}），从头跑")
        return set(), set()


def _save_checkpoint(out, done_queries, seen):
    p = _ckpt_path(out)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"done_queries": sorted(done_queries),
                   "seen_keys": sorted(seen)}, f, ensure_ascii=False)


def _append_rows(out, rows):
    """把本查询结果追加写入 CSV（无表头时先写表头；自动创建父目录）。"""
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    exists = os.path.exists(out) and os.path.getsize(out) > 0
    with open(out, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        for r in rows:
            w.writerow(r)


def scrape_query(page, query, args, seen):
    """跑单个查询：打开 Maps 搜索页 → 滚动加载 → 解析 → 返回本查询新命中。"""
    url = ("https://www.google.com/maps/search/"
           + urllib.parse.quote(query))
    if args.hl:
        url += "?hl=" + args.hl
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    time.sleep(random.uniform(5, 8))  # 等首屏渲染

    feed = page.query_selector('[role="feed"]')
    if not feed:
        print(f"  [warn] 未找到结果 feed（可能是验证页/无结果）: {query}")
        return []

    fresh = []
    prev_count = -1
    stall = 0
    while len(fresh) < args.max and stall < 4:
        for a in page.query_selector_all('div[role="article"]'):
            data = parse_article(a)
            data["query"] = query
            data["country"] = getattr(args, "country", "") or ""
            key = _dedup_key(data)
            if key and key not in seen:
                seen.add(key)
                fresh.append(data)
                if len(fresh) >= args.max:
                    break

        if len(fresh) == prev_count:
            stall += 1
        else:
            stall = 0
        prev_count = len(fresh)

        if len(fresh) >= args.max:
            break
        if feed:
            feed.evaluate("el => el.scrollTop = el.scrollHeight")
            time.sleep(random.uniform(2, 3))  # 限速，防反爬（合规：2-3 秒）
        else:
            break
    return fresh


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="抓取 Google Maps 搜索结果 -> CSV（v2 多查询批处理）")
    ap.add_argument("queries", nargs="*", help="搜索词（可多个），如 'hurtownik magazyn energii Warszawa'")
    ap.add_argument("--queries-file", default=None,
                    help="关键词文件（每行一个，# 开头为注释），与位置参数可混用")
    ap.add_argument("--max", type=int, default=50,
                    help="每个查询最多抓取条数（默认 50，合规上限）")
    ap.add_argument("--out", default="leads.csv", help="输出 CSV 路径（追加写）")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="代理地址")
    ap.add_argument("--locale", default="en-US",
                    help="浏览器 locale（如 pl-PL/de-DE），影响界面与本地化结果排序")
    ap.add_argument("--hl", default=None,
                    help="Maps 界面语言参数（hl=pl/de/...），默认从 --locale 推导")
    ap.add_argument("--country", default="",
                    help="国家码（如 PL/DE），写入 CSV country 列供 merge 入库")
    ap.add_argument("--resume", action="store_true",
                    help="断点续跑：跳过 <out>.ckpt.json 里已完成的查询")
    args = ap.parse_args()

    queries = _load_queries(args)
    if not queries:
        ap.error("至少提供一个查询（位置参数或 --queries-file）")
    if args.hl is None and "-" in args.locale:
        args.hl = args.locale.split("-")[0]

    done, seen = _load_checkpoint(args, len(queries))
    pending = [q for q in queries if q not in done]

    print(f"共 {len(queries)} 个查询（断点已完成 {len(done)}，本次待跑 {len(pending)}）")
    total_new = 0
    per_query_stats = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, proxy={"server": args.proxy})
        ctx = browser.new_context(user_agent=UA, locale=args.locale,
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        for i, query in enumerate(pending, 1):
            print(f"[{i}/{len(pending)}] {query}")
            try:
                fresh = scrape_query(page, query, args, seen)
            except Exception as e:
                print(f"  [error] 查询失败（跳过继续）: {type(e).__name__}: {e}")
                fresh = []
            if fresh:
                _append_rows(args.out, fresh)
                done.add(query)
                _save_checkpoint(args.out, done, seen)
            total_new += len(fresh)
            per_query_stats.append((query, len(fresh)))
            print(f"  -> {len(fresh)} 条新命中（累计 {total_new}）")

            # 合规：查询之间延迟，不并发
            if i < len(pending):
                time.sleep(random.uniform(5, 9))

        browser.close()

    print("\n===== 汇总 =====")
    for q, n in per_query_stats:
        print(f"  {n:>3}  {q}")
    print(f"抓取完成: 本次新命中 {total_new} 条 -> {args.out}（断点文件 {_ckpt_path(args.out)}，--resume 可续跑）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Google Search 批量抓取（走 AnySearch 聚合搜索 API，零额外成本）。

用于「多源挖掘」的第 1 源：批量搜索品牌/品类 + 国家 + 渠道词，命中即出线索。
相比手工 WebSearch，一次可批量跑几十个关键词，结构化输出线索 CSV。

用法:
    # 命令行传多个关键词
    python search_leads.py "grossiste photovoltaïque France" "Sungrow distributeur France" --out search.csv

    # 从文件读关键词（每行一个）
    python search_leads.py --queries-file keywords.txt --out search.csv

    # 指定语言 / 每个词结果数（1-10）
    python search_leads.py "installateur photovoltaïque Lyon" --language fr --max-results 10

输出 CSV 字段: company_name, website, snippet, query, source_url
  - company_name / website 来自搜索结果标题/URL（含噪声，需后续背调精判）
  - snippet 摘要用于初筛判断是否经销商
  - query 记录来源关键词（可追溯）

认证: API key 优先级 --api-key > 环境变量 ANYSEARCH_API_KEY > anysearch skill 的 .env。
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.request

API_BASE_URL = os.environ.get("ANYSEARCH_API_BASE_URL", "https://api.anysearch.com").rstrip("/")
CLIENT_HEADER = "skill/3.0.1"
CSV_FIELDS = ["company_name", "website", "snippet", "query", "source_url"]

# anysearch skill 的 .env 候选路径（读取账号 key）
_ENV_CANDIDATES = [
    os.path.expanduser("~/.agents/skills/anysearch/.env"),
    os.path.expanduser("~/.agents/skills/anysearch/scripts/.env"),
    os.path.expanduser("~/.claude/skills/anysearch/.env"),
]


def _load_anysearch_key():
    """从 anysearch skill 的 .env 读 ANYSEARCH_API_KEY（复用其账号 key）。"""
    for p in _ENV_CANDIDATES:
        if os.path.isfile(p):
            with open(p, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    if k.strip().strip("﻿") == "ANYSEARCH_API_KEY" and v.strip().strip("\"'"):
                        return v.strip().strip("\"'")
    return ""


def search(query, api_key, language="", max_results=10):
    """调 POST /v1/search，返回 [(title, url, snippet), ...]。"""
    payload = {"query": query, "max_results": max(1, min(max_results, 10))}
    if language:
        payload["language"] = language
    headers = {"Content-Type": "application/json", "X-Anysearch-Client": CLIENT_HEADER}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        f"{API_BASE_URL}/v1/search",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        envelope = json.loads(resp.read().decode("utf-8"))
    data = envelope.get("data") or {}
    results = data.get("results") or []
    out = []
    for r in results:
        title = (r.get("title") or "").strip()
        url = (r.get("url") or "").strip()
        snippet = (r.get("content") or r.get("snippet") or "").strip()
        if url:
            out.append((title, url, snippet))
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Google Search 批量抓取（AnySearch API）")
    ap.add_argument("queries", nargs="*", help="搜索关键词（可多个）")
    ap.add_argument("--queries-file", help="关键词文件，每行一个")
    ap.add_argument("--out", default="search_leads.csv", help="输出 CSV")
    ap.add_argument("--language", default="", help="语言代码，如 fr/de")
    ap.add_argument("--max-results", type=int, default=10, help="每个词结果数 1-10")
    ap.add_argument("--api-key", default="", help="API key（默认读 anysearch .env）")
    ap.add_argument("--delay", type=float, default=1.5, help="每个词之间延迟秒数（防限速）")
    args = ap.parse_args()

    queries = list(args.queries)
    if args.queries_file:
        with open(args.queries_file, encoding="utf-8") as f:
            queries.extend(line.strip() for line in f if line.strip())

    api_key = args.api_key or os.environ.get("ANYSEARCH_API_KEY") or _load_anysearch_key()
    if not api_key:
        print("⚠️ 未找到 API key，走匿名模式（限速更严）。建议确认 anysearch .env。", file=sys.stderr)

    rows = []
    seen_url = set()
    for qi, q in enumerate(queries, 1):
        try:
            items = search(q, api_key, args.language, args.max_results)
        except Exception as e:
            print(f"[{qi}/{len(queries)}] {q}: 失败 {e}", file=sys.stderr)
            items = []
        for title, url, snippet in items:
            if url in seen_url:
                continue
            seen_url.add(url)
            rows.append({
                "company_name": title,
                "website": url,
                "snippet": snippet[:500],
                "query": q,
                "source_url": url,
            })
        print(f"[{qi}/{len(queries)}] {q}: {len(items)} 条", flush=True)
        if qi < len(queries):
            time.sleep(args.delay)

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"搜索完成: {len(queries)} 词 -> {len(rows)} 条（去重后） -> {args.out}")


if __name__ == "__main__":
    main()

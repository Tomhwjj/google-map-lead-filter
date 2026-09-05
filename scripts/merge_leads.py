#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并多个 fetch_gmaps.py 输出的 CSV，按官网域名去重，文件名作为城市标签。

用法:
    # 目录模式：读目录下所有 *.csv，文件名(去扩展名)当城市标签
    python merge_leads.py D:/Agent/tmp/fr_gmaps/ --out merged.csv

    # 文件模式：显式列文件，城市标签取文件名
    python merge_leads.py a.csv b.csv --out merged.csv

输出字段: company_name, city, rating, phone, website, google_maps_url, raw_text
"""
import argparse
import csv
import glob
import os
import sys
from urllib.parse import urlparse

# 字段并集：兼容三种来源（enf 有 country/email/customer_type/address/profile_url；
# gmaps 有 rating/google_maps_url/raw_text；search 有 snippet/source_url），
# 每种来源缺的字段留空，绝不丢字段（2026-09-05 教训：字段丢失导致入库 country 全空）
OUT_FIELDS = ["company_name", "country", "city", "customer_type",
              "phone", "email", "website", "address", "profile_url",
              "source_url", "rating", "google_maps_url", "raw_text"]


def domain_of(url):
    """从 URL 提取主域名（去 www），用于去重。"""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc or urlparse("//" + url).netloc
        host = host.lower().lstrip("www.").lstrip(".")
        # 去掉端口
        return host.split(":")[0]
    except Exception:
        return url.lower()


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="合并多个 fetch_gmaps CSV 并按官网去重")
    ap.add_argument("inputs", nargs="+", help="CSV 文件 或 一个目录（读目录下所有 *.csv）")
    ap.add_argument("--out", default="merged.csv", help="输出 CSV 路径")
    args = ap.parse_args()

    # 展开输入：目录 -> 目录下所有 csv
    files = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            files.extend(sorted(glob.glob(os.path.join(inp, "*.csv"))))
        else:
            files.append(inp)

    seen_domain = set()
    seen_name = set()
    merged = []

    for fp in files:
        city = os.path.splitext(os.path.basename(fp))[0]  # 文件名当城市标签
        with open(fp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            website = (r.get("website") or "").strip()
            name = (r.get("company_name") or "").strip()
            dom = domain_of(website)
            # 去重：优先官网域名，其次公司名
            if dom and dom in seen_domain:
                continue
            if not dom and name and name.lower() in seen_name:
                continue
            if dom:
                seen_domain.add(dom)
            if name:
                seen_name.add(name.lower())
            merged.append({
                "company_name": name,
                "city": city,
                "rating": (r.get("rating") or "").strip(),
                "phone": (r.get("phone") or "").strip(),
                "website": website,
                "google_maps_url": (r.get("google_maps_url") or "").strip(),
                "raw_text": (r.get("raw_text") or "").strip(),
            })

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for m in merged:
            w.writerow(m)

    print(f"合并完成: {len(files)} 个文件 -> {len(merged)} 条（去重后） -> {args.out}")


if __name__ == "__main__":
    main()

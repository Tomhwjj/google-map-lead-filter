#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
合并多个 fetch_gmaps.py / fetch_enf.py / search_leads.py 输出的 CSV，按去重键合并。

去重键（硬证据才自动合并，弱证据只标疑似，绝不误杀）：
  L0  domain 完全相同                      → 自动合并
  L1  邮箱后缀相同（企业自有域，剔除免费域）→ 自动合并
      name_key（无官网时公司名精确匹配）    → 自动合并（既有逻辑）
  L2  电话完全相同（归一化非空）           → 只标疑似，输出 suspected_dups.csv，不自动合并
  L3  公司名相似度                         → 暂不实现（误杀风险高，待单独设计停用词表+阈值）

用法:
    # 目录模式：读目录下所有 *.csv，文件名(去扩展名)当城市标签
    python merge_leads.py D:/Agent/tmp/fr_gmaps/ --out merged.csv

    # 文件模式：显式列文件，城市标签取文件名
    python merge_leads.py a.csv b.csv --out merged.csv

输出字段: company_name, country, city, customer_type, phone, email, website,
         address, profile_url, source_url, rating, google_maps_url, raw_text
"""
import argparse
import csv
import glob
import os
import re
import sys
from urllib.parse import urlparse

# 字段并集：兼容三种来源（enf 有 country/email/customer_type/address/profile_url；
# gmaps 有 rating/google_maps_url/raw_text；search 有 snippet/source_url），
# 每种来源缺的字段留空，绝不丢字段（2026-09-05 教训：字段丢失导致入库 country 全空）
OUT_FIELDS = ["company_name", "country", "city", "customer_type",
              "phone", "email", "website", "address", "profile_url",
              "source_url", "rating", "google_maps_url", "raw_text"]

# 免费/公共邮箱域：邮箱后缀做「同公司」识别时排除，避免两家不同公司共用 gmail 误判
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.pl", "yahoo.de", "aol.com", "icloud.com",
    "me.com", "mac.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
    "gmx.net", "web.de", "mail.com", "zoho.com", "yandex.com", "yandex.ru",
    "qq.com", "163.com", "126.com", "sina.com",
    "wp.pl", "o2.pl", "interia.pl", "onet.pl", "poczta.onet.pl", "tlen.pl",
    "free.fr", "orange.fr", "laposte.net", "sfr.fr", "wanadoo.fr",
    "t-online.de", "freenet.de",
}


def domain_of(url):
    """从 URL 提取主域名（去 www），用于去重。"""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc or urlparse("//" + url).netloc
        host = host.lower().lstrip("www.").lstrip(".")
        return host.split(":")[0]
    except Exception:
        return url.lower()


def email_suffixes(email):
    """从邮箱串提取企业自有域名后缀（去重、剔除免费域）。"""
    out = set()
    for e in (email or "").split(","):
        e = e.strip().lower()
        if not e or "@" not in e:
            continue
        dom = e.rpartition("@")[2]
        if dom and dom not in FREE_EMAIL_DOMAINS:
            out.add(dom)
    return out


def normalize_phone(phone):
    """电话归一化：去所有非数字（用于「同电话」比对）。"""
    return re.sub(r"\D", "", phone or "")


def _make_rec(r, file_city):
    """从源行生成完整 13 字段记录（city 源优先，缺则用文件名）。"""
    return {
        "company_name": (r.get("company_name") or "").strip(),
        "country": (r.get("country") or "").strip(),
        "city": (r.get("city") or "").strip() or file_city,
        "customer_type": (r.get("customer_type") or "").strip(),
        "phone": (r.get("phone") or "").strip(),
        "email": (r.get("email") or "").strip(),
        "website": (r.get("website") or "").strip(),
        "address": (r.get("address") or "").strip(),
        "profile_url": (r.get("profile_url") or "").strip(),
        "source_url": (r.get("source_url") or "").strip(),
        "rating": (r.get("rating") or "").strip(),
        "google_maps_url": (r.get("google_maps_url") or "").strip(),
        "raw_text": (r.get("raw_text") or "").strip(),
    }


# 多值字段：合并时取并集（逗号分隔去重），其余字段非空互补
MULTI_FIELDS = {"email", "phone"}


def _merge_multi(a, b):
    """合并两个逗号分隔的多值串，去重保序（忽略大小写去重）。"""
    seen = set()
    out = []
    for v in ((a or "") + "," + (b or "")).split(","):
        v = v.strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return ",".join(out)


def _merge_into(target, rec):
    """把 rec 的字段互补合并进 target（email/phone 取并集，其余非空互补）。"""
    for field in OUT_FIELDS:
        if field in MULTI_FIELDS:
            target[field] = _merge_multi(target.get(field), rec.get(field))
        else:
            v = rec.get(field) or ""
            if v and not (target.get(field) or ""):
                target[field] = v


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="合并多源 CSV 并按去重键去重（domain+邮箱后缀自动合并，电话标疑似）")
    ap.add_argument("inputs", nargs="+", help="CSV 文件 或 一个目录（读目录下所有 *.csv）")
    ap.add_argument("--out", default="merged.csv", help="输出 CSV 路径")
    args = ap.parse_args()

    files = []
    for inp in args.inputs:
        if os.path.isdir(inp):
            files.extend(sorted(glob.glob(os.path.join(inp, "*.csv"))))
        else:
            files.append(inp)

    merged = []
    seen_domain = {}   # domain -> idx
    seen_suffix = {}   # 邮箱后缀 -> idx
    seen_name = {}     # name_key（无域名）-> idx
    seen_phone = {}    # 归一化电话 -> idx（L2 只标疑似，不合并）
    suspected = []     # 疑似重复清单（电话相同）

    for fp in files:
        file_city = os.path.splitext(os.path.basename(fp))[0]
        with open(fp, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            rec = _make_rec(r, file_city)
            dom = domain_of(rec["website"])
            name_key = re.sub(r"\s+", "", rec["company_name"].lower())
            suffixes = email_suffixes(rec["email"])
            phone = normalize_phone(rec["phone"])

            # 找命中：domain > 邮箱后缀 > name（无域名）
            idx = None
            if dom and dom in seen_domain:
                idx = seen_domain[dom]
            elif suffixes:
                for suf in suffixes:
                    if suf in seen_suffix:
                        idx = seen_suffix[suf]
                        break
            elif not dom and name_key and name_key in seen_name:
                idx = seen_name[name_key]

            if idx is not None:
                # L0/L1/name 命中：自动合并字段（非空互补）
                _merge_into(merged[idx], rec)
                if dom:
                    seen_domain.setdefault(dom, idx)
                for suf in suffixes:
                    seen_suffix.setdefault(suf, idx)
            else:
                # L2 电话相同但 domain/邮箱后缀/name 都不同：只标疑似，不合并
                if phone and phone in seen_phone:
                    j = seen_phone[phone]
                    suspected.append({
                        "name_a": merged[j]["company_name"],
                        "website_a": merged[j]["website"],
                        "name_b": rec["company_name"],
                        "website_b": rec["website"],
                        "signal": "phone",
                        "phone": rec["phone"],
                    })
                merged.append(rec)
                idx = len(merged) - 1
                if dom:
                    seen_domain[dom] = idx
                for suf in suffixes:
                    seen_suffix[suf] = idx
                if not dom and name_key:
                    seen_name[name_key] = idx
                if phone:
                    seen_phone[phone] = idx

    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS)
        w.writeheader()
        for m in merged:
            w.writerow(m)

    print(f"合并完成: {len(files)} 个文件 -> {len(merged)} 条（去重后） -> {args.out}")

    if suspected:
        sp = os.path.splitext(args.out)[0] + "_suspected_dups.csv"
        with open(sp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["name_a", "website_a", "name_b",
                                              "website_b", "signal", "phone"])
            w.writeheader()
            w.writerows(suspected)
        print(f"⚠️ 疑似同公司（电话相同，待人工判定）: {len(suspected)} 对 -> {sp}")


if __name__ == "__main__":
    main()

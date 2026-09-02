#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评分脚本：根据线索字段自动计算两套评分（头部模式 + 长尾模式）。

两套评分体系（总分各 100）：
  头部模式（啃大客户）：渠道 25 / 规模 20 / 触达 15 / 活跃 10
  长尾模式（铺小客户）：渠道 20 / 规模 5 / 触达 20 / 活跃 10 / 开发难度 15

核心口径：
  - 产品匹配 30：卖 Deye=30（存量，直接深化）> 卖竞品=27（增量，替换机会）> 纯品类=24（基础）
  - 触达：电话(可加 WhatsApp) > Facebook > 邮箱（底线）。电话是获客主路径，权重最高。
  - 开发难度（仅长尾，基于规模反向）：中 > 小 > 大；大公司无联系方式直接 0。

用法:
    python score_leads.py leads.json --out leads_scored.json

输入 JSON 每条字段：company_name, country, city, website, phone, email, facebook(可选),
    linkedin, customer_type, brands_found, score_detail(含"规模"/"活跃"旧值), reason 等。
输出：在输入基础上新增 sells_deye、score(头部,重算)、grade(头部)、score_detail(头部)、
    score_lt(长尾)、grade_lt(长尾)、score_detail_lt(长尾)。
"""
import argparse
import json
import sys

# 贴牌映射：我方品牌 Deye 的贴牌/代工品牌，命中即视为卖 Deye
DEYE_BRANDS = ("deye", "sunsynk", "sol-ark", "inge")

# 渠道角色分类关键词（小写匹配 customer_type）
CHANNEL_DISTRIBUTOR = ("distribut", "grossiste", "wholesale", "grossh", "importer",
                       "importateur", "import", "fournisseur", "supplier", "epc")
CHANNEL_INSTALLER = ("installat", "install", "revendeur", "reseller", "artisan", "energie")

# 触达分值（头部 / 长尾）
CONTACT_HEAD = {"phone": 8, "facebook": 4, "email": 3}   # 合计 15
CONTACT_TAIL = {"phone": 12, "facebook": 5, "email": 3}  # 合计 20

# 渠道分值（头部 / 长尾）
CHANNEL_HEAD = {"distributor": 25, "installer": 15, "retail": 0}
CHANNEL_TAIL = {"distributor": 20, "installer": 12, "retail": 0}

# 开发难度（仅长尾，基于规模旧值 0-20）
DEV_MID = (6, 12)          # 中型规模区间（最好开发）
DEV_SCORES = {"mid": 15, "small": 9, "big_contact": 4, "big_nocontact": 0}

GRADE_THRESHOLDS = [(80, "A"), (50, "B"), (0, "C")]


def grade_of(score):
    for th, g in GRADE_THRESHOLDS:
        if score >= th:
            return g
    return "C"


def sells_deye(brands):
    return any(any(db in b.lower() for db in DEYE_BRANDS) for b in (brands or []))


def product_score(brands, ctype):
    """产品匹配 30：Deye 30 / 竞品 27 / 品类 24（默认，初筛已过滤不相关）。"""
    if sells_deye(brands):
        return 30
    if brands:
        return 27
    return 24


def classify_channel(ctype):
    c = (ctype or "").lower()
    if any(k in c for k in CHANNEL_DISTRIBUTOR):
        return "distributor"
    if any(k in c for k in CHANNEL_INSTALLER):
        return "installer"
    return "retail"


def contact_score(phone, facebook, email, weights):
    return (weights["phone"] if phone else 0) + \
           (weights["facebook"] if facebook else 0) + \
           (weights["email"] if email else 0)


def dev_score(old_scale, has_contact):
    """开发难度 15（长尾）：中 > 小 > 大；大公司无联系方式 = 0。"""
    lo, hi = DEV_MID
    if lo <= old_scale <= hi:
        return DEV_SCORES["mid"]
    if old_scale < lo:
        return DEV_SCORES["small"]
    # 大公司（>hi）：有联系方式给 4，无联系方式直接 0
    return DEV_SCORES["big_contact"] if has_contact else DEV_SCORES["big_nocontact"]


def scale_basis(scale):
    """规模依据：员工数 + 覆盖范围 → 档位（0-20）。"""
    if scale >= 18:
        return "大型 · 跨国/全国批发"
    if scale >= 13:
        return "中大型 · 全国覆盖"
    if scale >= 6:
        return "中型 · 区域覆盖"
    if scale >= 1:
        return "小型 · 本地/区域"
    return "未确认"


def active_basis(active):
    """活跃依据：近期动态信号（招聘/新闻/社媒/展会），0-10。"""
    if active >= 8:
        return "近6月有招聘/新闻/社媒/展会信号"
    if active >= 5:
        return "近1年有更新"
    if active >= 1:
        return "官网静态，无明显近期动态"
    return "未确认"


def contact_basis(phone, facebook, email):
    parts = []
    if phone:
        parts.append("电话")
    if facebook:
        parts.append("FB")
    if email:
        parts.append("邮箱")
    return "+".join(parts) if parts else "无联系方式"


def score_lead(lead):
    phone = bool((lead.get("phone") or "").strip())
    email = bool((lead.get("email") or "").strip())
    facebook = bool((lead.get("facebook") or "").strip())
    brands = lead.get("brands_found") or []
    ctype = lead.get("customer_type") or ""
    old = lead.get("score_detail") or {}
    old_scale = int(old.get("规模", 0) or 0)
    old_active = int(old.get("活跃", 0) or 0)

    prod = product_score(brands, ctype)
    ch = classify_channel(ctype)
    has_contact = phone or email or facebook

    prod_basis = "已卖Deye·存量" if sells_deye(brands) else \
                 ("卖竞品·增量" if brands else "品类相关")
    ch_basis = {"distributor": "批发/分销商", "installer": "安装商/零售商", "retail": "零售"}[ch]
    cont_basis = contact_basis(phone, facebook, email)
    scale_b = scale_basis(old_scale)
    active_b = active_basis(old_active)

    # --- 头部模式 ---
    chan_h = CHANNEL_HEAD[ch]
    scale_h = old_scale
    cont_h = contact_score(phone, facebook, email, CONTACT_HEAD)
    active_h = old_active
    score_h = prod + chan_h + scale_h + cont_h + active_h
    detail_h = {"产品匹配": prod, "渠道": chan_h, "规模": scale_h,
                "触达": cont_h, "活跃": active_h}
    basis_h = {"产品匹配": prod_basis, "渠道": ch_basis, "规模": scale_b,
               "触达": cont_basis, "活跃": active_b}

    # --- 长尾模式 ---
    chan_t = CHANNEL_TAIL[ch]
    scale_t = round(old_scale * 5 / 20)
    cont_t = contact_score(phone, facebook, email, CONTACT_TAIL)
    active_t = old_active
    dev_t = dev_score(old_scale, has_contact)
    if DEV_MID[0] <= old_scale <= DEV_MID[1]:
        dev_key = "mid"
    elif old_scale < DEV_MID[0]:
        dev_key = "small"
    else:
        dev_key = "big_contact" if has_contact else "big_nocontact"
    dev_basis = {"mid": "中型 · 最好开发", "small": "小型 · 可开发",
                 "big_contact": "大型 · 有联系方式", "big_nocontact": "大型 · 无联系方式 · 难"}[dev_key]
    score_t = prod + chan_t + scale_t + cont_t + active_t + dev_t
    detail_t = {"产品匹配": prod, "渠道": chan_t, "规模": scale_t,
                "触达": cont_t, "活跃": active_t, "开发难度": dev_t}
    basis_t = {"产品匹配": prod_basis, "渠道": ch_basis, "规模": scale_b,
               "触达": cont_basis, "活跃": active_b, "开发难度": dev_basis}

    return {
        "sells_deye": sells_deye(brands),
        "score": score_h, "grade": grade_of(score_h), "score_detail": detail_h, "score_basis": basis_h,
        "score_lt": score_t, "grade_lt": grade_of(score_t), "score_detail_lt": detail_t, "score_basis_lt": basis_t,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="计算头部/长尾两套评分")
    ap.add_argument("json", help="已背调的线索 JSON")
    ap.add_argument("--out", default="leads_scored.json", help="输出 JSON")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        leads = json.load(f)

    for l in leads:
        r = score_lead(l)
        l.update(r)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=1)

    from collections import Counter
    g_head = Counter(x["grade"] for x in leads)
    g_tail = Counter(x["grade_lt"] for x in leads)
    deye_n = sum(1 for x in leads if x["sells_deye"])
    print(f"评分完成: {len(leads)} 条 -> {args.out}")
    print(f"  头部模式分级: {dict(g_head)}")
    print(f"  长尾模式分级: {dict(g_tail)}")
    print(f"  卖 Deye: {deye_n} 家")


if __name__ == "__main__":
    main()

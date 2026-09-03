#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
评分脚本：根据线索字段自动计算两套评分（头部模式 + 长尾模式）。

两套评分体系（总分各 100）：
  头部模式（啃大客户）：产品匹配 30 / 渠道 25 / 规模 25 / 触达 20
  长尾模式（铺小客户）：产品匹配 30 / 渠道 20 / 规模 25 / 触达 25

核心口径（唯一来源 references/qualification-rules.md，本脚本不重复解释规则）：
  - 产品匹配 30：卖 Deye/贴牌=30（存量）> 卖竞品储能/逆变器=24（增量）> 无证据=0
  - 规模：经营痕迹三档（大型/中型/小型），不看精确员工数；头部越大越高，长尾中>小>大
  - 触达：电话(可WhatsApp) > 邮箱 > 仅官网，取最高档（不累加）；德国因地制宜由背调判断

用法:
    python score_leads.py leads.json --out leads_scored.json

输入 JSON 每条字段：company_name, country, city, website, phone, email,
    linkedin, customer_type, brands_found, reason 等。
    ⚠️ 规模三态输入（防幻觉）：
      - scale_tier(str)："large"/"mid"/"small"，经营痕迹硬证据 → 按档位
      - scale_estimated(bool)：背调过但经营痕迹不足 → 档位+「估」
      - backfilled(bool)：是否背调过。缺/未背调 → 未确认 → 中性分（按小型档，保守）
输出：在输入基础上新增 sells_deye、score(头部)、grade(头部)、score_detail(头部)、
    score_basis(头部)、score_lt(长尾)、grade_lt(长尾)、score_detail_lt(长尾)、score_basis_lt(长尾)。
"""
import argparse
import json
import re
import sys

# 贴牌映射：我方品牌 Deye 的贴牌/代工品牌，命中即视为卖 Deye（存量）。与 brand-mapping.md 一致
DEYE_BRANDS = ("deye", "sunsynk", "sol-ark", "inge", "fusion", "ohm", "noark")

# 渠道角色分类关键词（小写匹配 customer_type）
CHANNEL_DISTRIBUTOR = ("distribut", "grossiste", "wholesale", "grossh", "importer",
                       "importateur", "import", "fournisseur", "supplier", "epc")
CHANNEL_INSTALLER = ("installat", "install", "revendeur", "reseller", "artisan", "energie")

# 渠道分值（头部 / 长尾）
CHANNEL_HEAD = {"distributor": 25, "installer": 15, "retail": 0}
CHANNEL_TAIL = {"distributor": 20, "installer": 12, "retail": 0}

# 触达分值（头部 / 长尾）：电话 > 邮箱 > 仅官网，取最高档（不累加）
CONTACT_HEAD = {"phone": 20, "email": 14, "website": 8, "none": 0}
CONTACT_TAIL = {"phone": 25, "email": 18, "website": 10, "none": 0}
CONTACT_LABEL = {"phone": "电话(可WhatsApp)", "email": "邮箱", "website": "仅官网", "none": "无联系方式"}

# 规模分值（经营痕迹三档）：头部越大越高，长尾中>小>大
SCALE_HEAD = {"large": 25, "mid": 17, "small": 8}
SCALE_TAIL = {"large": 12, "mid": 25, "small": 20}
SCALE_LABEL = {"large": "大型", "mid": "中型", "small": "小型"}

GRADE_THRESHOLDS = [(80, "A"), (50, "B"), (0, "C")]


def grade_of(score):
    for th, g in GRADE_THRESHOLDS:
        if score >= th:
            return g
    return "C"


def _brand_match(brand, deye):
    """词边界匹配，避免 INGE 误命中 Ingenieur/springen、Fusion 误命中 FusionSolar。"""
    return re.search(r"(?<![a-z0-9])" + re.escape(deye) + r"(?![a-z0-9])", brand.lower()) is not None


def sells_deye(brands):
    return any(any(_brand_match(b, db) for db in DEYE_BRANDS) for b in (brands or []))


def product_score(brands):
    """产品匹配 30：卖 Deye/贴牌=30（存量）> 卖竞品=24（增量）> 无证据=0。"""
    if sells_deye(brands):
        return 30
    if brands:
        return 24
    return 0


def classify_channel(ctype):
    c = (ctype or "").lower()
    if any(k in c for k in CHANNEL_DISTRIBUTOR):
        return "distributor"
    if any(k in c for k in CHANNEL_INSTALLER):
        return "installer"
    return "retail"


def contact_tier(phone, email, website):
    """触达三档取最高：电话 > 邮箱 > 仅官网 > 无。"""
    if phone:
        return "phone"
    if email:
        return "email"
    if website:
        return "website"
    return "none"


def read_scale(lead):
    """规模三态：经营痕迹证据(scale_tier) > 估(scale_estimated) > 未确认(中性=小型档)。

    返回 (tier, basis)。tier ∈ {"large","mid","small",None}；None=未确认。
    """
    tier = (lead.get("scale_tier") or "").lower()
    if tier in SCALE_LABEL:
        suffix = " · 估" if lead.get("scale_estimated") else ""
        return tier, SCALE_LABEL[tier] + suffix
    if lead.get("backfilled"):
        return None, "未确认 · 官网无规模信息"
    return None, "未确认 · 未背调"


def scale_score(tier, table):
    """规模分：未确认(tier=None)按小型档保守给分，不归零不假装判断。"""
    return table[tier if tier in table else "small"]


def score_lead(lead):
    phone = bool((lead.get("phone") or "").strip())
    email = bool((lead.get("email") or "").strip())
    website = bool((lead.get("website") or "").strip())
    brands = lead.get("brands_found") or []
    ctype = lead.get("customer_type") or ""

    prod = product_score(brands)
    ch = classify_channel(ctype)
    tier, scale_b = read_scale(lead)
    ct = contact_tier(phone, email, website)

    prod_basis = "已卖Deye·存量" if sells_deye(brands) else \
                 ("卖竞品·增量" if brands else "无逆变器/储能证据")
    ch_basis = {"distributor": "批发/分销商", "installer": "安装商", "retail": "零售"}[ch]
    cont_basis = CONTACT_LABEL[ct]

    # --- 头部模式 ---
    chan_h = CHANNEL_HEAD[ch]
    cont_h = CONTACT_HEAD[ct]
    scale_h = scale_score(tier, SCALE_HEAD)
    score_h = prod + chan_h + scale_h + cont_h
    detail_h = {"产品匹配": prod, "渠道": chan_h, "规模": scale_h, "触达": cont_h}
    basis_h = {"产品匹配": prod_basis, "渠道": ch_basis, "规模": scale_b, "触达": cont_basis}

    # --- 长尾模式 ---
    chan_t = CHANNEL_TAIL[ch]
    cont_t = CONTACT_TAIL[ct]
    scale_t = scale_score(tier, SCALE_TAIL)
    score_t = prod + chan_t + scale_t + cont_t
    detail_t = {"产品匹配": prod, "渠道": chan_t, "规模": scale_t, "触达": cont_t}
    basis_t = {"产品匹配": prod_basis, "渠道": ch_basis, "规模": scale_b, "触达": cont_basis}

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

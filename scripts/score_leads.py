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
    python score_leads.py leads.json --out leads_scored.json --require-judged   # 硬闸门

输入 JSON 每条字段：company_name, country, city, website, phone, email,
    linkedin, customer_type, brands_found, reason 等。
    ⚠️ 规模三态输入（防幻觉）：
      - scale_tier(str)："large"/"mid"/"small"，经营痕迹硬证据 → 按档位
      - scale_estimated(bool)：背调过但经营痕迹不足 → 档位+「估」
      - backfilled(bool)：是否背调过。缺/未背调 → 未确认 → 中性分（按小型档，保守）
输出：在输入基础上新增 sells_deye、score(头部)、grade(头部)、score_detail(头部)、
    score_basis(头部)、score_lt(长尾)、grade_lt(长尾)、score_detail_lt(长尾)、score_basis_lt(长尾)、
    judge_gaps(本条缺哪些手工判输入)。

⚠️ 手工判闸门（2026-09-22 加，task_issues #14 item4）：
    三个手工判输入 customer_type / product_tier / scale_tier 缺任一项，该项就只剩机械
    兜底（渠道按零售 0 / 规模按小型档 / 产品 0），分数照样出得来但系统性偏低。**每次都打
    完整度体检**；加 --require-judged 则齐全率低于 --min-judged（默认 80%）直接 exit 2
    拒绝出分 —— 用来堵住「跳过手工判那一步直接出分」。
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


def dev_reason(grade, basis):
    """中文开发理由：一句话结论（等级 · 渠道 · 产品 · 触达）。

    basis = {'产品匹配':..., '渠道':..., '规模':..., '触达':...}（中文评分依据）
    例：A级 · 批发/分销商 · 已代理Deye · 电话(可WhatsApp)
    """
    ch = basis.get("渠道", "")
    prod = basis.get("产品匹配", "")
    cont = basis.get("触达", "")
    if prod.startswith("已卖Deye"):
        prod_short = "已代理Deye"
    elif "竞品" in prod:
        prod_short = "卖竞品/光伏"
    else:
        prod_short = "无产品证据"
    return f"{grade}级 · {ch} · {prod_short} · {cont}"


def _brand_match(brand, deye):
    """词边界匹配，避免 INGE 误命中 Ingenieur/springen、Fusion 误命中 FusionSolar。"""
    return re.search(r"(?<![a-z0-9])" + re.escape(deye) + r"(?![a-z0-9])", brand.lower()) is not None


def sells_deye(brands):
    return any(any(_brand_match(b, db) for db in DEYE_BRANDS) for b in (brands or []))


def product_score(lead):
    """产品匹配 30：手工判 product_tier 优先 > 机械 brands_found > 品类词兜底。

    product_tier（Claude 读 body/兜底手工判，覆盖机械判断，见 qualification-rules.md）：
      "deye"=存量30 / "competitor"=卖竞品/光伏品类24 / "none"=不相关0；缺省用 brands_found。
    机械兜底：brands_found 命中 Deye/贴牌=30 > 命中竞品=24 > 空但 body 自述卖品类=24 > 空=0。
    品类词兜底（2026-09-24 第 2 步）：brands_found 空 ≠ 无产品证据——body 明写
    falownik/magazyn energii/wechselrichter 的是品类渠道（增量 24），与「真无证据」分开
    （口径 qualification-rules.md L47/L59，断点 3 修的就是「没查到」和「确实没有」混成同一个 0）。
    """
    tier = lead.get("product_tier")
    if tier == "deye":
        return 30
    if tier == "competitor":
        return 24
    if tier == "none":
        return 0
    brands = lead.get("brands_found") or []
    if sells_deye(brands):
        return 30
    if brands:
        return 24
    if lead.get("category_hits"):
        return 24
    return 0


def product_basis(lead):
    """产品匹配评分依据（与 product_score 对应，含手工判标注）。"""
    tier = lead.get("product_tier")
    if tier == "deye":
        return "已卖Deye·存量（手工判）"
    if tier == "competitor":
        return "卖竞品/光伏品类·增量（手工判）"
    if tier == "none":
        return "不相关（手工判）"
    brands = lead.get("brands_found") or []
    if sells_deye(brands):
        return "已卖Deye·存量"
    if brands:
        return "卖竞品·增量"
    if lead.get("category_hits"):
        return "卖光伏/储能品类·增量（body 自述）"
    return "无逆变器/储能证据"


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


def evidence_source(lead):
    """每个维度的证据来源：judged（判出来的）/ mechanical（规则命中）/ fallback（缺值走兜底）。

    为什么要有这一层：分数算出来之后，「这一分是有依据的，还是兜底给的」在产物里
    看不出来 —— 兜底分和真判分长得一模一样。task_issues #14 那 1199 家落 58 分基线
    无人察觉，差的就是这一层标注。（2026-09-22 收编进正式流程，提案见 对接-workbuddy.md）
    """
    src = {}
    if lead.get("product_tier") in ("deye", "competitor", "none"):
        src["产品匹配"] = "judged"
    elif (lead.get("brands_found") or []):
        src["产品匹配"] = "mechanical"
    elif lead.get("category_hits"):
        # 品类词自述（body 明写卖光伏/储能）= 弱于品牌命中、但非兜底（2026-09-24 第 2 步）
        src["产品匹配"] = "mechanical"
    else:
        src["产品匹配"] = "fallback"
    src["渠道"] = "mechanical" if str(lead.get("customer_type") or "").strip() else "fallback"
    src["规模"] = "judged" if (str(lead.get("scale_tier") or "").lower() in SCALE_LABEL) else "fallback"
    # 触达：三样全空才算兜底；「仅官网」是一次真实观测，不算兜底
    if contact_tier(bool(lead.get("phone")), bool(lead.get("email")), bool(lead.get("website"))) == "none":
        src["触达"] = "fallback"
    else:
        src["触达"] = "mechanical"
    return src


def fallback_dims(lead):
    """本条哪几个维度的分是兜底给的（空列表 = 四维都有真实来源）。"""
    return [k for k, v in evidence_source(lead).items() if v == "fallback"]


def score_lead(lead):
    phone = bool((lead.get("phone") or "").strip())
    email = bool((lead.get("email") or "").strip())
    website = bool((lead.get("website") or "").strip())
    brands = lead.get("brands_found") or []
    ctype = lead.get("customer_type") or ""

    prod = product_score(lead)
    ch = classify_channel(ctype)
    tier, scale_b = read_scale(lead)
    ct = contact_tier(phone, email, website)

    prod_basis = product_basis(lead)
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
        "reason": dev_reason(grade_of(score_h), basis_h),
        # 证据来源标注（新增）：让「兜底分」在产物里可辨，不再与真判分同形
        "evidence_source": evidence_source(lead),
        "fallback_dims": fallback_dims(lead),
    }


# 三个**手工判**输入（qualification-rules.md）：缺了就只剩机械兜底，
# 分数会系统性偏低且看不出异常（2026-09 教训：1199 家证据缺失，全落 58 分基线兜底，
# 无人察觉）。这组完整度检查就是在堵这个洞。
JUDGE_INPUTS = ("customer_type", "product_tier", "scale_tier")


def _filled(lead, field):
    """该输入是否已判：非空即算。转 str 再判，容错非字符串输入。"""
    return bool(str(lead.get(field) or "").strip())


def judge_gaps(lead):
    """本条的「手工判缺口」：三输入里哪些还是空的。空列表 = 证据齐。"""
    return [f for f in JUDGE_INPUTS if not _filled(lead, f)]


def judge_report(leads):
    """证据完整度统计：每输入的填充率 + 三输入有缺口的条数。"""
    n = len(leads) or 1
    rep = {}
    for f in JUDGE_INPUTS:
        filled = sum(1 for l in leads if _filled(l, f))
        rep[f] = (filled, n, filled / n)
    gaps = [l for l in leads if judge_gaps(l)]
    return rep, len(gaps)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # abort 提示别在 GBK 终端成乱码
    ap = argparse.ArgumentParser(description="计算头部/长尾两套评分")
    ap.add_argument("json", help="已背调的线索 JSON")
    ap.add_argument("--out", default="leads_scored.json", help="输出 JSON")
    # --require-tiers 是 WorkBuddy 工单里用的名字，保留为别名，免对接时 flag 对不上
    ap.add_argument("--require-judged", "--require-tiers", dest="require_judged",
                    action="store_true",
                    help="硬闸门：三输入齐全率低于 --min-judged 就拒绝出分（exit 2）")
    ap.add_argument("--min-judged", type=float, default=0.8,
                    help="硬闸门阈值（默认 0.8，即至少 80%% 的线索三个手工判输入齐全）")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        leads = json.load(f)

    # ---- 证据完整度体检（每次都打，先于评分）----
    # 为什么要先打：分数是算出来的，**输入缺证据时分数照样出得来**，
    # 只是悄悄走兜底 —— 不体检就等于默认「手工判那一步已经做过了」。
    rep, n_gap = judge_report(leads)
    n_full = len(leads) - n_gap
    rate_full = n_full / (len(leads) or 1)
    print(f"证据完整度（{len(leads)} 条）:", flush=True)
    for f in JUDGE_INPUTS:
        filled, tot, rate = rep[f]
        flag = "OK" if rate >= args.min_judged else "⚠️ 偏低"
        print(f"  {f:14} {filled:5}/{tot:<5} {rate:6.1%}  {flag}", flush=True)
    print(f"  三输入齐全 {n_full} 条 / 有缺口 {n_gap} 条（{rate_full:.1%}）", flush=True)
    if n_gap:
        print("  → 缺证据的条目评分会走兜底（渠道按零售 0 分 / 规模按小型档 / 产品 0 分），"
              "补证据见 qualification-rules.md 第六步", flush=True)
    if args.require_judged and rate_full < args.min_judged:
        # abort 走 stderr；上面已 flush，保证终端里顺序是「体检 → abort」
        print(f"\n[abort] 三输入齐全率 {rate_full:.1%} 低于阈值 {args.min_judged:.0%}，拒绝出分。"
              f"先补 customer_type/product_tier/scale_tier，或加 --min-judged 放宽。",
              file=sys.stderr)
        sys.exit(2)

    for l in leads:
        r = score_lead(l)
        l.update(r)
        # 把缺口写进结果：下游（报告/入库）能看出这条分数建立在多少兜底上
        l["judge_gaps"] = judge_gaps(l)

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
    # 兜底显形（新增）：有兜底的条数 + 兜在哪个维度。以前这一层是隐形的，
    # 全批走兜底也照样报「评分完成」，这正是 #14 能瞒住 1199 家的原因。
    fb_rows = [x for x in leads if x.get("fallback_dims")]
    fb_counts = Counter(d for x in fb_rows for d in x["fallback_dims"])
    print(f"  ⚠️ 含兜底维度的条目: {len(fb_rows)}/{len(leads)}（{len(fb_rows)/ (len(leads) or 1):.1%}）"
          f" · 分布 {dict(fb_counts)}")
    if fb_rows:
        print("     → 兜底 = 该维度没有证据、按保守默认值给分（不是判出来的）。"
              "补证据见 evidence_gate.py 出的工作单。")


if __name__ == "__main__":
    main()

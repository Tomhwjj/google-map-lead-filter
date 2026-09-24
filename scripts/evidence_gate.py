#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""证据完整性闸门（evidence gate）—— 补上获客流水线里缺的那个「证据够不够」判断点。

【为什么需要这个脚本】
runner.py 的获客流水线是 6 步确定性脚本：
    search → enf → merge → backfill → score → ingest
其中 ④ backfill.py 只负责「把官网翻开、把证据抄下来」，抄不到就留空；
⑤ score_leads.py 在输入缺失时按兜底给分（产品 0 / 渠道按零售 0 / 规模按小型档），
**分数照样出得来** —— 于是一批「证据缺失」的线索以正常分数悄悄入库。
（2026-09 实证：1199 家证据缺失全落 58 分基线兜底，无人察觉；见 task_issues #14）

references/qualification-rules.md §「兜底：官网拿不到品牌/规模证据时」写了一套逐级补证
规则（kitesurf / anysearch / WebSearch），**但代码里一行都没实现**，所以它一次都没触发过。
（实证：09-05 那批正文为报错页的线索全部只标「未确认」，没有一家去补抓/补搜。）

本脚本 = 那个判断点。它做三件事：
    1. 判定：逐条判断证据够不够（触发条件逐条照抄 qualification-rules.md）
    2. 出单：把「缺什么 + 该用什么手段 + 该搜什么词」写成工作单（JSON）
    3. 显形：把缺口率打到终端/日志，让「证据缺失」不再无声

【刻意的分工】它**不联网、不抓取、不写库**。
补证手段（kitesurf / anysearch / WebSearch）都是 agent 侧能力（要浏览器/搜索），
脚本只负责「知道缺什么、该搜什么词」。这样脚本保持确定性、可重跑、零成本。

------------------------------------------------------------------
【2026-09-22 首轮实测后修正的三处】（在 2768 条真实背调记录上量的）
------------------------------------------------------------------
A. `linkedin` **不是 backfill 的产出字段**（6 个产物文件、2768 条，非空 0，键都不存在）。
   原实现把它算进 gaps → `evidence_status` 永远不是 ok、缺口率恒为 100%（无信息量）。
   修正：linkedin 归入 `soft_gaps`（可补项），**不参与**证据齐否的判定；查询仍照出。

B. 页面失败**不是一种**，实测 4 种原型，补证手段各不相同：
   `page_not_found` 201（7.3%）｜`page_security_block` 19｜`page_host_broken` 13｜`page_parked`
   原实现一律塞进 `no_body` + 一律 kitesurf —— 工作单指不出该做什么。
   修正：拆成 4 个码，每个带**不同的补证动作**。

C. `fetch_error`（error 字段非空）**不等于没拿到正文**：1099 条 error 非空里，
   119 条正文反而可用（例：Sun Home 正文 7996 字，error 只是 contact 子页超时）。
   原实现把它算阻断级 → 误杀。修正：降为提示级，阻断只由「正文本身不可用」决定。

   另：`404` 这个宽匹配曾疑似误伤，实测 2768 条里误伤 0 条
   （4 个候选经逐条查看全是真 404 页）。故保留，但要求 404 独立成词且出现在前 600 字。
------------------------------------------------------------------

用法:
    python evidence_gate.py backfill.json --out evidence_gaps.json
    python evidence_gate.py backfill.json --out gaps.json --strict   # 有阻断级缺口则 exit 3
    python evidence_gate.py --selftest
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

# ---- 可调阈值 ----
MIN_BODY = 200          # 正文短于此 = 没拿到可用正文
SHORT_BODY = 600        # 正文偏短，规模自述大概率为假阴性

# ---- 默认品牌表（与 runner.py / score_leads.py 一致）----
DEFAULT_OWN_BRANDS = ("Deye", "Sunsynk", "Sol-Ark", "INGE", "Fusion", "OHm", "Noark")
DEFAULT_COMPETITORS = ("Huawei", "Sungrow", "GoodWe", "Fronius", "SMA", "Solax",
                       "Sofar", "Growatt", "Kostal", "SolarEdge", "Enphase",
                       "Hoymiles", "FoxESS", "Solis")

# ---- 页面失败原型（按真实语料 2768 条归纳，判定顺序即优先级）----
# v2/v3（2026-09-23，拿 1169 条真实坏数据做回归测试后修正）：
#   ① `_P_SECURITY` 补措辞变体："Please wait while your request is being verified"、
#      "checking your browser"、"are you a robot" —— 实测 3 条拦截页原先漏判。
#   ② 新增 `_P_FORBIDDEN`：nginx 403 / "Request forbidden by administrative rules"
#      —— 实测 6 条拒绝访问页原先漏判（正文只有 219~626 字的 403 模板）。
#   ③ `_P_404_WORD` 补 5 类漏判短语（实测 22 条真 404 页原先判成「正文可用」）：
#      `strona nie została (znaleziona|odnaleziona)`、`niczego tutaj nie ma`、
#      `nie możemy znaleźć`、`page not found`、`404 -`。波兰语 404 模板措辞极多，
#      **不要靠收窄词表来"减少误判"** —— 试过，结果是把 11 条真 404 页一起放了。
#   ④ 弱词加 `\b` 词边界：`\bnie znaleźliśmy\b` 不再命中 `Ostatecz**nie** znaleźliśmy`
#      （真实假阳性：「我们最终找到了合适的房子」）。
#      仍存 2 条语义假阳性（"Firma nie istnieje" 公司不存在 / "sprzedaż nie istnieje"），
#      占 1169 条的 0.17%，属可接受噪声 —— 默认档下只是多出一条复抓建议。
_P_SECURITY = re.compile(
    r"(performing security verification|ray id|just a moment|cloudflare|access denied|"
    r"please wait while your request is being verified|verify you are human|"
    r"checking your browser|attention required|are you a robot|"
    r"sprawdzanie przeglądarki|weryfikacja przeglądarki|weryfikacj|"
    r"enable javascript|włącz javascript)", re.I)
_P_FORBIDDEN = re.compile(
    r"(403 forbidden|401 unauthorized|request forbidden by administrative rules)", re.I)
# v4（2026-09-24，入库实测揪出）：停放页正则漏掉**域名交易平台**的页面措辞。
# 实测 9 条真停放页被判「正文可用」，其中 `jak-sprzedawac-fotowoltaike.pl` 正是
# 对接文件 #20 点名的 aftermarket.eu 停放页；另有一条（cuweic.pl / Sedo）
# 因此把 `contact@sedo.com` 当合法邮箱喂进了入库路径。
# 教训同 v3：**别靠收窄词表降误判，要补全模式**。
_P_PARKED = re.compile(
    r"(domain for sale|domena na sprzedaż|this domain is for sale|"
    r"this domain[\s\S]{0,80}?(?:for sale|available for purchase)|"
    r"the domain[\s\S]{0,80}?for sale|buy this domain|"
    r"domena[\s\S]{0,60}?na sprzedaż|"
    r"sedo\.com|aftermarket\.(?:pl|eu|com)|afternic\.com|hugedomains|"
    r"domain transactions|registrar account|"
    r"witryna w budowie|strona w budowie|under construction)", re.I)
_P_BROKEN = re.compile(
    r"(błąd krytyczny|fatal error|critical error|error 402|subscription inactive|"
    r"bad gateway|service unavailable|internal server error)", re.I)
_P_404_WORD = re.compile(
    r"(error\s*\[?404|błąd\s*404|404\s*[-–:]\s|"
    r"\bnie znaleziono\b|\bnie znaleźliśmy\b|\bnie udało się znaleźć\b|"
    r"\bnie istnieje\b|\bnie możemy (?:odnaleźć|znaleźć)\b|"
    r"strona nie została (?:znaleziona|odnaleziona)|niczego tutaj nie ma|"
    r"file not found|page not found|not found\.|"
    r"doesn't exist|does not exist|could not be found|deployment_not_found)", re.I)
_P_404_TOKEN = re.compile(r"\b404\b")

# 失败码 → (该用什么手段, 该怎么做) —— 这是本脚本相对「一律 kitesurf」的核心价值
PAGE_FIX = {
    "page_not_found": ("kitesurf",
                       "抓取落在 404 页 —— contact/子页路径错或站点改版；"
                       "复抓首页并校正路径（**不是网站挂了**，别当死号）"),
    "page_security_block": ("kitesurf",
                            "被人机校验/反爬拦截 —— 换 UA 或降低抓取频率后重抓"),
    "page_forbidden": ("kitesurf",
                       "服务器拒绝访问（403/401，多为 UA/IP 被规则拦下）—— 换 UA 或降频后重抓；"
                       "持续被拒则转 anysearch 补证"),
    "page_host_broken": ("kitesurf",
                         "站点自身报错（WP 致命错误 / 402 / 5xx）—— 稍后重试；"
                         "仍不可用则转 anysearch 补证"),
    "page_parked": ("anysearch",
                    "域名停放 / 建设中 —— 该站没有正文，直接用 anysearch 补品牌与规模证据"),
    "body_empty": ("kitesurf",
                   "正文空或过短 —— 用 kitesurf 转 Markdown 复抓（规则 §61-76 首行）"),
}
BLOCKING_PAGE = tuple(PAGE_FIX)      # 正文不可用的 5 种形态 = 阻断级

# 规模自述信号（多语言：英/德/波/荷/意/西/法）
SCALE_SIGNAL = re.compile(
    r"(wholesale|wholesaler|warehouse|distributor|distribution|importer|import\b|"
    r"stock|inventory|großhandel|grosshandel|lager|logistik|filiale|mitarbeiter|"
    r"hurt|hurtown|magazyn|dystrybu|oddział|pracownik|flota|"
    r"groothandel|magazijn|voorraad|grossista|ingrosso|mayorista|almacén|"
    r"grossiste|entrepôt|"
    r"years|jahre|lat\b|rok|employees|customers|kunden|partner|brands|marken|marek|b2b)", re.I)

# 光伏/储能品类词
CATEGORY_WORD = re.compile(
    r"(photovoltaic|photovoltaik|fotowoltaik|wechselrichter|inverter|omvormer|invertor|"
    r"speicher|storage|battery|batterie|baterie|akumulator|magazyn energii|"
    r"thuisbatterij|hybrid inverter|solar|pv\b)", re.I)

# 自有品牌 / 生产商信号（brands_found 空时用来甄别「同行生产商」）
OWN_BRAND_WORD = re.compile(
    r"(manufacturer|producent|producer|hersteller|we produce|we manufacture|"
    r"our own brand|own brand|nasza marka|własna marka|marka własna|"
    r"our products|our brand|fabryka|factory|zakład produkcyjny)", re.I)

# 片段像「列表页/比价页/电商」的特征（触发交叉验证）
LISTISH = re.compile(
    r"(price|preis|cena|cennik|buy|kaufen|kup\b|shop|sklep|compare|porównanie|"
    r"vergleich|lista|list\b|oferta|angebot|cart|koszyk|warenkorb)", re.I)

# 缺口码 → 中文说明
GAP_LABEL = {
    "fetch_error":        "抓取报错（error 非空；正文可能不完整，建议复抓）",
    "body_empty":         "正文空或过短",
    "page_not_found":     "页面 404（抓取路径错或站点改版）",
    "page_security_block": "人机校验页（反爬拦截）",
    "page_forbidden":     "服务器拒绝访问（403/401）",
    "page_host_broken":   "站点自身报错（WP 致命错误/402/5xx）",
    "page_parked":        "域名停放 / 建设中",
    "brand_miss":         "品牌证据缺失（brands_found 空）",
    "own_brand_suspect":  "疑似自有品牌生产商（同行/竞品，非下游渠道）",
    "category_only":      "品牌证据薄弱（品牌名 ≤1 个）—— 竞品档不可靠，需交叉验证",
    "brand_nature_doubt": "品牌命中但片段像列表/比价页 —— 需交叉验证『销售 vs 提及』",
    "scale_miss":         "规模自述缺失（正文无任何经营痕迹信号）",
    "linkedin_miss":      "缺 LinkedIn 链接（可补项，不阻断判档）",
}
# 阻断级缺口：有了它，判档无从下手（其余为提示级，可判但要标"未确认"）
BLOCKING = BLOCKING_PAGE
# 可补项（soft gaps）：不参与「证据是否齐」的判定
SOFT_GAPS = ("linkedin_miss",)


def _page_fail(head: str) -> str | None:
    """判定这段正文是不是「页面存在但正文不可用」，并给出失败原型。"""
    if _P_SECURITY.search(head):
        return "page_security_block"
    if _P_FORBIDDEN.search(head):
        return "page_forbidden"
    if _P_PARKED.search(head):
        return "page_parked"
    if _P_BROKEN.search(head):
        return "page_host_broken"
    if _P_404_WORD.search(head):
        return "page_not_found"
    # 裸 404：要求独立成词，且出现在最前面 600 字（错误页都把 404 放在最上面）
    # v2 实测：1169 条里裸 404 命中 67 条，命中位置都落在真错误页的导航之后，
    # 未见「40-404 邮编 / 404 型号」这类误伤，故保留 600 字窗口。
    if _P_404_TOKEN.search(head[:600]):
        return "page_not_found"
    return None


def _body_kind(body: str) -> str:
    b = (body or "").strip()
    if len(b) < MIN_BODY:
        return "empty"
    if _page_fail(b[:3000]):
        return "bad"
    return "ok"


def evaluate(rec: dict, own_brands=DEFAULT_OWN_BRANDS,
             competitors=DEFAULT_COMPETITORS, min_body=MIN_BODY) -> dict:
    """判断一条背调记录的证据是否够用，并给出补证工作单。纯函数，不联网。"""
    name = (rec.get("company_name") or "").strip()
    web = (rec.get("website") or "").strip()
    body = rec.get("body") or ""
    err = (rec.get("error") or "").strip()
    brands = rec.get("brands_found") or []
    if isinstance(brands, str):
        brands = [brands]
    ctx = rec.get("brands_context") or ""
    if isinstance(ctx, (list, dict)):
        ctx = json.dumps(ctx, ensure_ascii=False)

    head = (body or "").strip()[:3000]
    if len((body or "").strip()) < min_body:
        body_fail = "body_empty"
    else:
        body_fail = _page_fail(head)
    kind = "ok" if body_fail is None else ("empty" if body_fail == "body_empty" else "bad")

    gaps: list[str] = []
    soft_gaps: list[str] = []
    actions: list[dict] = []
    queries: list[dict] = []

    # ---- E1 正文不可用（阻断级；按失败原型给不同补法）----
    if body_fail:
        gaps.append(body_fail)
        tool, why = PAGE_FIX[body_fail]
        actions.append({"tool": tool, "why": why, "target": web})
    # ---- E1b error 非空（提示级：正文可能不完整，但不等于没正文）----
    if err:
        gaps.append("fetch_error")
        # 正文已经不可用时，E1 的动作已覆盖该 URL，不重复出单
        if kind == "ok":
            actions.append({
                "tool": "kitesurf",
                "why": f"error 非空（{err[:60]}）—— 正文可能被截断或不完整，建议复抓确认",
                "target": web,
            })

    # ---- E2 品牌证据缺失 ----
    if not brands:
        gaps.append("brand_miss")
        # 规则原文：搜「公司名 + 品牌名 + distributor」
        for ob in own_brands[:3]:
            queries.append({"tool": "anysearch", "q": f"{name} {ob} distributor"})
        queries.append({"tool": "anysearch", "q": f"{name} inverter brands products"})

    # ---- E3 自有品牌生产商嫌疑（brands 空 + 品类词 + 生产商词）----
    if not brands and OWN_BRAND_WORD.search(body) and CATEGORY_WORD.search(body):
        gaps.append("own_brand_suspect")
        queries.append({"tool": "anysearch", "q": f"{name} manufacturer inverter storage"})

    # ---- E4 只有品类词、无品牌名 ----
    if (not brands or len(brands) <= 1) and CATEGORY_WORD.search(body):
        gaps.append("category_only")

    # ---- E5 品牌命中但整站性质存疑 ----
    doubt_brands = [b for b in brands if LISTISH.search(ctx)]
    if doubt_brands:
        gaps.append("brand_nature_doubt")
        for b in doubt_brands[:2]:
            queries.append({"tool": "WebSearch", "q": f"{name} {b} price"})
            queries.append({"tool": "WebSearch", "q": f"{name} {b} shop"})

    # ---- E6 规模自述缺失 ----
    if kind == "ok" and not SCALE_SIGNAL.search(body):
        gaps.append("scale_miss")
        # 规则原文：搜「公司名 + wholesale / warehouse / importer / about」
        for kw in ("wholesale", "warehouse", "importer", "about"):
            queries.append({"tool": "anysearch", "q": f"{name} {kw}"})

    # ---- E7 LinkedIn 缺失（soft gap：不参与证据齐否的判定）----
    if not (rec.get("linkedin") or "").strip():
        soft_gaps.append("linkedin_miss")
        queries.append({"tool": "WebSearch", "q": f"{name} linkedin"})

    # 去重
    seen, qs = set(), []
    for q in queries:
        key = (q["tool"], q["q"].lower())
        if key not in seen:
            seen.add(key)
            qs.append(q)

    blocking = [g for g in gaps if g in BLOCKING]
    status = "ok" if not gaps else "gap"
    if blocking:
        nxt = ("先补证再判档（正文不可用，判档无从下手）："
               + "；".join(dict.fromkeys(PAGE_FIX[b][1].split("——")[0] for b in blocking)))
    elif gaps:
        nxt = "可判档，但缺项维度必须标『未确认』，不得当确定档入分"
    else:
        nxt = "可直接判档"

    return {
        "main_id": rec.get("main_id"),
        "company_name": name,
        "website": web,
        "evidence_status": status,
        "body_kind": kind,
        "body_fail": body_fail,
        "body_len": len((body or "").strip()),
        "gaps": gaps,
        "gap_labels": [GAP_LABEL.get(g, g) for g in gaps],
        "soft_gaps": soft_gaps,
        "soft_gap_labels": [GAP_LABEL.get(g, g) for g in soft_gaps],
        "severity": "blocking" if blocking else ("advisory" if gaps else "none"),
        "actions": actions,
        "queries": qs,
        "needs_llm_judge": True,
        "next_action": nxt,
    }


def summarize(rows: list[dict]) -> dict:
    n = len(rows) or 1
    cnt = Counter(g for r in rows for g in r["gaps"])
    soft = Counter(g for r in rows for g in r.get("soft_gaps", []))
    return {
        "total": len(rows),
        "ok": sum(1 for r in rows if r["evidence_status"] == "ok"),
        "gap": sum(1 for r in rows if r["evidence_status"] == "gap"),
        "blocking": sum(1 for r in rows if r["severity"] == "blocking"),
        "advisory": sum(1 for r in rows if r["severity"] == "advisory"),
        "gap_rate": round(sum(1 for r in rows if r["evidence_status"] == "gap") / n, 4),
        "blocking_rate": round(sum(1 for r in rows if r["severity"] == "blocking") / n, 4),
        "gap_counts": dict(cnt.most_common()),
        "soft_gap_counts": dict(soft.most_common()),
        "queries_total": sum(len(r["queries"]) for r in rows),
    }


def _load(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if isinstance(d, dict):
        for k in ("results", "leads", "companies", "records"):
            if isinstance(d.get(k), list):
                d = d[k]
                break
    if not isinstance(d, list):
        raise SystemExit(f"[err] 不认识的 JSON 结构: {type(d)}")
    return [x for x in d if isinstance(x, dict)]


def _selftest() -> int:
    """内置样本自测：每例断言期望的缺口集合。"""
    long_ok = ("We are a wholesale distributor of photovoltaic inverters and battery storage. "
               "Our warehouse in Warsaw stocks over 20 brands, and we have served customers for "
               "12 years. We supply installers across the whole country with hybrid inverter "
               "systems. ") * 2
    cases = [
        # ---- 基础路径 ----
        ("完整证据 → ok",
         {"company_name": "FullCo", "website": "https://full.example", "body": long_ok,
          "brands_found": ["Deye", "Huawei"], "brands_context": "authorized distributor of Deye",
          "linkedin": "https://linkedin.com/company/fullco"},
         {"evidence_status": "ok", "severity": "none"}, set()),
        # 修正 A：linkedin 缺失不参与证据齐否（原实现会把它算成缺口 → 永远不齐）
        ("只缺 linkedin → 仍算证据齐（soft gap）",
         {"company_name": "NoLiCo", "website": "https://nl.example", "body": long_ok,
          "brands_found": ["Deye", "Huawei"],
          "brands_context": "authorized distributor of Deye and Huawei"},
         {"evidence_status": "ok", "severity": "none"}, set()),
        # ---- 正文不可用：5 种原型 ----
        ("正文空 → body_empty 阻断",
         {"company_name": "EmptyCo", "website": "https://e.example", "body": "", "brands_found": []},
         {"severity": "blocking", "body_fail": "body_empty"}, {"body_empty", "brand_miss"}),
        ("WordPress 致命错误页 → page_host_broken",
         {"company_name": "WPCo", "website": "https://wp.example",
          "body": "Witrynie wystąpił błąd krytyczny. Dowiedz się więcej o debugowaniu w WordPressie. "
                  "Witrynie wystąpił błąd krytyczny. Prosimy spróbować później." * 3,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_host_broken"}, set()),
        ("404 页 → page_not_found（波兰语）",
         {"company_name": "NFCo", "website": "https://nf.example",
          "body": "404 NIE MOŻEMY ODNALEŹĆ TEJ STRONY Tej strony nie ma. Wróć na stronę główną. " * 4,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_not_found"}, set()),
        ("404 页 → page_not_found（英语，裸 404 在最前）",
         {"company_name": "NF2Co", "website": "https://nf2.example",
          "body": "404 This page could not be found. Go back to the homepage. " * 6,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_not_found"}, set()),
        ("人机校验页 → page_security_block",
         {"company_name": "SecCo", "website": "https://sec.example",
          "body": "www.sec.example Performing security verification This website uses a security "
                  "service to protect against malicious bots. This page is displayed while the "
                  "website is being verified. Please wait." * 2,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_security_block"}, set()),
        # v2 新增：拦截页的措辞变体（1169 条真实数据里原先漏判 3 条）
        ("拦截页变体 Please wait while your request is being verified → page_security_block",
         {"company_name": "VerifyCo", "website": "https://v2.example",
          "body": "Please wait while your request is being verified... " * 12,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_security_block"}, set()),
        # v2 新增：403/401 拒绝访问（真实数据里原先漏判 6 条，正文只有 219~626 字的模板）
        ("nginx 403 → page_forbidden",
         {"company_name": "ForbidCo", "website": "https://f.example",
          "body": "403 Forbidden nginx 403 Forbidden nginx " * 8,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_forbidden"}, set()),
        ("停放页 → page_parked（补法不是 kitesurf）",
         {"company_name": "ParkCo", "website": "https://park.example",
          "body": "This domain is for sale. Buy this domain. Inquire about this domain name. " * 5,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_parked"}, set()),
        # v4 回归：域名交易平台（Sedo / Aftermarket.pl）的停放页措辞——原正则漏判
        # 真实样本：`cuweic.pl is available for purchase - Sedo.com`（正文 1220 字，被判"可用"）
        ("Sedo 停放页 → page_parked（v4 补的模式）",
         {"company_name": "SedoParked", "website": "http://www.cuweic.pl/",
          "body": ("This domain\ncuweic.pl\nis for sale! 16 people have already viewed this "
                   "offer Buy domain Buy Now for 500 EUR Submit your Offer Buy now "
                   "Add to watchlist Free transfer service domain transactions ") * 4,
          "brands_found": [], "emails": ["contact@sedo.com"]},
         {"severity": "blocking", "body_fail": "page_parked"}, set()),
        ("Aftermarket.pl 停放页（BUY THIS DOMAIN）→ page_parked",
         {"company_name": "AftermarketParked", "website": "http://safeguard24.pl/",
          "body": ("Homepage Domains for sale Finance Insurance safeguard24.pl "
                   "HOW TO BUY THIS DOMAIN? VAT invoice form Aftermarket.pl " * 6),
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_parked"}, set()),
        # 修正 C：error 非空但正文可用 → 提示级，不阻断（原实现误杀 119 条）
        ("error 非空 + 正文可用 → 提示级，不阻断",
         {"company_name": "PartCo", "website": "https://p.example", "body": long_ok,
          "error": "Page.goto: Timeout 20000ms exceeded", "brands_found": ["Deye"],
          "brands_context": "distributor of Deye", "linkedin": "https://linkedin.com/company/p"},
         {"severity": "advisory", "body_fail": None}, {"fetch_error"}),
        ("error 非空 + 正文空 → 阻断",
         {"company_name": "ErrCo", "website": "https://err.example", "body": "",
          "error": "Timeout 30000ms exceeded", "brands_found": []},
         {"severity": "blocking", "body_fail": "body_empty"}, {"fetch_error", "body_empty"}),
        # v3 回归：`Ostatecznie znaleźliśmy`（"nie" 只是词尾）**不得**判 404 —— 词边界修复
        ("正常文案含 Ostatecznie znaleźliśmy → 不得判 404（\\b 词边界）",
         {"company_name": "MarketingCo", "website": "https://m.example",
          "body": ("Ostatecznie znaleźliśmy tę właściwą ofertę i sfinalizowaliśmy transakcję. "
                   "Dziękujemy za pomoc i zapraszamy ponownie. " * 8),
          "brands_found": [], "linkedin": "https://linkedin.com/company/m"},
         {"severity": "advisory", "body_kind": "ok", "body_fail": None}, set()),
        # v3 新增：波兰语 404 模板的常见措辞（真实数据里原先漏判 22 条）
        ("波兰语 404 模板 Strona nie została znaleziona → page_not_found",
         {"company_name": "PlNFCo", "website": "https://plnf.example",
          "body": "Przejdź do treści Strona Główna O firmie Kontakt "
                  "Strona nie została znaleziona. Wygląda na to, że niczego tutaj nie ma. " * 3,
          "brands_found": []},
         {"severity": "blocking", "body_fail": "page_not_found"}, set()),
        # ---- 提示级 ----
        ("正文正常但品牌未命中 → brand_miss（提示级）",
         {"company_name": "NoBrandCo", "website": "https://nb.example",
          "body": "We sell solar panels and batteries to installers. " * 6,
          "brands_found": [], "linkedin": "https://linkedin.com/company/nb"},
         {"severity": "advisory"}, {"brand_miss"}),
        ("自有品牌生产商嫌疑 → own_brand_suspect",
         {"company_name": "VoltLike", "website": "https://v.example",
          "body": "We are a manufacturer of our own brand energy storage systems. "
                  "We produce hybrid inverter and battery storage products in our factory. " * 3,
          "brands_found": [], "linkedin": "https://linkedin.com/company/v"},
         {"severity": "advisory"}, {"own_brand_suspect", "brand_miss"}),
        ("品牌命中但片段像比价页 → brand_nature_doubt",
         {"company_name": "CompareCo", "website": "https://c.example", "body": long_ok,
          "brands_found": ["Deye"],
          "brands_context": "...Deye... price comparison ... shop ... best price...",
          "linkedin": "https://linkedin.com/company/c"},
         {"severity": "advisory"}, {"brand_nature_doubt"}),
        ("正文正常但无经营痕迹词 → scale_miss",
         {"company_name": "TinyCo", "website": "https://t.example",
          "body": "Kontakt: ul. Kwiatowa 1, 00-001 Miasto. Telefon 123 456 789. "
                  "Godziny otwarcia pon-pt 8-16. Zapraszamy. " * 4,
          "brands_found": ["Deye"], "brands_context": "Deye inverter available",
          "linkedin": "https://linkedin.com/company/t"},
         {"severity": "advisory"}, {"scale_miss"}),
    ]
    fails = 0
    for title, rec, want, want_gaps in cases:
        got = evaluate(rec)
        bad = []
        for k, v in want.items():
            if got[k] != v:
                bad.append(f"{k}={got[k]!r}（期望 {v!r}）")
        missing = want_gaps - set(got["gaps"])
        if missing:
            bad.append(f"缺缺口 {sorted(missing)}")
        if bad:
            fails += 1
        print(f"  [{'PASS' if not bad else 'FAIL'}] {title}"
              + ("" if not bad else "  ← " + "; ".join(bad)))
        if bad:
            print(f"         gaps={got['gaps']} soft={got['soft_gaps']} "
                  f"severity={got['severity']}")
    print(f"\n自测: {len(cases) - fails}/{len(cases)} 通过")
    return 0 if fails == 0 else 1


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="证据完整性闸门：判定缺口 + 出补证工作单（不联网）")
    ap.add_argument("json", nargs="?", help="backfill 产物 JSON")
    ap.add_argument("--out", default="evidence_gaps.json", help="缺口工作单输出 JSON")
    ap.add_argument("--min-body", type=int, default=MIN_BODY, help=f"正文最小长度（默认 {MIN_BODY}）")
    ap.add_argument("--own-brands", default=",".join(DEFAULT_OWN_BRANDS), help="我方品牌（逗号分隔）")
    ap.add_argument("--competitors", default=",".join(DEFAULT_COMPETITORS), help="竞品品牌（逗号分隔）")
    ap.add_argument("--only-gaps", action="store_true", help="工作单只写有缺口的条目")
    ap.add_argument("--strict", action="store_true", help="有阻断级缺口则 exit 3（供流水线卡入库）")
    ap.add_argument("--selftest", action="store_true", help="跑内置样本自测")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(_selftest())
    if not args.json:
        ap.error("需要 backfill.json（或用 --selftest）")

    recs = _load(args.json)
    own = tuple(b.strip() for b in args.own_brands.split(",") if b.strip())
    comp = tuple(b.strip() for b in args.competitors.split(",") if b.strip())
    rows = [evaluate(r, own, comp, args.min_body) for r in recs]
    s = summarize(rows)

    out_rows = [r for r in rows if r["evidence_status"] == "gap"] if args.only_gaps else rows
    payload = {"source": args.json, "summary": s, "records": out_rows}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print(f"证据完整性闸门 · {args.json}")
    print(f"  条数 {s['total']}｜证据齐 {s['ok']}（{s['ok'] / max(s['total'], 1):.1%}）"
          f"｜有缺口 {s['gap']}（{s['gap_rate']:.1%}）"
          f"｜阻断级 {s['blocking']}（{s['blocking_rate']:.1%}）")
    print(f"  缺口分布: {s['gap_counts']}")
    print(f"  可补项: {s['soft_gap_counts']}")
    print(f"  补证工作单: {s['queries_total']} 条查询 → {args.out}")
    print("  → 补证手段（kitesurf / anysearch / WebSearch）由 agent 执行；"
          "补到的证据按规则 §76 落到 source_url，之后才判档。")

    if args.strict and s["blocking"]:
        print(f"\n[abort] 存在 {s['blocking']} 条阻断级缺口（正文不可用），拒绝进入评分/入库。",
              file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()

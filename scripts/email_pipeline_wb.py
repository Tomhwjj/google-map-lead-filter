#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WorkBuddy 邮件回复分类管线（WorkBuddy 拥有并迭代，Claude Code 只审不写）。

职责（契约见 spec.json，唯一契约）：
  读入邮件（JSON）→ 发件人匹配企业 → 按 spec.classify_rules R1→R7 瀑布分类
  → 产出带审计字段的结果 → 写 email_review（经 core 函数，绝不裸 SQL）。

写边界（spec.write_boundary）：
  - email_review      主写（经 core.record_email_review，Claude 补函数后启用）
  - email_anomalies   仅 R1 bounce（经 core.mark_email_invalid）
  - gmail_contacts    仅 R1 bounce 置 invalid（经 core.mark_contact_invalid，Claude 补函数后启用）
  - 其余一律不碰；companies.pool / pool_log 只提议（transfer_review）不执行。

状态判定：
  confidence < 0.7            → status=review（spec 硬规则，禁止 applied）
  R4/R5/R6（transfer_review） → status=review（换池 100% 人工，只提议）
  R1 mark_invalid             → status=applied（高置信确定性动作，直接执行）
  R2 no_action                → status=applied（记录即可，无需人工）
  R3/R7 ignored               → status=ignored

用法：
  python scripts/email_pipeline_wb.py --input data/email_samples --dry-run
  python scripts/email_pipeline_wb.py --input data/email_samples/a.json
  python scripts/email_pipeline_wb.py --input data/email_samples --dry-run --verbose

输入 JSON 字段：message_id / from / to / subject / date / body / in_reply_to（均可缺省）。
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC_PATH = os.path.join(PROJECT_ROOT, "spec.json")

LOW_CONFIDENCE = 0.7          # spec.audit_required：低于此值一律 review
SNIPPET_MAX = 500             # spec.audit_required：body_snippet ≤500 字

# 复用 core.py 的免费邮箱域清单（R6 判个人邮箱用）；import 失败则内置兜底
try:
    from core import FREE_EMAIL_DOMAINS
except Exception:  # pragma: no cover
    FREE_EMAIL_DOMAINS = {
        "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
        "yahoo.com", "icloud.com", "proton.me", "gmx.de", "web.de", "wp.pl",
        "o2.pl", "interia.pl", "onet.pl",
    }

# ---------------------------------------------------------------------------
# 规则信号（与 spec.json classify_rules 的 signals 对齐；spec 是唯一契约，
# 这里只做正则实现，改规则先改 spec.json）
# ---------------------------------------------------------------------------

RE_R1_SUBJECT = re.compile(
    r"undelivered mail returned|delivery status notification|failure notice|returned mail",
    re.I)
RE_R1_BODY_STRONG = re.compile(
    r"user doesn't exist|user unknown|no such user|mailbox unavailable|"
    r"recipient address rejected|address not found|mailbox not found|"
    r"adresat nie istnieje|die adresse wurde nicht gefunden|usuario no existe",
    re.I)
RE_R1_BODY_CODE = re.compile(r"\b(550|551|552|553)\b")

RE_R2_SUBJECT = re.compile(
    r"out of office|automatic reply|autoreply|自动回复|auto:|"
    # 多语言（核心市场 PL/DE/ES，Claude 真实样本教训：Automatyczna odpowiedź 漏网）
    r"automatyczna odpowiedź|automatische antwort|respuesta automática|自動回復",
    re.I)
RE_R2_BODY = re.compile(
    r"out of office|on vacation|annual leave|away from (the office|my desk)|"
    r"automatic reply|urlopie|im urlaub|de vacaciones|"
    # 自动确认措辞（TIM 教训：客服团队自动 ack，24h 内回复，非工单终态）
    r"ta wiadomość jest automatyczna|dziękujemy za kontakt|wrócimy z odpowiedzią|"
    r"otrzymaliśmy państwa (wiadomość|zgłoszenie)|"
    r"thank you for (contacting|your (e-?mail|message))|we have received your (message|e-?mail|request)",
    re.I)

# 工单终态（KSTAR 教训）：ticket has been closed = 不会再有下文，≠ 自动 ack
RE_TICKET_CLOSED = re.compile(
    r"ticket[^.\n]{0,40}(has been|was|is) closed|"
    r"(request|case|zgłoszenie)[^.\n]{0,40}(has been|was|zostało) (closed|zamknięt)", re.I)

# 营销/通知类发件（Alibaba 教训）：notice@/noreply@/ marketplace 通知域 → 纯 spam
RE_PROMO_SENDER = re.compile(
    r"^(no-?reply|noreply|notice|newsletter|marketing|promo)[@.\-]|"
    r"@(notice\.)?(alibaba|aliexpress|linkedin|facebookmail|amazon|ebay)\.", re.I)

RE_R3_PROMO = re.compile(
    r"unsubscribe|opt.?out|% ?(off|discount)|special offer|best price|"
    r"click here|limited time|free shipping|newsletter|www\.https?://|bit\.ly/",
    re.I)

RE_R4_STRONG = re.compile(
    # 强停业信号：优先级高于 R2 自动回复（Claude 真实样本教训：Menlo 主题
    # "Automatic reply:" 抢占 R2，正文却是"ended its business operations"）
    r"ended (its|their) (business )?operations|business operations (have|has|was|were) ended|"
    r"no longer in business|out of business|ceased (trading|operations)|"
    r"bankrupt|liquidation|破产|停业", re.I)

RE_R4_BODY = re.compile(
    r"no longer in business|out of business|ceased (trading|operations)|"
    r"bankrupt|liquidation|"
    # own_brand（Volt Polska 教训）：「we are (a) producer/manufacturer/importer ... and importer/...」
    r"we are (a )?(producer|manufacturer|importer)s?[^.!?]{0,80}(and|&)\s*(an?\s+)?(importer|manufacturer|producer)s?|"
    r"we (only )?(sell|produce|manufacture)( only)? (our|under) (own|our) brand|"
    r"we want to sell our products|you want to sell our products|"
    r"not interested|do not (sell|carry|distribute)|"
    r"we don't (sell|carry|distribute)|破产|停业|不合作|自有品牌",
    re.I)

# 客服工单语（KSTAR 教训）：[#1023] / request closed / support team → 非经销商线索，归 R7
RE_TICKET = re.compile(
    r"\[?#\d+\]?|ticket( id| number)?[:#]?\s*\d+|"
    r"(your|our) (request|ticket|case) (has|was|is) (been )?(received|closed|resolved)|"
    r"support (team|center|ticket)|service desk|customer service|automated message from",
    re.I)

RE_R5_BODY = re.compile(
    r"price|quotation|quote|catalog|catalogue|moq|minimum order|"
    r"stock|availability|lead time|cooperation|partnership|distribut(or|ion)|"
    r"reseller|deal(er)?ship|fob|cif|样品|询价|报价|合作|经销", re.I)

RE_R6_BODY = re.compile(
    r"my (inverter|battery|panel|system)|one unit|single unit|"
    r"install(ation)?|return|refund|warranty|repair|broken|not working|"
    r"how much is|where can i buy|退货|退款|安装|维修|保修", re.I)

# 个人邮箱弱信号（Solicitud 教训）：个人 gmail 想做经销商/想买，非 B2B 批量询盘
# ⚠ 主题也要扫：「Solicitud de información para ser distribuidor」的意图在主题里
RE_R6_WEAK = re.compile(
    r"(para|quiero|queremos|me gustaría|deseo)\s+(ser|convertirme|convertirnos en)?\s*(un\s+)?distribuidor|"
    r"become (a )?(distributor|dealer|reseller)|want to be( come)? (a )?(distributor|dealer|reseller)|"
    r"quiero comprar|where can (i|we) (buy|purchase)|dónde puedo comprar", re.I)

# 每条规则的命中实现：返回 (hit: bool, confidence: float, basis: str)
def _hit_r1(msg):
    subj, body = msg.get("subject") or "", msg.get("body") or ""
    if RE_R1_SUBJECT.search(subj):
        return True, 0.95, "subject 命中退信标题模式"
    m = RE_R1_BODY_STRONG.search(body)
    if m:
        return True, 0.90, f"body 命中退信强信号: {m.group(0)!r}"
    if RE_R1_BODY_CODE.search(body) and re.search(
            r"user|mailbox|recipient|address|exist|unknown", body, re.I):
        return True, 0.65, "body 命中 SMTP 55x 代码 + 邮箱上下文（中等置信）"
    return False, 0.0, ""

def _hit_r2(msg):
    subj, body = msg.get("subject") or "", msg.get("body") or ""
    # 强停业信号压过自动回复（真教训：Menlo「Automatic reply:」主题 + 停业正文）
    if RE_R4_STRONG.search(body):
        return False, 0.0, ""   # 让位给 R4
    # 工单终态压过自动 ack（KSTAR 教训：ticket closed ≠ 待回复的自动确认）
    if RE_TICKET_CLOSED.search(body) or RE_TICKET_CLOSED.search(subj):
        return False, 0.0, ""   # 让位给 R7
    if RE_R2_SUBJECT.search(subj):
        return True, 0.92, "subject 命中自动回复标题模式"
    m = RE_R2_BODY.search(body)
    if m:
        return True, 0.85, f"body 命中外出/休假/自动确认措辞: {m.group(0)!r}"
    return False, 0.0, ""

def _hit_r3(msg):
    # R3 前提：不是对我们群发的回复（in_reply_to 为空）
    if msg.get("in_reply_to"):
        return False, 0.0, ""
    body = msg.get("body") or ""
    sender = (msg.get("from") or "").strip().lower()
    # 营销/通知类发件域（Alibaba 教训）：无 in_reply_to + 平台通知号 = 纯 spam
    if RE_PROMO_SENDER.search(sender):
        return True, 0.60, f"营销/通知类发件人: {sender!r}"
    hits = RE_R3_PROMO.findall(body)
    n_links = len(re.findall(r"https?://", body))
    if len(hits) >= 2 or (hits and n_links >= 5):
        return True, 0.75, f"无 in_reply_to + 推广信号 x{len(hits)} + 链接 x{n_links}"
    if hits:
        return True, 0.60, f"无 in_reply_to + 弱推广信号: {hits[:2]}"
    return False, 0.0, ""

def _hit_r4(msg):
    body = msg.get("body") or ""
    m = RE_R4_STRONG.search(body)
    if m:
        return True, 0.65, f"body 命中强停业措辞: {m.group(0)!r}"
    m = RE_R4_BODY.search(body)
    if m:
        return True, 0.60, f"body 命中停业/自有品牌/不合作措辞: {m.group(0)!r}"
    return False, 0.0, ""

def _hit_r5(msg):
    # R5 前提：是对我们群发的回复（in_reply_to 有值）
    if not msg.get("in_reply_to"):
        return False, 0.0, ""
    body = msg.get("body") or ""
    # 让位规则：own_brand/强停业压过询价（Volt Polska 教训：正文引用了我们的
    # stock/price 措辞被 R5 抢走，实为 own_brand 拒绝）
    if RE_R4_STRONG.search(body) or RE_R4_BODY.search(body):
        return False, 0.0, ""   # 让位给 R4
    m = RE_R5_BODY.search(body)
    if m:
        return True, 0.75, f"in_reply_to 有值 + body 命中询价/合作措辞: {m.group(0)!r}"
    # 客服工单语 ≠ 询价（KSTAR 教训）：无询价关键词且有工单措辞 → 让位给 R7
    if RE_TICKET.search(body):
        return False, 0.0, ""
    # 有 in_reply_to 但无关键词：仍是真人回复，低置信进 review 人工看
    return True, 0.55, "in_reply_to 有值（真人回复）但未命中询价关键词"

def _hit_r6(msg):
    sender = (msg.get("from") or "").lower()
    _, _, dom = sender.rpartition("@")
    if not dom or dom not in FREE_EMAIL_DOMAINS:
        return False, 0.0, ""   # 企业域名发件不走 R6
    m = RE_R6_BODY.search(msg.get("body") or "")
    if m:
        return True, 0.60, f"个人邮箱({dom}) + body 命中消费者售后措辞: {m.group(0)!r}"
    m = RE_R6_WEAK.search(msg.get("subject") or "") or RE_R6_WEAK.search(msg.get("body") or "")
    if m:
        return True, 0.55, f"个人邮箱({dom}) + 想做经销商/购买意愿（非 B2B 批量）: {m.group(0)!r}"
    return False, 0.0, ""

def _hit_r7(msg):
    """R7 兜底，但客服工单语单独识别（置信度更高，裁决(ii)下可 ignored）。"""
    m = RE_TICKET.search(msg.get("body") or "") or RE_TICKET.search(msg.get("subject") or "")
    if m:
        return True, 0.65, f"客服工单/系统通知措辞: {m.group(0)!r}（非经销商线索）"
    return True, 0.50, "兜底：R1–R6 均未命中"

RULE_HIT = {"R1": _hit_r1, "R2": _hit_r2, "R3": _hit_r3, "R4": _hit_r4,
            "R5": _hit_r5, "R6": _hit_r6, "R7": _hit_r7}

# ---------------------------------------------------------------------------
# 分类瀑布：按 spec 的 priority R1→R7 顺序，命中即停
# ---------------------------------------------------------------------------

def snippet(body, basis=""):
    """取命中关键词上下文 ±200 字符（无命中取开头），≤500 字。"""
    body = body or ""
    m = re.search(r"(\w+'?\w*|破产|停业|不合作|自有品牌|询价|报价|合作|经销|退货|安装|维修)", basis)
    pos = 0
    if m:
        kw = re.escape(m.group(1))
        mm = re.search(kw, body, re.I)
        if mm:
            pos = max(0, mm.start() - 200)
    s = body[pos:pos + SNIPPET_MAX].strip()
    return ("…" + s) if pos > 0 else s

RE_EMAIL_ADDR = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

def parse_address(raw):
    """'Name' <a@b.c> / <a@b.c> / 裸地址 → 裸地址（小写）。真实样本全是带显示名格式。"""
    m = RE_EMAIL_ADDR.search(raw or "")
    return m.group(0).lower() if m else (raw or "").strip().lower()

def classify(msg):
    """返回结果 dict（含全部审计字段，除 matched_* / status 由调用方补）。"""
    msg = dict(msg)
    msg["from"] = parse_address(msg.get("from"))
    msg["to"] = parse_address(msg.get("to"))
    rules = load_spec()["classify_rules"]  # spec 已按 priority 排序 R1→R7
    for rule in rules:
        rid = rule["rule_id"]
        hit, conf, basis = RULE_HIT[rid](msg)
        if hit:
            return _result(msg, rid, rule, conf, basis)
    return _result(msg, "R7", rules[-1], 0.50, "兜底：R1–R6 均未命中")

def _result(msg, rid, rule, conf, basis):
    return {
        "message_id": msg.get("message_id") or "",
        "from_address": (msg.get("from") or "").strip().lower(),
        "to_address": (msg.get("to") or "").strip().lower(),
        "subject": msg.get("subject") or "",
        "mail_date": msg.get("date") or "",
        "body_snippet": snippet(msg.get("body") or "", basis) if basis else
                        (msg.get("body") or "")[:SNIPPET_MAX],
        "in_reply_to": msg.get("in_reply_to") or "",
        "classification": rule["label"],
        "confidence": round(conf, 2),
        "rule_id": rid,
        # spec 的 action 形如 "mark_invalid：写 email_review(...)"，冒号有全角/半角两种
        "proposed_action": re.split(r"[:：]", rule["action"])[0].strip(),
        "action_detail": basis,
    }

# ---------------------------------------------------------------------------
# 发件人 → 企业匹配（spec.match_logic：gmail_contacts → companies.email → 陌生）
# 匹配全程走 core 现有读函数，零裸 SQL。
# ---------------------------------------------------------------------------

def match_sender(from_email, db_path=None):
    from core import list_gmail_contacts, list_companies, split_emails
    if not from_email or "@" not in from_email:
        return None, "", "unmatched"
    e = from_email.strip().lower()
    for g in list_gmail_contacts(limit=5000, db_path=db_path):
        if (g.get("email") or "").strip().lower() == e:
            return g["main_id"], g["email"], "exact_gmail_contacts"
    for c in list_companies(query=e, limit=50, db_path=db_path):
        if e in [x.lower() for x in split_emails(c.get("email"))]:
            return c["main_id"], e, "exact_companies_email"
    return None, "", "unmatched"

# ---------------------------------------------------------------------------
# 写库（全部经 core 函数；函数未就绪则降级 dry-run 打印）
# ---------------------------------------------------------------------------

def extract_bounced_recipient(body, self_addr="", known_emails=None):
    """从退信正文提取真正被弹回的收件人地址。

    ⚠ bounce 邮件的 from 是 mailer-daemon（postmaster），绝不能拿它标无效；
    该标的是「我们发出去、被弹回」的那个地址——正文里的 to:/recipient/rejected
    上下文中的地址，且优先取能命中已知联系人集合的（退信必然是我们发过的地址）。
    """
    body = body or ""
    known = known_emails or set()
    cands = []
    for e in RE_EMAIL_ADDR.findall(body.lower()):
        e = e.lower()
        if e == self_addr or e.startswith("mailer-daemon") or e.startswith("postmaster"):
            continue
        if e not in cands:
            cands.append(e)
    for e in cands:                      # 首选：已知联系人命中
        if e in known:
            return e
    for e in cands:                      # 次选：退信上下文（to:/recipient/rejected...）
        idx = body.lower().find(e)
        ctx = body[max(0, idx - 100):idx].lower()
        if re.search(r"to:|recipient|failed|rejected|unknown|no existe|nie istnieje|"
                     r"destinatari|empfänger|adresse", ctx):
            return e
    return cands[0] if cands else ""

def decide_status(res):
    """按 spec 硬规则 + 裁决(ii) 定 status。

    裁决(ii)（Claude 已改 spec.json audit_required）：confidence<0.7 且 status=applied
    时强制转 review（core.record_email_review 也有同款拦截，双保险）；R3/R7 的
    ignored 不受 0.7 限制，conf≥0.5 允许 ignored，<0.5 仍进 review。"""
    conf, act = res["confidence"], res["proposed_action"]
    if act == "transfer_review":
        return "review"                       # 换池/closed 只提议，100% 人工
    if act == "mark_invalid":
        return "applied" if conf >= LOW_CONFIDENCE else "review"
    if act == "ignored":                      # R3/R7
        return "ignored" if conf >= 0.5 else "review"
    return "applied" if conf >= LOW_CONFIDENCE else "review"   # no_action（R2）

def execute_actions(res, db_path, self_addr=""):
    """R1 bounce 的库外动作：mark_email_invalid + gmail_contacts 置 invalid。

    ⚠ 标的对象是退信收件人（bounced_recipient），不是 mailer-daemon。"""
    import core
    out = []
    if res["proposed_action"] != "mark_invalid" or res["status"] != "applied":
        return out
    target = res.get("bounced_recipient") or ""
    if not target:
        out.append("⚠ 未能从退信正文识别被弹回的收件人，未标无效邮箱（需人工看原文）")
        return out
    reason = f"bounce (rule {res['rule_id']}, conf {res['confidence']}): {res['action_detail'][:200]}"
    aid = core.mark_email_invalid(target, reason=reason,
                                  main_id=res.get("matched_main_id"), db_path=db_path)
    out.append(f"email_anomalies#{aid} 已记无效邮箱: {target}")
    if hasattr(core, "mark_contact_invalid"):   # Claude 补函数后自动启用
        r = core.mark_contact_invalid(target, error=reason, db_path=db_path)
        out.append(f"gmail_contacts.status=invalid 已置 ({target}, {r.get('updated', 0)} 行)")
    else:
        out.append("⚠ core.mark_contact_invalid 未就绪，gmail_contacts 未改（待 Claude 补函数）")
    return out

def write_result(res, db_path, dry_run=False):
    import core
    res["status"] = decide_status(res)
    actions = execute_actions(res, db_path) if not dry_run else []
    if dry_run:
        return res, ["[dry-run] 未写库"]
    if hasattr(core, "record_email_review"):
        core.record_email_review(**res, db_path=db_path)
        return res, actions + ["email_review 已写入"]
    return res, actions + ["⚠ core.record_email_review 未就绪，email_review 未写（待 Claude 补函数）"]

# ---------------------------------------------------------------------------

def load_spec():
    with open(SPEC_PATH, encoding="utf-8") as f:
        return json.load(f)

def load_messages(inp):
    """输入可以是目录（扫 .json）或单文件，返回 [msg, ...]。"""
    files = []
    if os.path.isdir(inp):
        files = [os.path.join(inp, f) for f in sorted(os.listdir(inp)) if f.endswith(".json")]
    else:
        files = [inp]
    msgs = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
        msgs.extend(data if isinstance(data, list) else [data])
    return msgs

def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 邮件回复分类管线（契约见 spec.json）")
    ap.add_argument("--input", required=True, help="邮件 JSON 文件或目录")
    ap.add_argument("--dry-run", action="store_true", help="只打印判定，不写库不执行动作")
    ap.add_argument("--db", default=None, help="覆盖 spec.json 的 db_path")
    ap.add_argument("--verbose", action="store_true", help="打印完整判定明细")
    args = ap.parse_args()

    db_path = args.db or load_spec()["db_path"]
    msgs = load_messages(args.input)
    if not msgs:
        print(f"未读到邮件: {args.input}")
        return

    stats = {}
    # 自发邮件拦截：from = 被监控邮箱账号本身（email_accounts 表）= 出站原件，非回复
    self_addr = ""
    known_emails = set()
    try:
        import core as _core
        acc = _core.get_email_account(db_path=db_path)
        self_addr = (acc or {}).get("account_email", "").strip().lower()
        for g in _core.list_gmail_contacts(limit=5000, db_path=db_path):
            if g.get("email"):
                known_emails.add(g["email"].strip().lower())
        for c in _core.list_companies(has_email=True, limit=5000, db_path=db_path):
            for e in _core.split_emails(c.get("email")):
                known_emails.add(e.strip().lower())
    except Exception:
        pass
    for msg in msgs:
        res = classify(msg)
        if self_addr and res["from_address"] == self_addr:
            res["classification"], res["rule_id"], res["confidence"] = "unrelated", "R7", 0.90
            res["action_detail"] = f"自发邮件（出站原件，from={self_addr}），非客户回复"
            res["proposed_action"] = "ignored"
        if res["classification"] == "bounce":
            # bounce 的 from 是 mailer-daemon：匹配 + 标无效都用「被弹回的收件人」
            res["bounced_recipient"] = extract_bounced_recipient(
                msg.get("body") or "", self_addr=self_addr, known_emails=known_emails)
            match_addr = res["bounced_recipient"]
        else:
            match_addr = res["from_address"]
        mid, mem, mby = match_sender(match_addr, db_path=db_path)
        res["matched_main_id"], res["matched_email"], res["matched_by"] = mid, mem, mby
        res, actions = write_result(res, db_path, dry_run=args.dry_run)
        label = res["classification"]
        stats[label] = stats.get(label, 0) + 1
        print(f"[{res['rule_id']}] {label:<20} conf={res['confidence']:<5} "
              f"status={res['status']:<8} from={res['from_address'] or '(空)'} "
              f"match={res['matched_by']}"
              + (f" → {mid}" if mid else " → 陌生邮箱"))
        for a in actions:
            print(f"    {a}")
        if args.verbose:
            print(f"    依据: {res['action_detail']}")
            print(f"    摘录: {res['body_snippet'][:120]}...")

    print(f"\n=== 汇总 {len(msgs)} 封 ===")
    for k in sorted(stats, key=lambda x: -stats[x]):
        print(f"  {k:<22} {stats[k]}")

if __name__ == "__main__":
    main()

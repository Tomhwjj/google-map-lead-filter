#!/usr/bin/env python3
"""存量/群发前邮箱质量过滤（WorkBuddy 落地版，规则来自 2026-09-13 退信根因分析）。

三级规则（回测数据：391 个已发邮箱，14 退信）：
  L1 模板/占位黑名单（硬过滤，精度高）：
     - 波兰模板人名 local-part：kowalski/kowalska/anna.kowalska/jan.kowalski…
     - 表单占位词：twoja(nazwa)/prosze(ę)/uzupelnic(ł)/nazwa.firmy/example/test
     - 垃圾域名：mail.com/firma.pl/uzupelnic.pl/example.com 等
     - 注意：不做域名子串模糊匹配（biuro@twojaenergia.pl 是正常邮箱）
  L2 SDK/系统端点黑名单（硬过滤）：sentry/ingest 类假邮箱
  L3 域名相似度降权（软信号，只标记不拦截）：邮箱域名与公司域名不匹配且非常见服务商

用法：
  python email_quality_filter_wb.py scan [--group skill8,skill9]   # 扫描存量，输出报告
  python email_quality_filter_wb.py check --emails a@b.com,c@d.com # 群发前校验单个/一批
只读，不写库；标 invalid 走 core.mark_email_invalid 由人确认后执行。
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402

# ---- L1: 模板/占位黑名单 ----
TPL_LOCAL = (
    "jankowalski", "jan.kowalski", "anna.kowalska",  # 波兰版 John Doe 模板人名
    "twoja.nazwa", "twoj.email", "twoj.adres",       # “您的名字/邮箱/地址”
    "prosze", "proszę", "uzupelnic", "uzupełnic",    # “请填写”
    "nazwa.firmy", "adres.email",
    "example", "test@", "dummy", "placeholder",
)
TPL_DOMAIN = (
    "mail.com", "firma.pl", "uzupelnic.pl", "example.com", "example.pl",
    "domena.pl", "email.pl.", "twojadomena.pl",
    # 2026-09-20 每周迭代新增（退信/扫描实证）：
    "przyklad.pl",   # 波兰语「例」= example（jan@przyklad.pl，HC INSTAL 官网抓到）
    "kowalski.com",  # 占位姓氏做域名（jan@kowalski.com 本周退信，EcoCollect 名下）
    "smith.com",     # 英文版 John Doe（john@smith.com，Lekkie Panele 名下）
    "mysite.com",    # 英文建站器占位（example@mysite.com，AIRSUIT 名下）
)
# ---- L2: SDK/系统端点 ----
SDK_PAT = re.compile(r"@.*(ingest\.|sentry\.io|sentry-next|hooks\.slack|amazonses\.com$)", re.I)

COMMON_PROVIDERS = (
    "gmail.com", "wp.pl", "onet.pl", "interia.pl", "interia.eu", "o2.pl",
    "poczta.fm", "op.pl", "go2.pl", "tlen.pl", "gmx.de", "web.de", "t-online.de",
    "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me",
)
# 主机商/建站服务商域（①类偷跑：官网联系页抓到的其实是建站商客服箱，如 wsparcie/webas@cyberfolks.pl）
# 这些域名出现的邮箱一律硬拦——不可能是企业自有域名
HOSTING_PROVIDERS = (
    "cyberfolks.pl", "nazwa.pl", "home.pl", "ovh.net", "ovh.com", "linuxpl.com",
    "dhosting.pl", "hitme.pl", "superhost.pl", "seohost.pl", "zenbox.pl",
    "kei.pl", "cyber_folks.pl", "smarterp.pl", "linuxpl.com",
)
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def host(email):
    return email.rpartition("@")[2].lower().rstrip(".")


def check_email(email, company_domain=""):
    """返回 (level, reason)。level: hard=拦截, soft=降权标记, ok=通过。"""
    e = (email or "").strip().lower()
    # L0：Claude 的 core.is_syncable_email（sentry/wix占位/no-reply/图片名等，
    # commit 3f33516 skill2 发送失败后加的同步过滤），单一事实来源，直接复用。
    if not core.is_syncable_email(e):
        return "hard", "L0 core.is_syncable_email 命中垃圾/占位/机器人规则"
    if not EMAIL_RE.match(e):
        return "hard", "格式非法"
    lp, _, d = e.partition("@")
    if SDK_PAT.search(e):
        return "hard", "L2 SDK/系统端点邮箱（爬虫误抓上报端点）"
    if d in TPL_DOMAIN:
        return "hard", f"L1 垃圾域名 @{d}（模板/表单占位）"
    for t in TPL_LOCAL:
        if t.endswith("@"):
            if lp == t[:-1]:
                return "hard", f"L1 模板占位 local-part「{t[:-1]}」"
        elif t in lp:
            return "hard", f"L1 模板占位词「{t}」"
    # L3 域名校验：域名不匹配（非常见服务商）= 硬拦——主机商箱（cyberfolks）/串址箱
    # （nicsell）这类直接进不了库，不再软降权；免费服务商兜底 = 保持软降权
    # （企业无正式邮箱时 Gmail 箱仍真实可用，只是优质轨排序靠后）。
    # 匹配放宽：连字符归一（eco-synergia==ecosynergia）+ 交叉词干包含（lmv.pl==lmvgroup.pl）
    if company_domain:
        cd = company_domain.lower().replace("www.", "").rstrip("/")
        eh = host(e)
        def _nz(x):
            return x.replace("-", "")
        cd_n, eh_n = _nz(cd), _nz(eh)
        cd_stem, eh_stem = cd_n.split(".")[0], eh_n.split(".")[0]
        mismatch = (eh_n != cd_n and not eh_n.endswith("." + cd_n)
                    and cd_stem not in eh_n and eh_stem not in cd_n)
        # 主机商箱无条件硬拦（①类：wsparcie/webas@cyberfolks.pl 这类建站商客服箱）。
        # 2026-09-20 迭代：支持子域匹配——rodo@serwer2133633.home.pl 这类
        # 服务器主机名子域此前绕过精确匹配，现在 *.home.pl 一并硬拦。
        if any(d == p or d.endswith("." + p) for p in HOSTING_PROVIDERS):
            return "hard", f"L3 主机商/建站服务商箱（@{d}），非企业自有域名"
        if mismatch:
            if d in COMMON_PROVIDERS:
                return "soft", f"L3 免费服务商兜底邮箱（@{d}），建议发送顺序靠后"
            return "soft", f"L3 域名不匹配（邮箱@{eh} vs 公司 {cd}），疑似品牌域/串址，人工过目"
    return "ok", ""


def cmd_scan(args):
    gc = core.list_gmail_contacts(db_path="data/leads.db")
    groups = {g.strip() for g in (args.group or "").split(",") if g.strip()}
    rows = [r for r in gc if r.get("email") and r["status"] in ("pending", "synced")]
    if groups:
        rows = [r for r in rows if (r.get("skill_group") or "?") in groups]
    hard, soft = [], []
    for r in rows:
        c = core.get_company(r["main_id"], db_path="data/leads.db").get("company", {}) if r.get("main_id") else {}
        lvl, why = check_email(r["email"], c.get("domain") or "")
        g = r.get("skill_group") or "?"
        if lvl == "hard":
            hard.append((g, r["email"], why, r["status"]))
        elif lvl == "soft":
            soft.append((g, r["email"], why, r["status"]))
    print(f"扫描范围：{len(rows)} 个邮箱（组：{sorted(groups) if groups else '全部'}）")
    print(f"\n=== 硬过滤（建议标 invalid，禁止入群发）{len(hard)} 个 ===")
    for g, e, w, s in sorted(hard):
        print(f"  [{g}] {e}  ({w}; 状态={s})")
    print(f"\n=== 软降权（可发但建议人工过目）{len(soft)} 个 ===")
    for g, e, w, s in sorted(soft):
        print(f"  [{g}] {e}  ({w})")
    return hard, soft


def cmd_check(args):
    bad = 0
    for e in (args.emails or "").split(","):
        e = e.strip()
        if not e:
            continue
        lvl, why = check_email(e)
        print(f"  {lvl.upper():4s} {e}  {why}")
        bad += lvl == "hard"
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("scan")
    s1.add_argument("--group", default="")
    s2 = sub.add_parser("check")
    s2.add_argument("--emails", required=True)
    a = p.parse_args()
    {"scan": cmd_scan, "check": cmd_check}[a.cmd](a)

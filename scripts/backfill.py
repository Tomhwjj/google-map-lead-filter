#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
背调脚本：读 fetch_gmaps.py 输出的 CSV，抓每个线索的官网首页 + contact 页，
提取标题 / meta 描述 / 邮箱 / 正文，输出 JSON 供 Claude 判断。

用法:
    python backfill.py leads.csv --out backfill.json
"""
import argparse
import atexit
import csv
import json
import os
import random
import re
import signal
import sys
import time

from playwright.sync_api import sync_playwright

DEFAULT_PROXY = ""  # 官网一般可直连，默认不走代理；需要时用 --proxy 指定
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 常见联系方式页路径（按优先级，找到邮箱即停）
# 波兰市场实证：PL 公司邮箱常在 /kontakt /bok（客服）/ impressum 页；首页页脚为兜底
CONTACT_PATHS = ["contact", "kontakt", "impressum", "bok", "about", "about-us",
                 "ueber-uns", "contact-us", "en/contact", "de/kontakt"]

# 品牌/产品页路径（品牌未命中时抓，判断官网代理哪些品牌；含德语路径 marken/hersteller/produkte）
# ⚠️ 仅作兜底：硬编码英文/德语路径对波兰语等 WooCommerce 站（如 /kategoria/.../falowniki/）全部 404，
#   导致竞品品牌漏判（2026-09-11 Oze-Ekoshop 教训）。主路径改为「从首页自动提取产品分类链接」。
BRAND_PATHS = ["brands", "products", "inverters", "battery-storage", "batteries",
               "manufacturers", "marken", "hersteller", "produkte",
               # 波兰语（主力市场）WooCommerce 常见产品/分类路径（2026-09-24 补，
               # 此前硬编码英/德对波兰语站全 404，见 task_issues #16 / 对接文归因断点 2）
               "produkty", "kategoria", "falowniki", "magazyn-energii", "sklep",
               "falownik", "magazyny-energii", "panele-fotowoltaiczne"]

# 品类词兜底（2026-09-24，第 2 步）：品牌名抓不到时，认品类不认品牌。
# body 自述卖光伏/储能/逆变器 = 品类渠道（增量 24），口径 qualification-rules.md L47/L59。
# 与「真无产品证据」区分开——后者才是 0 分兜底（断点 3 修的就是这个「混成同一个 0」）。
CATEGORY_KW = [
    # 逆变器（多语言）
    "falownik", "inwerter", "inverter", "wechselrichter",
    # 储能 / 电池
    "magazyn energii", "magazyn", "akumul", "battery", "storage", "speicher",
    # 光伏
    "fotowoltaik", "photovoltaic", "photovoltaik", "solar", "panele fotowoltaiczne",
    # 混合 / 并网
    "hybryd", "hybrid",
]

# 多语言产品/品牌链接关键词（自动提取分类链接用，避免语言硬编码）
LINK_PRODUCT_KW = [
    "invert", "falownik", "inwerter", "hybryd", "hybrid",
    "batter", "akumul", "magazyn", "storage",
    "solar", "fotowoltaik", "photovoltaic", "panele", "panel",
    "produkt", "product", "produkty", "sklep", "shop",
    "brand", "marka", "marki", "producent", "manufactur", "hersteller", "herstell",
]
# 导航/杂项链接排除（避免抓到博客/政策/登录页浪费请求）
LINK_EXCLUDE_KW = [
    "blog", "kontakt", "contact", "about", "o-firmie", "o-nas", "polityka",
    "regulamin", "cookie", "dostawa", "wysylka", "reklamacje", "zwroty",
    "konto", "login", "koszyk", "cart", "checkout", "strefa", "promocje",
    "okazje", "bestseller", "feed", "wp-json", "wp-content", "cdn-cgi",
    ".css", ".js", ".png", ".jpg", ".svg", ".ico", "xmlrpc", "comments",
]


def extract_product_links(html, website):
    """从首页 HTML 提取站内产品/品牌分类链接（多语言通用，替代硬编码英文路径）。"""
    import urllib.parse as up
    links = re.findall(r'href=["\']([^"\']+)["\']', html)
    base = website.rstrip("/")
    try:
        base_domain = up.urlparse(website).netloc
    except Exception:
        return []
    seen = set()
    out = []
    for l in links:
        l = l.strip()
        if not l or l.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        if l.startswith("//"):
            l = "https:" + l
        elif l.startswith("/"):
            l = base + l
        try:
            if up.urlparse(l).netloc != base_domain:
                continue
        except Exception:
            continue
        low = l.lower()
        if any(k in low for k in LINK_EXCLUDE_KW):
            continue
        if any(k in low for k in LINK_PRODUCT_KW):
            if l not in seen:
                seen.add(l)
                out.append(l)

    # 逆变器/储能/电池页品牌最集中，排最前
    def prio(u):
        low = u.lower()
        if any(k in low for k in ("invert", "falownik", "inwerter", "hybryd",
                                  "hybrid", "magazyn", "storage", "batter", "akumul")):
            return 0
        return 1
    out.sort(key=prio)
    return out


# 2026-09-12 WorkBuddy 加：邮箱格式过滤。scrape 常把资源文件/模板垃圾当邮箱抓回来
# （如 pvgroup-logo@2x.png、2023-07-11T06-26-22.775Z@900X1200-...、john@home.com）。
_EMAIL_JUNK_DOMAIN_PARTS = (
    "example.com", "example.net", "example.org", "domain.com", "yourdomain",
    "company.com", "home.com", "email.com", "test.com", "gtempaccount.com",
    "wixpress", "sentry", "godaddy",
)
_EMAIL_JUNK_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
                    ".css", ".js", ".webmanifest", ".woff", ".ttf")


def is_junk_email(e):
    """判断单个邮箱是否为 scrape 垃圾（资源文件后缀/模板地址/时间戳串/长哈希）。"""
    low = (e or "").strip().lower()
    if "@" not in low:
        return True
    local, dom = low.rsplit("@", 1)
    if any(x in dom for x in _EMAIL_JUNK_DOMAIN_PARTS):
        return True
    if dom.endswith(_EMAIL_JUNK_EXTS):
        return True
    if re.search(r"\d{4}-\d{2}-\d{2}", local):        # 时间戳串
        return True
    if re.fullmatch(r"[0-9a-f]{16,}", local):          # 长 hex 哈希
        return True
    if len(local) > 64:                                # 异常超长 local part
        return True
    return False


def extract_emails(text):
    out = set()
    for e in re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text):
        # HTML 转义残留会把真邮箱粘脏（如 \u003e 渲染成文本 'u003e' 粘在 local part 前），
        # 剥掉前缀后重新校验，'u003ekontakt@x.pl' -> 'kontakt@x.pl'
        e2 = re.sub(r"^(u003[eE]|u003[cC]|%3e|%3c|&gt;|&lt;|>+|<+)", "", e)
        if not is_junk_email(e2):
            out.add(e2)
    return sorted(out)


def find_brands(text, brands):
    """在文本中搜索品牌关键词（词边界匹配，避免 INGE 误命中 springen/Ingenieur），返回 {品牌: 上下文片段}。"""
    found = {}
    low = text.lower()
    for b in brands:
        m = re.search(r"(?<![a-z0-9])" + re.escape(b.lower()) + r"(?![a-z0-9])", low)
        if m:
            start = max(0, m.start() - 100)
            end = min(len(text), m.end() + 100)
            found[b] = text[start:end].strip()
    return found


def find_categories(text):
    """检测正文是否自述光伏/储能/逆变器品类，返回命中词列表。

    与 find_brands 并列的「品类证据」来源：brands_found 空 ≠ 无产品证据——
    body 明写 falownik / magazyn energii / wechselrichter 的是品类渠道（增量 24），
    与「真无产品证据」（0 分兜底）要分开。单词用词边界（防 inverter⊂converter 误命中），
    多词短语直接子串匹配。
    """
    low = text.lower()
    hits = []
    for kw in CATEGORY_KW:
        if " " in kw:
            if kw in low:
                hits.append(kw)
        elif re.search(r"(?<![a-z0-9])" + re.escape(kw) + r"(?![a-z0-9])", low):
            hits.append(kw)
    return hits


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # 2026-09-18 WorkBuddy 加：优雅停止。CTRL_C / CTRL_BREAK 只置标志，做完当前一条、
    # 落盘、关浏览器后退出。硬杀（taskkill /F / 关会话）也不丢数据——每条完成即原子
    # 落盘（tmp+os.replace），任何时刻停止最多损失「正在抓的那一条」，重跑同命令即续跑。
    _stop = {"flag": False}

    def _request_stop(sig, _frm):
        _stop["flag"] = True
        print(f"\n[stop] 收到信号 {sig}，完成当前一条后退出（已完成记录均已落盘）", flush=True)

    signal.signal(signal.SIGINT, _request_stop)          # Ctrl+C
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _request_stop)    # Ctrl+Break / taskkill 优雅信号

    # 连败熔断（2026-09-20 WorkBuddy 加）：连续 NET_BREAK 条"网络死亡级"错误
    # （ERR_INTERNET_DISCONNECTED / ERR_NETWORK_IO_SUSPENDED）即判定本机断网并停止，
    # 防止断网后把剩余队列逐条刷成假失败（2026-09-19 教训：451 家被污染需整轮重试）。
    # 只认断网类签名：DNS 失败（死域名）与 ERR_CONNECTION_CLOSED（反爬/烂服务器）
    # 不计数，避免误熔断。
    NET_BREAK = 5
    _net_fail = {"n": 0}

    ap = argparse.ArgumentParser(description="背调：抓官网提取邮箱/正文")
    ap.add_argument("csv", help="fetch_gmaps.py 输出的 CSV")
    ap.add_argument("--out", default="backfill.json", help="输出 JSON 路径")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, help="代理地址（默认直连）")
    ap.add_argument("--max", type=int, default=0, help="最多背调条数 (0=全部)")
    ap.add_argument("--brands", default="", help="品牌列表（我方+贴牌+竞品），逗号分隔，如 'Deye,Sungrow,Huawei'")
    ap.add_argument("--deye", default="", help="我方品牌（含贴牌），逗号分隔。品牌页抓到命中这些为止（命中竞品不算，继续找 Deye）")
    ap.add_argument("--fast", action="store_true", help="快速模式：只抓首页+品牌页找品牌，跳过 contact 页。⚠️仅限测试，正式跑会系统性丢证据")
    ap.add_argument("--goto-timeout", type=int, default=35000, help="首页 goto 超时毫秒（2026-09-20 起默认 35000；20s 时代超时偏紧损失大量慢站）")
    args = ap.parse_args()
    if args.fast:
        # 改进 #5（2026-09-24）：--fast 曾被当「加速开关」误用，09-12 批 1516 家
        # 「拿到正文 4%」就是它的代价（跳 contact 页 + 品牌页只抓 2 个 + 配额减半）。
        # 不拒绝（测试/调试场景要它），但必须醒目警告，让「证据系统性缺失」不再无声。
        print("\n⚠️  --fast 仅限测试：跳过 contact 页、品牌页只抓 2 个、正文配额减半，"
              "正式获客会系统性丢邮箱/品牌证据。正式跑请去掉 --fast。\n", flush=True)
    brands = [b.strip() for b in (args.brands or "").split(",") if b.strip()]
    deye_brands = {b.strip().lower() for b in (args.deye or "").split(",") if b.strip()}

    # 2026-09-18 WorkBuddy 加：防双开锁。同一 out 文件同时只允许一个实例，
    # 否则两个进程交替写同一个 json 会互相覆盖丢数据（自动化触发撞上手动续跑的风险）。
    # 锁内容是 PID：持有者死了（硬杀后残留锁）下次启动自动清理，不会卡死。
    lock_path = args.out + ".lock"

    def _pid_alive(pid):
        if pid <= 0:
            return False
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if h:
                try:
                    # 2026-09-22 WorkBuddy 修：OpenProcess 成功 ≠ 进程活着——
                    # 进程句柄被第三方持有（父进程/宿主）会延缓进程对象回收，
                    # 必须再查终止状态（WaitForSingleObject(0)=0 即已终止）。
                    # 实测：TaskStop/硬杀后死 PID 被 OpenProcess 打开成功，
                    # 旧逻辑误判"活着"导致锁永不清理、续跑被挡。
                    return ctypes.windll.kernel32.WaitForSingleObject(h, 0) != 0
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
            return False
        except Exception:
            return True  # 判不了就保守当活着

    if os.path.exists(lock_path):
        try:
            old_pid = int(open(lock_path, encoding="utf-8").read().strip() or 0)
        except Exception:
            old_pid = 0
        if _pid_alive(old_pid):
            print(f"[exit] 已有实例在运行 (PID {old_pid}，锁 {lock_path})，本实例直接退出："
                  f"不重复跑、不碰数据文件，重跑是安全的。", flush=True)
            return
        print(f"[lock] 清理失效锁 (旧 PID {old_pid})", flush=True)
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.path.exists(lock_path) and os.remove(lock_path))

    contact_paths = [] if args.fast else CONTACT_PATHS
    brand_paths = BRAND_PATHS[:2] if args.fast else BRAND_PATHS
    home_sleep = 0.5 if args.fast else random.uniform(1, 2)

    leads = []
    with open(args.csv, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            leads.append(row)

    results = []
    # 断点续跑（2026-09-14 WorkBuddy 加）：out json 已存在时载入已完成记录，
    # 按 website/公司名跳过；结果改为逐条落盘，中断后重跑同命令即续跑。
    done_keys = set()
    if os.path.exists(args.out):
        try:
            with open(args.out, encoding="utf-8") as f:
                results = json.load(f)
            done_keys = set()
            for r in results:
                k = ((r.get("website") or "").strip().rstrip("/").lower()
                     or (r.get("company_name") or "").strip().lower())
                if k:
                    done_keys.add(k)
            print(f"断点续跑：已有 {len(results)} 条，跳过已完成", flush=True)
        except Exception as e:
            print(f"[warn] 断点文件损坏，从头开始: {e}", flush=True)
            results = []
            done_keys = set()

    def _save():
        tmp = args.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        os.replace(tmp, args.out)

    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if args.proxy:
            launch_kwargs["proxy"] = {"server": args.proxy}
        else:
            # 默认直连，不读系统代理——否则系统代理挂掉会 ERR_PROXY_CONNECTION_FAILED（2026-09 实测 38/50 家失败）
            launch_kwargs["args"] = ["--no-proxy-server"]
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(user_agent=UA, locale="en-US",
                                  viewport={"width": 1280, "height": 800},
                                  ignore_https_errors=True)  # 2026-09-16: 证书无效/过期站照抓（此前 ~15 家 CERT 错误直接丢弃）
        page = ctx.new_page()

        for i, lead in enumerate(leads):
            if _stop["flag"]:
                print(f"[stop] 在第 {i + 1}/{len(leads)} 条前停止：已完成 {len(results)} 条，"
                      f"断点已落盘，重跑同命令即无损续跑。", flush=True)
                break
            if args.max and i >= args.max:
                break
            name = lead.get("company_name", "").strip()
            website = (lead.get("website") or "").strip()
            # 断点跳过：website 优先（空则退回公司名），统一去尾部斜杠避免变体重做
            _key = (website or name).strip().rstrip("/").lower()
            if _key and _key in done_keys:
                print(f"[{i + 1}/{len(leads)}] {name}: 已完成，跳过", flush=True)
                continue
            # 继承原始字段（city/phone/rating/country/email/customer_type/address/
            # profile_url/source_url/google_maps_url/raw_text），绝不丢字段
            # （2026-09-05 教训：rec 只输出自己抓的字段，丢 source 字段导致入库 country 全空）
            rec = dict(lead)
            rec.update({
                "company_name": name,
                "website": website,
                "title": "",
                "meta": "",
                "emails": [],
                "brands_found": [],
                "brands_context": {},
                "category_hits": [],  # 品类词命中（2026-09-24 第 2 步）：body 自述卖光伏/储能/逆变器
                "body": "",
                "error": "",
            })
            if website.startswith("http"):
                texts = []          # 公司信息正文（首页 + 联系页）
                product_texts = []  # 产品页正文（品牌证据主来源，2026-09-24 单列，别再混进 texts 被联系页挤占配额）
                home_html = ""  # 首页原始 HTML，供品牌链接自动提取（不落库）
                try:
                    page.goto(website, timeout=args.goto_timeout, wait_until="domcontentloaded")
                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    time.sleep(home_sleep)
                    rec["title"] = page.title()
                    rec["meta"] = page.evaluate(
                        "() => document.querySelector('meta[name=\"description\"]')?.content || ''"
                    )
                    home_html = page.content()
                    rec["emails"] = extract_emails(home_html)
                    texts.append((page.inner_text("body") or "")[:5000])
                except Exception as e:
                    rec["error"] = str(e)[:200]

                # 联系方式页（找到邮箱即停）
                for path in contact_paths:
                    if rec["emails"]:
                        break
                    try:
                        url = website.rstrip("/") + "/" + path
                        page.goto(url, timeout=15000, wait_until="domcontentloaded")
                        time.sleep(random.uniform(1, 2))
                        c = page.content()
                        rec["emails"] = sorted(set(rec["emails"] + extract_emails(c)))
                        # 联系页正文只留 1500：其唯一价值是邮箱（品牌证据主来源在产品页），
                        # 别让它挤占 body 配额（2026-09-24，归因断点 2）
                        texts.append((page.inner_text("body") or "")[:1500])
                    except Exception:
                        pass

                rec["body"] = " ".join(texts)[:8000]
                if brands and rec["body"]:
                    brand_ctx = find_brands(rec["body"], brands)  # 勿名 ctx：会遮蔽 Playwright context
                    rec["brands_found"] = list(brand_ctx.keys())
                    rec["brands_context"] = brand_ctx

                # 品牌页（没确认卖我方品牌时继续抓——命中竞品不代表排除卖 Deye）
                # 传了 --deye：抓到命中我方品牌才停；没传：命中任意品牌即停（原逻辑）
                def should_keep_going():
                    if deye_brands:
                        return not any((b or "").lower() in deye_brands for b in rec["brands_found"])
                    return not rec["brands_found"]

                if brands and should_keep_going():
                    # 品牌页来源：优先从首页自动提取产品分类链接（多语言通用，根治
                    # 波兰语 WooCommerce 站漏判），提取不到回退硬编码路径兜底（已补波兰语）。
                    product_links = extract_product_links(home_html, website)
                    brand_urls = product_links or [
                        website.rstrip("/") + "/" + p for p in brand_paths]
                    for url in brand_urls[:12]:  # 最多抓 12 个产品页（2026-09-24 6→12：产品证据主来源，别在取证上抠时间）
                        if not should_keep_going():
                            break
                        try:
                            try:
                                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                            except Exception:
                                # 产品页是品牌证据主来源，超时重试一次（10s 偏紧损失慢站，归因断点 2）
                                page.goto(url, timeout=20000, wait_until="domcontentloaded")
                            try:
                                page.wait_for_load_state("networkidle", timeout=4000)
                            except Exception:
                                pass
                            time.sleep(random.uniform(0.3, 0.6))
                            product_texts.append((page.inner_text("body") or "")[:3000])
                            # 品牌判断吃「产品页 + 公司信息」全部正文，产品页优先
                            brand_ctx = find_brands(" ".join(product_texts + texts), brands)
                            rec["brands_found"] = list(brand_ctx.keys())
                            rec["brands_context"] = brand_ctx
                        except Exception:
                            pass

                # 产品页正文并回 body（产品优先）+ 品类词检测（2026-09-24，归因断点 1）
                # 原先 body 在品牌页循环前就冻结，产品页正文从不写回 → 判档/闸门拿到的
                # 正文永远缺产品信号（941 家 90.6% 无产品证据的根因之一）。
                # body 产品优先组装，配额 8000→24000，容纳约 12 个产品页。
                rec["body"] = (" ".join(product_texts + texts))[:24000]
                rec["category_hits"] = find_categories(rec["body"]) if rec["body"] else []
            else:
                rec["error"] = "no website"

            results.append(rec)
            _save()  # 逐条落盘，断电/中断不丢
            print(f"[{i + 1}/{len(leads)}] {name}: {len(rec['emails'])} emails, "
                  f"brands={rec['brands_found']}", flush=True)
            # 连败熔断检查：断网签名连击即停，并回滚全部断网假失败记录
            # （回滚后断点键不再包含它们，恢复网络重跑同命令会真正重抓，而不是跳过）。
            if "ERR_INTERNET_DISCONNECTED" in rec["error"] or \
                    "ERR_NETWORK_IO_SUSPENDED" in rec["error"]:
                _net_fail["n"] += 1
                if _net_fail["n"] >= NET_BREAK:
                    before = len(results)
                    results = [r for r in results
                               if "ERR_INTERNET_DISCONNECTED" not in (r.get("error") or "")
                               and "ERR_NETWORK_IO_SUSPENDED" not in (r.get("error") or "")]
                    _save()
                    _stop["flag"] = True
                    print(f"[熔断] 连续 {NET_BREAK} 条断网级错误，判定本机断网，停止运行；"
                          f"已回滚 {before - len(results)} 条断网假失败"
                          f"（恢复网络后重跑同命令即补抓）。", flush=True)
            else:
                _net_fail["n"] = 0

        browser.close()

    _save()
    tail = "已停止（断点已保存，重跑同命令续跑）" if _stop["flag"] else "背调完成"
    print(f"{tail}: {len(results)} 条 -> {args.out}")


if __name__ == "__main__":
    main()

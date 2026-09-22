#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 Facebook 触达花名册（HTML，只读 DB，不写任何表）。

数据源：
  - data/wb_social_links.json  （wb_social_scan.py 抓的社交链接快照）
  - leads.db via core.py       （company 字段最新值：email/phone/city/country/grade/facebook）
  - gmail_contacts.skill_group （skill11 徽标）

用法：
  PYTHONPATH=scripts python scripts/wb_fb_roster.py [-o 输出.html]

说明：
  - 有 FB 主页的进第一张表（主战场，按 skill11 → A → B 排序）
  - 官网正常但没挂 FB 的进第二张表（去 FB 站内搜公司名）
  - 两张表都带「国家」列和 skill11 徽标（v3 修复：旧版第二张表没打徽标，导致看起来 skill11 只有 5 家）
"""
import argparse
import html
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import core  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOCIAL_JSON = os.path.join(ROOT, "data", "wb_social_links.json")
DEFAULT_OUT = r"C:\ProgramData\WorkBuddy\chromium-env\135wjb7\WorkBuddy\2026-09-10-19-25-30\FB触达花名册.html"

FLAG = {"PL": "🇵🇱", "DE": "🇩🇪", "ES": "🇪🇸", "CZ": "🇨🇿", "NL": "🇳🇱"}


def build_rows():
    results = json.load(open(SOCIAL_JSON, encoding="utf-8"))
    sk11 = {c.get("main_id") for c in core.list_gmail_contacts() if c.get("skill_group") == "skill11"}
    rows = []
    for r in results:
        co = (core.get_company(r["main_id"]) or {}).get("company") or {}
        emails = core.split_emails(co.get("email") or "")
        country = (co.get("country") or "").upper()
        rows.append({
            "mid": r["main_id"], "name": r["company"], "grade": co.get("grade") or "?",
            "city": co.get("city") or "", "country": country,
            "type": co.get("customer_type") or "",
            "fb": co.get("facebook") or r.get("facebook"),
            "li": co.get("linkedin") or r.get("linkedin"),
            "emails": emails, "nmail": len(emails),
            "phone": co.get("phone") or "",
            "sk11": r["main_id"] in sk11, "ok": r["ok"],
        })
    order = {"A": 0, "B": 1}
    rows.sort(key=lambda x: (not x["fb"], not x["sk11"], order.get(x["grade"], 2), x["name"]))
    return rows


def badges(r):
    b = ""
    if r["sk11"]:
        b += '<span class="b b11">skill11</span>'
    if r["grade"] in ("A", "B"):
        b += f'<span class="b b{r["grade"]}">{r["grade"]}</span>'
    return b


def cell(v, label):
    return f'<a href="{html.escape(v)}" target="_blank">{label}</a>' if v else '<span class="no">—</span>'


def flag(c):
    return f'{FLAG.get(c, "")} {c}' if c else '<span class="no">?</span>'


def render(rows, out_path):
    fb_rows, nofb = [], []
    for r in rows:
        if r["fb"]:
            email_txt = ('<span class="mono">' + html.escape(r["emails"][0]) +
                         (f' <span class="b bN">+{r["nmail"]-1}</span>' if r["nmail"] > 1 else '') + '</span>'
                         ) if r["emails"] else '<span class="no">无</span>'
            fb_rows.append(f'''<tr>
<td class="nm">{html.escape(r["name"])}{badges(r)}</td>
<td>{flag(r["country"])}</td>
<td>{html.escape(r["city"])}</td>
<td>{html.escape(r["type"])}</td>
<td>{cell(r["fb"], "FB主页")}</td>
<td>{cell(r["li"], "LI")}</td>
<td>{email_txt}</td>
<td class="mono">{html.escape((r["phone"] or "")[:26])}</td>
<td><label><input type="checkbox"> 完成</label></td></tr>''')
        elif r["ok"]:
            nofb.append(f'''<tr>
<td class="nm">{html.escape(r["name"])}{badges(r)}</td>
<td>{flag(r["country"])}</td>
<td>{html.escape(r["city"])}</td>
<td>{r["nmail"]} 箱</td>
<td><span class="mono">{html.escape(r["emails"][0]) if r["emails"] else "—"}</span></td>
<td>FB 站内搜公司名</td></tr>''')

    sk11_fb = [r for r in rows if r["sk11"] and r["fb"]]
    sk11_nofb = [r for r in rows if r["sk11"] and not r["fb"]]
    sk11_all = [r for r in rows if r["sk11"]]
    cnt = lambda xs, c: sum(1 for r in xs if r["country"] == c)
    pl_all = sum(1 for r in rows if r["country"] == "PL")
    de_all = sum(1 for r in rows if r["country"] == "DE")

    doc = f'''<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>Facebook 触达花名册 v3</title><style>
body{{font-family:system-ui,sans-serif;margin:24px;color:#1a1a1a;background:#fafafa}}
h1{{font-size:20px}} .sum{{color:#555;font-size:14px;margin-bottom:16px;line-height:1.7}}
table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{border:1px solid #ddd;padding:6px 8px;text-align:left}}
th{{background:#eef2f7;position:sticky;top:0}}
tr:nth-child(even){{background:#f7f9fb}} .nm{{font-weight:600}}
a{{color:#185fa5;text-decoration:none}} a:hover{{text-decoration:underline}}
.mono{{font-family:ui-monospace,monospace;font-size:12px}}
.no{{color:#bbb}} h2{{font-size:15px;margin-top:28px}}
.b{{display:inline-block;font-size:10px;padding:1px 6px;border-radius:8px;margin-left:6px;vertical-align:middle}}
.b11{{background:#0f6e56;color:#fff}} .bA{{background:#185fa5;color:#fff}} .bB{{background:#888;color:#fff}} .bN{{background:#c77c11;color:#fff}}
.k{{background:#fff8e6;border-left:3px solid #c77c11;padding:8px 12px;margin:10px 0;font-size:13px;line-height:1.7}}
</style></head><body>
<h1>Facebook 触达花名册 v3 — 有 FB 主页的企业（{len(fb_rows)} 家）</h1>
<div class="sum">数据取自库内最新值（含 9/14-9/16 扫图回填）。橙色 +N = 该企业有 N 个邮箱（多箱=波次换箱余地大）。排序：skill11 → A → B。全国别分布：PL {pl_all} / DE {de_all} / 其他 {len(rows)-pl_all-de_all}。</div>
<div class="k"><b>skill11（优质）组编制共 {len(sk11_all)} 家 = PL {cnt(sk11_all,"PL")} + DE {cnt(sk11_all,"DE")}（DE 为早期测试数据，用户 9/16 裁定保持现状）。</b><br>
本表（有 FB）能看到的 skill11 = <b>{len(sk11_fb)} 家</b>：PL {cnt(sk11_fb,"PL")}（7SUN、Elus）+ DE {cnt(sk11_fb,"DE")}（SchmitzSolar、MD Enrgy、Greenlimon）；<br>
另 <b>{len(sk11_nofb)} 家</b>因无 FB 主页落在下方第二张表（PL 2：Elementum PV、Free Energy / DE 5）。</div>
<table><thead><tr><th>企业</th><th>国家</th><th>城市</th><th>类型</th><th>Facebook</th><th>LinkedIn</th><th>主邮箱</th><th>电话</th><th>打卡</th></tr></thead>
<tbody>{"".join(fb_rows)}</tbody></table>
<h2>官网正常但没挂 FB 的（{len(nofb)} 家）— 去 FB 站内搜公司名</h2>
<table><thead><tr><th>企业</th><th>国家</th><th>城市</th><th>邮箱数</th><th>主邮箱</th><th>动作</th></tr></thead><tbody>{"".join(nofb)}</tbody></table>
</body></html>'''

    open(out_path, "w", encoding="utf-8").write(doc)
    print(f"已生成: {out_path}")
    print(f"有 FB: {len(fb_rows)} 家 | 需站内搜: {len(nofb)} 家")
    print(f"skill11 组 {len(sk11_all)} 家 (PL {cnt(sk11_all,'PL')} / DE {cnt(sk11_all,'DE')})")
    print(f"  ├ 有 FB 的 {len(sk11_fb)} 家: PL {cnt(sk11_fb,'PL')} / DE {cnt(sk11_fb,'DE')} -> " +
          "、".join(f'{r["name"].split()[0]}({r["country"]})' for r in sk11_fb))
    print(f"  └ 无 FB 的 {len(sk11_nofb)} 家: PL {cnt(sk11_nofb,'PL')} / DE {cnt(sk11_nofb,'DE')} -> " +
          "、".join(f'{r["name"].split()[0]}({r["country"]})' for r in sk11_nofb))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    render(build_rows(), a.out)

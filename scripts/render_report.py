#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
线索报告生成器：读已评分的线索 JSON，生成自包含 HTML 报告（双击即开）。

输入 JSON 字段（每条线索）：
  company_name, country, city, website, phone, email, linkedin,
  customer_type, score, grade, reason, google_maps_url, source_url,
  brands_found, score_detail

用法:
    python render_report.py leads_final.json --out report.html --title "英国光伏经销商线索"
"""
import argparse
import html
import json
import sys

GRADE_STYLE = {
    "A": {"color": "#16a34a", "bg": "#ecfdf3", "label": "A 级 · 优先跟进"},
    "B": {"color": "#d97706", "bg": "#fffbeb", "label": "B 级 · 培育跟进"},
    "C": {"color": "#6b7280", "bg": "#f3f4f6", "label": "C 级 · 暂存"},
}

DIM_CN = {"产品匹配": "产品匹配(30)", "渠道": "渠道匹配(25)", "规模": "公司规模(20)",
          "联系人": "联系人(15)", "活跃": "活跃度(10)"}
DIM_MAX = {"产品匹配": 30, "渠道": 25, "规模": 20, "联系人": 15, "活跃": 10}


def esc(s):
    return html.escape(str(s or ""))


def render(leads, title):
    total = len(leads)
    counts = {"A": 0, "B": 0, "C": 0}
    for l in leads:
        g = (l.get("grade") or "C").upper()
        counts[g] = counts.get(g, 0) + 1
    hit_brand = sum(1 for l in leads if l.get("brands_found"))

    cards = []
    for l in leads:
        g = (l.get("grade") or "C").upper()
        st = GRADE_STYLE.get(g, GRADE_STYLE["C"])
        brands = "".join(f'<span class="brand">{esc(b)}</span>'
                         for b in (l.get("brands_found") or []))
        detail = l.get("score_detail") or {}
        detail_rows = ""
        if detail:
            bars = "".join(
                f'<div class="dim"><span class="dim-name">{esc(DIM_CN.get(k, k))}</span>'
                f'<div class="bar"><i style="width:{int(v) / DIM_MAX.get(k, 100) * 100:.0f}%"></i></div>'
                f'<span class="dim-val">{int(v)}</span></div>'
                for k, v in detail.items())
            detail_rows = f'<div class="detail">{bars}</div>'
        linkedin = ""
        if l.get("linkedin"):
            linkedin = f'<a class="chip" href="{esc(l["linkedin"])}" target="_blank">LinkedIn</a>'
        cards.append(f'''
        <article class="card" data-grade="{g}">
          <div class="card-head">
            <span class="grade" style="color:{st['color']};background:{st['bg']}">{g}</span>
            <h3 class="name">{esc(l.get("company_name"))}</h3>
            <span class="score">{int(l.get("score", 0))}<small>分</small></span>
          </div>
          <div class="meta">
            <span class="ctype">{esc(l.get("customer_type"))}</span>
            <span class="loc">{esc(l.get("country"))} · {esc(l.get("city"))}</span>
            {brands}
          </div>
          <div class="contacts">
            {f'<a class="chip" href="{esc(l["website"])}" target="_blank">官网</a>' if l.get("website") else ""}
            {f'<a class="chip" href="tel:{esc(l["phone"])}">{esc(l["phone"])}</a>' if l.get("phone") else ""}
            {f'<a class="chip" href="mailto:{esc(l["email"])}">{esc(l["email"])}</a>' if l.get("email") else ""}
            {linkedin}
            {f'<a class="chip" href="{esc(l["google_maps_url"])}" target="_blank">Google Maps</a>' if l.get("google_maps_url") else ""}
          </div>
          {detail_rows}
          <p class="reason"><strong>开发理由：</strong>{esc(l.get("reason"))}</p>
        </article>''')
    cards_html = "\n".join(cards)

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{esc(title)}</title>
<style>
  :root {{ --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --bg:#f5f6f8; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:28px 20px 60px; }}
  header h1 {{ font-size:24px; font-weight:700; }}
  header p {{ color:var(--muted); margin-top:4px; font-size:14px; }}
  .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:22px 0; }}
  .stat {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .stat .num {{ font-size:26px; font-weight:700; }}
  .stat .lbl {{ font-size:12px; color:var(--muted); margin-top:2px; }}
  .filter {{ display:flex; gap:8px; margin:8px 0 16px; }}
  .filter button {{ border:1px solid var(--line); background:#fff; padding:7px 18px; border-radius:999px; cursor:pointer; font-size:14px; color:var(--ink); }}
  .filter button.active {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin-bottom:14px; }}
  .card-head {{ display:flex; align-items:center; gap:10px; }}
  .grade {{ font-weight:700; padding:2px 12px; border-radius:999px; font-size:13px; }}
  .name {{ font-size:17px; font-weight:700; flex:1; }}
  .score {{ font-size:22px; font-weight:800; color:var(--ink); }}
  .score small {{ font-size:12px; color:var(--muted); font-weight:400; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:10px 0 8px; }}
  .ctype {{ background:#eef2ff; color:#4338ca; padding:2px 10px; border-radius:6px; font-size:12px; }}
  .loc {{ color:var(--muted); font-size:13px; }}
  .brand {{ background:#ecfeff; color:#0e7490; padding:2px 10px; border-radius:6px; font-size:12px; font-weight:600; }}
  .contacts {{ display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 12px; }}
  .chip {{ font-size:13px; padding:5px 12px; border:1px solid var(--line); border-radius:8px; color:#2563eb; text-decoration:none; }}
  .chip:hover {{ background:#f8fafc; }}
  .detail {{ border-top:1px dashed var(--line); padding-top:10px; margin-bottom:10px; }}
  .dim {{ display:flex; align-items:center; gap:10px; margin:6px 0; font-size:13px; }}
  .dim-name {{ width:110px; color:var(--muted); flex-shrink:0; }}
  .bar {{ flex:1; height:8px; background:#eef2f7; border-radius:4px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:linear-gradient(90deg,#3b82f6,#2563eb); border-radius:4px; }}
  .dim-val {{ width:28px; text-align:right; font-weight:600; }}
  .reason {{ font-size:13.5px; color:#334155; }}
  .reason strong {{ color:var(--ink); }}
  @media (max-width:640px) {{ .stats {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{esc(title)}</h1>
    <p>外贸经销商线索挖掘与分级 · 来源 Google Maps + 官网背调 · 反幻觉：字段均可追溯</p>
  </header>
  <div class="stats">
    <div class="stat"><div class="num">{total}</div><div class="lbl">线索总数</div></div>
    <div class="stat"><div class="num" style="color:#16a34a">{counts.get("A",0)}</div><div class="lbl">A 级 · 优先跟进</div></div>
    <div class="stat"><div class="num" style="color:#d97706">{counts.get("B",0)}</div><div class="lbl">B 级 · 培育跟进</div></div>
    <div class="stat"><div class="num">{hit_brand}</div><div class="lbl">命中我方品牌</div></div>
  </div>
  <div class="filter">
    <button class="active" data-f="all">全部</button>
    <button data-f="A">A 级</button>
    <button data-f="B">B 级</button>
    <button data-f="C">C 级</button>
  </div>
  <main id="list">
    {cards_html}
  </main>
</div>
<script>
  const btns = document.querySelectorAll(".filter button");
  btns.forEach(b => b.addEventListener("click", () => {{
    btns.forEach(x => x.classList.remove("active"));
    b.classList.add("active");
    const f = b.dataset.f;
    document.querySelectorAll(".card").forEach(c => {{
      c.style.display = (f === "all" || c.dataset.grade === f) ? "" : "none";
    }});
  }}));
</script>
</body>
</html>'''


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成线索 HTML 报告")
    ap.add_argument("json", help="已评分的线索 JSON")
    ap.add_argument("--out", default="report.html", help="输出 HTML 路径")
    ap.add_argument("--title", default="外贸经销商线索报告", help="报告标题")
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        leads = json.load(f)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(leads, args.title))
    print(f"报告已生成: {args.out} ({len(leads)} 条线索)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
线索报告生成器：读已评分的线索 JSON，生成自包含 HTML 报告（双击即开）。

支持两套评分模式切换（头部模式 / 长尾模式），卖 Deye 标志，导出 CSV。

输入 JSON 字段（每条线索）：
  company_name, country, city, website, phone, email, facebook, linkedin,
  customer_type, score, grade, reason, google_maps_url, source_url,
  brands_found, sells_deye, score_detail, score_lt, grade_lt, score_detail_lt

用法:
    python render_report.py leads_scored.json --out report.html --title "法国光伏经销商线索"
"""
import argparse
import html
import json
import re
import sys

GRADE_STYLE = {
    "A": {"color": "#16a34a", "bg": "#ecfdf3", "label": "A 级 · 优先跟进"},
    "B": {"color": "#d97706", "bg": "#fffbeb", "label": "B 级 · 培育跟进"},
    "C": {"color": "#6b7280", "bg": "#f3f4f6", "label": "C 级 · 暂存"},
}

# 两套模式的维度显示名 + 满分
DIM_HEAD = {"产品匹配": ("产品匹配", 30), "渠道": ("渠道匹配", 25), "规模": ("公司规模", 25),
            "触达": ("触达(电话>邮箱>官网)", 20)}
DIM_TAIL = {"产品匹配": ("产品匹配", 30), "渠道": ("渠道匹配", 20), "规模": ("公司规模", 25),
            "触达": ("触达(电话>邮箱>官网)", 25)}

def esc(s):
    return html.escape(str(s or ""))


def wa_link(phone):
    """电话 -> WhatsApp 链接（去掉非数字，补国家码缺省法国 +33）。"""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    # 法国本地号一般 10 位，补 +33 并去前导 0
    if len(digits) == 10 and digits.startswith("0"):
        digits = "33" + digits[1:]
    elif len(digits) == 10:
        digits = "33" + digits
    return f"https://wa.me/{digits}"


def render_detail(detail, basis, dim_map):
    rows = []
    for k, (label, mx) in dim_map.items():
        v = int(detail.get(k, 0) or 0)
        pct = min(100, int(v / mx * 100))
        b = basis.get(k, "") if basis else ""
        rows.append(
            f'<div class="dim"><span class="dim-name">{esc(label)}</span>'
            f'<div class="bar"><i style="width:{pct}%"></i></div>'
            f'<span class="dim-val">{v}</span>'
            f'<span class="dim-basis">{esc(b)}</span></div>')
    return f'<div class="detail">{"".join(rows)}</div>'


def render(leads, title):
    total = len(leads)

    def counts(mode):
        c = {"A": 0, "B": 0, "C": 0}
        for l in leads:
            g = (l.get("grade_lt" if mode == "tail" else "grade") or "C").upper()
            c[g] = c.get(g, 0) + 1
        return c

    hit_deye = sum(1 for l in leads if l.get("sells_deye"))
    hit_brand = sum(1 for l in leads if l.get("brands_found"))

    # 头部模式排序
    leads_sorted_head = sorted(leads, key=lambda l: -(l.get("score") or 0))

    cards = []
    for l in leads_sorted_head:
        g = (l.get("grade") or "C").upper()
        gl = (l.get("grade_lt") or "C").upper()
        st = GRADE_STYLE.get(g, GRADE_STYLE["C"])
        brands = "".join(f'<span class="brand">{esc(b)}</span>'
                         for b in (l.get("brands_found") or []))
        deye_tag = '<span class="deye">✓ Deye</span>' if l.get("sells_deye") else ""
        detail_head = render_detail(l.get("score_detail") or {}, l.get("score_basis") or {}, DIM_HEAD)
        detail_tail = render_detail(l.get("score_detail_lt") or {}, l.get("score_basis_lt") or {}, DIM_TAIL)
        linkedin = f'<a class="chip" href="{esc(l["linkedin"])}" target="_blank">LinkedIn</a>' \
            if l.get("linkedin") else ""
        wa = wa_link(l.get("phone") or "")
        phone_chip = ""
        if l.get("phone"):
            phone_chip = (f'<a class="chip phone" href="tel:{esc(l["phone"])}">'
                          f'📞 {esc(l["phone"])}</a>')
        wa_chip = f'<a class="chip wa" href="{wa}" target="_blank">WhatsApp</a>' if wa else ""
        cards.append(f'''
        <article class="card" data-grade="{g}" data-grade-lt="{gl}"
                 data-score="{int(l.get('score', 0))}" data-score-lt="{int(l.get('score_lt', 0))}">
          <div class="card-head">
            <span class="grade head-grade" style="color:{st['color']};background:{st['bg']}">{g}</span>
            <span class="grade tail-grade" style="display:none">{gl}</span>
            <h3 class="name">{esc(l.get("company_name"))}</h3>
            {deye_tag}
            <span class="score"><b class="score-num">{int(l.get("score", 0))}</b><small>分</small></span>
          </div>
          <div class="meta">
            <span class="ctype">{esc(l.get("customer_type"))}</span>
            <span class="loc">{esc(l.get("country"))} · {esc(l.get("city"))}</span>
            {brands}
          </div>
          <div class="contacts">
            {phone_chip}
            {wa_chip}
            {f'<a class="chip" href="{esc(l["website"])}" target="_blank">官网</a>' if l.get("website") else ""}
            {f'<a class="chip" href="mailto:{esc(l["email"])}">{esc(l["email"])}</a>' if l.get("email") else ""}
            {linkedin}
            {f'<a class="chip" href="{esc(l["google_maps_url"])}" target="_blank">Google Maps</a>' if l.get("google_maps_url") else ""}
          </div>
          <div class="head-detail">{detail_head}</div>
          <div class="tail-detail" style="display:none">{detail_tail}</div>
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
  .toolbar {{ display:flex; gap:8px; align-items:center; margin:18px 0 4px; flex-wrap:wrap; }}
  .mode-switch {{ display:flex; border:1px solid var(--line); border-radius:999px; overflow:hidden; }}
  .mode-switch button {{ border:0; background:#fff; padding:8px 18px; cursor:pointer; font-size:14px; color:var(--ink); }}
  .mode-switch button.active {{ background:var(--ink); color:#fff; }}
  .export {{ margin-left:auto; border:1px solid var(--line); background:#fff; padding:8px 16px; border-radius:999px; cursor:pointer; font-size:14px; color:#2563eb; }}
  .export:hover {{ background:#f8fafc; }}
  .stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin:14px 0; }}
  .stat {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 16px; }}
  .stat .num {{ font-size:26px; font-weight:700; }}
  .stat .lbl {{ font-size:12px; color:var(--muted); margin-top:2px; }}
  .filter {{ display:flex; gap:8px; margin:8px 0 16px; }}
  .filter button {{ border:1px solid var(--line); background:#fff; padding:7px 18px; border-radius:999px; cursor:pointer; font-size:14px; color:var(--ink); }}
  .filter button.active {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:18px 20px; margin-bottom:14px; }}
  .card-head {{ display:flex; align-items:center; gap:10px; }}
  .grade {{ font-weight:700; padding:2px 12px; border-radius:999px; font-size:13px; }}
  .tail-grade {{ background:#f3f4f6; color:#6b7280; }}
  .name {{ font-size:17px; font-weight:700; flex:1; }}
  .deye {{ background:#dcfce7; color:#16a34a; padding:2px 10px; border-radius:6px; font-size:12px; font-weight:700; }}
  .score {{ font-size:22px; font-weight:800; color:var(--ink); }}
  .score small {{ font-size:12px; color:var(--muted); font-weight:400; }}
  .meta {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:10px 0 8px; }}
  .ctype {{ background:#eef2ff; color:#4338ca; padding:2px 10px; border-radius:6px; font-size:12px; }}
  .loc {{ color:var(--muted); font-size:13px; }}
  .brand {{ background:#ecfeff; color:#0e7490; padding:2px 10px; border-radius:6px; font-size:12px; font-weight:600; }}
  .contacts {{ display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 12px; }}
  .chip {{ font-size:13px; padding:5px 12px; border:1px solid var(--line); border-radius:8px; color:#2563eb; text-decoration:none; }}
  .chip:hover {{ background:#f8fafc; }}
  .chip.phone {{ background:#eff6ff; border-color:#bfdbfe; font-weight:600; }}
  .chip.wa {{ background:#dcfce7; border-color:#86efac; color:#16a34a; font-weight:600; }}
  .detail {{ border-top:1px dashed var(--line); padding-top:10px; margin-bottom:10px; }}
  .dim {{ display:flex; align-items:center; gap:10px; margin:6px 0; font-size:13px; }}
  .dim-name {{ width:150px; color:var(--muted); flex-shrink:0; }}
  .bar {{ flex:1; height:8px; background:#eef2f7; border-radius:4px; overflow:hidden; }}
  .bar i {{ display:block; height:100%; background:linear-gradient(90deg,#3b82f6,#2563eb); border-radius:4px; }}
  .dim-val {{ width:28px; text-align:right; font-weight:600; }}
  .dim-basis {{ flex-shrink:0; margin-left:6px; font-size:11px; color:var(--muted); white-space:nowrap; }}
  .legend {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:14px 18px; margin:6px 0 14px; }}
  .legend summary {{ cursor:pointer; font-weight:700; font-size:14px; color:var(--ink); }}
  .legend-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px 22px; margin-top:12px; }}
  .legend-item {{ font-size:13px; line-height:1.5; }}
  .legend-item b {{ color:var(--ink); }}
  .legend-item span {{ color:var(--muted); }}
  @media (max-width:640px) {{ .legend-grid {{ grid-template-columns:1fr; }} }}
  .reason {{ font-size:13.5px; color:#334155; }}
  .reason strong {{ color:var(--ink); }}
  @media (max-width:640px) {{ .stats {{ grid-template-columns:repeat(2,1fr); }} }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>{esc(title)}</h1>
    <p>外贸经销商线索挖掘与分级 · 双评分模式 · 反幻觉：字段均可追溯</p>
  </header>
  <div class="toolbar">
    <div class="mode-switch">
      <button class="active" data-mode="head">头部模式（啃大客户）</button>
      <button data-mode="tail">长尾模式（铺小客户）</button>
    </div>
    <button class="export" onclick="exportCSV()">⬇ 导出 CSV</button>
  </div>
  <details class="legend" open>
    <summary>评分规则说明（依据）</summary>
    <div class="legend-grid">
      <div class="legend-item"><b>产品匹配 30</b><br><span>已卖Deye/贴牌=30（存量） · 卖竞品储能/逆变器=24（增量） · 无证据=0</span></div>
      <div class="legend-item"><b>渠道 25/20</b><br><span>批发/分销商 &gt; 安装商 &gt; 零售</span></div>
      <div class="legend-item"><b>规模 25/25</b><br><span>经营痕迹三档：头部 大型25·中型17·小型8 · 长尾 中型25·小型20·大型12 · 未确认按小型</span></div>
      <div class="legend-item"><b>触达 20/25</b><br><span>电话(可WhatsApp) 20/25 &gt; 邮箱 14/18 &gt; 仅官网 8/10（德国因地制宜）</span></div>
    </div>
    <p style="margin-top:10px;font-size:12.5px;color:#64748b">⚠️ 规模三态防幻觉：<b>经营痕迹</b>(仓库/多品牌/评分数) → 按档位 · <b>估</b>(背调过无硬证据) → 档位+「估」 · <b>未确认</b>(未背调) → 按小型档保守、不归零不假装判断。</p>
  </details>
  <div class="stats">
    <div class="stat"><div class="num">{total}</div><div class="lbl">线索总数</div></div>
    <div class="stat"><div class="num" style="color:#16a34a" id="stat-a">{counts('head').get('A',0)}</div><div class="lbl">A 级 · 优先跟进</div></div>
    <div class="stat"><div class="num" style="color:#d97706" id="stat-b">{counts('head').get('B',0)}</div><div class="lbl">B 级 · 培育跟进</div></div>
    <div class="stat"><div class="num" id="stat-deye">{hit_deye}</div><div class="lbl">卖 Deye</div></div>
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
  let mode = 'head';
  let filter = 'all';
  const GRADE_STYLE = {{
    A: {{color:'#16a34a',bg:'#ecfdf3'}}, B: {{color:'#d97706',bg:'#fffbeb'}}, C: {{color:'#6b7280',bg:'#f3f4f6'}}
  }};

  function applyMode() {{
    document.querySelectorAll('.card').forEach(c => {{
      const isTail = mode === 'tail';
      const score = isTail ? c.dataset.scoreLt : c.dataset.score;
      const grade = isTail ? c.dataset.gradeLt : c.dataset.grade;
      c.querySelector('.score-num').textContent = score;
      c.querySelector('.head-grade').style.display = isTail ? 'none' : '';
      const tg = c.querySelector('.tail-grade');
      tg.style.display = isTail ? '' : 'none';
      tg.textContent = grade;
      const st = GRADE_STYLE[grade];
      tg.style.color = st.color; tg.style.background = st.bg;
      c.querySelector('.head-detail').style.display = isTail ? 'none' : '';
      c.querySelector('.tail-detail').style.display = isTail ? '' : 'none';
      c.dataset.grade = grade;
    }});
    sortCards();
    updateStats();
    applyFilter();
  }}

  function sortCards() {{
    const list = document.getElementById('list');
    const cards = Array.from(list.querySelectorAll('.card'));
    const key = mode === 'tail' ? 'dataset.scoreLt' : 'dataset.score';
    cards.sort((a,b) => (mode==='tail'?b.dataset.scoreLt-a.dataset.scoreLt : b.dataset.score-a.dataset.score));
    cards.forEach(c => list.appendChild(c));
  }}

  function updateStats() {{
    const cnt = {{A:0,B:0,C:0}};
    document.querySelectorAll('.card').forEach(c => {{
      const g = mode==='tail' ? c.dataset.gradeLt : c.dataset.grade;
      cnt[g] = (cnt[g]||0)+1;
    }});
    document.getElementById('stat-a').textContent = cnt.A;
    document.getElementById('stat-b').textContent = cnt.B;
  }}

  function applyFilter() {{
    document.querySelectorAll('.card').forEach(c => {{
      const g = mode==='tail' ? c.dataset.gradeLt : c.dataset.grade;
      c.style.display = (filter==='all' || g===filter) ? '' : 'none';
    }});
  }}

  document.querySelectorAll('.mode-switch button').forEach(b => {{
    b.addEventListener('click', () => {{
      document.querySelectorAll('.mode-switch button').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      mode = b.dataset.mode;
      applyMode();
    }});
  }});

  document.querySelectorAll('.filter button').forEach(b => {{
    b.addEventListener('click', () => {{
      document.querySelectorAll('.filter button').forEach(x=>x.classList.remove('active'));
      b.classList.add('active');
      filter = b.dataset.f;
      applyFilter();
    }});
  }});

  function exportCSV() {{
    const cards = Array.from(document.querySelectorAll('.card'))
      .filter(c => c.style.display !== 'none');
    const rows = [['公司名','国家','城市','官网','电话','邮箱','客户类型','卖Deye','品牌命中','分数','分级','开发理由']];
    cards.forEach(c => {{
      const name = c.querySelector('.name').textContent;
      const ctype = c.querySelector('.ctype')?.textContent || '';
      const loc = c.querySelector('.loc')?.textContent || '';
      const [country, city] = loc.split(' · ');
      const website = c.querySelector('a[href^="http"]')?.getAttribute('href') || '';
      const phoneA = c.querySelector('a.phone')?.getAttribute('href') || '';
      const phone = phoneA.replace('tel:','');
      const mailA = c.querySelector('a[href^="mailto:"]')?.getAttribute('href') || '';
      const email = mailA.replace('mailto:','');
      const deye = c.querySelector('.deye') ? '是' : '';
      const brands = Array.from(c.querySelectorAll('.brand')).map(b=>b.textContent).join('|');
      const score = c.querySelector('.score-num').textContent;
      const grade = mode==='tail' ? c.dataset.gradeLt : c.dataset.grade;
      const reason = c.querySelector('.reason')?.textContent.replace('开发理由：','') || '';
      rows.push([name, country, city, website, phone, email, ctype, deye, brands, score, grade, reason]);
    }});
    const csv = rows.map(r => r.map(x => '"' + (x||'').replace(/"/g,'""') + '"').join(',')).join('\\n');
    const blob = new Blob(['\\ufeff' + csv], {{type:'text/csv;charset=utf-8'}});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'leads_' + mode + '.csv';
    a.click();
  }}

  // 初始：头部模式排序
  sortCards();
</script>
</body>
</html>'''


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="生成线索 HTML 报告（双评分模式）")
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

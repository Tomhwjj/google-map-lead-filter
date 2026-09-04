#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 Web 管理后台（Flask）：获客系统的 UI 交互层。

页面：
  /                     仪表盘（任务列表 + 企业库概览）
  /tasks/new            新建获客任务（国家/关键词/数据源 → 生成 task_id）
  /tasks/<id>           任务详情（复刻报告数据 + 新增企业清单 + 差异清单）
  /tasks/<id>/report.md 导出复刻报告
  /ingest               入库（选任务 + 贴评分 JSON → 三段式比对）
  /diffs                差异审核队列（approve 覆盖 / reject 忽略）
  /companies            企业库检索（电话/企业名/域名），行内快捷换池
  /pool                 客户池总览（五池统计 + 各池列表 + 换池轨迹）
  /companies/<main_id>  企业详情（全字段 + 客户池轨迹 + 换池备注）
  /research             市调任务列表（历史 + 过期标记）
  /research/new         新建市调任务（目标国家 + 执行人 + 缓存天数）
  /research/<mr_id>     市调详情（各国热度研判录入 + 得分排序 + 复盘报告）

启动:
    python webapp/app.py        # 端口 8766，自动打开浏览器
"""
import json
import os
import sys
import threading
import webbrowser

from flask import Flask, redirect, render_template, request, url_for

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from core import (build_report, change_pool, finish_research, get_company,
                  get_research, ingest_leads, list_companies, list_diffs,
                  list_pool_log, list_research, list_tasks, pool_stats,
                  review_diff, save_country_score, start_research, start_task)
from db import POOLS, get_conn, init_db
from render_task_report import render_md, render_report
from render_research_report import (render_md as render_research_md,
                                    render_report as render_research_report)

PORT = 8766

app = Flask(__name__,
            template_folder=os.path.join(APP_DIR, "templates"),
            static_folder=os.path.join(APP_DIR, "static"))


@app.route("/")
def index():
    tasks = list_tasks()
    conn = get_conn()
    total_companies = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    pending_diffs = conn.execute("SELECT COUNT(*) FROM diffs WHERE status='pending'").fetchone()[0]
    running_tasks = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
    conn.close()
    stats = pool_stats()
    return render_template("index.html", tasks=tasks, total_companies=total_companies,
                           pending_diffs=pending_diffs, running_tasks=running_tasks,
                           pool_stats=stats, pools=POOLS)


@app.route("/tasks/new", methods=["GET", "POST"])
def tasks_new():
    if request.method == "POST":
        country = (request.form.get("country") or "").strip().upper()
        keywords_raw = (request.form.get("keywords") or "").strip()
        sources_raw = (request.form.get("sources") or "").strip()
        keywords = [k.strip() for k in keywords_raw.splitlines() if k.strip()]
        sources = [s.strip() for s in sources_raw.replace("，", ",").split(",") if s.strip()]
        task_id = start_task(country=country, keywords=keywords, sources=sources)
        return redirect(url_for("task_detail", task_id=task_id))
    return render_template("tasks_new.html")


@app.route("/tasks/<task_id>")
def task_detail(task_id):
    try:
        data = build_report(task_id)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 404
    task = data["task"]
    task["keywords_list"] = json.loads(task.get("keywords") or "[]")
    task["sources_list"] = json.loads(task.get("sources") or "[]")
    md, _ = render_report(task_id)
    return render_template("task_detail.html", data=data, md=md, task_id=task_id)


@app.route("/tasks/<task_id>/report.md")
def task_report_md(task_id):
    md, out = render_report(task_id)
    return app.response_class(md, mimetype="text/markdown; charset=utf-8",
                              headers={"Content-Disposition": f"attachment; filename=复刻报告_{task_id}.md"})


@app.route("/ingest", methods=["GET", "POST"])
def ingest():
    result = None
    error = None
    if request.method == "POST":
        task_id = (request.form.get("task_id") or "").strip()
        leads_raw = (request.form.get("leads_json") or "").strip()
        action = request.form.get("action") or "commit"
        dry_run = (action == "preview")
        try:
            leads = json.loads(leads_raw)
            if not isinstance(leads, list):
                raise ValueError("JSON 必须是数组（list）")
            stats = ingest_leads(leads, task_id, dry_run=dry_run)
            result = {"task_id": task_id, "stats": stats, "dry_run": dry_run}
        except Exception as e:
            error = f"入库失败: {e}"
    tasks = list_tasks()
    return render_template("ingest.html", tasks=tasks, result=result, error=error)


@app.route("/diffs", methods=["GET"])
def diffs():
    status = request.args.get("status", "pending")
    items = list_diffs(status=status)
    return render_template("diffs.html", items=items, status=status)


@app.route("/diffs/<int:diff_id>/<action>", methods=["POST"])
def diffs_review(diff_id, action):
    approve = action == "approve"
    review_diff(diff_id, approve=approve, reviewer="人工(webui)")
    return redirect(url_for("diffs"))


@app.route("/companies", methods=["GET"])
def companies():
    q = request.args.get("q", "").strip()
    pool = request.args.get("pool", "").strip()
    items = list_companies(query=q, pool=pool or None)
    return render_template("companies.html", items=items, q=q, pool=pool, pools=POOLS)


@app.route("/pool", methods=["GET"])
def pool():
    pool_filter = request.args.get("pool", "").strip() or None
    stats = pool_stats()
    items = list_companies(pool=pool_filter, limit=300) if pool_filter else []
    logs = list_pool_log(limit=50) if not pool_filter else []
    return render_template("pool.html", stats=stats, pool=pool_filter, pools=POOLS,
                           items=items, logs=logs)


@app.route("/companies/<main_id>", methods=["GET"])
def company_detail(main_id):
    try:
        data = get_company(main_id)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 404
    company = data["company"]
    # 解析 JSON 字段，便于模板展示
    for f in ("brands_found", "brands_context", "score_detail", "score_basis",
              "score_detail_lt", "score_basis_lt"):
        v = company.get(f)
        if isinstance(v, str) and v:
            try:
                company[f] = json.loads(v)
            except Exception:
                pass
    return render_template("company_detail.html", company=company,
                           logs=data["pool_log"], pools=POOLS)


@app.route("/companies/<main_id>/pool", methods=["POST"])
def company_change_pool(main_id):
    to_pool = (request.form.get("to_pool") or "").strip()
    operator = (request.form.get("operator") or "人工(webui)").strip()
    note = (request.form.get("note") or "").strip()
    next_url = request.form.get("next") or url_for("companies")
    try:
        change_pool(main_id, to_pool, operator=operator, note=note)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 400
    return redirect(next_url)


@app.route("/research", methods=["GET"])
def research():
    items = list_research()
    return render_template("research.html", items=items)


@app.route("/research/new", methods=["GET", "POST"])
def research_new():
    if request.method == "POST":
        countries_raw = (request.form.get("countries") or "").strip()
        executor = (request.form.get("executor") or "本地本机").strip()
        try:
            cache_days = int(request.form.get("cache_days") or 7)
        except ValueError:
            cache_days = 7
        countries = [c.strip().upper() for c in countries_raw.replace("，", ",").split(",") if c.strip()]
        if not countries:
            return render_template("error.html", msg="至少填一个目标国家"), 400
        mr_id = start_research(countries=countries, executor=executor, cache_days=cache_days)
        return redirect(url_for("research_detail", mr_id=mr_id))
    return render_template("research_new.html")


@app.route("/research/<mr_id>", methods=["GET"])
def research_detail(mr_id):
    try:
        data = get_research(mr_id)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 404
    task = data["task"]
    scores = data["scores"]
    scored_countries = {s["country"] for s in scores}
    return render_template("research_detail.html", task=task, scores=scores,
                           scored_countries=scored_countries)


@app.route("/research/<mr_id>/score", methods=["POST"])
def research_score(mr_id):
    country = (request.form.get("country") or "").strip().upper()
    try:
        save_country_score(
            mr_id, country,
            score=request.form.get("score") or 0,
            positives=(request.form.get("positives") or "").strip(),
            negatives=(request.form.get("negatives") or "").strip(),
            risks=(request.form.get("risks") or "").strip(),
            sources=(request.form.get("sources") or "").strip(),
        )
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 400
    return redirect(url_for("research_detail", mr_id=mr_id))


@app.route("/research/<mr_id>/finish", methods=["POST"])
def research_finish(mr_id):
    try:
        finish_research(mr_id)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 400
    return redirect(url_for("research_detail", mr_id=mr_id))


@app.route("/research/<mr_id>/report.md", methods=["GET"])
def research_report_md(mr_id):
    md, out = render_research_report(mr_id)
    return app.response_class(md, mimetype="text/markdown; charset=utf-8",
                              headers={"Content-Disposition": f"attachment; filename=市场洞察复盘报告_{mr_id}.md"})


def main():
    init_db()
    url = f"http://127.0.0.1:{PORT}"
    print(f"获客系统管理后台启动: {url}")
    print("关闭请按 Ctrl+C")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()

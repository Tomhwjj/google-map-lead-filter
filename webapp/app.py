#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地 Web 管理后台（Flask）：获客系统的 UI 交互层。

页面：
  /                     仪表盘（任务列表 + 企业库概览）
  /tasks/start/<country>  一键开始获客（POST，从市调排名触发 → 后台 Claude 自动跑流水线）
  /tasks/<id>           任务详情（复刻报告数据 + 新增企业清单 + 差异清单）
  /tasks/<id>/report.md 导出复刻报告
  /diffs                差异审核队列（approve 覆盖 / reject 忽略）
  /companies            企业库检索（电话/企业名/域名），行内快捷换池
  /pool                 客户池总览（五池统计 + 各池列表 + 换池轨迹）
  /companies/<main_id>  企业详情（全字段 + 客户池轨迹 + 换池备注）
  /research             市调排名看板（一键「开始市调」28 国 + 前10优先 + 单国详情）
  /research/start       一键开始市调（POST，默认欧盟 27 国 + 乌克兰）
  /research/<mr_id>     市调详情（各国热度研判录入 + 得分排序 + 复盘报告）
  /research/<mr_id>/country/<country>  单国研判详情（7 维度判断依据）

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

from core import (RESEARCH_DIMS, build_report, change_pool, finish_research,
                  get_company, get_country_detail, get_research,
                  latest_research_ranking, list_companies, list_countries,
                  list_diff_groups, list_pool_log, list_research, list_tasks,
                  pool_stats, review_diff, save_country_score, start_research,
                  start_task)
from db import POOLS, get_conn, init_db
from render_task_report import render_md, render_report
from render_research_report import (render_md as render_research_md,
                                    render_report as render_research_report)

PORT = 8766

app = Flask(__name__,
            template_folder=os.path.join(APP_DIR, "templates"),
            static_folder=os.path.join(APP_DIR, "static"))


def _parse_deye(deye):
    """把「是否卖 Deye」下拉值转成 list_companies 的 sells_deye 参数（None/True/False）。"""
    deye = (deye or "").strip().lower()
    if deye == "yes":
        return True
    if deye == "no":
        return False
    return None


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


@app.route("/tasks/start/<country>", methods=["POST"])
def tasks_start(country):
    """创建获客任务单（记录用），线索采集/评分/入库走命令行脚本或人工。"""
    country = (country or "").strip().upper()
    task_id = start_task(country=country, keywords=[], sources=["市调排名触发"])
    return redirect(url_for("task_detail", task_id=task_id))


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


@app.route("/diffs", methods=["GET"])
def diffs():
    status = request.args.get("status", "pending")
    items = list_diff_groups(status=status)
    return render_template("diffs.html", items=items, status=status)


@app.route("/diffs/<int:diff_id>/<action>", methods=["POST"])
def diffs_review(diff_id, action):
    approve = action == "approve"
    review_diff(diff_id, approve=approve, reviewer="人工(webui)")
    next_url = request.form.get("next") or url_for("diffs")
    return redirect(next_url)


@app.route("/companies", methods=["GET"])
def companies():
    q = request.args.get("q", "").strip()
    pool = request.args.get("pool", "").strip()
    country = request.args.get("country", "").strip()
    deye = request.args.get("deye", "").strip()
    sells_deye = _parse_deye(deye)
    items = list_companies(query=q, pool=pool or None, country=country or None,
                           sells_deye=sells_deye)
    countries = list_countries()
    return render_template("companies.html", items=items, q=q, pool=pool,
                           country=country, deye=deye, pools=POOLS, countries=countries)


@app.route("/pool", methods=["GET"])
def pool():
    pool_filter = request.args.get("pool", "").strip() or None
    country = request.args.get("country", "").strip() or None
    q = request.args.get("q", "").strip()
    deye = request.args.get("deye", "").strip()
    sells_deye = _parse_deye(deye)
    stats = pool_stats()
    countries = list_countries()
    has_filter = bool(pool_filter or country or q or sells_deye is not None)
    items = list_companies(query=q, pool=pool_filter, country=country,
                           sells_deye=sells_deye, limit=300) if has_filter else []
    logs = list_pool_log(limit=50) if not has_filter else []
    return render_template("pool.html", stats=stats, pool=pool_filter, pools=POOLS,
                           items=items, logs=logs, country=country, countries=countries,
                           has_filter=has_filter, q=q, deye=deye)


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
                           logs=data["pool_log"], diffs=data["diffs"], pools=POOLS)


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
    ranking = latest_research_ranking()
    return render_template("research.html", items=items, ranking=ranking)


@app.route("/research/start", methods=["POST"])
def research_start():
    """创建市调任务单（28 国），热度研判数据在详情页手动录入。"""
    executor = (request.form.get("executor") or "人工").strip()
    mr_id = start_research(countries=None, executor=executor)
    return redirect(url_for("research_detail", mr_id=mr_id))


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
                           scored_countries=scored_countries, dims=RESEARCH_DIMS)


@app.route("/research/<mr_id>/country/<country>", methods=["GET"])
def research_country(mr_id, country):
    """单国研判详情：热度分 + 7 维度判断依据 + 利好利空风险 + 来源。"""
    try:
        data = get_country_detail(mr_id, country)
    except ValueError as e:
        return render_template("error.html", msg=str(e)), 404
    return render_template("country_detail.html", task=data["task"],
                           detail=data["detail"], dims=RESEARCH_DIMS)


@app.route("/research/<mr_id>/score", methods=["POST"])
def research_score(mr_id):
    country = (request.form.get("country") or "").strip().upper()
    try:
        dimensions = {}
        for d in RESEARCH_DIMS:
            note = (request.form.get(f"dim_{d}") or "").strip()
            if note:
                dimensions[d] = note
        save_country_score(
            mr_id, country,
            score=request.form.get("score") or 0,
            positives=(request.form.get("positives") or "").strip(),
            negatives=(request.form.get("negatives") or "").strip(),
            risks=(request.form.get("risks") or "").strip(),
            sources=(request.form.get("sources") or "").strip(),
            dimensions=dimensions,
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

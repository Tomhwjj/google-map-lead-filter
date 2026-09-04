#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
业务逻辑层（纯函数，无 argparse / 无 Flask，被 webapp 和 Claude 直调共用）。

三个核心能力，对应架构文档「自动获客模块」+「复刻报告」：
  start_task     — 开始获客任务，生成 task_id + 时间戳 + 关键词/数据源快照
  finish_task    — 结束任务，写结束时间戳 + 运行时长
  ingest_leads   — 三段式比对入库（全新 / 重复跳过 / 差异→待核验队列），核心
  build_report   — 聚合某任务的复刻报告所需数据（供 render_task_report.py）

三段式比对铁律（架构文档）：
  ① 全新企业      → INSERT 入库，默认落「潜在客户(未联系)」，记 task_companies= new
  ② 完全重复      → 跳过不入库，记 dup
  ③ 存量有差异    → 禁止覆盖旧数据，写 diffs(pending) 等人工审核，记 diff

企业身份去重键：domain（官网主域名）优先，退回 name_key（公司名小写去空白）。
关键比对字段：公司名 / 电话 / 邮箱 / 官网 / 主营(customer_type) / 城市。
"""
import json
import re
import sys
from datetime import datetime, timedelta

from db import (DEFAULT_DB, DEFAULT_POOL, POOLS, gen_main_id, gen_mr_id,
                gen_task_id, get_conn, init_db, normalize_domain,
                normalize_name, now_iso)

# 判定「差异」的关键字段 + 归一化函数（normalize 后比较，忽略格式差异）
KEY_FIELDS = [
    ("company_name",  lambda v: re.sub(r"\s+", "", (v or "").lower())),
    ("phone",         lambda v: re.sub(r"[\s\-()]+", "", v or "")),
    ("email",         lambda v: (v or "").lower().strip()),
    ("website",       lambda v: (v or "").lower().strip().rstrip("/")),
    ("customer_type", lambda v: (v or "").lower().strip()),
    ("city",          lambda v: re.sub(r"\s+", "", (v or "").lower())),
]

# 企业字段全集（与 db.companies 列对齐；JSON 字段单独序列化）
JSON_FIELDS = {"brands_found", "brands_context", "score_detail", "score_basis",
               "score_detail_lt", "score_basis_lt"}
INT_FIELDS = {"scale_estimated", "backfilled", "sells_deye"}

COMPANY_COLS = [
    "main_id", "domain", "name_key", "company_name", "country", "city",
    "customer_type", "phone", "email", "linkedin", "facebook", "address",
    "website", "rating", "google_maps_url", "source_url", "profile_url",
    "brands_found", "brands_context", "product_tier", "scale_tier",
    "scale_estimated", "backfilled", "reason", "sells_deye", "score", "grade",
    "score_detail", "score_basis", "score_lt", "grade_lt", "score_detail_lt",
    "score_basis_lt", "pool", "first_seen_task", "first_seen_at",
    "last_seen_task", "last_seen_at", "created_at", "updated_at",
]


def start_task(country="", keywords=None, sources=None, db_path=None):
    """开始获客任务：生成 task_id、写开始时间戳 + 关键词/数据源快照。返回 task_id。"""
    conn = init_db(db_path)
    task_id = gen_task_id()
    now = now_iso()
    kw = json.dumps(keywords or [], ensure_ascii=False)
    src = json.dumps(sources or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO tasks (task_id, country, keywords, sources, started_at, status) "
        "VALUES (?,?,?,?,?, 'running')",
        (task_id, country, kw, src, now))
    conn.commit()
    conn.close()
    return task_id


def finish_task(task_id, db_path=None):
    """结束任务：写结束时间戳 + 运行时长（秒），状态置 done。"""
    conn = init_db(db_path)
    row = conn.execute("SELECT started_at FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"任务不存在: {task_id}")
    now = now_iso()
    duration = None
    if row["started_at"]:
        try:
            duration = int((datetime.now() - datetime.fromisoformat(row["started_at"])).total_seconds())
        except Exception:
            pass
    conn.execute(
        "UPDATE tasks SET finished_at=?, duration_sec=?, status='done' WHERE task_id=?",
        (now, duration, task_id))
    conn.commit()
    conn.close()
    return {"task_id": task_id, "finished_at": now, "duration_sec": duration}


def _find_existing(conn, domain, name_key):
    """按去重键查已有企业：domain 优先，退回 name_key。返回 row 或 None。"""
    if domain:
        row = conn.execute("SELECT * FROM companies WHERE domain=?", (domain,)).fetchone()
        if row:
            return row
    if name_key:
        row = conn.execute("SELECT * FROM companies WHERE name_key=?", (name_key,)).fetchone()
        if row:
            return row
    return None


def _field_diff(existing, lead):
    """比对关键字段，返回 [(field, old, new), ...]。归一化后不同且不都为空才算差异。"""
    diffs = []
    for field, norm in KEY_FIELDS:
        old = norm(existing[field]) if field in existing.keys() else ""
        new = norm(lead.get(field))
        if old != new and (old or new):
            diffs.append((field, existing[field] if field in existing.keys() else "", lead.get(field) or ""))
    return diffs


def _norm_val(lead, field):
    """序列化 lead 字段值到可入库形式（JSON 字段转字符串，int 字段转 0/1）。"""
    v = lead.get(field)
    if field in JSON_FIELDS:
        return json.dumps(v if v is not None else {}, ensure_ascii=False) \
            if field in ("brands_context", "score_detail", "score_basis",
                         "score_detail_lt", "score_basis_lt") \
            else json.dumps(v or [], ensure_ascii=False)
    if field in INT_FIELDS:
        return 1 if v else 0
    return v


def _insert_company(conn, main_id, lead, task_id, now):
    """插入一条全新企业。"""
    domain = normalize_domain(lead.get("website"))
    name_key = normalize_name(lead.get("company_name"))
    vals = {
        "main_id": main_id, "domain": domain, "name_key": name_key,
        "pool": DEFAULT_POOL,
        "first_seen_task": task_id, "first_seen_at": now,
        "last_seen_task": task_id, "last_seen_at": now,
        "created_at": now, "updated_at": now,
    }
    for col in COMPANY_COLS:
        if col not in vals:
            vals[col] = _norm_val(lead, col)
    cols = ", ".join(vals.keys())
    ph = ", ".join("?" for _ in vals)
    conn.execute(f"INSERT INTO companies ({cols}) VALUES ({ph})", tuple(vals.values()))


def ingest_leads(leads, task_id, dry_run=False, db_path=None):
    """三段式比对入库。

    参数：
      leads   — list[dict]，评分后的企业记录（leads_final.json 结构）
      task_id — 已存在的任务 ID（先 start_task）
      dry_run — True 只比对统计、不写库（用于 UI 预览）
    返回：{"total", "new", "dup", "diff", "new_main_ids", "diff_main_ids"}
    """
    conn = init_db(db_path)
    if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
        conn.close()
        raise ValueError(f"任务不存在: {task_id}（先 start_task）")

    now = now_iso()
    stats = {"total": len(leads), "new": 0, "dup": 0, "diff": 0,
             "new_main_ids": [], "diff_main_ids": []}

    for lead in leads:
        domain = normalize_domain(lead.get("website"))
        name_key = normalize_name(lead.get("company_name"))
        existing = _find_existing(conn, domain, name_key)

        if existing is None:
            stats["new"] += 1
            if not dry_run:
                main_id = gen_main_id(lead.get("country"))
                _insert_company(conn, main_id, lead, task_id, now)
                conn.execute(
                    "INSERT OR IGNORE INTO task_companies (task_id, main_id, action) VALUES (?,?,?)",
                    (task_id, main_id, "new"))
                stats["new_main_ids"].append(main_id)
        else:
            diffs = _field_diff(existing, lead)
            if not diffs:
                stats["dup"] += 1
                if not dry_run:
                    conn.execute(
                        "INSERT OR IGNORE INTO task_companies (task_id, main_id, action) VALUES (?,?,?)",
                        (task_id, existing["main_id"], "dup"))
                    conn.execute(
                        "UPDATE companies SET last_seen_task=?, last_seen_at=?, updated_at=? WHERE main_id=?",
                        (task_id, now, now, existing["main_id"]))
            else:
                stats["diff"] += 1
                stats["diff_main_ids"].append(existing["main_id"])
                if not dry_run:
                    for field, old, new in diffs:
                        conn.execute(
                            "INSERT INTO diffs (main_id, task_id, field, old_value, new_value, status, detected_at) "
                            "VALUES (?,?,?,?,?, 'pending', ?)",
                            (existing["main_id"], task_id, field, old, new, now))
                    conn.execute(
                        "INSERT OR IGNORE INTO task_companies (task_id, main_id, action) VALUES (?,?,?)",
                        (task_id, existing["main_id"], "diff"))

    if not dry_run:
        conn.commit()
    conn.close()
    return stats


def list_companies(query="", pool=None, limit=200, db_path=None):
    """企业库检索（电话/企业名/域名模糊匹配）。"""
    conn = init_db(db_path)
    sql = "SELECT main_id, company_name, country, city, customer_type, phone, email, website, grade, grade_lt, sells_deye, pool, domain FROM companies"
    conds, params = [], []
    if pool:
        conds.append("pool=?")
        params.append(pool)
    if query:
        q = f"%{query}%"
        conds.append("(company_name LIKE ? OR phone LIKE ? OR domain LIKE ? OR email LIKE ?)")
        params += [q, q, q, q]
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def list_tasks(limit=100, db_path=None):
    """任务列表（倒序）。"""
    conn = init_db(db_path)
    sql = """
        SELECT t.*,
               (SELECT COUNT(*) FROM task_companies tc WHERE tc.task_id=t.task_id AND tc.action='new') AS new_count,
               (SELECT COUNT(*) FROM task_companies tc WHERE tc.task_id=t.task_id AND tc.action='dup') AS dup_count,
               (SELECT COUNT(*) FROM task_companies tc WHERE tc.task_id=t.task_id AND tc.action='diff') AS diff_count
        FROM tasks t ORDER BY t.started_at DESC LIMIT ?
    """
    rows = [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]
    conn.close()
    return rows


def list_diffs(status="pending", limit=200, db_path=None):
    """差异待核验队列（pending 默认），join 企业名便于审核。"""
    conn = init_db(db_path)
    sql = """
        SELECT d.*, c.company_name, c.website AS company_website
        FROM diffs d LEFT JOIN companies c ON d.main_id=c.main_id
        WHERE d.status=? ORDER BY d.detected_at DESC LIMIT ?
    """
    rows = [dict(r) for r in conn.execute(sql, (status, limit)).fetchall()]
    conn.close()
    return rows


def review_diff(diff_id, approve, reviewer="人工", db_path=None):
    """审核一条差异：approve=True 覆盖旧值，False 忽略。"""
    conn = init_db(db_path)
    diff = conn.execute("SELECT * FROM diffs WHERE id=?", (diff_id,)).fetchone()
    if not diff:
        conn.close()
        raise ValueError(f"差异不存在: {diff_id}")
    now = now_iso()
    if approve and diff["main_id"]:
        # 覆盖：把 new_value 写回 companies 对应字段
        field = diff["field"]
        if field in [c for c in COMPANY_COLS]:
            conn.execute(
                f"UPDATE companies SET {field}=?, updated_at=? WHERE main_id=?",
                (diff["new_value"], now, diff["main_id"]))
    conn.execute(
        "UPDATE diffs SET status=?, reviewed_at=?, reviewer=? WHERE id=?",
        ("approved" if approve else "rejected", now, reviewer, diff_id))
    conn.commit()
    conn.close()
    return {"diff_id": diff_id, "status": "approved" if approve else "rejected"}


def build_report(task_id, db_path=None):
    """聚合某任务的复刻报告数据（供 render_task_report.py 渲染 md）。"""
    conn = init_db(db_path)
    task = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if not task:
        conn.close()
        raise ValueError(f"任务不存在: {task_id}")

    action_rows = conn.execute(
        "SELECT action, COUNT(*) AS n FROM task_companies WHERE task_id=? GROUP BY action",
        (task_id,)).fetchall()
    stats = {"new": 0, "dup": 0, "diff": 0}
    for r in action_rows:
        stats[r["action"]] = r["n"]

    diffs = [dict(r) for r in conn.execute(
        "SELECT d.*, c.company_name FROM diffs d LEFT JOIN companies c ON d.main_id=c.main_id "
        "WHERE d.task_id=? ORDER BY d.id", (task_id,)).fetchall()]
    new_companies = [dict(r) for r in conn.execute(
        "SELECT c.main_id, c.company_name, c.country, c.city, c.customer_type, c.phone, c.email, "
        "c.website, c.grade, c.grade_lt, c.sells_deye, c.pool FROM companies c "
        "JOIN task_companies tc ON c.main_id=tc.main_id "
        "WHERE tc.task_id=? AND tc.action='new' ORDER BY c.score DESC", (task_id,)).fetchall()]

    conn.close()
    return {
        "task": dict(task),
        "stats": stats,
        "diffs": diffs,
        "new_companies": new_companies,
    }


def change_pool(main_id, to_pool, operator="人工", note="", db_path=None):
    """客户池换池：更新 companies.pool + 写 pool_log 轨迹。

    铁律：客户状态 100% 人工，本函数只记轨迹、不自动判定。
    同池不变时跳过（不写空轨迹）。返回 {main_id, from_pool, to_pool, changed_at, skipped}。
    """
    if to_pool not in POOLS:
        raise ValueError(f"非法客户池: {to_pool}（可选 {POOLS}）")
    conn = init_db(db_path)
    row = conn.execute("SELECT main_id, company_name, pool FROM companies WHERE main_id=?",
                       (main_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    from_pool = row["pool"] or DEFAULT_POOL
    now = now_iso()
    if from_pool == to_pool:
        conn.close()
        return {"main_id": main_id, "from_pool": from_pool, "to_pool": to_pool,
                "changed_at": now, "skipped": True}
    conn.execute("UPDATE companies SET pool=?, updated_at=? WHERE main_id=?",
                 (to_pool, now, main_id))
    conn.execute(
        "INSERT INTO pool_log (main_id, from_pool, to_pool, changed_at, operator, note) "
        "VALUES (?,?,?,?,?,?)",
        (main_id, from_pool, to_pool, now, operator, note))
    conn.commit()
    conn.close()
    return {"main_id": main_id, "from_pool": from_pool, "to_pool": to_pool,
            "changed_at": now, "skipped": False}


def list_pool_log(main_id=None, limit=200, db_path=None):
    """客户池状态轨迹（倒序）。可传 main_id 过滤单个企业。"""
    conn = init_db(db_path)
    sql = ("SELECT pl.*, c.company_name FROM pool_log pl "
           "LEFT JOIN companies c ON pl.main_id=c.main_id ")
    params = []
    if main_id:
        sql += "WHERE pl.main_id=? "
        params.append(main_id)
    sql += "ORDER BY pl.changed_at DESC, pl.id DESC LIMIT ?"
    params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def pool_stats(db_path=None):
    """五池企业数统计（空池补 0）。"""
    conn = init_db(db_path)
    rows = conn.execute("SELECT pool, COUNT(*) AS n FROM companies GROUP BY pool").fetchall()
    conn.close()
    m = {r["pool"]: r["n"] for r in rows}
    return {p: m.get(p, 0) for p in POOLS}


def get_company(main_id, db_path=None):
    """取单个企业详情 + 其客户池轨迹。"""
    conn = init_db(db_path)
    c = conn.execute("SELECT * FROM companies WHERE main_id=?", (main_id,)).fetchone()
    if not c:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    logs = [dict(r) for r in conn.execute(
        "SELECT * FROM pool_log WHERE main_id=? ORDER BY changed_at DESC, id DESC",
        (main_id,)).fetchall()]
    conn.close()
    return {"company": dict(c), "pool_log": logs}


# ---------------------------------------------------------------------------
# 市调模块（市场趋势洞察）：热度 0-100 研判 + 复盘报告 + 缓存 7 天过期
# 热度得分由 Agent 深度全网研判后录入（非脚本自动算），系统只持久化 + 报告 + 过期。
# ---------------------------------------------------------------------------

CACHE_DEFAULT_DAYS = 7


def _parse_dt(s):
    """解析 ISO 时间戳，失败返回 None。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_expired(cache_expires_at, now=None):
    """缓存是否过期（默认 7 天）。"""
    exp = _parse_dt(cache_expires_at)
    if not exp:
        return False
    return (now or datetime.now()) > exp


def start_research(countries=None, executor="本地本机", cache_days=CACHE_DEFAULT_DAYS,
                   db_path=None):
    """开始市调任务：生成 mr_id、写时间戳 + 覆盖国家 + 缓存过期时间。返回 mr_id。"""
    conn = init_db(db_path)
    mr_id = gen_mr_id()
    now = now_iso()
    expires = (datetime.now() + timedelta(days=cache_days)).isoformat(timespec="seconds")
    cs = json.dumps(countries or [], ensure_ascii=False)
    conn.execute(
        "INSERT INTO market_tasks (mr_id, countries, executor, started_at, status, cache_expires_at) "
        "VALUES (?,?,?,?, 'running', ?)",
        (mr_id, cs, executor, now, expires))
    conn.commit()
    conn.close()
    return mr_id


def finish_research(mr_id, db_path=None):
    """结束市调任务：写结束时间戳 + 时长，status done。"""
    conn = init_db(db_path)
    row = conn.execute("SELECT started_at FROM market_tasks WHERE mr_id=?", (mr_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"市调任务不存在: {mr_id}")
    now = now_iso()
    duration = None
    if row["started_at"]:
        try:
            duration = int((datetime.now() - datetime.fromisoformat(row["started_at"])).total_seconds())
        except Exception:
            pass
    conn.execute(
        "UPDATE market_tasks SET finished_at=?, duration_sec=?, status='done' WHERE mr_id=?",
        (now, duration, mr_id))
    conn.commit()
    conn.close()
    return {"mr_id": mr_id, "finished_at": now, "duration_sec": duration}


def save_country_score(mr_id, country, score, positives="", negatives="",
                       risks="", sources="", db_path=None):
    """保存/更新某国家热度研判（UPSERT）。score 须 0-100 整数。"""
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise ValueError(f"热度得分须 0-100 整数，收到 {score}")
    if not 0 <= score <= 100:
        raise ValueError(f"热度得分须 0-100，收到 {score}")
    conn = init_db(db_path)
    if not conn.execute("SELECT 1 FROM market_tasks WHERE mr_id=?", (mr_id,)).fetchone():
        conn.close()
        raise ValueError(f"市调任务不存在: {mr_id}")
    now = now_iso()
    country = (country or "").strip().upper()
    conn.execute(
        "INSERT INTO country_scores (mr_id, country, score, positives, negatives, risks, sources, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mr_id, country) DO UPDATE SET score=excluded.score, positives=excluded.positives, "
        "negatives=excluded.negatives, risks=excluded.risks, sources=excluded.sources, updated_at=excluded.updated_at",
        (mr_id, country, score, positives, negatives, risks, sources, now, now))
    conn.commit()
    conn.close()
    return {"mr_id": mr_id, "country": country, "score": score}


def list_research(limit=100, db_path=None):
    """市调任务列表（倒序），附各国得分统计 + 过期标记。"""
    conn = init_db(db_path)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM market_tasks ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()]
    for r in rows:
        sc = conn.execute(
            "SELECT COUNT(*) AS n, AVG(score) AS avg FROM country_scores WHERE mr_id=?",
            (r["mr_id"],)).fetchone()
        r["score_count"] = sc["n"]
        r["avg_score"] = round(sc["avg"], 1) if sc["avg"] is not None else None
        r["countries_list"] = json.loads(r.get("countries") or "[]")
        r["expired"] = is_expired(r.get("cache_expires_at"))
    conn.close()
    return rows


def get_research(mr_id, db_path=None):
    """取市调任务详情 + 各国得分（按热度降序）。"""
    conn = init_db(db_path)
    task = conn.execute("SELECT * FROM market_tasks WHERE mr_id=?", (mr_id,)).fetchone()
    if not task:
        conn.close()
        raise ValueError(f"市调任务不存在: {mr_id}")
    scores = [dict(r) for r in conn.execute(
        "SELECT * FROM country_scores WHERE mr_id=? ORDER BY score DESC, country",
        (mr_id,)).fetchall()]
    conn.close()
    d = dict(task)
    d["countries_list"] = json.loads(d.get("countries") or "[]")
    d["expired"] = is_expired(d.get("cache_expires_at"))
    return {"task": d, "scores": scores}


if __name__ == "__main__":
    # 供人工冒烟：python scripts/core.py --task-id xxx
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import argparse
    ap = argparse.ArgumentParser(description="core 业务逻辑冒烟")
    ap.add_argument("--task-id", help="查看某任务复刻报告数据")
    args = ap.parse_args()
    if args.task_id:
        print(json.dumps(build_report(args.task_id), ensure_ascii=False, indent=1, default=str))
    else:
        print("core.py 是库，请用 webapp 或直接 import。可 --task-id 冒烟。")

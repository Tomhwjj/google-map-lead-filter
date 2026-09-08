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


# 免费/公共邮箱域：邮箱后缀做「同公司」识别时排除这些域，
# 避免两家不同公司共用 gmail.com 被误判为同一家。
# （企业自有域名后缀如 alians-oze.pl 才是同公司铁证）
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "msn.com", "yahoo.com", "yahoo.pl", "yahoo.de", "aol.com", "icloud.com",
    "me.com", "mac.com", "proton.me", "protonmail.com", "gmx.com", "gmx.de",
    "gmx.net", "web.de", "mail.com", "zoho.com", "yandex.com", "yandex.ru",
    "qq.com", "163.com", "126.com", "sina.com",
    "wp.pl", "o2.pl", "interia.pl", "onet.pl", "poczta.onet.pl", "tlen.pl",
    "free.fr", "orange.fr", "laposte.net", "sfr.fr", "wanadoo.fr",
    "t-online.de", "freenet.de",
}


def _email_suffixes(email):
    """从邮箱串提取企业自有域名后缀（去重、剔除免费域）。"""
    out = set()
    for e in split_emails(email):
        _, _, dom = (e or "").rpartition("@")
        dom = dom.lower().strip()
        if dom and dom not in FREE_EMAIL_DOMAINS:
            out.add(dom)
    return out


def _find_existing_by_email_suffix(conn, email):
    """domain/name_key 都查不到时，用邮箱后缀反查已有企业（同公司多域名场景）。

    主站 alians-oze.pl 与商城 alians-shop.pl：域名不同、公司名也不同，但邮箱后缀
    都是 @alians-oze.pl —— 这是「同一家」的铁证。命中即返回现有行，走 diff 审核
    而非误判全新 INSERT。仅匹配企业自有域（已剔除免费邮箱域）。
    """
    suffixes = _email_suffixes(email)
    if not suffixes:
        return None
    for row in conn.execute(
            "SELECT * FROM companies WHERE email IS NOT NULL AND email != ''").fetchall():
        if suffixes & _email_suffixes(row["email"]):
            return row
    return None


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
            # 三级兜底：域名/公司名都失效时，用邮箱后缀反查（同公司多域名场景）
            existing = _find_existing_by_email_suffix(conn, lead.get("email"))

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
    # 新入库的「有邮箱」企业自动排队进企业邮箱联系人（等 gmail_sync.sync 推送）
    if not dry_run and stats["new_main_ids"]:
        try:
            queue_gmail_contacts(stats["new_main_ids"], db_path=db_path)
        except Exception:
            pass  # 邮箱队列失败不影响主入库
    conn.close()
    return stats


def _parse_json(s):
    """解析 JSON 字符串，空/失败返回 None。"""
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        return None


# 国家码 → 国际电话区号（WhatsApp 链接用，修掉 render_report 硬编码法国 +33 的债）
_CC = {"DE": "49", "FR": "33", "NL": "31", "IT": "39", "ES": "34",
       "GB": "44", "UK": "44", "BE": "32", "AT": "43", "PL": "48"}

# 国家码 → 中文名（市调排名 / 国家筛选下拉用，覆盖欧盟 27 国 + 乌克兰）
COUNTRY_NAMES = {
    "DE": "德国", "FR": "法国", "NL": "荷兰", "IT": "意大利", "ES": "西班牙",
    "PT": "葡萄牙", "BE": "比利时", "LU": "卢森堡", "AT": "奥地利", "PL": "波兰",
    "CZ": "捷克", "SK": "斯洛伐克", "HU": "匈牙利", "RO": "罗马尼亚", "BG": "保加利亚",
    "HR": "克罗地亚", "SI": "斯洛文尼亚", "GR": "希腊", "CY": "塞浦路斯", "MT": "马耳他",
    "IE": "爱尔兰", "SE": "瑞典", "DK": "丹麦", "FI": "芬兰", "EE": "爱沙尼亚",
    "LV": "拉脱维亚", "LT": "立陶宛", "UA": "乌克兰",
}

# 市调默认覆盖范围：欧盟 27 国 + 乌克兰（一键「开始市调」自动总调查）
EU_UKRAINE = ["DE", "FR", "NL", "IT", "ES", "BE", "AT", "PL", "PT", "SE",
              "DK", "FI", "IE", "CZ", "HU", "RO", "SK", "SI", "HR", "GR",
              "BG", "LT", "LV", "EE", "LU", "CY", "MT", "UA"]

# 市调 7 维度（判断依据拆解，热度分 = 综合研判）
RESEARCH_DIMS = ["政策补贴", "装机增速", "经销商活跃度", "进口需求",
                 "贸易壁垒", "新闻情绪", "竞品供应链"]


def wa_link(phone, country=""):
    """电话 -> WhatsApp 链接（去非数字，10 位本地号按国家补区号）。"""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    cc = _CC.get((country or "").strip().upper(), "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = cc + digits[1:]
    elif len(digits) == 10 and cc:
        digits = cc + digits
    return f"https://wa.me/{digits}"


# 卡片渲染所需字段（与 render_report.py 卡片结构对齐）
CARD_COLS = ["main_id", "company_name", "country", "city", "customer_type", "phone",
             "email", "website", "linkedin", "google_maps_url", "brands_found",
             "sells_deye", "score", "grade", "score_detail", "score_basis",
             "score_lt", "grade_lt", "score_detail_lt", "score_basis_lt",
             "reason", "pool", "domain"]


def split_emails(email):
    """把逗号分隔的邮箱串拆成去重后的邮箱列表（保序，去空白，忽略大小写去重）。"""
    if not email:
        return []
    seen = set()
    out = []
    for e in email.split(","):
        e = (e or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


# 邮箱是联系底线，但这些是垃圾/占位/机器人，不能进企业邮箱联系人（同步时跳过）
# 占位域名（网站构建器默认、错误追踪、示例域名）——精确匹配域名，避免误伤真实邮箱
_JUNK_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "example.edu",
    "email.com", "email.pl", "email.net", "test.com",
    "home.com", "company.com",                       # Wix 等构建器占位默认
    "sentry.io", "sentry.wixpress.com", "sentry-next.wixpress.com",
}
# 前缀/子串提示（no-reply 机器人、PrestaShop 授权邮箱等）
_JUNK_EMAIL_HINTS = ("no-reply", "noreply", "license@", "@2x", "@900")
_JUNK_EMAIL_TLDS = {"png", "jpg", "jpeg", "webp", "svg", "gif", "bmp", "ico",
                    "tiff", "css", "js", "pdf", "zip"}


def is_syncable_email(email):
    """判断邮箱是否值得同步到企业邮箱联系人（排除垃圾/占位/图片名/机器人）。"""
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return False
    local, _, domain = e.rpartition("@")
    if domain in _JUNK_EMAIL_DOMAINS:
        return False
    if any(h in e for h in _JUNK_EMAIL_HINTS):
        return False
    tld = e.rsplit(".", 1)[-1]
    if tld in _JUNK_EMAIL_TLDS:
        return False
    return True


def contact_note(country, main_id, company_name, n):
    """企业邮箱联系人备注格式：{国家} {企业主码} {企业名} #{n}（n = 该企业第 n 个邮箱）。"""
    return f"{(country or 'XX').strip().upper()} {main_id} {company_name} #{n}"


def list_companies(query="", pool=None, country=None, sells_deye=None, has_email=None,
                   limit=None, db_path=None):
    """企业库检索（电话/企业名/域名模糊匹配 + 国家/客户池/是否卖 Deye/是否有邮箱筛选）。

    sells_deye: None=全部 / True=仅卖 Deye / False=仅不卖 Deye。
    has_email:  None=全部 / True=仅有邮箱 / False=仅无邮箱（无邮箱=联系底线缺失，需特殊标记）。"""
    conn = init_db(db_path)
    sql = f"SELECT {', '.join(CARD_COLS)} FROM companies"
    conds, params = [], []
    if pool:
        conds.append("pool=?")
        params.append(pool)
    if country:
        conds.append("country=?")
        params.append(country.strip().upper())
    if sells_deye is not None:
        conds.append("sells_deye=?")
        params.append(1 if sells_deye else 0)
    if has_email is True:
        conds.append("email IS NOT NULL AND email != ''")
    elif has_email is False:
        conds.append("(email IS NULL OR email = '')")
    if query:
        q = f"%{query}%"
        conds.append("(company_name LIKE ? OR phone LIKE ? OR domain LIKE ? OR email LIKE ?)")
        params += [q, q, q, q]
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY score DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # 批量取各企业的企业邮箱联系人同步状态（main_id -> (total, synced)）
    gmap = {}
    for g in conn.execute(
        "SELECT main_id, COUNT(*) AS total, "
        "SUM(CASE WHEN status='synced' THEN 1 ELSE 0 END) AS synced "
        "FROM gmail_contacts GROUP BY main_id").fetchall():
        gmap[g["main_id"]] = (g["total"], g["synced"] or 0)
    conn.close()

    for r in rows:
        r["brands_found"] = _parse_json(r.get("brands_found")) or []
        r["score_detail"] = _parse_json(r.get("score_detail")) or {}
        r["score_basis"] = _parse_json(r.get("score_basis")) or {}
        r["score_detail_lt"] = _parse_json(r.get("score_detail_lt")) or {}
        r["score_basis_lt"] = _parse_json(r.get("score_basis_lt")) or {}
        r["wa_url"] = wa_link(r.get("phone"), r.get("country"))
        r["email_list"] = split_emails(r.get("email"))
        r["email_count"] = len(r["email_list"])
        total, synced = gmap.get(r["main_id"], (0, 0))
        r["gmail_contacts_total"] = total
        r["gmail_contacts_synced"] = synced
        r["gmail_synced"] = synced > 0
    return rows


def list_countries(db_path=None):
    """企业库已入库的国家分布（国家码 + 中文名 + 企业数），供筛选下拉。"""
    conn = init_db(db_path)
    rows = [dict(r) for r in conn.execute(
        "SELECT country, COUNT(*) AS n FROM companies "
        "WHERE country IS NOT NULL AND country != '' GROUP BY country ORDER BY n DESC").fetchall()]
    conn.close()
    for r in rows:
        r["name"] = COUNTRY_NAMES.get(r["country"], r["country"])
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


def list_diff_groups(status="pending", limit=200, db_path=None):
    """差异按企业聚合：每家企业一行（含待核验字段数），替代散乱的单字段列表。

    审核入口先看「哪些企业有差异」，再点进企业详情看字段级明细。返回：
    [{main_id, company_name, country, company_website, diff_count, latest_detected_at}]
    """
    conn = init_db(db_path)
    sql = """
        SELECT d.main_id, c.company_name, c.country, c.website AS company_website,
               COUNT(*) AS diff_count, MAX(d.detected_at) AS latest_detected_at
        FROM diffs d LEFT JOIN companies c ON d.main_id=c.main_id
        WHERE d.status=?
        GROUP BY d.main_id
        ORDER BY latest_detected_at DESC LIMIT ?
    """
    rows = [dict(r) for r in conn.execute(sql, (status, limit)).fetchall()]
    conn.close()
    return rows


def list_company_diffs(main_id, status=None, db_path=None):
    """取某企业的差异明细（字段级 old→new），status 不传则全部，按检测时间倒序。"""
    conn = init_db(db_path)
    sql = "SELECT * FROM diffs WHERE main_id=?"
    params = [main_id]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY detected_at DESC, id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def record_issue(task_id, category, title, detail="", solution="", status="open",
                 db_path=None):
    """记录一次获客遇到的问题（结构化落库，供后续迭代复盘）。

    架构文档第三节《复刻报告》第六节「背调问题清单」的数据来源。
    category: 采集/字段/编码/网络/数据质量/机制 等；status: open/resolved/deferred。
    返回新 issue id。
    """
    conn = init_db(db_path)
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO task_issues (task_id, category, title, detail, solution, "
        "status, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?)",
        (task_id, category, title, detail, solution, status, now,
         now if status == "resolved" else None))
    conn.commit()
    issue_id = cur.lastrowid
    conn.close()
    return issue_id


def list_task_issues(task_id=None, status=None, db_path=None):
    """列出获客问题清单（可按 task_id / status 过滤，倒序）。"""
    conn = init_db(db_path)
    sql = "SELECT * FROM task_issues"
    conds, params = [], []
    if task_id:
        conds.append("task_id=?")
        params.append(task_id)
    if status:
        conds.append("status=?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY id DESC"
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
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
    issues = [dict(r) for r in conn.execute(
        "SELECT * FROM task_issues WHERE task_id=? ORDER BY id", (task_id,)).fetchall()]

    conn.close()
    return {
        "task": dict(task),
        "stats": stats,
        "diffs": diffs,
        "new_companies": new_companies,
        "issues": issues,
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
    """取单个企业详情 + 其客户池轨迹 + 差异明细（字段级 old→new）+ 邮箱联系人同步轨迹。"""
    conn = init_db(db_path)
    c = conn.execute("SELECT * FROM companies WHERE main_id=?", (main_id,)).fetchone()
    if not c:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    logs = [dict(r) for r in conn.execute(
        "SELECT * FROM pool_log WHERE main_id=? ORDER BY changed_at DESC, id DESC",
        (main_id,)).fetchall()]
    diffs = list_company_diffs(main_id, db_path=db_path)
    gmail_contacts = [dict(r) for r in conn.execute(
        "SELECT * FROM gmail_contacts WHERE main_id=? ORDER BY id", (main_id,)).fetchall()]
    anomalies = [dict(r) for r in conn.execute(
        "SELECT * FROM email_anomalies WHERE main_id=? ORDER BY id DESC", (main_id,)).fetchall()]
    conn.close()
    company = dict(c)
    company["email_list"] = split_emails(company.get("email"))
    company["email_count"] = len(company["email_list"])
    return {"company": company, "pool_log": logs, "diffs": diffs,
            "gmail_contacts": gmail_contacts, "email_anomalies": anomalies}


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
    """开始市调任务：生成 mr_id、写时间戳 + 覆盖国家 + 缓存过期时间。返回 mr_id。

    不传 countries 时默认覆盖欧盟 27 国 + 乌克兰（一键「开始市调」总调查）。"""
    conn = init_db(db_path)
    mr_id = gen_mr_id()
    now = now_iso()
    expires = (datetime.now() + timedelta(days=cache_days)).isoformat(timespec="seconds")
    cs = json.dumps(countries or EU_UKRAINE, ensure_ascii=False)
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
                       risks="", sources="", dimensions=None, db_path=None):
    """保存/更新某国家热度研判（UPSERT）。score 须 0-100 整数。

    dimensions: 7 维度判断依据 dict {维度名: 依据一句话}（判断依据详情用）。"""
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
    dims = json.dumps(dimensions or {}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO country_scores (mr_id, country, score, positives, negatives, risks, sources, dimensions, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(mr_id, country) DO UPDATE SET score=excluded.score, positives=excluded.positives, "
        "negatives=excluded.negatives, risks=excluded.risks, sources=excluded.sources, "
        "dimensions=excluded.dimensions, updated_at=excluded.updated_at",
        (mr_id, country, score, positives, negatives, risks, sources, dims, now, now))
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
    for s in scores:
        s["country_name"] = COUNTRY_NAMES.get(s["country"], s["country"])
        s["dimensions"] = _parse_json(s.get("dimensions")) or {}
    return {"task": d, "scores": scores}


def get_country_detail(mr_id, country, db_path=None):
    """取某国家在市调任务中的完整研判（含 7 维度判断依据）。"""
    data = get_research(mr_id, db_path=db_path)
    country = (country or "").strip().upper()
    for s in data["scores"]:
        if s["country"] == country:
            return {"task": data["task"], "detail": s}
    raise ValueError(f"该市调任务未收录国家: {country}")


def latest_research_ranking(db_path=None):
    """最新市调任务的各国热度排名（score 降序），无任务返回 None。"""
    items = list_research(limit=1, db_path=db_path)
    if not items:
        return None
    return get_research(items[0]["mr_id"], db_path=db_path)


# ---------------------------------------------------------------------------
# 邮箱底线模块：邮箱个数 / 无邮箱异常记录 / 企业邮箱(Gmail)联系人同步轨迹
# 铁律：邮箱是联系企业的底线，没拿到邮箱的企业要划入异常记录并分析原因。
#       自动加企业邮箱联系人 = 系统记录轨迹；是否发邮件仍 100% 人工确认。
# ---------------------------------------------------------------------------

def analyze_no_email_reason(company):
    """分析某企业「没拿到邮箱」的原因（启发式起点，人工可覆写）。

    判断依据优先级（详见异常记录 reason 字段）：
      1. 无官网   → 连背调入口都没有，邮箱拿不到
      2. 未背调   → backfilled=0，官网还没抓，邮箱尚未采集
      3. 已背调   → backfilled=1，官网抓过了仍无邮箱 = 官网未公开（只有表单/电话）
    """
    website = (company.get("website") or "").strip()
    backfilled = company.get("backfilled")
    phone = (company.get("phone") or "").strip()
    if not website:
        return "无官网，无法采集邮箱（仅 Google Maps 条目）"
    if not backfilled:
        return "未背调，邮箱尚未采集（需背调官网 contact 页）"
    if phone:
        return "已背调但未提取到邮箱（官网可能只公开电话/表单）"
    return "已背调但未提取到邮箱（官网可能未公开邮箱）"


def scan_no_email_anomalies(task_id=None, db_path=None):
    """扫描所有「无邮箱」企业，划入异常记录（幂等：已有 open 异常则跳过）。

    返回 {scanned, new, skipped}。获客后调用一次，把「没拿到邮箱」的企业全部标记异常。"""
    conn = init_db(db_path)
    rows = [dict(r) for r in conn.execute(
        "SELECT main_id, company_name, country, website, phone, backfilled FROM companies "
        "WHERE email IS NULL OR email = ''").fetchall()]
    existing = {r["main_id"] for r in conn.execute(
        "SELECT main_id FROM email_anomalies WHERE status='open'").fetchall()}
    now = now_iso()
    new = 0
    for r in rows:
        if r["main_id"] in existing:
            continue
        reason = analyze_no_email_reason(r)
        conn.execute(
            "INSERT INTO email_anomalies (main_id, task_id, company_name, country, reason, status, created_at) "
            "VALUES (?,?,?,?,?, 'open', ?)",
            (r["main_id"], task_id, r["company_name"], r["country"], reason, now))
        new += 1
    conn.commit()
    conn.close()
    return {"scanned": len(rows), "new": new, "skipped": len(rows) - new}


def record_email_anomaly(main_id, task_id=None, reason=None, company_name=None,
                         country=None, db_path=None):
    """手工/脚本给单个企业记一条无邮箱异常（reason 不传则启发式分析）。返回 anomaly id。"""
    conn = init_db(db_path)
    row = conn.execute(
        "SELECT company_name, country, website, phone, backfilled FROM companies WHERE main_id=?",
        (main_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"企业不存在: {main_id}")
    if reason is None:
        reason = analyze_no_email_reason(dict(row))
    now = now_iso()
    cur = conn.execute(
        "INSERT INTO email_anomalies (main_id, task_id, company_name, country, reason, status, created_at) "
        "VALUES (?,?,?,?,?, 'open', ?)",
        (main_id, task_id, company_name or row["company_name"],
         country or row["country"], reason, now))
    conn.commit()
    anomaly_id = cur.lastrowid
    conn.close()
    return anomaly_id


def list_email_anomalies(status="open", limit=None, db_path=None):
    """无邮箱异常记录列表（status: open/resolved/None=全部），join 企业信息。"""
    conn = init_db(db_path)
    sql = ("SELECT a.*, c.website, c.phone FROM email_anomalies a "
           "LEFT JOIN companies c ON a.main_id=c.main_id")
    conds, params = [], []
    if status:
        conds.append("a.status=?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY a.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def resolve_email_anomaly(anomaly_id, resolved=True, db_path=None):
    """关闭/重开一条邮箱异常（企业补到邮箱/换有效邮箱后人工关闭）。"""
    conn = init_db(db_path)
    now = now_iso()
    conn.execute(
        "UPDATE email_anomalies SET status=?, resolved_at=? WHERE id=?",
        ("resolved" if resolved else "open", now if resolved else None, anomaly_id))
    conn.commit()
    conn.close()
    return {"anomaly_id": anomaly_id, "status": "resolved" if resolved else "open"}


def mark_email_invalid(email, reason=None, main_id=None, company_name=None,
                       country=None, db_path=None):
    """标记某邮箱为无效（bounce 550 User doesn't exist 等），落 email_anomalies。

    无效邮箱（email 非空）与「无邮箱」异常（email 空）同表，同步联系人时按
    email 非空 + status=open 过滤跳过。幂等：同邮箱已有 open 记录则更新 reason 后
    返回既有 id，不重复建。返回 anomaly id。
    """
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError(f"非法邮箱: {email!r}")
    reason = reason or "bounce: User doesn't exist"
    conn = init_db(db_path)
    # 传了 main_id 但没传公司名/国家时，自动从企业表补，保证异常记录自描述
    if main_id and (not company_name or not country):
        row_c = conn.execute(
            "SELECT company_name, country FROM companies WHERE main_id=?",
            (main_id,)).fetchone()
        if row_c:
            company_name = company_name or row_c["company_name"]
            country = country or row_c["country"]
    row = conn.execute(
        "SELECT id, reason FROM email_anomalies WHERE email=? AND status='open'",
        (email,)).fetchone()
    if row:
        if row["reason"] != reason:
            conn.execute("UPDATE email_anomalies SET reason=? WHERE id=?",
                         (reason, row["id"]))
        # 回填历史记录缺失的 main_id/公司名/国家
        conn.execute(
            "UPDATE email_anomalies SET main_id=COALESCE(NULLIF(main_id,''), ?), "
            "company_name=COALESCE(NULLIF(company_name,''), ?), "
            "country=COALESCE(NULLIF(country,''), ?) WHERE id=?",
            (main_id, company_name, country, row["id"]))
        conn.commit()
        anomaly_id = row["id"]
    else:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO email_anomalies (main_id, task_id, company_name, country, "
            "email, reason, status, created_at) VALUES (?,?,?,?,?,?, 'open', ?)",
            (main_id, None, company_name, country, email, reason, now))
        conn.commit()
        anomaly_id = cur.lastrowid
    conn.close()
    return anomaly_id


def list_invalid_emails(status="open", limit=None, db_path=None):
    """无效邮箱列表（email 非空 = 无效邮箱，区别于无邮箱异常），join 企业信息。"""
    conn = init_db(db_path)
    sql = ("SELECT a.*, c.website, c.phone FROM email_anomalies a "
           "LEFT JOIN companies c ON a.main_id=c.main_id "
           "WHERE a.email IS NOT NULL AND a.email != ''")
    params = []
    if status:
        sql += " AND a.status=?"
        params.append(status)
    sql += " ORDER BY a.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def get_invalid_email_set(db_path=None):
    """返回所有 open 状态无效邮箱的小写集合（供同步联系人前过滤）。"""
    conn = init_db(db_path)
    s = {r["email"].lower() for r in conn.execute(
        "SELECT email FROM email_anomalies "
        "WHERE email IS NOT NULL AND email != '' AND status='open'")}
    conn.close()
    return s


# ---- 企业邮箱(Gmail/Workspace) 账号 + 联系人同步轨迹 ----

def save_email_account(account_email, account_type="workspace", db_path=None):
    """绑定企业邮箱账号（账号元数据落 email_accounts，OAuth token 由 gmail_sync 存文件）。"""
    conn = init_db(db_path)
    now = now_iso()
    conn.execute(
        "INSERT INTO email_accounts (account_email, account_type, status, bound_at, updated_at) "
        "VALUES (?,?, 'active', ?, ?) "
        "ON CONFLICT(account_email) DO UPDATE SET account_type=excluded.account_type, "
        "status='active', updated_at=excluded.updated_at",
        (account_email, account_type, now, now))
    conn.commit()
    conn.close()
    return {"account_email": account_email, "account_type": account_type, "status": "active"}


def get_email_account(db_path=None):
    """取当前绑定（最新 active）的企业邮箱账号，无则 None。"""
    conn = init_db(db_path)
    r = conn.execute(
        "SELECT * FROM email_accounts WHERE status='active' ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(r) if r else None


def update_email_account_sync(account_email, db_path=None):
    """同步完成后刷新 last_sync_at。"""
    conn = init_db(db_path)
    now = now_iso()
    conn.execute(
        "UPDATE email_accounts SET last_sync_at=?, updated_at=? WHERE account_email=?",
        (now, now, account_email))
    conn.commit()
    conn.close()


def queue_gmail_contacts(main_ids=None, db_path=None):
    """把企业的可同步邮箱标记为「待同步」pending（未入 gmail_contacts 的才入）。

    获客入库后调用，把「有邮箱」的新客户自动排队，等 gmail_sync.sync 推送到企业邮箱联系人。
    main_ids=None 时扫全部有邮箱企业。返回 {queued, skipped}。"""
    conn = init_db(db_path)
    if main_ids:
        ph = ",".join("?" for _ in main_ids)
        rows = [dict(r) for r in conn.execute(
            f"SELECT main_id, company_name, country, email FROM companies "
            f"WHERE main_id IN ({ph})", list(main_ids))]
    else:
        rows = [dict(r) for r in conn.execute(
            "SELECT main_id, company_name, country, email FROM companies "
            "WHERE email IS NOT NULL AND email != ''")]
    existing = {(r["main_id"], r["email"]) for r in conn.execute(
        "SELECT main_id, email FROM gmail_contacts").fetchall()}
    # 已标记无效的邮箱（bounce 退信）不再重新排队，避免把坏邮箱又同步进联系人分组
    invalid = {r["email"].lower() for r in conn.execute(
        "SELECT email FROM email_anomalies "
        "WHERE email IS NOT NULL AND email != '' AND status='open'")}
    now = now_iso()
    queued = skipped = 0
    for r in rows:
        for i, email in enumerate(split_emails(r["email"]), start=1):
            if not is_syncable_email(email):
                skipped += 1
                continue
            if email.lower() in invalid:
                skipped += 1
                continue
            if (r["main_id"], email) in existing:
                skipped += 1
                continue
            note = contact_note(r["country"], r["main_id"], r["company_name"], i)
            conn.execute(
                "INSERT OR IGNORE INTO gmail_contacts (main_id, email, note, status, created_at) "
                "VALUES (?,?,?, 'pending', ?)",
                (r["main_id"], email, note, now))
            existing.add((r["main_id"], email))
            queued += 1
    conn.commit()
    conn.close()
    return {"queued": queued, "skipped": skipped}


def mark_gmail_contact(main_id, email, note, resource_name=None, status="synced",
                       error=None, skill_group=None, db_path=None):
    """记录一个企业邮箱 → Google 联系人同步结果（UPSERT by main_id+email）。"""
    conn = init_db(db_path)
    now = now_iso()
    conn.execute(
        "INSERT INTO gmail_contacts (main_id, email, note, contact_resource_name, "
        "skill_group, status, error, synced_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(main_id, email) DO UPDATE SET note=excluded.note, "
        "contact_resource_name=excluded.contact_resource_name, "
        "skill_group=excluded.skill_group, status=excluded.status, "
        "error=excluded.error, synced_at=excluded.synced_at",
        (main_id, email, note, resource_name, skill_group, status, error,
         now if status == "synced" else None, now))
    conn.commit()
    conn.close()


def list_gmail_contacts(main_id=None, status=None, limit=None, db_path=None):
    """企业邮箱联系人同步轨迹（可按 main_id / status 过滤），join 企业名。"""
    conn = init_db(db_path)
    sql = ("SELECT gc.*, c.company_name, c.country FROM gmail_contacts gc "
           "LEFT JOIN companies c ON gc.main_id=c.main_id")
    conds, params = [], []
    if main_id:
        conds.append("gc.main_id=?")
        params.append(main_id)
    if status:
        conds.append("gc.status=?")
        params.append(status)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY gc.id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def gmail_sync_stats(db_path=None):
    """企业邮箱联系人同步概览：待同步/已同步/失败 各多少 + 覆盖企业数。"""
    conn = init_db(db_path)
    r = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN status='synced' THEN 1 ELSE 0 END) AS synced, "
        "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
        "FROM gmail_contacts").fetchone()
    n_companies = conn.execute(
        "SELECT COUNT(DISTINCT main_id) AS n FROM gmail_contacts WHERE status='synced'").fetchone()["n"]
    conn.close()
    return {"total": r["total"] or 0, "pending": r["pending"] or 0,
            "synced": r["synced"] or 0, "failed": r["failed"] or 0,
            "companies_synced": n_companies or 0}


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

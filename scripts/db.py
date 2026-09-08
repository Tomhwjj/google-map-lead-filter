#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
数据层：SQLite 企业库（本地私有化获客系统的持久化底座）。

11 张表：
  companies       — 企业主表（main_id 主键，全字段 + 客户池 pool + 时间戳轨迹）
  tasks           — 获客任务表（task_id 主键，起止时间戳 / 时长 / 关键词快照 / 数据源清单）
  task_companies  — 任务 ↔ 企业关联（task_id + main_id，action: new/dup/diff）
  diffs           — 差异待核验队列（新旧值冲突，status pending/approved/rejected，人工审核）
  pool_log        — 客户池状态轨迹（main_id + from/to + 时间戳 + 操作人 + 备注）
  market_tasks    — 市调任务表（mr_id 主键，覆盖国家 / 执行人 / 时间戳 / 缓存 7 天过期）
  country_scores  — 各国热度得分（mr_id + country，0-100 分 + 利好利空 / 风险 / 来源快照）
  task_issues     — 获客问题记录（task_id 关联任务，分类/标题/详情/方案，供迭代复盘）
  email_accounts  — 绑定企业邮箱账号（Gmail/Workspace，OAuth token 存 data/gmail_token.json）
  gmail_contacts  — 企业邮箱 → Google 联系人同步轨迹（备注=国家+主码+企业名+n）
  email_anomalies — 邮箱异常记录（无邮箱：获客没拿到邮箱 → 分析原因；无效邮箱：bounce 退信 → 记失效邮箱+原因，同步时过滤）

被 core.py / webapp 共用；也可直接跑初始化：
    python db.py                 # 用默认库路径初始化
    python db.py --db path.db    # 指定库

设计要点：
  - main_id 全局唯一（LD{国家}-{uuid 短码}），重装库也不撞
  - 去重键 domain 优先（官网主域名，复用 merge_leads.py 的 domain_of 逻辑），
    无官网退回 name_key（公司名小写去空白）
  - 企业最终字段全集与 render_report.py 读的 leads_final.json 对齐
"""
import argparse
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from urllib.parse import urlparse

# 项目根 = 上一级目录（本文件在 scripts/ 下）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "leads.db")

# 客户池六分类（架构文档五分类 + 新增「已用邮箱尝试联系」，默认落「潜在客户(未联系)」）
DEFAULT_POOL = "潜在客户(未联系)"
POOLS = ["潜在客户(未联系)", "潜在客户(已取得联系)", "已用邮箱尝试联系",
         "重点关注客户", "黑名单客户", "老客户"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    main_id         TEXT PRIMARY KEY,
    domain          TEXT,
    name_key        TEXT,
    company_name    TEXT NOT NULL,
    country         TEXT,
    city            TEXT,
    customer_type   TEXT,
    phone           TEXT,
    email           TEXT,
    linkedin        TEXT,
    facebook        TEXT,
    address         TEXT,
    website         TEXT,
    rating          TEXT,
    google_maps_url TEXT,
    source_url      TEXT,
    profile_url     TEXT,
    brands_found    TEXT,
    brands_context  TEXT,
    product_tier    TEXT,
    scale_tier      TEXT,
    scale_basis     TEXT,
    scale_estimated INTEGER DEFAULT 0,
    backfilled      INTEGER DEFAULT 0,
    reason          TEXT,
    sells_deye      INTEGER DEFAULT 0,
    score           INTEGER,
    grade           TEXT,
    score_detail    TEXT,
    score_basis     TEXT,
    score_lt        INTEGER,
    grade_lt        TEXT,
    score_detail_lt TEXT,
    score_basis_lt  TEXT,
    pool            TEXT DEFAULT '""" + DEFAULT_POOL + """',
    first_seen_task TEXT,
    first_seen_at   TEXT,
    last_seen_task  TEXT,
    last_seen_at    TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    country      TEXT,
    keywords     TEXT,
    sources      TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    duration_sec INTEGER,
    status       TEXT DEFAULT 'running',
    report_path  TEXT
);

CREATE TABLE IF NOT EXISTS task_companies (
    task_id TEXT,
    main_id TEXT,
    action  TEXT,
    PRIMARY KEY (task_id, main_id)
);

CREATE TABLE IF NOT EXISTS diffs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id     TEXT,
    task_id     TEXT,
    field       TEXT,
    old_value   TEXT,
    new_value   TEXT,
    status      TEXT DEFAULT 'pending',
    detected_at TEXT,
    reviewed_at TEXT,
    reviewer    TEXT
);

CREATE TABLE IF NOT EXISTS pool_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id    TEXT,
    from_pool  TEXT,
    to_pool    TEXT,
    changed_at TEXT,
    operator   TEXT,
    note       TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name_key);
CREATE INDEX IF NOT EXISTS idx_companies_pool ON companies(pool);
CREATE INDEX IF NOT EXISTS idx_diffs_status ON diffs(status);
CREATE INDEX IF NOT EXISTS idx_task_companies_task ON task_companies(task_id);

CREATE TABLE IF NOT EXISTS market_tasks (
    mr_id            TEXT PRIMARY KEY,
    countries        TEXT,
    executor         TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    duration_sec     INTEGER,
    status           TEXT DEFAULT 'running',
    cache_expires_at TEXT,
    report_path      TEXT
);

CREATE TABLE IF NOT EXISTS country_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mr_id      TEXT,
    country    TEXT,
    score      INTEGER,
    positives  TEXT,
    negatives  TEXT,
    risks      TEXT,
    sources    TEXT,
    dimensions TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(mr_id, country)
);

CREATE INDEX IF NOT EXISTS idx_country_scores_mr ON country_scores(mr_id);
CREATE INDEX IF NOT EXISTS idx_market_tasks_status ON market_tasks(status);

CREATE TABLE IF NOT EXISTS task_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT,
    category    TEXT,
    title       TEXT,
    detail      TEXT,
    solution    TEXT,
    status      TEXT DEFAULT 'open',
    created_at  TEXT,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_issues_task ON task_issues(task_id);

-- 绑定企业邮箱账号（Gmail/Workspace），OAuth token 另存 data/gmail_token.json
CREATE TABLE IF NOT EXISTS email_accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_email TEXT UNIQUE,
    account_type  TEXT,                -- workspace / gmail
    status        TEXT DEFAULT 'active',  -- active / disabled
    bound_at      TEXT,
    last_sync_at  TEXT,
    updated_at    TEXT
);

-- 企业邮箱 → Google 联系人同步轨迹（每个邮箱一条，备注=国家+主码+企业名+n）
CREATE TABLE IF NOT EXISTS gmail_contacts (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id                TEXT,
    email                  TEXT,
    note                   TEXT,          -- 备注：{国家} {主码} {企业名} #{n}
    contact_resource_name  TEXT,          -- people/{id}
    skill_group            TEXT,          -- 归入的 skill 分组 resourceName（contactGroups/xxx），保证同企业不拆组
    status                 TEXT DEFAULT 'pending',  -- pending / synced / failed / invalid
    error                  TEXT,
    synced_at              TEXT,
    created_at             TEXT,
    UNIQUE(main_id, email)
);

CREATE INDEX IF NOT EXISTS idx_gmail_contacts_main ON gmail_contacts(main_id);
CREATE INDEX IF NOT EXISTS idx_gmail_contacts_status ON gmail_contacts(status);

-- 邮箱异常记录：两类共用一张表，靠 email 列是否为空区分。
--   email 为空  → 「无邮箱」异常（获客没拿到邮箱，分析为什么没邮箱）
--   email 非空  → 「无效邮箱」异常（bounce 550 User doesn't exist 等，分析为什么无效）
CREATE TABLE IF NOT EXISTS email_anomalies (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    main_id      TEXT,
    task_id      TEXT,
    company_name TEXT,
    country      TEXT,
    email        TEXT,          -- 无效邮箱地址（无邮箱异常该列为空）
    reason       TEXT,          -- 分析原因（无邮箱=为什么没邮箱；无效邮箱=为什么无效）
    status       TEXT DEFAULT 'open',  -- open / resolved
    created_at   TEXT,
    resolved_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_anomalies_main ON email_anomalies(main_id);
CREATE INDEX IF NOT EXISTS idx_email_anomalies_status ON email_anomalies(status);
"""


def get_conn(db_path=None):
    """获取连接（自动建 data/ 目录）。"""
    db_path = db_path or DEFAULT_DB
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path=None):
    """建表（幂等），返回连接。"""
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    # 老库迁移：country_scores 补 dimensions 列（7 维度判断依据）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(country_scores)")]
    if "dimensions" not in cols:
        conn.execute("ALTER TABLE country_scores ADD COLUMN dimensions TEXT")
    # 老库迁移：companies 补 scale_basis 列（规模判档详细依据）
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(companies)")]
    if "scale_basis" not in ccols:
        conn.execute("ALTER TABLE companies ADD COLUMN scale_basis TEXT")
    # 老库迁移：email_anomalies 补 email 列（无效邮箱地址；无邮箱异常该列为空）
    ecols = [r[1] for r in conn.execute("PRAGMA table_info(email_anomalies)")]
    if "email" not in ecols:
        conn.execute("ALTER TABLE email_anomalies ADD COLUMN email TEXT")
    # email 列索引须在 ALTER 之后建（老库 execute SCHEMA 时该列尚不存在）
    conn.execute("CREATE INDEX IF NOT EXISTS idx_email_anomalies_email ON email_anomalies(email)")
    # 老库迁移：gmail_contacts 补 skill_group 列（企业→skill 分组映射，保证同企业不拆组）
    gcols = [r[1] for r in conn.execute("PRAGMA table_info(gmail_contacts)")]
    if "skill_group" not in gcols:
        conn.execute("ALTER TABLE gmail_contacts ADD COLUMN skill_group TEXT")
    conn.commit()
    return conn


def gen_main_id(country=""):
    """生成全局唯一企业主码：LD{国家2码}-{uuid 短码}。"""
    c = (country or "XX").strip().upper()[:2]
    return f"LD{c}-{uuid.uuid4().hex[:10]}"


def gen_task_id():
    """生成全局唯一任务 ID：T{时间戳}-{4位随机}。"""
    return f"T{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"


def gen_mr_id():
    """生成全局唯一市调任务 ID：MR{时间戳}-{4位随机}。"""
    return f"MR{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4]}"


def normalize_domain(url):
    """提取官网主域名（去 www/端口/大小写），用于去重键。复用 merge_leads.py::domain_of。"""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc or urlparse("//" + url).netloc
        host = host.lower().lstrip("www.").lstrip(".")
        return host.split(":")[0]
    except Exception:
        return url.lower()


def normalize_name(name):
    """公司名归一化（小写去空白），用于无官网时的去重键。"""
    return re.sub(r"\s+", "", (name or "").lower())


def now_iso():
    """本地 ISO 时间戳（秒级）。"""
    return datetime.now().isoformat(timespec="seconds")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="初始化 SQLite 企业库")
    ap.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    args = ap.parse_args()

    conn = init_db(args.db)
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    conn.close()
    print(f"数据库已初始化: {args.db}")
    print(f"表 ({len(tables)}): {', '.join(tables)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
后台任务执行层：把「UI 按钮」桥接到「自动干活」。

用户点按钮 = 下指令，本模块在后台异步完成工作，主 Flask 进程不阻塞。两条路径：

1. 市调（research）    — headless `claude -p` 联网研判（需 LLM 判断），输出 JSON → 本模块落库
2. 获客（acquisition） — 直接跑确定性脚本流水线（无需 LLM），subprocess 逐步执行 → 落库

为什么获客不走 claude：headless claude 在 Windows 下 shell 工具是 pwsh，本机未装 pwsh，
执行不了 `python scripts/...`。而获客流水线（search→enf→merge→backfill→score→ingest）
全是确定性脚本，直接 subprocess 跑更稳、更快、不烧 token。

关键设计：
  - 所有任务异步跑（守护线程），输出写 data/task_logs/
  - 状态复用现有表：市调 market_tasks.status / 获客 tasks.status（running→done）
  - 铁律：客户状态 100% 人工，本层只采集/评分/入库到默认「潜在客户(未联系)」，不判定池子
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
LOGS_DIR = os.path.join(PROJECT_ROOT, "data", "task_logs")
WORK_ROOT = os.path.join(PROJECT_ROOT, "data", "acq_work")
PYTHON = sys.executable  # 用当前解释器跑流水线脚本

# 欧盟 27 国 + 乌克兰（与 core.EU_UKRAINE 一致，避免循环 import）
EU_UKRAINE = ["DE", "FR", "NL", "IT", "ES", "BE", "AT", "PL", "PT", "SE",
              "DK", "FI", "IE", "CZ", "HU", "RO", "SK", "SI", "HR", "GR",
              "BG", "LT", "LV", "EE", "LU", "CY", "MT", "UA"]

# 7 个判断维度（与 core.RESEARCH_DIMS 一致）
RESEARCH_DIMS = ["政策补贴", "装机增速", "经销商活跃度", "进口需求",
                 "贸易壁垒", "新闻情绪", "竞品供应链"]

# 背调品牌三组清单（我方 Deye + 贴牌 + 竞品，与 references/brand-mapping.md 一致）
BRANDS = ("Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark,"
          "Huawei,Sungrow,GoodWe,Fronius,SMA,Solax,Sofar,Growatt,Kostal,"
          "SolarEdge,Enphase,Hoymiles,FoxESS,Solis")

# 国家码 → (搜索语言代码, 本地语言关键词若干) —— 获客抓取用，缺失国用英文兜底
COUNTRY_KEYWORDS = {
    "DE": ("de", ["Speicher Großhändler", "Batteriespeicher Großhandel",
                  "Hybridwechselrichter Distributor", "Photovoltaik Speicher Installateur"]),
    "FR": ("fr", ["grossiste stockage batterie", "distributeur onduleur hybride",
                  "installateur batterie solaire"]),
    "NL": ("nl", ["thuisbatterij groothandel", "batterij opslag distributeur",
                  "thuisbatterij installateur"]),
    "IT": ("it", ["grossista accumulo batteria", "distributore inverter ibrido",
                  "installatore batteria solare"]),
    "ES": ("es", ["mayorista almacenamiento batería", "distribuidor inversor híbrido",
                  "instalador batería solar"]),
    "PL": ("pl", ["hurtownik magazyn energii", "dystrybutor falownik hybrydowy",
                  "instalator magazyn energii"]),
    "PT": ("pt", ["grossista armazenamento bateria", "distribuidor inversor híbrido",
                  "instalador bateria solar"]),
}


def _resolve_claude():
    """定位 headless claude 可执行入口。

    Windows 下 `claude` 是 npm 生成的 shim（claude / claude.cmd / claude.ps1），
    Python subprocess 不经过 shell，无扩展名的 shell 脚本找不到，需用 claude.cmd
    完整路径（实测可直接执行，无需 shell=True）。"""
    return shutil.which("claude.cmd") or shutil.which("claude") or "claude"


def _log_path(task_type, ref_id):
    os.makedirs(LOGS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return os.path.join(LOGS_DIR, f"{task_type}_{ref_id}_{stamp}.log")


# ---------------------------------------------------------------------------
# 市调（research）：headless claude 联网研判 → 输出 JSON → 本模块落库
# ---------------------------------------------------------------------------

def build_research_prompt(mr_id):
    """生成市调研判指令：claude 联网调研 28 国，最后输出一个 JSON 数组（不跑 bash）。

    本机 headless claude 无可用 shell（pwsh 未装），所以让 claude 只做「联网搜索 +
    结构化输出」，落库交给 launch_research 解析 JSON 后调 core.save_country_score。"""
    countries = " ".join(EU_UKRAINE)
    dims = " / ".join(RESEARCH_DIMS)
    return f'''你是「光伏海外获客系统」的市场调研 Agent，独立完成任务，不要向用户提问，不要中途停下。

【任务】对欧洲市场 28 国（欧盟 27 国 + 乌克兰）做光伏/储能市场调研，给每个国家打「开发热度分」(0-100)。

【28 国代码】{countries}
【7 个判断维度】{dims}

【打分标准】
- 80+：欧洲核心市场（德国/波兰/荷兰/西班牙等，装机大 + 增长快 + 政策稳）
- 70-79：成熟或成长中市场
- 60-69：中等市场
- 50-59：小市场或增速一般
- 49 以下：极小市场或高风险

【执行】对每个国家用 WebSearch 联网调研（英文关键词，例如 "<country> residential battery storage market 2025 growth solar"），据结果打分，写核心利好/利空/风险/来源URL，并给 7 个维度各写一句判断依据。搜不到数据的国家按行业通识合理给分，sources 写「初判（待核实）」。

【输出格式】最后只输出一个 JSON 数组（不要输出任何别的解释文字，不要用 markdown 代码块包裹），以 [[[RESEARCH_JSON]]] 开头、[[[END_RESEARCH_JSON]]] 结尾，格式：

[[[RESEARCH_JSON]]]
[
  {{"country":"DE","score":83,"positives":"...","negatives":"...","risks":"...","sources":"...","dimensions":{{"政策补贴":"...","装机增速":"...","经销商活跃度":"...","进口需求":"...","贸易壁垒":"...","新闻情绪":"...","竞品供应链":"..."}}}},
  {{"country":"FR","score":72,"positives":"...","negatives":"...","risks":"...","sources":"...","dimensions":{{"政策补贴":"...","装机增速":"...","经销商活跃度":"...","进口需求":"...","贸易壁垒":"...","新闻情绪":"...","竞品供应链":"..."}}}}
]
[[[END_RESEARCH_JSON]]]

【硬要求】JSON 数组里必须正好 28 个对象（{countries}），score 是 0-100 整数，dimensions 7 维全填；只输出 JSON，不要夹杂其他文字。'''


def _parse_research_json(text):
    """从 claude 输出里抠出 JSON 数组。返回 list[dict]，失败返回 []。"""
    if not text:
        return []
    m = re.search(r"\[\[\[RESEARCH_JSON\]\]\]\s*(\[.*?\])\s*\[\[\[END_RESEARCH_JSON\]\]\]",
                  text, re.S)
    raw = m.group(1) if m else None
    if raw is None:
        # 兜底：找第一个 [ 到最后一个 ] 之间的内容
        lo, hi = text.find("["), text.rfind("]")
        if lo == -1 or hi <= lo:
            return []
        raw = text[lo:hi + 1]
    try:
        arr = json.loads(raw)
    except Exception:
        # 再兜底：逐个 {..} 对象解析
        arr = []
        for obj in re.findall(r"\{[^{}]*\}", raw):
            try:
                arr.append(json.loads(obj))
            except Exception:
                continue
    return arr if isinstance(arr, list) else []


def launch_research(mr_id, log_dir=None):
    """后台启动 headless claude 研判 28 国，解析 JSON 输出并落库 + finish_research。

    异步不阻塞；日志写 data/task_logs/，状态由 core.finish_research 收尾。"""
    prompt = build_research_prompt(mr_id)
    log_path = _log_path("research", mr_id)

    def _run():
        from core import finish_research, save_country_score
        claude_bin = _resolve_claude()
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"# 市调任务 {mr_id} · headless claude 研判日志\n")
            f.write(f"# 命令: {claude_bin} -p ...\n\n")
            f.flush()
            try:
                proc = subprocess.run(
                    [claude_bin, "-p", prompt, "--output-format", "text"],
                    cwd=PROJECT_ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                )
                out = proc.stdout or ""
                f.write(out)
                f.flush()
            except Exception as e:
                f.write(f"\n[claude 启动失败] {type(e).__name__}: {e}\n")
                out = ""
        items = _parse_research_json(out)
        f2 = open(log_path, "a", encoding="utf-8")
        if not items:
            f2.write("\n[解析失败] 未从 claude 输出里抠出 JSON，市调未落库\n")
            f2.close()
            return
        saved = 0
        for it in items:
            try:
                save_country_score(
                    mr_id, it.get("country", ""),
                    score=int(it.get("score", 0)),
                    positives=it.get("positives", ""),
                    negatives=it.get("negatives", ""),
                    risks=it.get("risks", ""),
                    sources=it.get("sources", ""),
                    dimensions=it.get("dimensions") or {},
                )
                saved += 1
            except Exception as e:
                f2.write(f"\n[落库失败 {it.get('country')}] {e}\n")
        try:
            finish_research(mr_id)
            f2.write(f"\nRESEARCH_DONE · 录入 {saved} 国\n")
        except Exception as e:
            f2.write(f"\n[finish_research 失败] {e}\n")
        f2.close()

    threading.Thread(target=_run, daemon=True).start()
    return log_path


# ---------------------------------------------------------------------------
# 获客（acquisition）：直接跑确定性脚本流水线，不依赖 claude
# ---------------------------------------------------------------------------

def launch_acquisition(task_id, country="", log_dir=None):
    """后台直接跑获客流水线：search → enf → merge → backfill → score → ingest。

    异步不阻塞；每步写日志，最后 core.ingest_leads 三段式比对入库 + finish_task。
    任一步脚本失败记日志继续，但最终必须尝试入库并收尾（避免任务卡 running）。"""
    country = (country or "").strip().upper()
    lang, local_kws = COUNTRY_KEYWORDS.get(country, ("", []))
    kws = local_kws or [
        "battery storage distributor", "hybrid inverter wholesaler",
        "solar battery installer", "energy storage importer"]
    work = os.path.join(WORK_ROOT, f"acq_{task_id}")
    os.makedirs(work, exist_ok=True)
    log_path = _log_path("acquisition", task_id)
    merged_csv = os.path.join(work, "merged.csv")
    backfill_json = os.path.join(work, "backfill.json")
    scored_json = os.path.join(work, "leads_scored.json")

    # 搜索词：本地语言词 + 国家码拼接（英文兜底）
    search_queries = [f"{k} {country}" if country else k for k in kws]

    def _run():
        from core import finish_task, ingest_leads

        def run_step(step, argv):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {step} =====\n")
                f.write("  cmd: " + " ".join(argv) + "\n")
                f.flush()
                r = subprocess.run(argv, cwd=PROJECT_ROOT, stdout=f,
                                   stderr=subprocess.STDOUT,
                                   encoding="utf-8", errors="replace")
                f.write(f"\n[exit {r.returncode}]\n")
                f.flush()
                return r.returncode

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"# 获客任务 {task_id} · 国家 {country or '未指定'} · 直接脚本流水线\n")
            f.write(f"# 工作目录 {work}\n\n")
            f.flush()

        # 1. 搜索 API 批量（AnySearch）
        search_argv = [PYTHON, os.path.join(SCRIPTS_DIR, "search_leads.py"),
                       *search_queries, "--max-results", "10",
                       "--out", os.path.join(work, "search.csv")]
        if lang:
            search_argv += ["--language", lang]
        run_step("1.搜索API(search_leads)", search_argv)

        # 2. ENF 目录批量（seller + installer）
        enf_argv = [PYTHON, os.path.join(SCRIPTS_DIR, "fetch_enf.py"),
                    "--country", country or "DE,FR,NL,IT,ES,PL",
                    "--category", "seller,installer", "--max", "150",
                    "--out", os.path.join(work, "enf.csv")]
        run_step("2.ENF目录(fetch_enf)", enf_argv)

        # 3. 合并去重（工作目录下所有 csv）
        run_step("3.合并去重(merge_leads)", [
            PYTHON, os.path.join(SCRIPTS_DIR, "merge_leads.py"), work,
            "--out", merged_csv])

        # 4. 官网背调（词边界品牌匹配）
        run_step("4.官网背调(backfill)", [
            PYTHON, os.path.join(SCRIPTS_DIR, "backfill.py"), merged_csv,
            "--out", backfill_json, "--brands", BRANDS,
            "--deye", "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark", "--fast"])

        # 5. 双模式评分 + 分级
        run_step("5.评分分级(score_leads)", [
            PYTHON, os.path.join(SCRIPTS_DIR, "score_leads.py"), backfill_json,
            "--out", scored_json])

        # 6. 三段式比对入库 + 收尾
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n===== 6.三段式入库(ingest) =====\n")
            f.flush()
            stats = {"total": 0, "new": 0, "dup": 0, "diff": 0}
            try:
                if os.path.exists(scored_json):
                    leads = json.load(open(scored_json, encoding="utf-8"))
                    if isinstance(leads, dict):
                        leads = leads.get("leads") or leads.get("results") or []
                    if isinstance(leads, list) and leads:
                        stats = ingest_leads(leads, task_id)
                f.write(json.dumps(stats, ensure_ascii=False) + "\n")
            except Exception as e:
                f.write(f"[ingest 失败] {type(e).__name__}: {e}\n")
            finally:
                try:
                    finish_task(task_id)
                    f.write("ACQUISITION_DONE\n")
                except Exception as e:
                    f.write(f"[finish_task 失败] {e}\n")

    threading.Thread(target=_run, daemon=True).start()
    return log_path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(build_research_prompt("MR_TEST")[:2000])

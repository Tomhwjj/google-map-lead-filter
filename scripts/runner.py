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

# 7+2 个判断维度（与 core.RESEARCH_DIMS 一致；v2 加「潜在客户总量/有效获客源」）
try:
    from core import RESEARCH_DIMS as _CORE_DIMS
    RESEARCH_DIMS = _CORE_DIMS
except ImportError:
    RESEARCH_DIMS = ["政策补贴", "装机增速", "经销商活跃度", "进口需求",
                     "贸易壁垒", "新闻情绪", "竞品供应链",
                     "潜在客户总量", "有效获客源"]

# 背调品牌三组清单（我方 Deye + 贴牌 + 竞品，与 references/brand-mapping.md 一致）
BRANDS = ("Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark,"
          "Huawei,Sungrow,GoodWe,Fronius,SMA,Solax,Sofar,Growatt,Kostal,"
          "SolarEdge,Enphase,Hoymiles,FoxESS,Solis")

# 国家码 → (搜索语言代码, 本地语言关键词若干) —— 获客抓取用
# v2（2026-09-12 WorkBuddy）：补齐欧盟 27 国 + 乌克兰全覆盖（此前仅 7 国，其余英文兜底
# 导致小语种市场召回差——语言问题频发的根源）。词序：储能批发 / 经销 / 安装。
# 口径同 references/search-keywords.md（储能优先）；关键语言翻译建议背调时人工抽查校准。
COUNTRY_KEYWORDS = {
    "DE": ("de", ["Speicher Großhändler", "Batteriespeicher Distributor",
                  "Photovoltaik Speicher Installateur"]),
    "FR": ("fr", ["grossiste stockage batterie", "distributeur onduleur hybride",
                  "installateur batterie solaire"]),
    "NL": ("nl", ["thuisbatterij groothandel", "batterij opslag distributeur",
                  "thuisbatterij installateur"]),
    "BE": ("nl", ["thuisbatterij groothandel", "batterij opslag distributeur",
                  "thuisbatterij installateur"]),
    "AT": ("de", ["Speicher Großhändler", "Batteriespeicher Distributor",
                  "Photovoltaik Speicher Installateur"]),
    "IT": ("it", ["grossista sistemi di accumulo", "distributore inverter ibrido",
                  "installatore batteria solare"]),
    "ES": ("es", ["mayorista almacenamiento batería", "distribuidor inversor híbrido",
                  "instalador batería solar"]),
    "PL": ("pl", ["hurtownik magazynów energii", "dystrybutor magazynu energii",
                  "instalator magazynów energii"]),
    "PT": ("pt", ["grossista armazenamento bateria", "distribuidor inversor híbrido",
                  "instalador bateria solar"]),
    "SE": ("sv", ["batterilager grossist", "solcellsbatteri distributör",
                  "solcellsinstallatör batterilager"]),
    "DK": ("da", ["batterilager grossist", "solcellebatteri distributør",
                  "solcelleinstallatør"]),
    "FI": ("fi", ["energiavarasto tukku", "hybridivaihtosuuntaaja jälleenmyyjä",
                  "aurinkopaneeli akku asentaja"]),
    "IE": ("en", ["battery storage wholesaler", "hybrid inverter distributor",
                  "solar battery installer"]),
    "CZ": ("cs", ["velkoobchod bateriová úložiště", "distributor hybridních střídačů",
                  "instalace fotovoltaiky s baterií"]),
    "HU": ("hu", ["energiatároló nagykereskedő", "hibrid inverter forgalmazó",
                  "napelem akkumulátor telepítő"]),
    "RO": ("ro", ["angrosist sisteme de stocare", "distribuitor invertor hibrid",
                  "instalator panouri solare baterii"]),
    "SK": ("sk", ["veľkoobchod batériové úložiská", "distributor hybridných meničov",
                  "inštalácia fotovoltaiky s batériou"]),
    "SI": ("sl", ["trgovina na veliko baterijska skladišča",
                  "distributer hibridnih pretvornikov",
                  "namestitev fotovoltaike z baterijo"]),
    "HR": ("hr", ["veleprodaja baterijskih sustava", "distributer hibridnih invertera",
                  "instalater solarnih sustava"]),
    "GR": ("el", ["χονδρικό εμπόριο συστημάτων αποθήκευσης",
                  "διανομέας υβριδικών αντιστροφέων",
                  "εγκαταστάτης φωτοβολταϊκών με μπαταρία"]),
    "BG": ("bg", ["едро на акумулаторни системи", "дистрибутор на хибридни инвертори",
                  "монтажник на соларни батерии"]),
    "LT": ("lt", ["didmeninė prekyba energijos kaupikliais",
                  "platintojas hibridiniai keitikliai",
                  "montuotojas saulės baterijų sistemos"]),
    "LV": ("lv", ["vairumtirdzniecība enerģijas uzglabāšana",
                  "izplatītājs hibrīdie invertori",
                  "uzstādītājs saules bateriju sistēmas"]),
    "EE": ("et", ["hulgimüük akudesüsteemid", "edasimüüja hübriidinverterid",
                  "paigaldaja päikesepaneelide akud"]),
    "LU": ("fr", ["grossiste stockage batterie", "distributeur onduleur hybride",
                  "installateur batterie solaire"]),
    "CY": ("el", ["χονδρικό εμπόριο συστημάτων αποθήκευσης",
                  "διανομέας υβριδικών αντιστροφέων",
                  "εγκαταστάτης φωτοβολταϊκών με μπαταρία"]),
    "MT": ("en", ["battery storage wholesaler", "hybrid inverter distributor",
                  "solar battery installer"]),
    "UA": ("uk", ["оптовий продавець накопичувачів енергії",
                  "дистриб'ютор гібридних інверторів",
                  "монтажник сонячних станцій з накопичувачами"]),
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
    n_dims = len(RESEARCH_DIMS)
    return f'''你是「光伏海外获客系统」的市场调研 Agent，独立完成任务，不要向用户提问，不要中途停下。

【任务】对欧洲市场 28 国（欧盟 27 国 + 乌克兰）做光伏/储能市场调研，给每个国家打「开发热度分」(0-100)。

【28 国代码】{countries}
【{n_dims} 个判断维度】{dims}

【打分标准】
- 80+：欧洲核心市场（德国/波兰/荷兰/西班牙等，装机大 + 增长快 + 政策稳）
- 70-79：成熟或成长中市场
- 60-69：中等市场
- 50-59：小市场或增速一般
- 49 以下：极小市场或高风险

【两个量化维度的硬要求】
- 「潜在客户总量」：给出该国可触达的经销商/安装商/批发商**数量上限测算**，必须带来源佐证——目录源按「页数×每页条数」算（如 ENF installer 目录 12 页×100≈1200 家）、官方认证注册库给注册数（如波兰 UDT 认证安装商、德国 TÜV/BSW 名单）、行业协会给会员数、行业报告给渠道商总数。格式：「约 N 家（依据：…）」，拿不到硬数据给区间并标「估」。
- 「有效获客源」：列出该国**实测或强证据**的获客渠道及其产量证据——如「ENF installer 目录 12 页（实测）」「Google Maps 城市级矩阵有效，20 城可铺」「品牌官网 find-a-distributor 有本地页」「UDT 注册库公开可查」。没证据的渠道不写。

【执行】对每个国家用 WebSearch 联网调研（英文关键词，例如 "<country> residential battery storage market 2025 growth solar"），据结果打分，写核心利好/利空/风险/来源URL，并给 {n_dims} 个维度各写一句判断依据。搜不到数据的国家按行业通识合理给分，sources 写「初判（待核实）」。

【输出格式】最后只输出一个 JSON 数组（不要输出任何别的解释文字，不要用 markdown 代码块包裹），以 [[[RESEARCH_JSON]]] 开头、[[[END_RESEARCH_JSON]]] 结尾，格式：

[[[RESEARCH_JSON]]]
[
  {{"country":"DE","score":83,"positives":"...","negatives":"...","risks":"...","sources":"...","dimensions":{{"政策补贴":"...","装机增速":"...","经销商活跃度":"...","进口需求":"...","贸易壁垒":"...","新闻情绪":"...","竞品供应链":"...","潜在客户总量":"约 N 家（依据：…）","有效获客源":"ENF 目录 N 页（实测）；Maps 城市级矩阵…"}}}},
  {{"country":"FR","score":72,"positives":"...","negatives":"...","risks":"...","sources":"...","dimensions":{{"政策补贴":"...","装机增速":"...","经销商活跃度":"...","进口需求":"...","贸易壁垒":"...","新闻情绪":"...","竞品供应链":"...","潜在客户总量":"约 N 家（依据：…）","有效获客源":"…"}}}}
]
[[[END_RESEARCH_JSON]]]

【硬要求】JSON 数组里必须正好 28 个对象（{countries}），score 是 0-100 整数，dimensions {n_dims} 维全填（潜在客户总量/有效获客源必须给数量和证据，不许写「无数据」空话，实在没有就给区间+「估」）；只输出 JSON，不要夹杂其他文字。'''


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
        from core import finish_task, ingest_leads, normalize_domain

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
            "--deye", "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark"])  # 正式跑全量模式（--fast 仅限测试，勿加回）

        # 4.5 证据完整性闸门（2026-09-22 收编进正式流程，默认开启）
        # 为什么要有这一步：backfill 抄不到证据就留空，score_leads 会按兜底给分，
        # 「证据缺失」的线索会以正常分数悄悄入库（task_issues #14：1199 家落 58 分基线无人察觉）。
        # references/qualification-rules.md §61-76 那套联网补证规则此前**代码里没有触发点**，
        # 实测一次都没跑过（09-05 批 5 家正文是报错页的线索全部只标「未确认」，无一家补抓/补搜）。
        # 本步只做「判定 + 出工作单」：不联网、不抓取、不写库 —— 补证手段（kitesurf / anysearch /
        # WebSearch）是 agent 侧能力，脚本只负责说清「缺什么、该用什么手段、搜什么词」。
        # 三态（默认出单，不改现有入库吞吐，但让证据缺口从此留痕、可审计）：
        #   默认 / =1 / =on     出工作单到 work/evidence_gaps.json，缺口摘要写进任务日志，不阻断入库
        #   =strict             有「正文不可用」的阻断级缺口则 exit 3，拒绝进入评分/入库
        #   =0 / =off / =none  显式关闭本步（逃生阀：闸门自身出问题时用它绕过，无需改代码）
        gate_mode = os.environ.get("ACQ_EVIDENCE_GATE", "1").strip().lower()
        gaps_json = os.path.join(work, "evidence_gaps.json")
        gate_rc = 0
        if gate_mode in ("0", "off", "none"):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n===== 4.5 证据闸门(evidence_gate) =====\n"
                        "  跳过：ACQ_EVIDENCE_GATE 显式关闭（=1 出工作单 / =strict 卡入库）\n")
                f.flush()
        else:
            gate_argv = [PYTHON, os.path.join(SCRIPTS_DIR, "evidence_gate.py"),
                         backfill_json, "--out", gaps_json, "--only-gaps"]
            if gate_mode == "strict":
                gate_argv.append("--strict")
            gate_rc = run_step("4.5 证据闸门(evidence_gate)", gate_argv)

        # 5. 双模式评分 + 分级
        # 2026-09-22（task_issues #14）：rc 原先被丢掉，评分失败也照常入库 —— 而
        # scored_json 若是上一轮的残留文件就照样存在，「存在即入库」等于把过期分数灌进库。
        # 现在评分失败一律不入库。加 --require-judged 后这里就是手工判闸门的实际落点。
        rc_score = run_step("5.评分分级(score_leads)", [
            PYTHON, os.path.join(SCRIPTS_DIR, "score_leads.py"), backfill_json,
            "--out", scored_json])

        # 6. 三段式比对入库 + 收尾
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n===== 6.三段式入库(ingest) =====\n")
            f.flush()
            stats = {"total": 0, "new": 0, "dup": 0, "diff": 0}
            ok = True
            if rc_score != 0:
                ok = False
                f.write(f"[abort] 评分步骤 exit {rc_score}，跳过入库"
                        f"（避免把过期/缺失的评分产物灌进库）\n")
                f.flush()
            # 证据闸门条目级过滤（2026-09-24 改进 #1）：strict 模式下不再整批 abort，
            # 而是剔除「正文不可用」的阻断条、好条照常入库。原先 exit 3 让整批 0 条入库，
            # 对「1 条坏」和「359 条坏」是同一个信号。默认档（=1）本就不卡入库，此分支不触发。
            blocked_domains = set()
            if gate_rc == 3 and os.path.exists(gaps_json):
                try:
                    gaps = json.load(open(gaps_json, encoding="utf-8"))
                    for r in gaps.get("records", []):
                        if r.get("severity") == "blocking":
                            d = normalize_domain(r.get("website"))
                            if d:
                                blocked_domains.add(d)
                    f.write(f"[闸门] 阻断级 {len(blocked_domains)} 条（正文不可用）挂起不入库，好条照常入库\n")
                    f.flush()
                except Exception as e:
                    # 读不到 gaps_json 就回退整批 abort，别把坏条当新鲜数据灌进库
                    ok = False
                    f.write(f"[闸门] 读 gaps_json 失败，回退整批 abort: {e}\n")
                    f.flush()
            try:
                if ok and os.path.exists(scored_json):
                    leads = json.load(open(scored_json, encoding="utf-8"))
                    if isinstance(leads, dict):
                        leads = leads.get("leads") or leads.get("results") or []
                    if isinstance(leads, list):
                        if blocked_domains:
                            kept, blocked_n = [], 0
                            for l in leads:
                                if normalize_domain(l.get("website")) in blocked_domains:
                                    blocked_n += 1
                                    continue
                                kept.append(l)
                            f.write(f"[闸门] 剔除阻断条 {blocked_n}，待入库 {len(kept)} 条\n")
                            f.flush()
                            leads = kept
                        if leads:
                            stats = ingest_leads(leads, task_id)
                f.write(json.dumps(stats, ensure_ascii=False) + "\n")
            except Exception as e:
                ok = False
                f.write(f"[ingest 失败] {type(e).__name__}: {e}\n")
            finally:
                try:
                    # 跑挂的标 failed，不冒充 done（否则僵尸单与正常单在库里长得一样）
                    finish_task(task_id, status="done" if ok else "failed")
                    f.write("ACQUISITION_DONE\n" if ok else "ACQUISITION_FAILED\n")
                except Exception as e:
                    f.write(f"[finish_task 失败] {e}\n")

    threading.Thread(target=_run, daemon=True).start()
    return log_path


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(build_research_prompt("MR_TEST")[:2000])

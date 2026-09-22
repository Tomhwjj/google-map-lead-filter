"""Google Maps 单源获客流水线（fetch → merge → backfill → score → ingest）。

v1（2026-09-12 WorkBuddy）：补 runner.py 没接 Maps 的缺口，复用同一套
merge/backfill/score/ingest 链路，任务轨迹与三段式入库完全一致。
只在抓取步与 runner 不同：fetch_gmaps.py v2 多查询批处理 + --locale + --resume。

用法：
  python scripts/run_gmaps_acq.py --country PL --queries-file data/acq_work/kw_pl_matrix.txt \
      --locale pl-PL --max 45
中断后重跑同一命令即可续跑（--resume 断点 + ingest 幂等）。"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner  # noqa: E402  复用 PROJECT_ROOT/SCRIPTS_DIR/BRANDS/WORK_ROOT

from core import finish_task, ingest_leads, start_task  # noqa: E402

PYTHON = "C:\\Python314\\python.exe"


def sh(step, argv, log):
    log.write(f"\n===== {step} =====\n  cmd: {' '.join(argv)}\n")
    log.flush()
    r = subprocess.run(argv, cwd=runner.PROJECT_ROOT, stdout=log,
                       stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
    log.write(f"  [exit {r.returncode}]\n")
    log.flush()
    return r.returncode


def fail_task(task_id, log):
    """中止流水线：标 failed（不冒充 done），日志留痕。"""
    try:
        finish_task(task_id, status="failed")
        log.write("ACQUISITION_FAILED\n")
    except Exception as e:
        log.write(f"[finish_task 失败] {e}\n")
    log.flush()


def main():
    ap = argparse.ArgumentParser(description="gmaps 单源获客流水线")
    ap.add_argument("--country", required=True, help="国家码，如 PL")
    ap.add_argument("--queries-file", required=True)
    ap.add_argument("--locale", default="")
    ap.add_argument("--max", type=int, default=45, help="单查询抓取上限（≤50 合规）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划不执行")
    ap.add_argument("--backfill-fast", action="store_true",
                    help="背调快速模式（跳过联系页）。⚠ 仅限测试：正式跑默认全量")
    ap.add_argument("--task-id", default="",
                    help="续跑既有任务：指定旧 task_id 复用其工作目录（gmaps.ckpt.json 断点），不新建任务")
    args = ap.parse_args()

    kws = [l.strip() for l in open(args.queries_file, encoding="utf-8")
           if l.strip() and not l.strip().startswith("#")]
    country = args.country.strip().upper()
    print(f"任务计划：{country} · {len(kws)} 查询 × max{args.max} · locale={args.locale or '默认'}")
    if args.dry_run:
        for k in kws[:5]:
            print("  e.g.", k)
        return

    if args.task_id:
        # 续跑：复用旧 task_id 及其工作目录（ckpt/merged/backfill 断点都在里面）
        task_id = args.task_id.strip()
        print(f"续跑既有任务 task_id={task_id}（不新建任务记录）")
    else:
        task_id = start_task(country=country, keywords=kws, sources=["gmaps"])
    work = os.path.join(runner.WORK_ROOT, f"acq_{task_id}")
    os.makedirs(work, exist_ok=True)
    log_path = runner._log_path("acquisition", task_id)
    log = open(log_path, "a", encoding="utf-8")
    log.write(f"\n# 获客任务 {task_id} · 国家 {country} · gmaps 单源流水线\n")
    log.write(f"# {len(kws)} 查询 · locale={args.locale} · max={args.max}\n")
    log.flush()
    print(f"task_id={task_id}  log={log_path}")

    gmaps_csv = os.path.join(work, "gmaps.csv")
    merged_csv = os.path.join(work, "merged.csv")
    backfill_json = os.path.join(work, "backfill.json")
    scored_json = os.path.join(work, "leads_scored.json")

    # 1. Maps 批量抓取（--resume 断点，中断重跑同一命令即续）
    argv = [PYTHON, os.path.join(runner.SCRIPTS_DIR, "fetch_gmaps.py"),
            "--queries-file", args.queries_file, "--max", str(args.max),
            "--out", gmaps_csv, "--resume", "--country", country]
    if args.locale:
        argv += ["--locale", args.locale]
    rc = sh("1.Maps抓取(fetch_gmaps)", argv, log)
    if rc != 0:
        # 抓取挂了就别往下走：work 目录里的 gmaps.csv 可能是上一轮的残留，
        # merge 它会安静地把旧数据当新数据灌进库。
        log.write(f"[abort] 抓取步骤 exit {rc}，中止流水线（不 merge 残留文件）\n")
        fail_task(task_id, log)
        log.close()
        print(f"FAILED 抓取步骤 exit {rc}")
        return

    # 2. 合并去重
    sh("2.合并去重(merge_leads)",
       [PYTHON, os.path.join(runner.SCRIPTS_DIR, "merge_leads.py"), work,
        "--out", merged_csv], log)

    # 3. 官网背调
    sh("3.官网背调(backfill)",
       [PYTHON, os.path.join(runner.SCRIPTS_DIR, "backfill.py"), merged_csv,
        "--out", backfill_json, "--brands", runner.BRANDS,
        # 正式跑一律全量模式（含 contact/impressum 页）；--fast 仅限测试，经 --backfill-fast 显式传入
        "--deye", "Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark"]
       + (["--fast"] if args.backfill_fast else []), log)

    # 4. 评分分级
    # 2026-09-22（task_issues #14）：rc 同 runner.py step5 —— 评分失败不入库，
    # 否则 scored_json 的上一轮残留照样存在，「存在即入库」等于把过期分数灌进库。
    rc_score = sh("4.评分分级(score_leads)",
                  [PYTHON, os.path.join(runner.SCRIPTS_DIR, "score_leads.py"), backfill_json,
                   "--out", scored_json], log)

    # 5. 三段式入库 + 收尾
    log.write("\n===== 5.三段式入库(ingest) =====\n")
    log.flush()
    stats = {"total": 0, "new": 0, "dup": 0, "diff": 0}
    ok = True
    if rc_score != 0:
        ok = False
        log.write(f"[abort] 评分步骤 exit {rc_score}，跳过入库"
                  f"（避免把过期/缺失的评分产物灌进库）\n")
        log.flush()
    try:
        if ok and os.path.exists(scored_json):
            leads = json.load(open(scored_json, encoding="utf-8"))
            if isinstance(leads, dict):
                leads = leads.get("leads") or leads.get("results") or []
            if isinstance(leads, list) and leads:
                stats = ingest_leads(leads, task_id)
        log.write(json.dumps(stats, ensure_ascii=False) + "\n")
    except Exception as e:
        ok = False
        log.write(f"[ingest 失败] {type(e).__name__}: {e}\n")
    finally:
        try:
            # 跑挂的标 failed，不冒充 done（否则僵尸单与正常单在库里长得一样）
            finish_task(task_id, status="done" if ok else "failed")
            log.write("ACQUISITION_DONE\n" if ok else "ACQUISITION_FAILED\n")
        except Exception as e:
            log.write(f"[finish_task 失败] {e}\n")
    log.close()
    print(f"{'DONE' if ok else 'FAILED'} stats={json.dumps(stats, ensure_ascii=False)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()

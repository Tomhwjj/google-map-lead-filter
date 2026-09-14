#!/usr/bin/env bash
# PL 获客管线一键续跑（2026-09-14 WorkBuddy）
# 两个任务均可断点续跑：backfill.py 自动跳过 out json 里已完成的公司（逐条落盘）；
# run_gmaps_acq.py 的 Maps 抓取走 gmaps.ckpt.json --resume（剩 9 个查询，需 VPN）。
# 同一命令重复执行即续跑；跑完的命令再执行会自动跳过全部并秒退。
cd "D:/Agent/git/google-map-lead-filter" || exit 1
PY_VENV="C:/Users/何伟/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
PY_SYS="C:/Python314/python.exe"

echo "=== [1/2] round3 流水线续跑（Maps 抓取剩9查询→背调→入库） ==="
"$PY_VENV" scripts/run_gmaps_acq.py --country PL --queries-file data/acq_work/kw_pl_round3.txt --locale pl-PL

echo "=== [2/2] 存量背调续跑（1169 家，断点自动跳过） ==="
"$PY_SYS" scripts/backfill.py data/acq_work/pl_noemail_backfill_rerun.csv \
  --out data/acq_work/backfill_lh4wfq_rerun.json \
  --brands Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark,Huawei,Sungrow,GoodWe,Fronius,SMA,Solax,Sofar,Growatt,Kostal,SolarEdge,Enphase,Hoymiles,FoxESS,Solis \
  --deye Deye,Sunsynk,Sol-Ark,INGE,Fusion,OHm,Noark

echo "=== 完成。收尾：存量背调需 score_leads + ingest_leads 入库（round3 流水线已自带） ==="

"""backfill_retry.py — 背调失败重试工具（2026-09-16 WorkBuddy）

背景：backfill.py 断点续跑按 website 在 out json 即跳过，error 非空的失败记录
（timeout/crash/证书/网络）也算"已完成"，永不重试。本脚本做两件事：

  gen   从背调 out json 挑出失败家 → 生成重试 csv（与 backfill.py 输入格式一致）
        筛选：error 非空，或 (emails 空 且 body < --min-body 字符，JS 渲染嫌疑)
  merge 把重试 out json 中拿到邮箱的记录合并回主 out json（按 main_id/website 匹配，
        只覆盖抓到邮箱的，失败痕迹保留）

用法:
  python scripts/backfill_retry.py gen  --src-json data/acq_work/backfill_lh4wfq_rerun.json --out-csv data/acq_work/pl_retry.csv
  python scripts/backfill_retry.py merge --src-json <主json> --retry-json <重试json>
"""
import argparse
import csv
import json
import sys
from collections import Counter

CSV_COLS = ["main_id", "company_name", "website", "google_maps_url", "city",
            "phone", "country", "address", "rating", "customer_type"]


def _key(r):
    return ((r.get("website") or "").strip().lower()
            or (r.get("company_name") or "").strip().lower())


def cmd_gen(args):
    with open(args.src_json, encoding="utf-8") as f:
        recs = json.load(f)
    with_site = [r for r in recs if (r.get("website") or "").strip()]
    picked, stat = [], Counter()
    for r in with_site:
        err = (r.get("error") or "").strip()
        has_mail = bool(r.get("emails"))
        if has_mail:
            stat["有邮箱(不动)"] += 1
        elif err:
            stat["技术失败(error)"] += 1
            picked.append(r)
        elif len((r.get("body") or "").strip()) < args.min_body:
            stat["无error但body过短(JS嫌疑)"] += 1
            picked.append(r)
        else:
            stat["打开正常但确实没邮箱"] += 1
    stat["无网站(无法重试)"] = len(recs) - len(with_site)

    with open(args.out_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for r in picked:
            w.writerow({k: r.get(k, "") for k in CSV_COLS})
    print(json.dumps({"src": args.src_json, "总数": len(recs),
                      "重试候选": len(picked),
                      **{k: v for k, v in stat.items()}},
                     ensure_ascii=False, indent=1))
    print(f"-> {args.out_csv}")


def cmd_merge(args):
    with open(args.src_json, encoding="utf-8") as f:
        recs = json.load(f)
    with open(args.retry_json, encoding="utf-8") as f:
        retry = json.load(f)
    idx = {_key(r): i for i, r in enumerate(recs)}
    merged, hit = 0, 0
    for rr in retry:
        i = idx.get(_key(rr))
        if i is None:
            merged += 0
            continue
        if rr.get("emails"):
            # 整行覆盖：重试 rec 继承了 csv 全字段，主记录可能有更多历史字段，取并集
            old = recs[i]
            new = dict(old)
            new.update({k: v for k, v in rr.items() if v not in ("", None, [], {})} or {})
            new["emails"] = rr["emails"]
            new["error"] = ""  # 抓到邮箱即视为成功
            recs[i] = new
            hit += 1
    with open(args.src_json + ".tmp", "w", encoding="utf-8") as f:
        json.dump(recs, f, ensure_ascii=False, indent=1)
    import os
    os.replace(args.src_json + ".tmp", args.src_json)
    print(json.dumps({"主json": args.src_json, "重试记录": len(retry),
                      "匹配到": sum(1 for rr in retry if _key(rr) in idx),
                      "新增邮箱": hit}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen", help="生成重试 csv")
    g.add_argument("--src-json", required=True)
    g.add_argument("--out-csv", required=True)
    g.add_argument("--min-body", type=int, default=200,
                   help="无 error 时 body 低于该字符数视为 JS 渲染嫌疑，一并重试")
    g.set_defaults(fn=cmd_gen)
    m = sub.add_parser("merge", help="重试结果合并回主 json")
    m.add_argument("--src-json", required=True, help="主 out json（原地更新）")
    m.add_argument("--retry-json", required=True, help="重试轮 out json")
    m.set_defaults(fn=cmd_merge)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

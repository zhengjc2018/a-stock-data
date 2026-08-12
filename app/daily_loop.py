# -*- coding: utf-8 -*-
"""每日自优化闭环：
1. 盘后记录当日候选
2. 次日开盘后验证真实高开命中
3. 周日自动重训 + 择优发布/回滚

用法：
  python daily_loop.py --record     # 盘后记录
  python daily_loop.py --verify     # 验证昨日候选
  python daily_loop.py --retrain    # 手动重训
  python daily_loop.py --auto       # 按当前时间自动执行
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone

import datahub
import paths

OUT_DIR = paths.bundle_path("outcomes")
GAP_SCOPE = {"main": True, "chi_next": False, "st": False}
TOP_N = 100
CN_TZ = timezone(timedelta(hours=8))


def _ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)


def _candidate_file(date):
    return os.path.join(OUT_DIR, f"candidates_{date}.json")


def _hits_file(date):
    return os.path.join(OUT_DIR, f"hits_{date}.json")


def record_candidates():
    import gap_model
    import gap_pick

    _ensure_out()
    deadline = time.time() + 900
    data = None
    while time.time() < deadline:
        data = gap_pick.get_cache(GAP_SCOPE)
        if data and not gap_pick.is_computing():
            break
        print("[daily] waiting gap scan...", flush=True)
        time.sleep(15)
    if not data:
        print("[daily] no gap data", flush=True)
        return
    date = data["date"]
    path = _candidate_file(date)
    if os.path.isfile(path):
        print(f"[daily] candidates {date} already recorded", flush=True)
        return
    payload = {
        "date": date,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": GAP_SCOPE,
        "model": gap_model.meta(),
        "total": data.get("total", 0),
        "candidates": data.get("candidates", [])[:TOP_N],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[daily] recorded {len(payload['candidates'])} candidates -> {path}", flush=True)


def _last_market_date():
    rows = datahub._klines("1.000001", 101, 3)
    if not rows:
        return ""
    return str(rows[-1]["date"])[:10]


def verify_pending():
    _ensure_out()
    market_date = _last_market_date()
    for name in sorted(os.listdir(OUT_DIR)):
        if not name.startswith("candidates_") or not name.endswith(".json"):
            continue
        date = name.replace("candidates_", "").replace(".json", "")
        if os.path.isfile(_hits_file(date)):
            continue
        if market_date <= date:
            print(f"[daily] {date} not yet next trading day (market={market_date})", flush=True)
            continue
        with open(os.path.join(OUT_DIR, name), encoding="utf-8") as f:
            cand = json.load(f)
        codes = [c["code"] for c in cand.get("candidates", [])]
        quotes = {}
        for i in range(0, len(codes), 80):
            quotes.update(datahub.tencent_quote(codes[i:i + 80]))
        results = []
        for c in cand.get("candidates", []):
            q = quotes.get(c["code"]) or {}
            open_ = q.get("open")
            prev = c.get("price")
            if not open_ or not prev:
                continue
            pct = (open_ / prev - 1) * 100
            results.append({
                "code": c["code"],
                "name": c.get("name", ""),
                "open": round(open_, 3),
                "pct": round(pct, 2),
                "hit": pct >= 1.0,
            })
        payload = {
            "date": date,
            "market_date": market_date,
            "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "results": results,
        }
        with open(_hits_file(date), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        hits = sum(1 for r in results if r["hit"])
        print(f"[daily] verified {date}: {hits}/{len(results)} hits", flush=True)


def run_auto():
    now = datetime.now(CN_TZ)
    hour = now.hour + now.minute / 60
    if 9.5 <= hour < 10:
        verify_pending()
    if 18.0 <= hour < 18.5:
        record_candidates()
        if now.weekday() == 6:
            import auto_train
            auto_train.main()


def main():
    ap = argparse.ArgumentParser(description="每日自优化闭环")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--retrain", action="store_true")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    if args.record:
        record_candidates()
    if args.verify:
        verify_pending()
    if args.retrain:
        import auto_train
        auto_train.main()
    if args.auto:
        run_auto()
    if not any([args.record, args.verify, args.retrain, args.auto]):
        ap.print_help()


if __name__ == "__main__":
    main()

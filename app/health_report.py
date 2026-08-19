# -*- coding: utf-8 -*-
"""一键健康报告：模型指标 + 选股命中 + 做T命中 + 可选完整回测。

用法：
  python health_report.py            # 快速读取当前状态
  python health_report.py --full     # 额外跑 400 只真实回测和做T滚动验证
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import paths


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _read_csv_summary(path, columns):
    if not os.path.isfile(path):
        return {}
    import pandas as pd
    df = pd.read_csv(path)
    out = {}
    for col in columns:
        if col in df.columns and df[col].notna().any():
            out[col] = round(float(df[col].mean()), 4)
    return out


def _model_meta(path):
    d = _read_json(path) or {}
    return {
        "features": len(d.get("features") or []),
        "metrics": d.get("metrics"),
        "trained_at": d.get("trained_at"),
        "date_range": d.get("date_range"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    report = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "gap_model": _model_meta(paths.data_path("gap_model.json")),
        "tail_model": _model_meta(paths.data_path("tail_reach_model.json")),
        "model_history": (_read_json(paths.bundle_path("model_history.json")) or [])[-10:],
        "outcomes": {},
        "t_ledger": {},
    }
    out_dir = paths.data_path("outcomes")
    days = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if not name.startswith("hits_") or not name.endswith(".json"):
                continue
            hits = _read_json(os.path.join(out_dir, name))
            results = (hits or {}).get("results") or []
            if not results:
                continue
            hit = sum(1 for r in results if r.get("hit"))
            days.append({"date": name.replace("hits_", "").replace(".json", ""),
                         "total": len(results), "hits": hit})
    report["outcomes"] = {
        "verified_days": len(days),
        "total_results": sum(d["total"] for d in days),
        "total_hits": sum(d["hits"] for d in days),
        "days": days[-10:],
    }
    ledger = _read_json(paths.data_path("t_signal_ledger.json")) or []
    verified = [s for s in ledger if s.get("status") == "verified"]
    wins = sum(1 for s in verified if s.get("outcome") == "win")
    report["t_ledger"] = {
        "signals": len(ledger),
        "verified": len(verified),
        "win_rate": round(wins / len(verified), 4) if verified else None,
        "avg_net_ret": round(sum(float(s.get("ret") or 0) for s in verified) / len(verified), 4)
        if verified else None,
    }
    import trend_report
    report["trend"] = {
        "selection_top3": trend_report.selection_trend(),
        "t_win": trend_report.t_trend(),
    }

    if args.full:
        print("[health] running realistic backtest...", flush=True)
        subprocess.run([sys.executable, "realistic_backtest.py", "--limit", "400",
                        "--out", "/tmp/health_realistic.csv"], check=False)
        realistic = _read_csv_summary(
            "/tmp/health_realistic.csv",
            ["hit_rate_net", "trade_win_rate", "trade_avg_net_ret", "trade_top3_day_win"])
        report["realistic_backtest"] = realistic
        print("[health] running T rolling...", flush=True)
        subprocess.run([sys.executable, "backtest_t_rolling.py",
                        "--out", "/tmp/health_t_rolling.csv"], check=False)
        t_rolling = _read_csv_summary(
            "/tmp/health_t_rolling.csv",
            ["combined_win_rate", "buy_win_rate", "sell_win_rate"])
        report["t_rolling"] = t_rolling

    report_path = paths.bundle_path("backtest_report", "health_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("saved", report_path)


if __name__ == "__main__":
    main()

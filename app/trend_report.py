# -*- coding: utf-8 -*-
"""趋势报告：按周汇总选股 Top3 命中率和做T胜率，判断是否持续提升。

用法：
  python trend_report.py
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone

import paths

CN_TZ = timezone(timedelta(hours=8))


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _week_key(ts):
    dt = datetime.fromtimestamp(ts, CN_TZ)
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def selection_trend():
    out_dir = paths.data_path("outcomes")
    weeks = {}
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if not name.startswith("hits_") or not name.endswith(".json"):
                continue
            hits = _read_json(os.path.join(out_dir, name))
            results = (hits or {}).get("results") or []
            if len(results) < 3:
                continue
            top3_hit = any(r.get("hit") for r in results[:3])
            date = name.replace("hits_", "").replace(".json", "")
            try:
                dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=CN_TZ)
            except ValueError:
                continue
            iso = dt.isocalendar()
            key = f"{iso[0]}-W{iso[1]:02d}"
            week = weeks.setdefault(key, {"days": 0, "top3_hits": 0})
            week["days"] += 1
            week["top3_hits"] += int(top3_hit)
    rows = []
    for key in sorted(weeks):
        w = weeks[key]
        rows.append({
            "week": key,
            "days": w["days"],
            "top3_rate": round(w["top3_hits"] / w["days"], 4) if w["days"] else None,
        })
    return rows


def t_trend():
    ledger = _read_json(paths.data_path("t_signal_ledger.json")) or []
    verified = [s for s in ledger if s.get("status") == "verified"]
    weeks = {}
    for s in verified:
        key = _week_key(int(s.get("ts") or 0))
        week = weeks.setdefault(key, {"signals": 0, "wins": 0})
        week["signals"] += 1
        week["wins"] += int(s.get("outcome") == "win")
    rows = []
    for key in sorted(weeks):
        w = weeks[key]
        rows.append({
            "week": key,
            "signals": w["signals"],
            "win_rate": round(w["wins"] / w["signals"], 4) if w["signals"] else None,
        })
    return rows


def t_confidence_analysis():
    ledger = _read_json(paths.data_path("t_signal_ledger.json")) or []
    verified = [s for s in ledger if s.get("status") == "verified"]
    buckets = {"high": [], "mid": [], "low": []}
    for s in verified:
        conf = float(s.get("confidence") or 0)
        key = "high" if conf >= 0.8 else ("mid" if conf >= 0.6 else "low")
        buckets[key].append(s)
    rows = []
    for key, items in buckets.items():
        if not items:
            continue
        wins = sum(1 for s in items if s.get("outcome") == "win")
        avg = sum(float(s.get("ret") or 0) for s in items) / len(items)
        rows.append({
            "bucket": key,
            "signals": len(items),
            "win_rate": round(wins / len(items), 4),
            "avg_net_ret": round(avg, 4),
        })
    return rows


def run():
    selection = selection_trend()
    t = t_trend()
    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "selection_top3_trend": selection,
        "t_win_trend": t,
        "t_confidence": t_confidence_analysis(),
    }
    if len(selection) >= 2:
        cur = selection[-1]["top3_rate"] or 0
        prev = selection[-2]["top3_rate"] or 0
        summary["selection_delta_pp"] = round((cur - prev) * 100, 2)
    if len(t) >= 2:
        cur = t[-1]["win_rate"] or 0
        prev = t[-2]["win_rate"] or 0
        summary["t_delta_pp"] = round((cur - prev) * 100, 2)
    out_path = paths.bundle_path("backtest_report", "trend_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("saved", out_path)
    return summary


if __name__ == "__main__":
    run()

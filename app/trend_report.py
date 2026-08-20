# -*- coding: utf-8 -*-
"""趋势报告：按周汇总选股 Top3 命中率和做T胜率，判断是否持续提升。

用法：
  python trend_report.py
"""
from __future__ import annotations

import json
import csv
import os
import time
from datetime import datetime, timedelta, timezone

import paths
import do_t

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


def confidence_filter_suggestion(rows):
    high = next((r for r in rows if r.get("bucket") == "high"), None)
    if not high or high.get("signals", 0) < 30:
        return _backtest_confidence_suggestion()
    ledger = _read_json(paths.data_path("t_signal_ledger.json")) or []
    verified = [s for s in ledger if s.get("status") == "verified"]
    if not verified:
        return {"suggest": False, "reason": "无已确认信号"}
    overall = sum(1 for s in verified if s.get("outcome") == "win") / len(verified)
    if high["win_rate"] >= overall + 0.05:
        return {
            "suggest": True,
            "min_confidence": 0.8,
            "reason": f"高置信胜率 {high['win_rate']:.1%} 显著高于整体 {overall:.1%}",
        }
    return {"suggest": False, "reason": "高置信信号优势未达到5个百分点"}


def _backtest_confidence_suggestion():
    path = paths.bundle_path("backtest_report", "t_confidence_backtest.csv")
    if not os.path.isfile(path):
        return {"suggest": False, "reason": "高置信信号样本不足"}
    buckets = {}
    overall = {"signals": 0, "wins": 0}
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                bucket = row.get("bucket", "")
                win = int(row.get("win") or 0)
                buckets.setdefault(bucket, {"signals": 0, "wins": 0})
                buckets[bucket]["signals"] += 1
                buckets[bucket]["wins"] += win
                overall["signals"] += 1
                overall["wins"] += win
    except Exception:
        return {"suggest": False, "reason": "置信度回测数据读取失败"}
    if overall["signals"] < 50:
        return {"suggest": False, "reason": "置信度回测样本不足"}
    high = buckets.get("high")
    if not high or high["signals"] < 30:
        return {"suggest": False, "reason": "高置信回测样本不足"}
    high_rate = high["wins"] / high["signals"]
    overall_rate = overall["wins"] / overall["signals"]
    if high_rate >= overall_rate + 0.05:
        return {
            "suggest": True,
            "min_confidence": 0.8,
            "reason": f"回测高置信胜率 {high_rate:.1%} 高于整体 {overall_rate:.1%}",
        }
    return {"suggest": False, "reason": "回测高置信优势未达到5个百分点"}


def apply_confidence_suggestion(suggestion):
    if not suggestion.get("suggest"):
        return False
    cfg = do_t._load_t_config()
    cfg["confidence_filter"] = float(suggestion.get("min_confidence", 0.8))
    do_t.save_t_config(cfg)
    return True


def run():
    selection = selection_trend()
    t = t_trend()
    conf_rows = t_confidence_analysis()
    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "selection_top3_trend": selection,
        "t_win_trend": t,
        "t_confidence": conf_rows,
        "t_confidence_filter": confidence_filter_suggestion(conf_rows),
        "t_config": _read_json(paths.data_path("t_config.json")) or {},
        "alerts": [],
    }
    if len(selection) >= 2:
        cur = selection[-1]["top3_rate"] or 0
        prev = selection[-2]["top3_rate"] or 0
        summary["selection_delta_pp"] = round((cur - prev) * 100, 2)
        if summary["selection_delta_pp"] < -10:
            summary["alerts"].append("选股Top3命中率较上周下降超过10个百分点")
    if len(t) >= 2:
        cur = t[-1]["win_rate"] or 0
        prev = t[-2]["win_rate"] or 0
        summary["t_delta_pp"] = round((cur - prev) * 100, 2)
        if summary["t_delta_pp"] < -5:
            summary["alerts"].append("做T胜率较上周下降超过5个百分点")
    summary["t_confidence_filter_applied"] = apply_confidence_suggestion(
        summary["t_confidence_filter"])
    for alert in summary.get("alerts", []):
        try:
            do_t.notify("策略趋势预警", alert)
        except Exception:
            pass
    out_path = paths.bundle_path("backtest_report", "trend_report.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("saved", out_path)
    return summary


if __name__ == "__main__":
    run()

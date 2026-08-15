# -*- coding: utf-8 -*-
"""统计回测：T日开盘 >= +5% 的股票，在 T-1 的特征画像。

对比命中组/未命中组的 T-1 量价、位置、资金、板块/大盘关联。
"""
from __future__ import annotations

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

import datahub
import gap_pick
import train_gap_v2

FEATURES = [
    "pct_chg", "vol_ratio_5", "vol_ratio_10", "amplitude_pct",
    "pos_ma20", "pos_ma60", "dist_high60", "dist_low60", "amount_yi",
    "ret_5", "ret_10", "prev_limit_up", "limit_streak_prev", "up_days_5",
]


def fetch_index_daily():
    rows = datahub._klines("1.000001", 101, 800)
    out = {}
    prev = None
    for r in rows:
        date = str(r["date"])[:10]
        close = float(r["close"])
        if prev:
            out[date] = close / prev - 1
        prev = close
    return out


def collect(threshold):
    index_ret = fetch_index_daily()
    codes = train_gap_v2.sample_codes(500)
    print(f"[open5] codes {len(codes)}", flush=True)

    with ThreadPoolExecutor(max_workers=4) as ex:
        industries = list(ex.map(
            lambda c: (c, gap_pick._stock_industry(c) or "未知行业"), codes))
    industry_map = dict(industries)
    print("[open5] industries fetched", flush=True)

    rows = []
    t0 = time.time()
    for idx, code in enumerate(codes, 1):
        df = train_gap_v2.fetch_daily(code)
        if df is None or len(df) < 40:
            continue
        industry = industry_map.get(code, "未知行业")
        for j in range(1, len(df) - 1):
            r = df.iloc[j]
            nxt = df.iloc[j + 1]
            t_date = str(nxt["date"])[:10]
            prev_date = str(r["date"])[:10]
            if prev_date not in index_ret:
                continue
            feats = {}
            bad = False
            for name in FEATURES:
                try:
                    v = float(r.get(name))
                except (TypeError, ValueError):
                    bad = True
                    break
                if np.isnan(v):
                    bad = True
                    break
                feats[name] = v
            if bad:
                continue
            label = 1 if float(nxt["open"]) >= float(r["close"]) * (1 + threshold) else 0
            rows.append({
                "date": t_date,
                "prev_date": prev_date,
                "code": code,
                "industry": industry,
                "label": label,
                "index_ret_prev": index_ret.get(prev_date, 0.0),
                **feats,
            })
        if idx % 100 == 0:
            print(f"[open5] {idx}/{len(codes)} rows {len(rows)} "
                  f"elapsed {time.time() - t0:.0f}s", flush=True)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.05,
                    help="开盘涨幅阈值，如 0.03 表示 +3%")
    args = ap.parse_args()
    threshold = args.threshold
    df = collect(threshold)
    if df.empty:
        print("no data")
        return
    df["industry_mean_prev"] = df.groupby(["date", "industry"])["pct_chg"].transform("mean")
    hit = df[df["label"] == 1]
    miss = df[df["label"] == 0]
    base = len(hit) / len(df)
    print(f"[open5] threshold {threshold * 100:.0f}% rows {len(df)} "
          f"命中 {len(hit)} 基准 {base:.4f}", flush=True)
    print(f"{'feature':<18} {'命中均值':>10} {'未命中均值':>12} {'差值':>10} {'倍数':>6}")
    report = []
    for f in FEATURES + ["index_ret_prev", "industry_mean_prev"]:
        hm = hit[f].mean()
        mm = miss[f].mean()
        d = hm - mm
        mult = hm / mm if mm != 0 else float("nan")
        print(f"{f:<18} {hm:>10.4f} {mm:>12.4f} {d:>10.4f} {mult:>6.2f}")
        report.append({"feature": f, "hit_mean": round(hm, 5),
                       "miss_mean": round(mm, 5), "delta": round(d, 5),
                       "ratio": round(mult, 3)})
    os.makedirs("backtest_report", exist_ok=True)
    out = f"backtest_report/open{int(threshold * 100)}_profile.csv"
    pd.DataFrame(report).to_csv(out, index=False, encoding="utf-8-sig")
    print(f"saved {out}", flush=True)
    top = hit[FEATURES].quantile([0.5, 0.75, 0.9]).T
    print("\n命中组特征分位数：")
    print(top.round(3).to_string())


if __name__ == "__main__":
    main()

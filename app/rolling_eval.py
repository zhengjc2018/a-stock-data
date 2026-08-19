# -*- coding: utf-8 -*-
"""滚动样本外验证：按交易日分批训练，验证模型在时间上的稳定性。

每次用截至 cutoff 的样本训练，预测接下来 horizon 个交易日的候选，
统计 Top3 篮子净命中率和单笔交易胜率，最后汇总均值/中位数/波动。
"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

import realistic_backtest
import train_gap_v2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--step", type=int, default=30)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--min-train-days", type=int, default=120)
    ap.add_argument("--label-gap", type=float, default=0.03)
    ap.add_argument("--cost-rate", type=float, default=0.003)
    ap.add_argument("--stop-rate", type=float, default=0.015)
    ap.add_argument("--out", default="backtest_report/rolling_eval.csv")
    args = ap.parse_args()

    data_args = argparse.Namespace(
        codes=None,
        limit=args.limit,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out="/tmp/rolling_tmp.json",
        no_zt_heat=True,
        outcomes_dir=None,
        label_gap=args.label_gap,
        trees=150,
        depth=3,
        reach=True,
    )
    df = train_gap_v2.prepare_data(data_args)
    if df is None:
        return
    dates = sorted(df["date"].unique())
    rows = []
    for i in range(args.min_train_days, len(dates), args.step):
        cutoff = dates[i - 1]
        end = dates[min(i - 1 + args.horizon, len(dates) - 1)]
        train = df[df["date"] <= cutoff].copy()
        test = df[(df["date"] > cutoff) & (df["date"] <= end)].copy()
        if len(train) < 1000 or len(test) < 30:
            continue
        model = GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=42, init="zero")
        model.fit(train[train_gap_v2.MODEL_FEATURES].values, train["label"].values)
        test = test.copy()
        test["prob"] = model.predict_proba(
            test[train_gap_v2.MODEL_FEATURES].values)[:, 1]
        top3_net = train_gap_v2.topk_rates(test, "prob", (3,), "label_net")[3]
        sim = realistic_backtest.simulate_trades(
            test, args.label_gap, args.cost_rate, args.stop_rate)
        rows.append({
            "cutoff": cutoff,
            "end": end,
            "train_days": train["date"].nunique(),
            "test_days": test["date"].nunique(),
            "test_samples": len(test),
            "top3_net": top3_net,
            "win_rate": sim.get("win_rate"),
            "avg_net_ret": sim.get("avg_net_ret"),
            "top3_day_win": sim.get("top3_day_win"),
        })
        print(f"[rolling] {cutoff} -> {end} top3_net={top3_net} "
              f"win={sim.get('win_rate')} avg={sim.get('avg_net_ret')}", flush=True)

    if not rows:
        print("no rolling windows")
        return
    out_df = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out_df.to_csv(args.out, index=False, encoding="utf-8-sig")
    summary = {
        "windows": len(out_df),
        "mean_top3_net": round(float(out_df["top3_net"].mean()), 4),
        "median_top3_net": round(float(out_df["top3_net"].median()), 4),
        "std_top3_net": round(float(out_df["top3_net"].std()), 4),
        "min_top3_net": round(float(out_df["top3_net"].min()), 4),
        "mean_win_rate": round(float(out_df["win_rate"].mean()), 4) if out_df["win_rate"].notna().any() else None,
        "mean_avg_net_ret": round(float(out_df["avg_net_ret"].mean()), 4) if out_df["avg_net_ret"].notna().any() else None,
    }
    print("[rolling] summary", summary)
    print("saved", args.out)


if __name__ == "__main__":
    main()

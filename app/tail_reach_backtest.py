# -*- coding: utf-8 -*-
"""尾盘买入 TopN 篮子回测：
目标 = T+1 开盘或盘中最高 >= T收盘 * 1.03；
统计 Top1/3/5 单日“至少一只命中”的胜率。
"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

import train_gap_v2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1200)
    ap.add_argument("--label-gap", type=float, default=0.03)
    args = ap.parse_args()

    data_args = argparse.Namespace(
        codes=None,
        limit=args.limit,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out="/tmp/tail_reach_tmp.json",
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
    train, val, test = train_gap_v2.split_by_date(df)
    print(f"[reach] train {len(train)} val {len(val)} test {len(test)}", flush=True)
    model = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05,
        subsample=0.8, random_state=42, init="zero")
    model.fit(train[train_gap_v2.MODEL_FEATURES].values, train["label"].values)
    test = test.copy()
    test["prob"] = model.predict_proba(test[train_gap_v2.MODEL_FEATURES].values)[:, 1]
    test["amount_proxy_yi"] = test["volume"] * test["close"] / 1e8
    test = test[test["amount_proxy_yi"] >= 0.5]
    print(f"[reach] test after amount filter {len(test)}", flush=True)

    def evaluate(col):
        top1 = top3 = top5 = 0
        days = 0
        trades = 0
        for _, g in test.groupby("date"):
            g = g.sort_values(col, ascending=False)
            if g.empty:
                continue
            days += 1
            trades += min(5, len(g))
            top1 += int(bool(g.iloc[:1]["label"].any()))
            top3 += int(bool(g.iloc[:3]["label"].any()))
            top5 += int(bool(g.iloc[:5]["label"].any()))
        return {
            "days": days, "trades": trades,
            "top1": round(top1 / days, 4) if days else None,
            "top3": round(top3 / days, 4) if days else None,
            "top5": round(top5 / days, 4) if days else None,
        }

    model_res = evaluate("prob")
    baseline_res = evaluate("pct_chg")
    print("[reach] model", model_res)
    print("[reach] baseline top by pct_chg", baseline_res)
    rows = pd.DataFrame([
        {"strategy": "模型TopN", **model_res},
        {"strategy": "基准-涨幅TopN", **baseline_res},
    ])
    os.makedirs("backtest_report", exist_ok=True)
    out = "backtest_report/tail_reach_backtest.csv"
    rows.to_csv(out, index=False, encoding="utf-8-sig")
    print("saved", out)


if __name__ == "__main__":
    main()

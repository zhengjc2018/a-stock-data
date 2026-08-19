# -*- coding: utf-8 -*-
"""真实口径回测：交易成本 + 止盈/止损，评估尾盘买入次日达 +3% 的策略。

与现有 topk 命中率不同，这里把每只推荐个股当作一笔真实交易：
1. 次日开盘或盘中最高 >= 成本修正后的目标价 => 止盈，净收益 = label_gap
2. 次日盘中最低 <= 成本修正后的止损价   => 止损，净收益 = -stop_rate
3. 否则按次日收盘退出，净收益 = 次日收益 - cost_rate
"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier

import train_gap_v2


def simulate_trades(test, label_gap=0.03, cost_rate=0.003, stop_rate=0.015):
    trades = []
    per_day = []
    for date, g in test.groupby("date", sort=True):
        g = g.sort_values("prob", ascending=False).head(3)
        day_trades = []
        for row in g.itertuples():
            entry = float(row.close)
            if entry <= 0:
                continue
            target = entry * (1 + label_gap + cost_rate)
            stop = entry * (1 - stop_rate - cost_rate)
            nxt_open = float(row.next_open)
            nxt_high = float(row.next_high)
            nxt_low = float(row.next_low)
            nxt_close = float(row.next_close)
            if nxt_open >= target:
                net_ret = label_gap
            elif nxt_high >= target:
                net_ret = label_gap
            elif nxt_low <= stop:
                net_ret = -stop_rate
            else:
                net_ret = nxt_close / entry - 1 - cost_rate
            day_trades.append(net_ret)
            trades.append(net_ret)
        if day_trades:
            per_day.append(max(day_trades))
    if not trades:
        return {"trades": 0, "days": 0, "win_rate": None,
                "avg_net_ret": None, "top3_day_win": None}
    wins = sum(1 for x in trades if x > 0)
    day_wins = sum(1 for x in per_day if x > 0)
    return {
        "trades": len(trades),
        "days": len(per_day),
        "win_rate": round(wins / len(trades), 4),
        "avg_net_ret": round(sum(trades) / len(trades), 4),
        "top3_day_win": round(day_wins / len(per_day), 4),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--label-gap", type=float, default=0.03)
    ap.add_argument("--cost-rate", type=float, default=0.003,
                    help="单笔往返摩擦成本，默认 0.3%%")
    ap.add_argument("--stop-rate", type=float, default=0.015)
    ap.add_argument("--out", default="backtest_report/realistic_backtest.csv")
    args = ap.parse_args()

    data_args = argparse.Namespace(
        codes=None,
        limit=args.limit,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out="/tmp/realistic_tmp.json",
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
    print(f"[realistic] train {len(train)} val {len(val)} test {len(test)}", flush=True)
    model = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05,
        subsample=0.8, random_state=42, init="zero")
    model.fit(train[train_gap_v2.MODEL_FEATURES].values, train["label"].values)
    test = test.copy()
    test["prob"] = model.predict_proba(test[train_gap_v2.MODEL_FEATURES].values)[:, 1]

    top_raw = train_gap_v2.topk_rates(test, "prob", (1, 3, 5))
    top_net = train_gap_v2.topk_rates(test, "prob", (1, 3, 5), "label_net")
    model_trades = simulate_trades(test, args.label_gap, args.cost_rate, args.stop_rate)
    baseline = test.copy()
    baseline["prob"] = baseline["pct_chg"]
    base_trades = simulate_trades(baseline, args.label_gap, args.cost_rate, args.stop_rate)

    metrics = {
        "label_gap": args.label_gap,
        "cost_rate": args.cost_rate,
        "stop_rate": args.stop_rate,
        "test_days": len(test["date"].unique()),
        "top1_raw": top_raw[1],
        "top3_raw": top_raw[3],
        "top5_raw": top_raw[5],
        "top1_net": top_net[1],
        "top3_net": top_net[3],
        "top5_net": top_net[5],
        "model": model_trades,
        "baseline": base_trades,
    }
    print("[realistic] metrics", metrics, flush=True)
    rows = pd.DataFrame([
        {"strategy": "模型Top1", "hit_rate_raw": top_raw[1], "hit_rate_net": top_net[1],
         **{f"trade_{k}": v for k, v in model_trades.items()}},
        {"strategy": "模型Top3", "hit_rate_raw": top_raw[3], "hit_rate_net": top_net[3],
         **{f"trade_{k}": v for k, v in model_trades.items()}},
        {"strategy": "模型Top5", "hit_rate_raw": top_raw[5], "hit_rate_net": top_net[5],
         **{f"trade_{k}": v for k, v in model_trades.items()}},
        {"strategy": "基准Top3", "hit_rate_raw": None, "hit_rate_net": None,
         **{f"trade_{k}": v for k, v in base_trades.items()}},
    ])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    rows.to_csv(args.out, index=False, encoding="utf-8-sig")
    print("saved", args.out)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""做T滚动样本外验证：按交易日滚动选参，验证买/卖净胜率稳定性。

对每只样本股：
1. 用截至 cutoff 的 5 分钟 K 线做参数网格搜索
2. 在 cutoff 后一段样本外日期回测
3. 统计买入/卖出净胜率、综合胜率和平均净收益
"""
from __future__ import annotations

import argparse
import os
import time

import pandas as pd

import t_strategy


def _best_params(train_df, baseline_params):
    grid = t_strategy.build_grid()
    best = None
    for p in grid:
        buy, sell = t_strategy.signal_indices(train_df, p)
        res = t_strategy.summarize(t_strategy.target_stop_winrate(train_df, buy, sell, p))
        n = res["combined"]["signals"]
        wr = res["combined"]["win_rate"] or 0
        if n < 10:
            continue
        key = (wr, n)
        if best is None or key > best[0]:
            best = (key, p, res)
    if best:
        return best[1], best[2]
    return baseline_params, {"combined": {"signals": 0, "win_rate": None}}


def run_stock(code, symbol, secid):
    intraday = t_strategy.fetch_kline(symbol, 5, 1023)
    if intraday.empty or len(intraday) < 120:
        return []
    daily = t_strategy.fetch_kline(symbol, 240, 120)
    profile = t_strategy.profile_stock(daily, intraday) if not daily.empty else None
    profile_params = t_strategy.params_for_profile(profile)
    fund = t_strategy.fetch_fund_flow(secid)
    df = t_strategy.add_signals_features(intraday, fund)
    df["main_net_prev"] = df["main_net_prev"].fillna(0)
    idx_df = t_strategy.fetch_kline("sh000001", 5, 1023)
    if idx_df.empty:
        df["idx_close"] = 0.0
        df["idx_trend"] = False
    else:
        idx_map = dict(zip(idx_df["dt"], idx_df["close"]))
        df["idx_close"] = df["dt"].map(idx_map)
        df["idx_trend"] = (df["idx_close"] > df["idx_close"].shift(3)).fillna(False)
    df = df.dropna(subset=["prev_close", "first_vol_ratio", "idx_close"]).reset_index(drop=True)
    days = sorted(df["day"].unique())
    if len(days) < 8:
        return []
    rows = []
    for start in range(max(1, int(len(days) * 0.2)), len(days) - 1, 5):
        cutoff = days[start - 1]
        end = days[min(start - 1 + 10, len(days) - 1)]
        train = df[df["day"] <= cutoff].copy()
        test = df[(df["day"] > cutoff) & (df["day"] <= end)].copy()
        if len(train) < 300 or test.empty:
            continue
        params, _ = _best_params(train, profile_params)
        buy, sell = t_strategy.signal_indices(test, params)
        res = t_strategy.summarize(t_strategy.target_stop_winrate(test, buy, sell, params))
        buy_f, sell_f = [], []
        for i in buy:
            buy_score, _ = t_strategy._score_row(test.iloc[i], params)
            if buy_score / 5.0 >= 0.8:
                buy_f.append(i)
        for i in sell:
            _, sell_score = t_strategy._score_row(test.iloc[i], params)
            if sell_score / 5.0 >= 0.8:
                sell_f.append(i)
        filtered_res = t_strategy.summarize(
            t_strategy.target_stop_winrate(test, buy_f, sell_f, params))
        base_buy, base_sell = t_strategy.signal_indices(test, profile_params)
        base_res = t_strategy.summarize(
            t_strategy.target_stop_winrate(test, base_buy, base_sell, profile_params))
        rows.append({
            "code": code,
            "cutoff": cutoff,
            "end": end,
            "trend": (profile or {}).get("trend", ""),
            "volatility": (profile or {}).get("volatility", ""),
            "buy_signals": res["buy"]["signals"],
            "buy_win_rate": res["buy"]["win_rate"],
            "sell_signals": res["sell"]["signals"],
            "sell_win_rate": res["sell"]["win_rate"],
            "combined_signals": res["combined"]["signals"],
            "combined_win_rate": res["combined"]["win_rate"],
            "filtered_signals": filtered_res["combined"]["signals"],
            "filtered_win_rate": filtered_res["combined"]["win_rate"],
            "profile_buy_signals": base_res["buy"]["signals"],
            "profile_buy_win_rate": base_res["buy"]["win_rate"],
            "profile_sell_signals": base_res["sell"]["signals"],
            "profile_sell_win_rate": base_res["sell"]["win_rate"],
            "profile_combined_win_rate": base_res["combined"]["win_rate"],
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--codes", type=str, default=None)
    ap.add_argument("--out", default="backtest_report/t_rolling.csv")
    args = ap.parse_args()
    if args.codes:
        wanted = set(args.codes.split(","))
        stocks = [s for s in t_strategy.STOCKS if s[0] in wanted]
    else:
        stocks = t_strategy.STOCKS
    rows = []
    for code, symbol, secid in stocks:
        print(f"[t-rolling] {code} start", flush=True)
        rows.extend(run_stock(code, symbol, secid))
    if not rows:
        print("no rolling windows")
        return
    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    out.to_csv(args.out, index=False, encoding="utf-8-sig")
    print(out.to_string(index=False))
    summary = {
        "windows": len(out),
        "mean_combined_win": round(float(out["combined_win_rate"].mean()), 4),
        "median_combined_win": round(float(out["combined_win_rate"].median()), 4),
        "min_combined_win": round(float(out["combined_win_rate"].min()), 4),
        "mean_buy_win": round(float(out["buy_win_rate"].mean()), 4),
        "mean_sell_win": round(float(out["sell_win_rate"].mean()), 4),
        "mean_optimized_win": round(float(out["combined_win_rate"].mean()), 4),
        "mean_profile_win": round(float(out["profile_combined_win_rate"].mean()), 4),
        "mean_filtered_win": round(float(out["filtered_win_rate"].fillna(0).mean()), 4),
        "filtered_better": int((out["filtered_win_rate"].fillna(0) >
                                out["combined_win_rate"].fillna(0)).sum()),
        "profile_wins": int((out["combined_win_rate"].fillna(0) >
                            out["profile_combined_win_rate"].fillna(0)).sum()),
        "profile_ties": int((out["combined_win_rate"].fillna(0) ==
                             out["profile_combined_win_rate"].fillna(0)).sum()),
    }
    print("[t-rolling] summary", summary)
    print("saved", args.out)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""做T信号置信度分层回测：验证高置信信号是否真的胜率更高。"""
from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

import do_t
import t_strategy

T_COST_RATE = 0.0012


def _signal_win(df, i, side, p):
    entry = float(df.iloc[i]["close"])
    day = str(df.iloc[i]["day"])
    target = float(p.get("target", 0.005))
    stop = float(p.get("stop", 0.005))
    horizon = int(p.get("horizon", 12))
    for j in range(i + 1, min(i + 1 + horizon, len(df))):
        r = df.iloc[j]
        if str(r["day"]) != day:
            break
        if side == "buy":
            if float(r["high"]) >= entry * (1 + target + T_COST_RATE):
                return True
            if float(r["low"]) <= entry * (1 - stop - T_COST_RATE):
                return False
        else:
            if float(r["low"]) <= entry * (1 - target - T_COST_RATE):
                return True
            if float(r["high"]) >= entry * (1 + stop + T_COST_RATE):
                return False
    if i + 1 < len(df) and str(df.iloc[i + 1]["day"]) == day:
        ret = float(df.iloc[i + 1]["close"]) / entry - 1 - T_COST_RATE
        if side == "sell":
            ret = -ret
        return ret > 0
    return None


def main():
    rows = []
    for code, symbol, secid in t_strategy.STOCKS:
        bars = t_strategy.fetch_kline(symbol, 5, 1023)
        if bars.empty or len(bars) < 120:
            continue
        fund = t_strategy.fetch_fund_flow(secid)
        df = t_strategy.add_signals_features(bars, fund)
        df["main_net_prev"] = df["main_net_prev"].fillna(0)
        idx = t_strategy.fetch_kline("sh000001", 5, 1023)
        if idx.empty:
            df["idx_close"] = 0.0
            df["idx_trend"] = False
        else:
            idx_map = dict(zip(idx["dt"], idx["close"]))
            df["idx_close"] = df["dt"].map(idx_map)
            df["idx_trend"] = (df["idx_close"] > df["idx_close"].shift(3)).fillna(False)
        df = df.dropna(subset=["prev_close", "first_vol_ratio", "idx_close"]).reset_index(drop=True)
        params, _ = do_t._resolve_params(code)
        buy_idx, sell_idx = t_strategy.signal_indices(df, params)
        for i in buy_idx:
            buy_score, _ = t_strategy._score_row(df.iloc[i], params)
            win = _signal_win(df, i, "buy", params)
            if win is not None:
                rows.append({"code": code, "side": "buy",
                             "score": buy_score,
                             "confidence": round(buy_score / 5.0, 2),
                             "win": int(win)})
        for i in sell_idx:
            _, sell_score = t_strategy._score_row(df.iloc[i], params)
            win = _signal_win(df, i, "sell", params)
            if win is not None:
                rows.append({"code": code, "side": "sell",
                             "score": sell_score,
                             "confidence": round(sell_score / 5.0, 2),
                             "win": int(win)})
    out = pd.DataFrame(rows)
    if out.empty:
        print("no signals")
        return
    out["bucket"] = np.where(out["confidence"] >= 0.8, "high",
                             np.where(out["confidence"] >= 0.6, "mid", "low"))
    grouped = out.groupby("bucket").agg(
        signals=("win", "size"),
        wins=("win", "sum"),
    ).reset_index()
    grouped["win_rate"] = grouped["wins"] / grouped["signals"]
    print(grouped.to_string(index=False))
    overall = out["win"].mean()
    print(f"overall_win_rate={overall:.4f} signals={len(out)}")
    os.makedirs("backtest_report", exist_ok=True)
    out.to_csv("backtest_report/t_confidence_backtest.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()

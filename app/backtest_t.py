# -*- coding: utf-8 -*-
"""做T信号回测：指标评分 vs 纯阈值（新浪5分钟K线）。

评价口径：信号后 3 根 5 分钟 K 线（约15分钟）的方向胜率。
买入信号胜 = 3根后涨幅 >= +0.3%；卖出信号胜 = 3根后跌幅 >= -0.3%。
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
import requests

CODES = ["sh600519", "sz000001", "sz002594", "sh601318",
         "sz000858", "sh600036", "sz002415", "sh603501"]


def fetch_5m(symbol, datalen=1023):
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
    r = requests.get(url, params={"symbol": symbol, "scale": "5", "ma": "no",
                                  "datalen": str(datalen)},
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://finance.sina.com.cn/"},
                     timeout=15)
    text = r.text
    payload = text[text.index("([") + 1:text.rindex("])") + 1]
    rows = []
    import json
    for it in json.loads(payload):
        rows.append({
            "day": str(it["day"])[:10],
            "dt": it["day"],
            "open": float(it["open"]),
            "high": float(it["high"]),
            "low": float(it["low"]),
            "close": float(it["close"]),
            "volume": float(it.get("volume") or 0),
            "amount": float(it.get("amount") or 0),
        })
    return pd.DataFrame(rows)


def add_indicators(df):
    df = df.copy()
    df = df.sort_values("dt").reset_index(drop=True)
    df["day_open"] = df.groupby("day")["open"].transform("first")
    tp = (df["high"] + df["low"] + df["close"]) / 3
    df["pv"] = tp * df["volume"]
    df["vwap"] = df.groupby("day")["pv"].cumsum() / df.groupby("day")["volume"].cumsum()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    df["ma20"] = df["close"].rolling(20).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["upper"] = df["ma20"] + 2 * df["std20"]
    df["lower"] = df["ma20"] - 2 * df["std20"]
    df["vol_ma20"] = df["volume"].rolling(20).mean().shift(1)
    df["day_ret"] = df["close"] / df["day_open"] - 1
    return df


def evaluate_signals(df, buy_idx, sell_idx):
    out = {"buy": [], "sell": []}
    for i in buy_idx:
        if i + 3 >= len(df):
            continue
        if df.iloc[i]["day"] != df.iloc[i + 3]["day"]:
            continue
        ret = df.iloc[i + 3]["close"] / df.iloc[i]["close"] - 1
        out["buy"].append({"ret": ret, "win": ret >= 0.003})
    for i in sell_idx:
        if i + 3 >= len(df):
            continue
        if df.iloc[i]["day"] != df.iloc[i + 3]["day"]:
            continue
        ret = df.iloc[i + 3]["close"] / df.iloc[i]["close"] - 1
        out["sell"].append({"ret": ret, "win": ret <= -0.003})
    return out


def evaluate_target_stop(df, buy_idx, sell_idx, horizon=12, target=0.005, stop=0.005):
    out = {"buy": [], "sell": []}
    for i in buy_idx:
        entry = df.iloc[i]["close"]
        day = df.iloc[i]["day"]
        win = None
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if df.iloc[j]["day"] != day:
                break
            if df.iloc[j]["high"] >= entry * (1 + target):
                win = True
                break
            if df.iloc[j]["low"] <= entry * (1 - stop):
                win = False
                break
        if win is None and i + 1 < len(df) and df.iloc[i + 1]["day"] == day:
            win = df.iloc[i + 1]["close"] > entry
        if win is not None:
            out["buy"].append({"win": win, "ret": 0})
    for i in sell_idx:
        entry = df.iloc[i]["close"]
        day = df.iloc[i]["day"]
        win = None
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if df.iloc[j]["day"] != day:
                break
            if df.iloc[j]["low"] <= entry * (1 - target):
                win = True
                break
            if df.iloc[j]["high"] >= entry * (1 + stop):
                win = False
                break
        if win is None and i + 1 < len(df) and df.iloc[i + 1]["day"] == day:
            win = df.iloc[i + 1]["close"] < entry
        if win is not None:
            out["sell"].append({"win": win, "ret": 0})
    return out


def summarize(name, res):
    total = len(res)
    if not total:
        return {"name": name, "signals": 0, "win_rate": None, "avg_ret": None}
    wins = sum(1 for x in res if x["win"])
    avg = float(np.mean([x["ret"] for x in res]))
    return {"name": name, "signals": total, "win_rate": round(wins / total, 4),
            "avg_ret": round(avg, 4)}


def run():
    rows = []
    for symbol in CODES:
        df = add_indicators(fetch_5m(symbol))
        if df.empty or len(df) < 80:
            continue
        buy_idx_a = []
        sell_idx_a = []
        buy_idx_b = []
        sell_idx_b = []
        for i in range(20, len(df) - 3):
            r = df.iloc[i]
            near_below_vwap = r["close"] <= r["vwap"] * 1.003
            near_above_vwap = r["close"] >= r["vwap"] * 1.004
            rsi_low = not np.isnan(r["rsi"]) and r["rsi"] < 38
            rsi_high = not np.isnan(r["rsi"]) and r["rsi"] > 68
            near_lower = not np.isnan(r["lower"]) and r["close"] <= r["lower"] * 1.002
            near_upper = not np.isnan(r["upper"]) and r["close"] >= r["upper"] * 0.998
            vol_shrink = not np.isnan(r["vol_ma20"]) and r["volume"] < r["vol_ma20"] * 0.9
            vol_expand = not np.isnan(r["vol_ma20"]) and r["volume"] > r["vol_ma20"] * 1.4
            buy_score = sum([near_below_vwap, rsi_low, near_lower, vol_shrink])
            sell_score = sum([near_above_vwap, rsi_high, near_upper, vol_expand])
            if buy_score >= 3:
                buy_idx_a.append(i)
            if sell_score >= 3:
                sell_idx_a.append(i)
            if r["day_ret"] <= -0.015:
                buy_idx_b.append(i)
            if r["day_ret"] >= 0.015:
                sell_idx_b.append(i)
        ev_a = evaluate_signals(df, buy_idx_a, sell_idx_a)
        ev_b = evaluate_signals(df, buy_idx_b, sell_idx_b)
        ts_a = evaluate_target_stop(df, buy_idx_a, sell_idx_a)
        ts_b = evaluate_target_stop(df, buy_idx_b, sell_idx_b)
        rows.append({"symbol": symbol, "bars": len(df),
                     "a_buy": ev_a["buy"], "a_sell": ev_a["sell"],
                     "b_buy": ev_b["buy"], "b_sell": ev_b["sell"],
                     "ts_a_buy": ts_a["buy"], "ts_a_sell": ts_a["sell"],
                     "ts_b_buy": ts_b["buy"], "ts_b_sell": ts_b["sell"]})
    return rows


def main():
    global CODES
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default=",".join(CODES))
    args = ap.parse_args()
    CODES = [s.strip() for s in args.symbols.split(",") if s.strip()]
    rows = run()
    print(f"{'symbol':<12} {'sig':>4} {'A买胜率':>8} {'A买均收':>8} {'A卖胜率':>8} {'A卖均收':>8} | {'B买胜率':>8} {'B买均收':>8} {'B卖胜率':>8} {'B卖均收':>8}")
    for r in rows:
        a_buy = summarize("A买", r["a_buy"])
        a_sell = summarize("A卖", r["a_sell"])
        b_buy = summarize("B买", r["b_buy"])
        b_sell = summarize("B卖", r["b_sell"])
        ta_buy = summarize("目标A买", r["ts_a_buy"])
        ta_sell = summarize("目标A卖", r["ts_a_sell"])
        tb_buy = summarize("目标B买", r["ts_b_buy"])
        tb_sell = summarize("目标B卖", r["ts_b_sell"])
        print(f"{r['symbol']:<12} {r['bars']:>4} "
              f"{fmt(a_buy)} {fmt(a_sell)} | {fmt(b_buy)} {fmt(b_sell)}")
        print(f"{'':<12} {'':>4} "
              f"{ta_buy['signals']:>4} {ta_buy['win_rate'] * 100 if ta_buy['win_rate'] is not None else 0:>6.1f}% "
              f"{ta_sell['signals']:>4} {ta_sell['win_rate'] * 100 if ta_sell['win_rate'] is not None else 0:>6.1f}% | "
              f"{tb_buy['signals']:>4} {tb_buy['win_rate'] * 100 if tb_buy['win_rate'] is not None else 0:>6.1f}% "
              f"{tb_sell['signals']:>4} {tb_sell['win_rate'] * 100 if tb_sell['win_rate'] is not None else 0:>6.1f}%")
    agg = {"A买": [], "A卖": [], "B买": [], "B卖": []}
    agg_ts = {"目标A买": [], "目标A卖": [], "目标B买": [], "目标B卖": []}
    for r in rows:
        agg["A买"].extend(r["a_buy"])
        agg["A卖"].extend(r["a_sell"])
        agg["B买"].extend(r["b_buy"])
        agg["B卖"].extend(r["b_sell"])
        agg_ts["目标A买"].extend(r["ts_a_buy"])
        agg_ts["目标A卖"].extend(r["ts_a_sell"])
        agg_ts["目标B买"].extend(r["ts_b_buy"])
        agg_ts["目标B卖"].extend(r["ts_b_sell"])
    print()
    print("合计：")
    for name, res in agg.items():
        s = summarize(name, res)
        print(f"{name}: 信号 {s['signals']} 次，胜率 {s['win_rate'] * 100 if s['win_rate'] is not None else 0:.1f}%，平均收益 {s['avg_ret'] * 100 if s['avg_ret'] is not None else 0:.2f}%")
    print("目标止损（+0.5%/-0.5%，60分钟）胜率：")
    for name, res in agg_ts.items():
        s = summarize(name, res)
        print(f"{name}: 信号 {s['signals']} 次，胜率 {s['win_rate'] * 100 if s['win_rate'] is not None else 0:.1f}%")


def fmt(x):
    if x["signals"] == 0:
        return f"{'0':>4} {'--':>7} {'--':>7}"
    return f"{x['signals']:>4} {x['win_rate'] * 100:>6.1f}% {x['avg_ret'] * 100:>6.2f}%"


if __name__ == "__main__":
    main()

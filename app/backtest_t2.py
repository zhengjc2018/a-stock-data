# -*- coding: utf-8 -*-
"""做T强信号回测 v2：竞价量价 + 主力资金流 + 大盘共振 + 量价背离。

评价口径同 v1：+0.5% 止盈 / -0.5% 止损，60 分钟内。
"""
from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import requests

from astock_data import UA, em_get

STOCKS = [
    ("sh600519", "1.600519"), ("sz000001", "0.000001"), ("sz002594", "0.002594"),
    ("sh601318", "1.601318"), ("sz000858", "0.000858"), ("sh600036", "1.600036"),
    ("sz002415", "0.002415"), ("sh603501", "1.603501"),
]
INDEX = "sh000001"


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
    for it in json.loads(payload):
        rows.append({
            "day": str(it["day"])[:10],
            "dt": it["day"],
            "open": float(it["open"]),
            "high": float(it["high"]),
            "low": float(it["low"]),
            "close": float(it["close"]),
            "volume": float(it.get("volume") or 0),
        })
    return pd.DataFrame(rows)


def fetch_fund_flow(secid):
    out = {}
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    for host in ("https://push2his.eastmoney.com", "http://push2his.eastmoney.com",
                 "https://push2delay.eastmoney.com"):
        for attempt in range(2):
            try:
                r = em_get(f"{host}/api/qt/stock/fflow/daykline/get",
                           params=params, headers=headers, timeout=12)
                d = r.json() or {}
                for line in (d.get("data") or {}).get("klines", []) or []:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        out[parts[0]] = float(parts[1]) if parts[1] != "-" else 0.0
                if out:
                    return out
            except Exception:
                time.sleep(1)
    return out


def add_features(df, idx_df, fund_flow):
    df = df.copy().sort_values("dt").reset_index(drop=True)
    idx = idx_df.copy().sort_values("dt").reset_index(drop=True)
    idx_map = dict(zip(idx["dt"], idx["close"]))
    df["idx_close"] = df["dt"].map(idx_map)
    df["idx_trend"] = df["idx_close"] > df["idx_close"].shift(3)
    df["idx_trend"] = df["idx_trend"].fillna(False)
    df["prev_day_close"] = df.groupby("day")["close"].shift(1)
    # 当日第一根K线的前收 = 前一交易日最后一根收盘
    last_close = df.groupby("day")["close"].last().shift(1)
    df["prev_close"] = df["day"].map(last_close)
    first_vol = df.groupby("day")["volume"].first()
    first_vol_ma = first_vol.rolling(5, min_periods=2).mean()
    df["first_vol_ratio"] = df["day"].map(first_vol / first_vol_ma)
    df["open_gap"] = df["open"] / df["prev_close"] - 1
    df["main_net_prev"] = df["day"].map(fund_flow)
    df["vwap"] = (df["close"] * df["volume"]).groupby(df["day"]).cumsum() / \
                 df["volume"].groupby(df["day"]).cumsum()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    df["ma20"] = df["close"].rolling(20).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["upper"] = df["ma20"] + 2 * df["std20"]
    df["lower"] = df["ma20"] - 2 * df["std20"]
    df["vol_ma20"] = df["volume"].rolling(20).mean().shift(1)
    df["new_high20"] = df["close"] >= df["high"].rolling(20).max().shift(1)
    df["new_low20"] = df["close"] <= df["low"].rolling(20).min().shift(1)
    df["new_high20"] = df["new_high20"].fillna(False)
    df["new_low20"] = df["new_low20"].fillna(False)
    return df


def target_stop_winrate(df, buy_idx, sell_idx, horizon=12, target=0.005, stop=0.005):
    out = {"buy": [0, 0], "sell": [0, 0]}
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
            out["buy"][0] += 1
            out["buy"][1] += int(win)
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
            out["sell"][0] += 1
            out["sell"][1] += int(win)
    return out


def run():
    idx_df = fetch_5m(INDEX)
    rows = []
    for sym, secid in STOCKS:
        df = fetch_5m(sym)
        if df.empty or len(df) < 80:
            continue
        fund = fetch_fund_flow(secid)
        df = add_features(df, idx_df, fund)
        buy_idx, sell_idx = [], []
        for i in range(20, len(df) - 3):
            r = df.iloc[i]
            if pd.isna(r["prev_close"]) or pd.isna(r["first_vol_ratio"]) or pd.isna(r["main_net_prev"]) or pd.isna(r["idx_close"]):
                continue
            if np.isnan(r["rsi"]):
                continue
            near_vwap = r["close"] <= r["vwap"] * 1.003
            above_vwap = r["close"] >= r["vwap"] * 1.004
            low_rsi = r["rsi"] < 38
            high_rsi = r["rsi"] > 68
            near_lower = not np.isnan(r["lower"]) and r["close"] <= r["lower"] * 1.002
            near_upper = not np.isnan(r["upper"]) and r["close"] >= r["upper"] * 0.998
            low_vol = not np.isnan(r["vol_ma20"]) and r["volume"] < r["vol_ma20"] * 0.9
            high_vol = not np.isnan(r["vol_ma20"]) and r["volume"] > r["vol_ma20"] * 1.4
            buy_score = sum([
                r["open_gap"] <= 0.005 or near_vwap,
                r["main_net_prev"] > 0,
                bool(r["idx_trend"]),
                bool(r["new_low20"]) and low_vol,
                low_rsi or near_lower,
            ])
            sell_score = sum([
                r["open_gap"] >= 0.005 or above_vwap,
                r["main_net_prev"] < 0,
                not bool(r["idx_trend"]),
                bool(r["new_high20"]) and high_vol,
                high_rsi or near_upper,
            ])
            if buy_score >= 3:
                buy_idx.append(i)
            if sell_score >= 3:
                sell_idx.append(i)
        res = target_stop_winrate(df, buy_idx, sell_idx)
        rows.append({
            "symbol": sym,
            "bars": len(df),
            "buy": res["buy"],
            "sell": res["sell"],
        })
    return rows


def main():
    rows = run()
    print(f"{'symbol':<12} {'买信号':>6} {'买胜率':>8} | {'卖信号':>6} {'卖胜率':>8}")
    for r in rows:
        b, bw = r["buy"]
        s, sw = r["sell"]
        print(f"{r['symbol']:<12} {b:>6} {bw / b * 100 if b else 0:>7.1f}% | {s:>6} {sw / s * 100 if s else 0:>7.1f}%")
    tb = sum(r["buy"][0] for r in rows)
    tbw = sum(r["buy"][1] for r in rows)
    ts = sum(r["sell"][0] for r in rows)
    tsw = sum(r["sell"][1] for r in rows)
    print()
    print(f"合计 买 {tb} 次，胜率 {tbw / tb * 100 if tb else 0:.1f}%；卖 {ts} 次，胜率 {tsw / ts * 100 if ts else 0:.1f}%")


if __name__ == "__main__":
    main()

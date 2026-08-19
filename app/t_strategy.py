# -*- coding: utf-8 -*-
"""个股做T策略画像 + 参数优化回测。

对每只股票：
1. 计算趋势/波动/量能/日内画像
2. 在 5 分钟历史数据上用参数网格做时间序列回测（前70%选参，后30%验证）
3. 与全局默认参数对比，输出 t_params_<code>.json
"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd
import requests
import threading

from astock_data import UA, em_get

_KLINE_CACHE = {}
_FUND_CACHE = {}
_CACHE_LOCK = threading.Lock()

DEFAULT_PARAMS = {
    "target": 0.005,
    "stop": 0.005,
    "rsi_low": 38,
    "rsi_high": 68,
    "score": 3,
    "vwap_buy": 0.004,
    "vwap_sell": 0.004,
    "vol_shrink": 0.9,
    "vol_expand": 1.4,
    "horizon": 12,
}


def params_for_profile(profile=None):
    """根据趋势/波动画像调整默认做T参数，避免低波动、高波动一刀切。"""
    p = dict(DEFAULT_PARAMS)
    profile = profile or {}
    trend = str(profile.get("trend") or "震荡")
    volatility = str(profile.get("volatility") or "中波动")
    if trend == "下降趋势":
        p["score"] = max(int(p.get("score", 3)), 4)
        p["target"] = 0.004
        p["stop"] = 0.004
        p["rsi_low"] = 35
        p["rsi_high"] = 65
    elif trend == "上升趋势":
        p["target"] = 0.006
        p["stop"] = 0.006
    if volatility == "高波动":
        p["target"] = max(p.get("target", 0.005), 0.006)
        p["stop"] = max(p.get("stop", 0.005), 0.006)
    elif volatility == "低波动":
        p["target"] = min(p.get("target", 0.005), 0.004)
        p["stop"] = min(p.get("stop", 0.005), 0.004)
    return p


STOCKS = [
    ("600519", "sh600519", "1.600519"), ("000001", "sz000001", "0.000001"),
    ("002594", "sz002594", "0.002594"), ("601318", "sh601318", "1.601318"),
    ("000858", "sz000858", "0.000858"), ("600036", "sh600036", "1.600036"),
    ("002415", "sz002415", "0.002415"), ("603501", "sh603501", "1.603501"),
]


def fetch_kline(symbol, scale, datalen):
    key = (symbol, scale)
    with _CACHE_LOCK:
        hit = _KLINE_CACHE.get(key)
        if hit and time.time() - hit[0] < 120:
            return hit[1].copy()
    url = "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData"
    r = requests.get(url, params={"symbol": symbol, "scale": str(scale), "ma": "no",
                                  "datalen": str(datalen)},
                     headers={"User-Agent": "Mozilla/5.0",
                              "Referer": "https://finance.sina.com.cn/"},
                     timeout=15)
    text = r.text
    if "([" not in text or "])" not in text:
        return pd.DataFrame()
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
    df = pd.DataFrame(rows)
    with _CACHE_LOCK:
        _KLINE_CACHE[key] = (time.time(), df.copy())
    return df


def fetch_fund_flow(secid):
    with _CACHE_LOCK:
        hit = _FUND_CACHE.get(secid)
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
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
        try:
            r = em_get(f"{host}/api/qt/stock/fflow/daykline/get",
                       params=params, headers=headers, timeout=6)
            d = r.json() or {}
            for line in (d.get("data") or {}).get("klines", []) or []:
                parts = line.split(",")
                if len(parts) >= 2:
                    out[parts[0]] = float(parts[1]) if parts[1] != "-" else 0.0
            if out:
                break
        except Exception:
            continue
    with _CACHE_LOCK:
        _FUND_CACHE[secid] = (time.time(), out)
    return out


def add_signals_features(df, fund_flow):
    df = df.copy().sort_values("dt").reset_index(drop=True)
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
    df["new_high20"] = (df["close"] >= df["high"].rolling(20).max().shift(1)).fillna(False)
    df["new_low20"] = (df["close"] <= df["low"].rolling(20).min().shift(1)).fillna(False)
    return df


def signal_indices(df, p):
    buy, sell = [], []
    for i in range(20, len(df) - 1):
        r = df.iloc[i]
        if pd.isna(r["prev_close"]) or pd.isna(r["first_vol_ratio"]) or pd.isna(r["idx_close"]):
            continue
        if np.isnan(r["rsi"]) or np.isnan(r["vol_ma20"]):
            continue
        buy_score, sell_score = _score_row(r, p)
        if buy_score >= p["score"]:
            buy.append(i)
        if sell_score >= p["score"]:
            sell.append(i)
    return buy, sell


def _score_row(r, p):
    near_vwap = r["close"] <= r["vwap"] * (1 + p["vwap_buy"])
    above_vwap = r["close"] >= r["vwap"] * (1 + p["vwap_sell"])
    near_lower = not np.isnan(r["lower"]) and r["close"] <= r["lower"] * 1.002
    near_upper = not np.isnan(r["upper"]) and r["close"] >= r["upper"] * 0.998
    low_vol = r["volume"] < r["vol_ma20"] * p["vol_shrink"]
    high_vol = r["volume"] > r["vol_ma20"] * p["vol_expand"]
    buy_score = sum([
        r["open_gap"] <= 0.005 or near_vwap,
        r["main_net_prev"] > 0,
        bool(r["idx_trend"]),
        bool(r["new_low20"]) and low_vol,
        r["rsi"] < p["rsi_low"] or near_lower,
    ])
    sell_score = sum([
        r["open_gap"] >= 0.005 or above_vwap,
        r["main_net_prev"] < 0,
        not bool(r["idx_trend"]),
        bool(r["new_high20"]) and high_vol,
        r["rsi"] > p["rsi_high"] or near_upper,
    ])
    return buy_score, sell_score


def target_stop_winrate(df, buy_idx, sell_idx, p, cost_rate=0.0012):
    horizon = p.get("horizon", 12)
    target = p["target"]
    stop = p["stop"]
    res = {"buy": [0, 0], "sell": [0, 0]}
    for i in buy_idx:
        entry = df.iloc[i]["close"]
        day = df.iloc[i]["day"]
        win = None
        buy_target = entry * (1 + target + cost_rate)
        buy_stop = entry * (1 - stop - cost_rate)
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if df.iloc[j]["day"] != day:
                break
            if df.iloc[j]["high"] >= buy_target:
                win = True
                break
            if df.iloc[j]["low"] <= buy_stop:
                win = False
                break
        if win is None and i + 1 < len(df) and df.iloc[i + 1]["day"] == day:
            win = df.iloc[i + 1]["close"] > entry
        if win is not None:
            res["buy"][0] += 1
            res["buy"][1] += int(win)
    for i in sell_idx:
        entry = df.iloc[i]["close"]
        day = df.iloc[i]["day"]
        win = None
        sell_target = entry * (1 - target - cost_rate)
        sell_stop = entry * (1 + stop + cost_rate)
        for j in range(i + 1, min(i + 1 + horizon, len(df))):
            if df.iloc[j]["day"] != day:
                break
            if df.iloc[j]["low"] <= sell_target:
                win = True
                break
            if df.iloc[j]["high"] >= sell_stop:
                win = False
                break
        if win is None and i + 1 < len(df) and df.iloc[i + 1]["day"] == day:
            win = df.iloc[i + 1]["close"] < entry
        if win is not None:
            res["sell"][0] += 1
            res["sell"][1] += int(win)
    return res


def summarize(res):
    out = {}
    for side in ("buy", "sell"):
        n, w = res[side]
        out[side] = {"signals": n, "win_rate": round(w / n, 4) if n else None}
    n = out["buy"]["signals"] + out["sell"]["signals"]
    w = (out["buy"]["win_rate"] or 0) * out["buy"]["signals"] + \
        (out["sell"]["win_rate"] or 0) * out["sell"]["signals"]
    out["combined"] = {"signals": n, "win_rate": round(w / n, 4) if n else None}
    return out


def build_grid():
    grid = []
    for target in (0.004, 0.005, 0.006):
        for rsi_low, rsi_high in ((35, 68), (38, 72)):
            for score in (3, 4):
                for vwap in (0.003, 0.005):
                    p = dict(DEFAULT_PARAMS)
                    p.update(target=target, stop=target, rsi_low=rsi_low,
                             rsi_high=rsi_high, score=score,
                             vwap_buy=vwap, vwap_sell=vwap)
                    grid.append(p)
    return grid


def profile_stock(daily, intraday):
    daily = daily.sort_values("day").reset_index(drop=True)
    close = daily["close"]
    ret20 = close.iloc[-1] / close.iloc[-21] - 1 if len(close) > 21 else 0.0
    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1] if len(close) >= 60 else close.rolling(20).mean().iloc[-1]
    prev_close = daily["close"].shift(1)
    amp = ((daily["high"] - daily["low"]) / prev_close).dropna()
    avg_daily_amp = float(amp.mean()) if len(amp) else 0.0
    vol_ratio = daily["volume"].iloc[-5:].mean() / daily["volume"].iloc[-20:-5].mean() if len(daily) >= 20 else 1.0
    bar_range = ((intraday["high"] - intraday["low"]) / intraday["close"]).replace([np.inf, -np.inf], np.nan).dropna()
    avg_bar_range = float(bar_range.mean()) if len(bar_range) else 0.0
    vwap_dev = (intraday["close"] / intraday["vwap"] - 1).abs().mean() if "vwap" in intraday.columns else 0.0
    if ret20 > 0.05 and ma5 > ma20 > ma60:
        trend = "上升趋势"
    elif ret20 < -0.05 and ma5 < ma20 < ma60:
        trend = "下降趋势"
    else:
        trend = "震荡"
    if avg_daily_amp > 0.04:
        vol = "高波动"
    elif avg_daily_amp < 0.02:
        vol = "低波动"
    else:
        vol = "中波动"
    return {
        "trend": trend,
        "volatility": vol,
        "ret_20": round(float(ret20), 4),
        "avg_daily_amp": round(avg_daily_amp, 4),
        "avg_bar_range": round(avg_bar_range, 5),
        "vol_ratio_5_20": round(float(vol_ratio), 3),
        "vwap_dev_avg": round(float(vwap_dev), 5),
        "ma5": round(float(ma5), 2),
        "ma20": round(float(ma20), 2),
        "ma60": round(float(ma60), 2),
    }


def quick_profile(code, symbol, secid):
    """只算趋势画像，不跑参数优化，用于页面快速展示。"""
    intraday = fetch_kline(symbol, 5, 1023)
    daily = fetch_kline(symbol, 240, 120)
    if intraday.empty or daily.empty or len(intraday) < 60:
        return None
    df = add_signals_features(intraday, {})
    df["main_net_prev"] = df["main_net_prev"].fillna(0)
    return profile_stock(daily, df)


def save_params(payload, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"t_params_{payload['code']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def optimize_code(code, symbol, secid, out_dir, write=True):
    intraday = fetch_kline(symbol, 5, 1023)
    daily = fetch_kline(symbol, 240, 120)
    if intraday.empty or daily.empty or len(intraday) < 120:
        print(f"[t] {code} data insufficient", flush=True)
        return None
    fund = fetch_fund_flow(secid)
    df = add_signals_features(intraday, fund)
    df["main_net_prev"] = df["main_net_prev"].fillna(0)
    idx_df = fetch_kline("sh000001", 5, 1023)
    if idx_df.empty:
        df["idx_close"] = 0.0
        df["idx_trend"] = False
    else:
        idx_map = dict(zip(idx_df["dt"], idx_df["close"]))
        df["idx_close"] = df["dt"].map(idx_map)
        df["idx_trend"] = (df["idx_close"] > df["idx_close"].shift(3)).fillna(False)
    df = df.dropna(subset=["prev_close", "first_vol_ratio", "idx_close"]).reset_index(drop=True)
    split = int(len(df) * 0.7)
    train_df, test_df = df.iloc[:split].copy(), df.iloc[split:].copy()
    profile = profile_stock(daily, df)
    grid = build_grid()
    best = None
    for p in grid:
        buy, sell = signal_indices(train_df, p)
        res = summarize(target_stop_winrate(train_df, buy, sell, p))
        n = res["combined"]["signals"]
        wr = res["combined"]["win_rate"] or 0
        if n < 10:
            continue
        key = (wr, n)
        if best is None or key > best[0]:
            best = (key, p, res)
    if best is None:
        params = dict(DEFAULT_PARAMS)
        train_summary = {"combined": {"signals": 0, "win_rate": None}}
    else:
        params = best[1]
        train_summary = best[2]
    buy_test, sell_test = signal_indices(test_df, params)
    test_summary = summarize(target_stop_winrate(test_df, buy_test, sell_test, params))
    profile_params = params_for_profile(profile)
    buy_def, sell_def = signal_indices(test_df, profile_params)
    default_summary = summarize(target_stop_winrate(test_df, buy_def, sell_def, profile_params))
    payload = {
        "code": code,
        "profile": profile,
        "params": params,
        "train": train_summary,
        "test": test_summary,
        "test_default": default_summary,
        "improved": bool(
            (test_summary["combined"]["win_rate"] or 0) >
            (default_summary["combined"]["win_rate"] or 0)
        ),
    }
    if write:
        save_params(payload, out_dir)
    print(f"[t] {code} {profile['trend']} {profile['volatility']} "
          f"opt {test_summary['combined']['win_rate']} vs default "
          f"{default_summary['combined']['win_rate']}", flush=True)
    return payload


def main():
    ap = argparse.ArgumentParser(description="个股做T策略画像与参数优化")
    ap.add_argument("--codes", type=str, default=None)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "t_params"))
    args = ap.parse_args()
    if args.codes:
        wanted = set(args.codes.split(","))
        stocks = [s for s in STOCKS if s[0] in wanted]
    else:
        stocks = STOCKS
    for code, symbol, secid in stocks:
        optimize_code(code, symbol, secid, args.out)


if __name__ == "__main__":
    main()

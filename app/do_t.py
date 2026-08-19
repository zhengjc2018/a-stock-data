# -*- coding: utf-8 -*-
"""做T模拟盘：持仓管理 + 每分钟信号监控 + 系统通知 + 自动模拟成交。"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

import datahub
import paths
import t_strategy

CN_TZ = timezone(timedelta(hours=8))
STATE_FILE = paths.data_path("t_holdings.json")
STATE_LOCK = threading.RLock()
_MONITOR = {"thread": None, "stop": False, "running": False}
_CACHE = {"bars": {"ts": 0, "data": {}}, "fund": {"ts": 0, "data": {}}}
T_PARAMS_DIR = os.path.join(paths.data_dir(), "t_params")

DEFAULT_STATE = {
    "holdings": [],
    "monitoring": False,
    "auto_execute": True,
    "signals": [],
    "trades": [],
    "daily_count": {},
    "last_check": None,
}


def _now():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def load_state():
    with STATE_LOCK:
        if not os.path.isfile(STATE_FILE):
            state = dict(DEFAULT_STATE)
            save_state(state)
            return state
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            for k, v in DEFAULT_STATE.items():
                state.setdefault(k, v)
            return state
        except Exception:
            return dict(DEFAULT_STATE)


def save_state(state):
    with STATE_LOCK:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def _bars_cache(symbol, scale=1):
    now = time.time()
    if now - _CACHE["bars"]["ts"] > 60:
        _CACHE["bars"] = {"ts": now, "data": {}}
    cache = _CACHE["bars"]["data"]
    key = f"{symbol}:{scale}"
    if key not in cache:
        cache[key] = t_strategy.fetch_kline(symbol, scale, 240 if scale == 1 else 120)
    return cache[key]


def _fund_cache(secid):
    now = time.time()
    if now - _CACHE["fund"]["ts"] > 3600:
        _CACHE["fund"] = {"ts": now, "data": {}}
    cache = _CACHE["fund"]["data"]
    if secid not in cache:
        cache[secid] = t_strategy.fetch_fund_flow(secid)
    return cache[secid]


def _symbol_secid(code):
    code = str(code).zfill(6)
    return (f"sh{code}", f"1.{code}") if code.startswith("6") else (f"sz{code}", f"0.{code}")


def _params_for(code):
    path = os.path.join(T_PARAMS_DIR, f"t_params_{code}.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            test = d.get("test", {}).get("combined", {}) or {}
            default = d.get("test_default", {}).get("combined", {}) or {}
            if (d.get("improved") and (test.get("win_rate") or 0) >
                    (default.get("win_rate") or 0) + 0.02 and
                    (test.get("signals") or 0) >= 20):
                return d.get("params") or dict(t_strategy.DEFAULT_PARAMS), d
        except Exception:
            pass
    return dict(t_strategy.DEFAULT_PARAMS), None


def _resolve_params(code):
    params, doc = _params_for(code)
    source = "default"
    if doc and doc.get("improved"):
        test = doc.get("test", {}).get("combined", {}) or {}
        default = doc.get("test_default", {}).get("combined", {}) or {}
        if ((test.get("win_rate") or 0) > (default.get("win_rate") or 0) + 0.02 and
                (test.get("signals") or 0) >= 20):
            source = "optimized"
    return params, source


def _run_analysis(h):
    try:
        code = h["code"]
        symbol, secid = _symbol_secid(code)
        profile = t_strategy.quick_profile(code, symbol, secid)
        if profile:
            state = load_state()
            for item in state["holdings"]:
                if item.get("id") == h.get("id"):
                    item["analysis"] = {"status": "profile", "profile": profile}
                    break
            save_state(state)
        payload = t_strategy.optimize_code(code, symbol, secid, T_PARAMS_DIR)
        if payload is None:
            raise RuntimeError("数据不足，无法分析")
        analysis = {"status": "done", **payload}
        params, source = _resolve_params(code)
        analysis["params_used"] = params
        analysis["params_source"] = source
    except Exception as e:
        analysis = {"status": "error", "error": str(e)}
    state = load_state()
    for item in state["holdings"]:
        if item.get("id") == h.get("id"):
            item["analysis"] = analysis
            break
    save_state(state)


def ensure_analysis(state=None):
    state = state or load_state()
    for h in state["holdings"]:
        analysis = h.get("analysis") or {}
        if analysis.get("status") == "done" and "params_source" not in analysis:
            params, source = _resolve_params(h["code"])
            analysis["params_used"] = params
            analysis["params_source"] = source
            continue
        if analysis.get("status") in ("done", "analyzing"):
            continue
        h["analysis"] = {"status": "analyzing"}
        threading.Thread(target=_run_analysis, args=(h,), daemon=True).start()
    save_state(state)


def _compute_signal(code, name, cost, qty):
    symbol, secid = _symbol_secid(code)
    bars = _bars_cache(symbol, 1)
    if bars.empty or len(bars) < 60:
        return None
    idx = _bars_cache("sh000001", 1)
    fund = _fund_cache(secid)
    df = t_strategy.add_signals_features(bars, fund)
    df["main_net_prev"] = df["main_net_prev"].fillna(0)
    if idx.empty:
        df["idx_close"] = 0.0
        df["idx_trend"] = False
    else:
        idx_map = dict(zip(idx["dt"], idx["close"]))
        df["idx_close"] = df["dt"].map(idx_map)
        df["idx_trend"] = (df["idx_close"] > df["idx_close"].shift(3)).fillna(False)
    df = df.dropna(subset=["prev_close", "first_vol_ratio", "idx_close"]).reset_index(drop=True)
    params, _ = _resolve_params(code)
    buy_idx, sell_idx = t_strategy.signal_indices(df, params)
    last = df.iloc[-1]["dt"]
    signal = None
    if buy_idx and df.iloc[buy_idx[-1]]["dt"] == last:
        signal = {"code": code, "name": name, "side": "buy", "price": float(df.iloc[-1]["close"]),
                  "dt": last, "reason": "低吸信号：VWAP/RSI/缩量/大盘共振"}
    elif sell_idx and df.iloc[sell_idx[-1]]["dt"] == last:
        signal = {"code": code, "name": name, "side": "sell", "price": float(df.iloc[-1]["close"]),
                  "dt": last, "reason": "高抛信号：偏离VWAP/RSI超买/放量滞涨"}
    if signal:
        signal["cost"] = cost
        signal["qty"] = qty
        signal["profile"] = profile.get("profile") if profile else None
    return signal


def notify(title, message):
    try:
        with open(paths.data_path("t_notify.json"), "w", encoding="utf-8") as f:
            json.dump({
                "title": title,
                "message": message,
                "ts": int(time.time() * 1000),
            }, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
    except Exception:
        pass


def _auto_execute(state, signal):
    if not state.get("auto_execute", True):
        return
    code = signal["code"]
    day = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    count = state.setdefault("daily_count", {}).get(code, {}).get(day, 0)
    if count >= 3:
        return
    for h in state["holdings"]:
        if h["code"] != code:
            continue
        trade_qty = max(100, int(h["qty"] * 0.33) // 100 * 100)
        if trade_qty <= 0:
            return
        price = signal["price"]
        if signal["side"] == "buy":
            new_qty = h["qty"] + trade_qty
            h["cost"] = round((h["cost"] * h["qty"] + price * trade_qty) / new_qty, 3)
            h["qty"] = new_qty
        else:
            if h["qty"] < trade_qty + 100:
                return
            h["qty"] -= trade_qty
        state["trades"].insert(0, {
            "time": _now(), "code": code, "name": signal["name"],
            "side": signal["side"], "price": price, "qty": trade_qty,
        })
        state["trades"] = state["trades"][:100]
        state.setdefault("daily_count", {}).setdefault(code, {})[day] = count + 1
        break


def check_once():
    state = load_state()
    quotes = {}
    if state["holdings"]:
        quotes = datahub.tencent_quote([h["code"] for h in state["holdings"]])
    for h in state["holdings"]:
        q = quotes.get(h["code"]) or {}
        h["price"] = q.get("price") or h.get("price") or h["cost"]
        h["change_pct"] = q.get("change_pct")
    signals = []
    for h in state["holdings"]:
        try:
            sig = _compute_signal(h["code"], h.get("name") or h["code"], h["cost"], h["qty"])
        except Exception as e:
            print(f"[do_t] signal err {h['code']}: {e}", flush=True)
            sig = None
        if sig:
            signals.append(sig)
            state["signals"].insert(0, sig)
            state["signals"] = state["signals"][:50]
            notify(f"做T信号 {sig['code']} {sig['name']}",
                   f"{'买入' if sig['side'] == 'buy' else '卖出'} @ {sig['price']}")
            _auto_execute(state, sig)
    state["last_check"] = _now()
    save_state(state)
    return state


def start():
    state = load_state()
    state["monitoring"] = True
    save_state(state)
    if _MONITOR["thread"] and _MONITOR["thread"].is_alive():
        return
    _MONITOR["stop"] = False

    def _loop():
        while not _MONITOR["stop"]:
            try:
                check_once()
            except Exception as e:
                print(f"[do_t] check err: {e}", flush=True)
            time.sleep(60)

    _MONITOR["thread"] = threading.Thread(target=_loop, daemon=True)
    _MONITOR["thread"].start()


def stop():
    _MONITOR["stop"] = True
    state = load_state()
    state["monitoring"] = False
    save_state(state)


def add_holding(code, name, cost, qty):
    state = load_state()
    code = str(code).zfill(6)
    for h in state["holdings"]:
        if h["code"] == code:
            h.update(name=name or h.get("name"), cost=float(cost), qty=int(qty))
            save_state(state)
            return state
    q = datahub.tencent_quote([code]).get(code) or {}
    state["holdings"].append({
        "id": f"{code}-{int(time.time())}",
        "code": code,
        "name": name or q.get("name") or code,
        "cost": float(cost),
        "qty": int(qty),
    })
    save_state(state)
    ensure_analysis(state)
    return state


def delete_holding(hid):
    state = load_state()
    state["holdings"] = [h for h in state["holdings"] if h.get("id") != hid]
    save_state(state)
    return state


def search_stocks(q, limit=10):
    q = str(q or "").strip()
    if not q:
        return []
    try:
        r = requests.get("https://smartbox.gtimg.cn/s3/",
                         params={"v": "2", "q": q, "t": "all"}, timeout=8)
        r.encoding = "gbk"
        text = r.text
    except Exception:
        return []
    if '"' not in text:
        return []

    def _u(s):
        try:
            return s.encode("utf-8").decode("unicode_escape")
        except Exception:
            return s

    out = []
    for item in text.split('"')[1].split("^"):
        parts = item.split("~")
        if len(parts) >= 5 and parts[4] in ("GP-A", "GP-B", "GP"):
            out.append({
                "code": parts[1],
                "name": _u(parts[2]),
                "market": parts[0].upper(),
            })
            if len(out) >= limit:
                break
    return out

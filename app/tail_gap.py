# -*- coding: utf-8 -*-
"""尾盘买入 -> 次日开盘 >= +3% 获利了结（模拟盘）。

流程：T日 14:45 后生成候选 -> 手动买入；T+1 开盘后验证并记录成绩。
"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pandas as pd

import datahub
import gap_pick
import paths
import tail_model

CN_TZ = timezone(timedelta(hours=8))
STATE_FILE = paths.data_path("tail_state.json")
STATE_LOCK = threading.RLock()
_SIGNAL = {"running": False}

DEFAULT_STATE = {
    "positions": [],
    "trades": [],
    "candidates": [],
    "last_signal": None,
    "last_verify": None,
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


def generate_signal():
    state = load_state()
    if _SIGNAL["running"]:
        state["last_signal"] = {"time": _now(), "msg": "计算中，请稍候"}
        save_state(state)
        return state
    _SIGNAL["running"] = True
    state["last_signal"] = {"time": _now(), "msg": "计算中，约需几分钟"}
    save_state(state)
    threading.Thread(target=_safe_run, daemon=True).start()
    return state


def _safe_run():
    try:
        _run_signal()
    except Exception as e:
        state = load_state()
        state["last_signal"] = {"time": _now(), "msg": f"计算失败：{e}"}
        save_state(state)
    finally:
        _SIGNAL["running"] = False


def _run_signal():
    state = load_state()
    snapshot = gap_pick.fetch_market_snapshot()
    if snapshot.empty:
        state["last_signal"] = {"time": _now(), "msg": "全市场快照为空"}
        _SIGNAL["running"] = False
        save_state(state)
        return
    industry_mean_map = (
        snapshot.assign(industry=snapshot["industry"].fillna("未知行业"))
        .groupby("industry")["pct_chg"].mean().to_dict()
    )
    industry_rank_map = pd.Series(industry_mean_map).rank(pct=True).to_dict()
    idx_rows = datahub._klines("1.000001", 101, 5)
    index_ret_prev = 0.0
    index_ma5_up = 0.0
    if len(idx_rows) >= 2:
        c1 = float(idx_rows[-2]["close"])
        c2 = float(idx_rows[-1]["close"])
        if c1:
            index_ret_prev = c2 / c1 - 1
    if len(idx_rows) >= 5:
        closes = [float(r["close"]) for r in idx_rows[-5:]]
        index_ma5_up = 1.0 if closes[-1] > sum(closes) / len(closes) else 0.0
    feats_names = tail_model.model_features()
    if not feats_names:
        state["last_signal"] = {"time": _now(), "msg": "尾盘模型不存在"}
        _SIGNAL["running"] = False
        save_state(state)
        return

    def _score(row):
        code = str(row.get("code", "")).zfill(6)
        name = str(row.get("name", ""))
        if "退" in name:
            return None
        price = float(row.get("price") or 0)
        if price <= 0:
            return None
        amount_yi = float(row.get("volume_lots") or 0) * 100 * price / 1e8
        if amount_yi < 0.5:
            return None
        secid = f"{'1' if code[0] in '689' else '0'}.{code}"
        hist = gap_pick._history_df(
            secid, time.strftime("%Y-%m-%d"), price,
            row.get("high"), row.get("low"), row.get("volume_lots"), row.get("amount"))
        if hist is None:
            return None
        last = hist.iloc[-1]
        feats = {}
        for k in feats_names:
            if k in ("index_ret_prev", "index_ma5_up", "industry_mean_prev", "industry_rank_prev"):
                continue
            try:
                v = float(last.get(k))
            except (TypeError, ValueError):
                return None
            if pd.isna(v):
                return None
            feats[k] = v
        industry = str(row.get("industry") or "").strip() or "未知行业"
        feats["index_ret_prev"] = index_ret_prev
        feats["index_ma5_up"] = index_ma5_up
        feats["industry_mean_prev"] = float(industry_mean_map.get(industry, 0.0))
        feats["industry_rank_prev"] = float(industry_rank_map.get(industry, 0.0))
        prob = tail_model.score(feats)
        if prob is None:
            return None
        return {"code": code, "name": name, "price": price, "prob": round(float(prob), 4),
                "amount_yi": round(amount_yi, 2)}

    top = []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tail-scan") as ex:
        for c in ex.map(_score, snapshot.to_dict("records")):
            if c:
                top.append(c)
    top.sort(key=lambda x: x["prob"], reverse=True)
    top = top[:3]
    state["candidates"] = [{
        "code": c["code"], "name": c["name"], "price": c["price"],
        "prob": c["prob"], "amount_yi": c["amount_yi"],
    } for c in top]
    state["last_signal"] = {"time": _now(), "count": len(top)}
    _SIGNAL["running"] = False
    save_state(state)


def buy(code, name, price):
    state = load_state()
    today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    state["positions"].append({
        "code": str(code).zfill(6),
        "name": name,
        "entry_price": float(price),
        "entry_date": today,
    })
    save_state(state)
    return state


def verify_next_day():
    state = load_state()
    if not state["positions"]:
        return state
    today = datetime.now(CN_TZ).strftime("%Y-%m-%d")
    codes = [p["code"] for p in state["positions"]]
    quotes = datahub.tencent_quote(codes)
    remaining = []
    for p in state["positions"]:
        q = quotes.get(p["code"]) or {}
        open_ = q.get("open") or 0
        high = q.get("high") or 0
        if not open_:
            remaining.append(p)
            continue
        hit = max(open_, high) / p["entry_price"] - 1 >= 0.03
        state["trades"].insert(0, {
            "code": p["code"],
            "name": p["name"],
            "entry_date": p["entry_date"],
            "entry_price": p["entry_price"],
            "exit_open": round(open_, 3),
            "exit_high": round(high, 3),
            "exit_date": today,
            "pct": round((max(open_, high) / p["entry_price"] - 1) * 100, 2),
            "hit": hit,
        })
    state["trades"] = state["trades"][:500]
    state["positions"] = remaining
    state["last_verify"] = _now()
    save_state(state)
    return state


def stats():
    state = load_state()
    trades = state.get("trades", [])
    total = len(trades)
    hits = sum(1 for t in trades if t.get("hit"))
    by_day = {}
    for t in trades:
        by_day.setdefault(t["entry_date"], []).append(t)
    day_total = len(by_day)
    day_hit = sum(1 for d, items in by_day.items() if any(i.get("hit") for i in items))
    recent = [t for t in trades if t.get("entry_date", "") >=
              (datetime.now(CN_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")]
    recent_total = len(recent)
    recent_hits = sum(1 for t in recent if t.get("hit"))
    return {
        "positions": state.get("positions", []),
        "candidates": state.get("candidates", []),
        "last_signal": state.get("last_signal"),
        "last_verify": state.get("last_verify"),
        "signal_running": _SIGNAL["running"],
        "stats": {
            "trades": total,
            "hit_rate": round(hits / total, 4) if total else None,
            "day_trades": day_total,
            "day_hit_rate": round(day_hit / day_total, 4) if day_total else None,
            "recent_trades": recent_total,
            "recent_hit_rate": round(recent_hits / recent_total, 4) if recent_total else None,
        },
        "trades": state.get("trades", [])[:50],
    }

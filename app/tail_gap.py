# -*- coding: utf-8 -*-
"""尾盘买入 -> 次日开盘 >= +3% 获利了结（模拟盘）。

流程：T日 14:45 后生成候选 -> 手动买入；T+1 开盘后验证并记录成绩。
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import datahub
import gap_pick
import paths

CN_TZ = timezone(timedelta(hours=8))
STATE_FILE = paths.data_path("tail_state.json")
STATE_LOCK = threading.RLock()

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
    scope = {"main": True, "chi_next": False, "st": False}
    data = gap_pick.get_cache(scope, trigger=False)
    if not data or not data.get("candidates"):
        state["last_signal"] = {"time": _now(), "msg": "尚无候选，请先计算"}
        save_state(state)
        return state
    cands = data["candidates"]
    strong = []
    for c in cands:
        if c.get("event_note") in ("解禁/减持",):
            continue
        if not (c.get("prev_limit_up") or c.get("limit_streak_prev")):
            continue
        if (c.get("main_net_yi") or 0) < 0:
            continue
        strong.append(c)
    strong.sort(key=lambda x: x.get("enhanced_prob") or x.get("prob") or 0, reverse=True)
    top = strong[:3] if strong else cands[:3]
    state["candidates"] = [{
        "code": c["code"],
        "name": c.get("name", ""),
        "price": c.get("price"),
        "prob": c.get("enhanced_prob") or c.get("prob"),
        "prev_limit_up": c.get("prev_limit_up"),
        "main_net_yi": c.get("main_net_yi"),
    } for c in top]
    state["last_signal"] = {"time": _now(), "count": len(top)}
    save_state(state)
    return state


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
        if not open_:
            remaining.append(p)
            continue
        hit = open_ / p["entry_price"] - 1 >= 0.03
        state["trades"].insert(0, {
            "code": p["code"],
            "name": p["name"],
            "entry_date": p["entry_date"],
            "entry_price": p["entry_price"],
            "exit_open": round(open_, 3),
            "exit_date": today,
            "pct": round((open_ / p["entry_price"] - 1) * 100, 2),
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

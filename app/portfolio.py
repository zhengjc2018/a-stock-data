# -*- coding: utf-8 -*-
"""模拟账户绩效与风控：统计胜率/盈亏/回撤，并控制做T频率与止损。"""
from __future__ import annotations

import json
import os
import threading

import do_t
import paths

STATE_FILE = paths.data_path("portfolio_state.json")
STATE_LOCK = threading.RLock()

DEFAULT = {
    "initial_capital": 1000000.0,
    "risk": {
        "max_daily_trades": 3,
        "per_stock_daily_max": 3,
        "stop_loss_pct": 2.0,
        "max_drawdown_pct": 20.0,
    },
    "equity_history": [],
}


def load_state():
    with STATE_LOCK:
        if not os.path.isfile(STATE_FILE):
            save_state(dict(DEFAULT))
            return dict(DEFAULT)
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            for k, v in DEFAULT.items():
                state.setdefault(k, v)
            return state
        except Exception:
            return dict(DEFAULT)


def save_state(state):
    with STATE_LOCK:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


def _chrono_trades(trades):
    return list(reversed(trades))


def compute():
    state = load_state()
    st = do_t.load_state()
    trades = _chrono_trades(st.get("trades", []))
    cash = float(state.get("initial_capital", DEFAULT["initial_capital"]))
    for t in trades:
        qty = float(t.get("qty") or 0)
        price = float(t.get("price") or 0)
        if t.get("side") == "buy":
            cash -= price * qty
        else:
            cash += price * qty
    holdings_value = 0.0
    for h in st.get("holdings", []):
        holdings_value += float(h.get("qty") or 0) * float(h.get("price") or h.get("cost") or 0)
    equity = cash + holdings_value
    pcts = [float(t.get("pct") or 0) for t in trades if t.get("pct") is not None]
    wins = [p for p in pcts if p > 0]
    losses = [p for p in pcts if p < 0]
    win_rate = round(len(wins) / len(pcts), 4) if pcts else None
    avg_win = round(sum(wins) / len(wins), 3) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 3) if losses else 0.0
    profit_factor = round(abs(sum(wins) / sum(losses)), 3) if losses else (99.0 if wins else 0.0)
    max_loss_streak = 0
    cur = 0
    for p in pcts:
        if p < 0:
            cur += 1
            max_loss_streak = max(max_loss_streak, cur)
        else:
            cur = 0
    hist = state.get("equity_history", [])
    peak = 0.0
    max_drawdown = 0.0
    for item in hist:
        eq = float(item.get("equity") or 0)
        peak = max(peak, eq)
        if peak:
            dd = (peak - eq) / peak
            max_drawdown = max(max_drawdown, dd)
    return {
        "cash": round(cash, 2),
        "holdings_value": round(holdings_value, 2),
        "equity": round(equity, 2),
        "return_pct": round((equity / float(state["initial_capital"]) - 1) * 100, 2),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_loss_streak": max_loss_streak,
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "trades": len(trades),
        "risk": state["risk"],
        "equity_history": hist[-30:],
    }


def update_risk(risk):
    state = load_state()
    merged = dict(DEFAULT["risk"])
    merged.update({k: v for k, v in (risk or {}).items() if v is not None})
    state["risk"] = merged
    save_state(state)
    return state["risk"]


def record_equity():
    state = load_state()
    hist = state.setdefault("equity_history", [])
    today = do_t.datetime.now(do_t.CN_TZ).strftime("%Y-%m-%d")
    equity = compute()["equity"]
    if hist and hist[-1].get("date") == today:
        hist[-1]["equity"] = equity
    else:
        hist.append({"date": today, "equity": equity})
    state["equity_history"] = hist[-500:]
    save_state(state)


def can_trade(side):
    state = load_state()
    risk = state["risk"]
    st = do_t.load_state()
    today = do_t.datetime.now(do_t.CN_TZ).strftime("%Y-%m-%d")
    count = sum(1 for t in st.get("trades", [])
                if str(t.get("entry_date") or t.get("time") or "")[:10] == today)
    if count >= int(risk.get("max_daily_trades", 3)):
        return False, "当日交易次数已达上限"
    if side == "buy":
        stop = float(risk.get("stop_loss_pct", 2.0))
        for h in st.get("holdings", []):
            cost = float(h.get("cost") or 0)
            price = float(h.get("price") or cost)
            if cost and price <= cost * (1 - stop / 100):
                return False, "持仓已触发止损线，暂停买入"
    return True, ""

# -*- coding: utf-8 -*-
"""A股高开雷达：轻量 Flask 后端 + 单页前端。"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, jsonify, request, send_from_directory

import astock_data as ad
import datahub
import extra_data as ex
import gap_model
import gap_pick

app = Flask(__name__, static_folder="frontend", static_url_path="")

_OVERVIEW_CACHE = {"ts": 0.0, "data": None, "err": None}
_OVERVIEW_LOCK = threading.Lock()
_OVERVIEW_TTL = 30
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def cached(key, ttl, fn):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception as e:
        val = {"error": str(e)}
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)
    return val


def _overview():
    indices = datahub.tencent_quote(
        ["sh000001", "sz399001", "sz399006", "sh000300"]
    )
    try:
        sentiment = datahub.market_sentiment()
    except Exception as e:
        sentiment = {"error": str(e)}
    try:
        flow = datahub.board_fund_flow("industry", "today", 8)
    except Exception as e:
        flow = {"error": str(e), "rows": []}
    return {
        "ts": int(time.time()),
        "indices": indices,
        "sentiment": sentiment,
        "board_flow": flow,
    }


def get_overview():
    with _OVERVIEW_LOCK:
        hit = _OVERVIEW_CACHE
        if hit["data"] and time.time() - hit["ts"] < _OVERVIEW_TTL:
            return hit["data"]
    try:
        data = _overview()
        with _OVERVIEW_LOCK:
            _OVERVIEW_CACHE.update(ts=time.time(), data=data, err=None)
        return data
    except Exception as e:
        with _OVERVIEW_LOCK:
            _OVERVIEW_CACHE["err"] = str(e)
        return {"ts": int(time.time()), "indices": {}, "sentiment": {}, "board_flow": {},
                "error": str(e)}


@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})


@app.route("/api/overview")
def api_overview():
    return jsonify(get_overview())


@app.route("/api/gap")
def api_gap():
    data = gap_pick.get_cache()
    return jsonify({
        "data": data,
        "computing": gap_pick.is_computing(),
        "ts": gap_pick.cache_ts(),
        "last_err": gap_pick.last_err(),
        "model": gap_model.meta(),
    })


@app.route("/api/gap/refresh", methods=["POST"])
def api_gap_refresh():
    started = gap_pick.trigger_refresh()
    return jsonify({"started": started, "computing": gap_pick.is_computing()})


@app.route("/api/pools")
def api_pools():
    def load():
        date = ad.cn_today()
        return {
            "date": date,
            "zt": ad.em_zt_pool(date),
            "zb": ad.em_zb_pool(date),
            "dt": ad.em_dt_pool(date),
            "yzt": ad.em_yzt_pool(date),
            "monitor": ex.em_stock_monitor(),
            "anomaly": ex.em_price_anomaly(100),
        }
    return jsonify(cached("pools", 120, load))


@app.route("/api/board_flow")
def api_board_flow():
    board_type = request.args.get("type", "industry")
    period = request.args.get("period", "today")
    try:
        top = min(int(request.args.get("top", "10")), 20)
    except ValueError:
        top = 10
    return jsonify(cached(
        f"bf:{board_type}:{period}:{top}", 120,
        lambda: ad.board_fund_flow(board_type, period, top),
    ))


@app.route("/api/hot")
def api_hot():
    def load():
        return {
            "ths": ad.ths_hot_list(),
            "em_rank": ad.em_hot_rank(20),
            "telegraph": ad.cls_telegraph(30),
            "global": ad.eastmoney_global_news(20),
        }
    return jsonify(cached("hot", 120, load))


@app.route("/api/lhb")
def api_lhb():
    trade_date = request.args.get("date") or ex.cn_today_iso()
    return jsonify(cached(f"lhb:{trade_date}", 300,
                          lambda: ex.daily_dragon_tiger(trade_date)))


@app.route("/api/stock/<code>")
def api_stock(code):
    code = ex.norm_code(code)
    key = f"stock:{code}"

    def load():
        def safe(fn, default):
            try:
                return fn() or default
            except Exception:
                return default
        with ThreadPoolExecutor(max_workers=4) as pool:
            f_quote = pool.submit(lambda: safe(lambda: ad.tencent_quote([code]).get(code), {}))
            f_info = pool.submit(lambda: safe(lambda: ex.eastmoney_stock_info(code), {}))
            f_eps = pool.submit(lambda: safe(lambda: ex.ths_eps_forecast(code), []))
            f_fin = pool.submit(lambda: safe(lambda: ex.sina_financial_report(code, "lrb", 4), []))
            f_ann = pool.submit(lambda: safe(lambda: ex.cninfo_announcements(code, 10), []))
            f_extra = pool.submit(lambda: safe(lambda: ad.stock_extra(code), {}))
        return {
            "code": code,
            "quote": f_quote.result(),
            "info": f_info.result(),
            "eps": f_eps.result(),
            "finance": f_fin.result(),
            "announcements": f_ann.result(),
            "extra": f_extra.result(),
        }
    return jsonify(cached(key, 600, load))


@app.route("/api/options")
def api_options():
    etf = request.args.get("etf", "510050")
    key = f"opt:{etf}"

    def load():
        calls = ex.sina_option_codes(etf, True)
        puts = ex.sina_option_codes(etf, False)
        months = list(calls)
        if not months:
            return {"etf": etf, "month": "", "rows": []}
        month = months[0]
        call_codes = calls[month]
        put_codes = puts.get(month, [])
        n = 5
        start = max(0, len(call_codes) // 2 - n // 2)
        rows = []
        for i in range(start, min(start + n, len(call_codes))):
            cc = call_codes[i]
            pp = put_codes[i] if i < len(put_codes) else ""
            cq = ex.sina_option_tquote(cc)
            cg = ex.sina_option_greeks(cc)
            pq = ex.sina_option_tquote(pp) if pp else {}
            pg = ex.sina_option_greeks(pp) if pp else {}
            rows.append({
                "strike": cq.get("strike") or pq.get("strike"),
                "call": {**cq, **cg},
                "put": {**pq, **pg},
            })
        return {"etf": etf, "month": month, "rows": rows}
    return jsonify(cached(key, 600, load))


def start_background():
    def _warm():
        time.sleep(1)
        try:
            get_overview()
        except Exception:
            pass
        try:
            gap_pick.trigger_refresh()
        except Exception as e:
            print(f"[background] gap warm err: {e}", flush=True)

    threading.Thread(target=_warm, daemon=True).start()


if __name__ == "__main__":
    start_background()
    app.run(host="127.0.0.1", port=int(__import__("os").environ.get("APANEL_PORT", "5050")),
            threaded=True)

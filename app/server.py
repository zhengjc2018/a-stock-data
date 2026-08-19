# -*- coding: utf-8 -*-
"""A股高开雷达：轻量 Flask 后端 + 单页前端。"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, request, send_from_directory

import astock_data as ad
import datahub
import daily_loop
import do_t
import extra_data as ex
import gap_model
import gap_pick
import paths as app_paths
import portfolio
import tail_model

app = Flask(__name__, static_folder="frontend", static_url_path="")

_OVERVIEW_CACHE = {"ts": 0.0, "data": None, "err": None}
_OVERVIEW_LOCK = threading.Lock()
_OVERVIEW_TTL = 30
_CACHE = {}
_CACHE_LOCK = threading.Lock()
GAP_SCOPE = {"main": True, "chi_next": False, "st": False}
_RETRAIN_STATE = {
    "running": False,
    "started": 0,
    "finished": 0,
    "result": None,
    "err": None,
}
_RETRAIN_LOCK = threading.Lock()
_NEWS_NOTIFICATIONS = []
_NEWS_LAST = {"ths": set(), "em": set(), "telegraph": set(), "global": set()}


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


def _load_hot():
    return {
        "ths": ad.ths_hot_list(),
        "em_rank": ad.em_hot_rank(20),
        "telegraph": ad.cls_telegraph(30),
        "global": ad.eastmoney_global_news(20),
    }


def _diff_new(key, items, idfn):
    fresh = []
    seen = set(_NEWS_LAST.get(key, set()))
    for it in items:
        ident = idfn(it)
        if ident not in seen:
            fresh.append(it)
            seen.add(ident)
    _NEWS_LAST[key] = seen
    return fresh


def _refresh_news():
    try:
        d = cached("hot", 120, _load_hot)
    except Exception:
        return
    for it in _diff_new("ths", d.get("ths", []),
                        lambda x: f"{x.get('code')}:{x.get('rank')}"):
        msg = f"热榜 {it.get('rank')}. {it.get('name')} {it.get('pct')}%"
        do_t.notify("A股热榜", msg)
        _NEWS_NOTIFICATIONS.insert(0, {"ts": time.time(), "kind": "hot",
                                        "title": "A股热榜", "content": msg})
    for it in _diff_new("telegraph", d.get("telegraph", []),
                        lambda x: x.get("title") or x.get("content", "")[:40]):
        msg = str(it.get("title") or it.get("content") or "")[:80]
        do_t.notify("财联社电报", msg)
        _NEWS_NOTIFICATIONS.insert(0, {"ts": time.time(), "kind": "telegraph",
                                        "title": "财联社电报", "content": msg})
    for it in _diff_new("global", d.get("global", []),
                        lambda x: x.get("title", "")[:60]):
        msg = str(it.get("title") or "")[:80]
        do_t.notify("全球资讯", msg)
        _NEWS_NOTIFICATIONS.insert(0, {"ts": time.time(), "kind": "global",
                                        "title": "全球资讯", "content": msg})
    del _NEWS_NOTIFICATIONS[50:]


def _news_loop():
    while True:
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            h = now.hour + now.minute / 60
            if (9.5 <= h < 11.5) or (13 <= h < 15):
                _refresh_news()
        except Exception:
            pass
        time.sleep(120)


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
    data = gap_pick.get_cache(GAP_SCOPE, trigger=False)
    return jsonify({
        "data": data,
        "computing": gap_pick.is_computing(),
        "ts": gap_pick.cache_ts(),
        "last_err": gap_pick.last_err(),
        "model": gap_model.meta(),
        "tail_model": tail_model.meta(),
        "scope": GAP_SCOPE,
        "stats": _outcome_stats(),
    })


@app.route("/api/gap/refresh", methods=["POST"])
def api_gap_refresh():
    started = gap_pick.trigger_refresh(GAP_SCOPE)
    return jsonify({"started": started, "computing": gap_pick.is_computing()})


@app.route("/api/gap/premarket")
def api_gap_premarket():
    now = datetime.now(timezone(timedelta(hours=8)))
    if now.hour < 9 or (now.hour == 9 and now.minute < 25):
        return jsonify({"status": "before_auction",
                        "msg": "竞价未开始，9:25 后可用"})
    if now.hour >= 10:
        return jsonify({"status": "after_auction",
                        "msg": "已过竞价时段（9:25-10:00 可用）"})
    data = gap_pick.get_cache(GAP_SCOPE, trigger=False)
    if not data or not data.get("candidates"):
        return jsonify({"status": "no_data", "msg": "请先点击「立即计算」生成候选"})
    cands = data["candidates"][:10]
    quotes = datahub.tencent_quote([c["code"] for c in cands])
    confirmed = []
    for c in cands:
        q = quotes.get(c["code"]) or {}
        open_ = q.get("open") or 0
        prev = c.get("price") or q.get("last_close") or 0
        if not open_ or not prev:
            continue
        gap_pct = (open_ / prev - 1) * 100
        vol = q.get("volume") or 0
        prev_day_vol = 0
        secid = f"{'1' if c['code'].startswith('6') else '0'}.{c['code']}"
        try:
            bars = datahub._klines(secid, 101, 5)
            if bars:
                prev_day_vol = float(bars[-1].get("vol") or 0)
        except Exception:
            pass
        auction_ratio = vol / (prev_day_vol / 48) if prev_day_vol else None
        base = c.get("enhanced_prob") or c.get("prob") or 0
        boost = 0.0
        status = "保留"
        if 0.5 <= gap_pct <= 5:
            boost += 0.02
        elif gap_pct > 7:
            boost -= 0.05
            status = "降级"
        elif gap_pct < 0.5:
            boost -= 0.03
            status = "降级"
        if auction_ratio is not None and auction_ratio > 1.2:
            boost += 0.01
        confirmed.append({
            "code": c["code"],
            "name": c.get("name", ""),
            "open": round(open_, 3),
            "gap_pct": round(gap_pct, 2),
            "auction_ratio": round(auction_ratio, 2) if auction_ratio else None,
            "status": status,
            "confirmed_prob": round(max(min(base + boost, 1.0), 0.0), 4),
        })
    confirmed.sort(key=lambda x: x["confirmed_prob"], reverse=True)
    return jsonify({
        "status": "ok",
        "time": now.strftime("%H:%M"),
        "confirmed": confirmed[:3],
    })


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
    return jsonify(cached("hot", 120, _load_hot))


@app.route("/api/notifications")
def api_notifications():
    return jsonify(_NEWS_NOTIFICATIONS)


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


def _outcome_stats():
    out_dir = app_paths.data_path("outcomes")
    days = []
    if os.path.isdir(out_dir):
        for name in sorted(os.listdir(out_dir)):
            if not name.startswith("hits_") or not name.endswith(".json"):
                continue
            date = name.replace("hits_", "").replace(".json", "")
            try:
                with open(os.path.join(out_dir, name), encoding="utf-8") as f:
                    hits = json.load(f)
            except Exception:
                continue
            results = hits.get("results", [])
            if not results:
                continue
            hit = [1 if r.get("hit") else 0 for r in results]
            def topk(k):
                return 1 if any(hit[:k]) else 0
            days.append({
                "date": date,
                "total": len(results),
                "hits": sum(hit),
                "top1": topk(1),
                "top3": topk(3),
                "top10": topk(10),
            })
    last_days = days[-30:]
    def avg(key):
        vals = [d[key] for d in last_days]
        return round(sum(vals) / len(vals), 4) if vals else None
    total_results = sum(d["total"] for d in last_days)
    total_hits = sum(d["hits"] for d in last_days)
    return {
        "verified_days": len(days),
        "recent_days": len(last_days),
        "total_results": total_results,
        "base_rate": round(total_hits / total_results, 4) if total_results else None,
        "top1_rate": avg("top1"),
        "top3_rate": avg("top3"),
        "top10_rate": avg("top10"),
        "recent": last_days[-10:],
    }


@app.route("/api/strategy_health")
def api_strategy_health():
    history = []
    history_path = app_paths.bundle_path("model_history.json")
    if os.path.isfile(history_path):
        try:
            with open(history_path, encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    return jsonify({
        "model": gap_model.meta(),
        "tail_model": tail_model.meta(),
        "history": history[-20:],
        "stats": _outcome_stats(),
        "t_stats": do_t.t_stats(),
    })


@app.route("/api/retrain", methods=["POST"])
def api_retrain():
    use_download = False
    try:
        import sklearn  # noqa: F401
    except ImportError:
        use_download = True
    with _RETRAIN_LOCK:
        if _RETRAIN_STATE["running"]:
            return jsonify(_RETRAIN_STATE), 409
        _RETRAIN_STATE.update(running=True, started=int(time.time()), finished=0,
                              result=None, err=None)

    def _run():
        try:
            import auto_train
            if use_download:
                metrics = auto_train.download_model()
                result = {"action": "remote_update", "metrics": metrics}
            else:
                result = auto_train.main()
            with _RETRAIN_LOCK:
                _RETRAIN_STATE.update(running=False, finished=int(time.time()), result=result)
        except Exception as e:
            with _RETRAIN_LOCK:
                _RETRAIN_STATE.update(running=False, finished=int(time.time()), err=str(e))

    threading.Thread(target=_run, daemon=True).start()
    return jsonify(_RETRAIN_STATE)


@app.route("/api/retrain/status")
def api_retrain_status():
    return jsonify(_RETRAIN_STATE)


@app.route("/api/t/state")
def api_t_state():
    return jsonify(do_t.load_state())


@app.route("/api/t/holdings", methods=["POST"])
def api_t_add_holding():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get("code", "")).zfill(6)
    if len(code) != 6:
        return jsonify({"error": "code required"}), 400
    return jsonify(do_t.add_holding(
        code, body.get("name"), body.get("cost", 0), body.get("qty", 0)))


@app.route("/api/t/holdings/delete", methods=["POST"])
def api_t_delete_holding():
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(do_t.delete_holding(body.get("id", "")))


@app.route("/api/t/start", methods=["POST"])
def api_t_start():
    do_t.start()
    return jsonify(do_t.load_state())


@app.route("/api/t/stop", methods=["POST"])
def api_t_stop():
    do_t.stop()
    return jsonify(do_t.load_state())


@app.route("/api/t/check", methods=["POST"])
def api_t_check():
    return jsonify(do_t.check_once())


@app.route("/api/search")
def api_search():
    return jsonify(do_t.search_stocks(request.args.get("q", "")))


@app.route("/api/portfolio")
def api_portfolio():
    return jsonify(portfolio.compute())


@app.route("/api/portfolio/settings", methods=["POST"])
def api_portfolio_settings():
    body = request.get_json(force=True, silent=True) or {}
    return jsonify(portfolio.update_risk(body.get("risk") or {}))


def start_background():
    threading.Thread(target=_news_loop, daemon=True).start()
    def _warm():
        time.sleep(1)
        try:
            get_overview()
        except Exception:
            pass
        try:
            do_t.ensure_analysis()
        except Exception:
            pass
        try:
            do_t.verify_ledger()
        except Exception:
            pass
        try:
            if do_t.load_state().get("monitoring"):
                do_t.start()
        except Exception:
            pass
        try:
            daily_loop.verify_pending()
        except Exception:
            pass
        try:
            now = datetime.now(timezone(timedelta(hours=8)))
            if now.hour >= 18:
                daily_loop.record_candidates()
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True).start()


if __name__ == "__main__":
    start_background()
    app.run(host="127.0.0.1", port=int(__import__("os").environ.get("APANEL_PORT", "5050")),
            threaded=True)

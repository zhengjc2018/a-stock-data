# -*- coding: utf-8 -*-
"""A股高开雷达：轻量 Flask 后端 + 单页前端。"""
from __future__ import annotations

import threading
import time

from flask import Flask, jsonify, send_from_directory

import datahub
import gap_model
import gap_pick

app = Flask(__name__, static_folder="frontend", static_url_path="")

_OVERVIEW_CACHE = {"ts": 0.0, "data": None, "err": None}
_OVERVIEW_LOCK = threading.Lock()
_OVERVIEW_TTL = 30


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

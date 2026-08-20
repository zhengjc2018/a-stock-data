# -*- coding: utf-8 -*-
"""a-stock-data 端点精简移植 + HKS K 线兜底，供独立仪表盘使用。

东财请求统一走 em_get() 串行限流；行情优先通达信，失败回退新浪/百度。
"""
from __future__ import annotations

import os
import json
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from easy_tdx import Period

import tdx_source as tdx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"
SINA_HDR = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
NO_PROXY = {"http": None, "https": None}

_EM_SESSION = requests.Session()
_EM_SESSION.headers.update({"User-Agent": UA})
_EM_LOCK = threading.Lock()
_EM_LAST = [0.0]
EM_MIN_INTERVAL = 0.8

_req_lock = threading.Lock()
_last_req = 0.0

_TDX_LOCK = threading.Lock()


def em_get(base, path, params, timeout=8, retries=2):
    """东财统一请求入口：串行限流 + 会话复用 + 指数退避重试。"""
    delay = 0.3
    for _ in range(retries + 1):
        with _EM_LOCK:
            wait = EM_MIN_INTERVAL - (time.time() - _EM_LAST[0])
            if wait > 0:
                time.sleep(wait + random.uniform(0.05, 0.25))
            try:
                r = _EM_SESSION.get(
                    base + path,
                    params=params,
                    headers={"User-Agent": UA},
                    timeout=timeout,
                    proxies=NO_PROXY,
                )
                if r.status_code != 200:
                    time.sleep(delay)
                    delay *= 2
                    continue
                return r.json()
            except Exception:
                time.sleep(delay)
                delay *= 2
            finally:
                _EM_LAST[0] = time.time()
    return None


def cn_today(fmt="%Y%m%d"):
    return datetime.now(timezone(timedelta(hours=8))).strftime(fmt)


# ---------------------------------------------------------------------------
# 打板情绪
# ---------------------------------------------------------------------------
def _fmt_zt_time(t):
    s = str(t or "").zfill(6)
    if len(s) < 6:
        return ""
    return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"


def _em_zt_api(endpoint, sort, date):
    url = f"https://push2ex.eastmoney.com/{endpoint}"
    params = {
        "ut": ZTB_UT,
        "dpt": "wz.ztzt",
        "Pageindex": 0,
        "pagesize": 10000,
        "sort": sort,
        "date": date,
    }
    data = em_get("https://push2ex.eastmoney.com", f"/{endpoint}", params, timeout=10)
    return (data or {}).get("data") or {}


def _zt_stat(p):
    zttj = p.get("zttj") or {}
    return f'{zttj.get("days", "?")}天{zttj.get("ct", "?")}板'


def em_zt_pool(date):
    out = []
    pool = _em_zt_api("getTopicZTPool", "fbt:asc", date).get("pool") or []
    for p in pool:
        try:
            out.append({
                "code": str(p.get("c", "")).zfill(6),
                "name": p.get("n", ""),
                "price": (p.get("p") or 0) / 1000,
                "pct": round(p.get("zdp") or 0, 2),
                "limit_days": p.get("lbc") or 0,
                "first_seal": _fmt_zt_time(p.get("fbt")),
                "last_seal": _fmt_zt_time(p.get("lbt")),
                "seal_fund": p.get("fund") or 0,
                "break_times": p.get("zbc") or 0,
                "industry": p.get("hybk", ""),
                "zt_stat": _zt_stat(p),
            })
        except Exception:
            continue
    return out


def em_zb_pool(date):
    out = []
    pool = _em_zt_api("getTopicZBPool", "fbt:asc", date).get("pool") or []
    for p in pool:
        try:
            out.append({
                "code": str(p.get("c", "")).zfill(6),
                "name": p.get("n", ""),
                "price": (p.get("p") or 0) / 1000,
                "pct": round(p.get("zdp") or 0, 2),
                "first_seal": _fmt_zt_time(p.get("fbt")),
                "break_times": p.get("zbc") or 0,
                "industry": p.get("hybk", ""),
                "zt_stat": _zt_stat(p),
            })
        except Exception:
            continue
    return out


def em_dt_pool(date):
    out = []
    pool = _em_zt_api("getTopicDTPool", "fund:asc", date).get("pool") or []
    for p in pool:
        try:
            out.append({
                "code": str(p.get("c", "")).zfill(6),
                "name": p.get("n", ""),
                "price": (p.get("p") or 0) / 1000,
                "pct": round(p.get("zdp") or 0, 2),
                "seal_fund": p.get("fund") or 0,
                "last_seal": _fmt_zt_time(p.get("lbt")),
                "dt_days": p.get("days") or 0,
                "open_times": p.get("oc") or 0,
                "industry": p.get("hybk", ""),
            })
        except Exception:
            continue
    return out


def em_yzt_pool(date):
    out = []
    pool = _em_zt_api("getYesterdayZTPool", "zs:desc", date).get("pool") or []
    for p in pool:
        try:
            out.append({
                "code": str(p.get("c", "")).zfill(6),
                "name": p.get("n", ""),
                "price": (p.get("p") or 0) / 1000,
                "pct": round(p.get("zdp") or 0, 2),
                "turnover": round(p.get("hs") or 0, 2),
                "amplitude": round(p.get("zf") or 0, 2),
                "speed": round(p.get("zs") or 0, 2),
                "y_first_seal": _fmt_zt_time(p.get("yfbt")),
                "y_limit_days": p.get("ylbc") or 0,
                "industry": p.get("hybk", ""),
                "zt_stat": _zt_stat(p),
            })
        except Exception:
            continue
    return out


def limit_up_sentiment(date=None):
    date = date or cn_today()
    zt = em_zt_pool(date)
    zb = em_zb_pool(date)
    dt = em_dt_pool(date)
    ladder = {}
    for s in zt:
        ladder[s["limit_days"]] = ladder.get(s["limit_days"], 0) + 1
    return {
        "date": date,
        "zt_count": len(zt),
        "zb_count": len(zb),
        "dt_count": len(dt),
        "break_rate": round(len(zb) / (len(zt) + len(zb)) * 100, 1) if (zt or zb) else 0,
        "max_height": max((s["limit_days"] for s in zt), default=0),
        "ladder": dict(sorted(ladder.items())),
    }


def market_sentiment():
    date = cn_today()
    data = limit_up_sentiment(date)
    yzt = em_yzt_pool(date)
    data["yzt_count"] = len(yzt)
    data["promotion_rate"] = None
    if yzt:
        promoted = sum(1 for x in yzt if (x.get("pct") or 0) >= 9.8)
        data["promotion_rate"] = round(promoted / len(yzt) * 100, 1)
    return data


# ---------------------------------------------------------------------------
# 板块资金流
# ---------------------------------------------------------------------------
_BOARD_FS = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}
_BOARD_PERIOD = {
    "today": ("f62", "f62", "f184", "f3", "f204"),
}


def board_fund_flow(board_type="industry", period="today", top_n=8):
    if board_type not in _BOARD_FS or period not in _BOARD_PERIOD:
        return {"total": 0, "rows": []}
    fid, f_main, f_pct, f_chg, f_leader = _BOARD_PERIOD[period]
    fields = ["f12", "f14", f_chg, f_main, f_pct, f_leader]
    url = "https://push2delay.eastmoney.com/api/qt/clist/get"
    base = {
        "pn": "1",
        "pz": "200",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": fid,
        "fs": _BOARD_FS[board_type],
        "fields": ",".join(dict.fromkeys(fields)),
    }
    data = em_get("https://push2delay.eastmoney.com", "/api/qt/clist/get", base, timeout=15)
    d = data or {}
    diff = (d.get("data") or {}).get("diff") or []
    if not diff:
        return {"total": 0, "rows": []}
    rows = []
    for i, it in enumerate(diff[:top_n]):
        rows.append({
            "rank": i + 1,
            "name": it.get("f14", ""),
            "code": it.get("f12", ""),
            "change_pct": it.get(f_chg, 0),
            "main_net": it.get(f_main, 0),
            "main_pct": it.get(f_pct, 0),
            "leader": it.get(f_leader, ""),
        })
    return {"total": len(diff), "rows": rows}


# ---------------------------------------------------------------------------
# 腾讯行情
# ---------------------------------------------------------------------------
def tencent_quote(codes):
    """腾讯财经批量行情：PE/PB/市值/涨跌幅等。"""
    sh_index = {"000300", "000905", "000016", "000688", "000852", "000010"}
    prefixed = []
    key_of = {}
    for c in codes:
        low = str(c).lower()
        if low.startswith(("sh", "sz", "bj")):
            p = low
        elif str(c).startswith("92"):
            p = f"bj{c}"
        elif str(c) in sh_index or str(c).startswith(("5", "6", "9")):
            p = f"sh{c}"
        elif str(c).startswith(("4", "8")):
            p = f"bj{c}"
        else:
            p = f"sz{c}"
        prefixed.append(p)
        key_of[p] = str(c)
    try:
        r = requests.get(
            "https://qt.gtimg.cn/q=" + ",".join(prefixed),
            headers={"User-Agent": UA},
            timeout=10,
            proxies=NO_PROXY,
        )
        r.encoding = "gbk"
        text = r.text
    except Exception:
        return {}
    result = {}
    for line in text.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key_of.get(key, key[2:])

        def _f(idx):
            try:
                return float(vals[idx])
            except (TypeError, ValueError, IndexError):
                return 0.0

        result[code] = {
            "name": vals[1] if len(vals) > 1 else "",
            "price": _f(3),
            "last_close": _f(4),
            "open": _f(5),
            "volume": _f(6),
            "change_pct": _f(32),
            "high": _f(33),
            "low": _f(34),
            "turnover_pct": _f(38),
            "pe_ttm": _f(39),
            "float_mcap_yi": _f(44),
            "mcap_yi": _f(45),
            "pb": _f(46),
            "limit_up": _f(47),
            "limit_down": _f(48),
        }
    return result


# ---------------------------------------------------------------------------
# K 线：通达信优先，失败回退新浪/百度
# ---------------------------------------------------------------------------
def _rate_limit(gap=0.06):
    global _last_req
    with _req_lock:
        now = time.time()
        wait = gap - (now - _last_req)
        if wait > 0:
            time.sleep(wait)
        _last_req = time.time()


def _call_timeout(fn, seconds, default=None, label=""):
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=seconds)
        except Exception as e:
            print(f"[err] {label or getattr(fn, '__name__', '?')}: {e}")
            return default
    finally:
        ex.shutdown(wait=False)


def _sina_symbol(secid):
    m, code = secid.split(".")
    return ("sh" if m == "1" else "sz") + code


def _sina_to_internal(rows):
    out, prev = [], None
    for r in rows or []:
        try:
            day = r.get("day") or r.get("date") or ""
            o, c = float(r["open"]), float(r["close"])
            h, l, v = float(r["high"]), float(r["low"]), float(r["volume"])
            amt = float(r.get("amount") or 0.0)
            if amt <= 0:
                amt = c * v
        except (KeyError, TypeError, ValueError):
            continue
        pct = (c - prev) / prev * 100 if prev else 0.0
        amp = (h - l) / c * 100 if c else 0.0
        out.append({"date": day, "open": o, "close": c, "high": h, "low": l,
                    "vol": v, "amount": amt, "amp": round(amp, 2),
                    "pct": round(pct, 2), "change": round(c - prev, 2) if prev else 0.0,
                    "turnover": 0.0})
        prev = c
    return out


def _sina_k(sym, scale, lmt):
    _rate_limit()
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData")
    try:
        r = requests.get(url, params={"symbol": sym, "scale": scale,
                                      "ma": 5, "datalen": lmt},
                         headers=SINA_HDR, timeout=10, proxies=NO_PROXY)
        return r.json() or []
    except Exception:
        return []


def _sina_k2(sym, scale, lmt):
    """新浪新版K线接口：比旧 money.finance 端点稳定，返回 JSONP。"""
    _rate_limit()
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/"
           "var%20_data=/CN_MarketDataService.getKLineData")
    try:
        r = requests.get(url, params={"symbol": sym, "scale": str(scale),
                                      "ma": "no", "datalen": str(lmt)},
                         headers=SINA_HDR, timeout=8, proxies=NO_PROXY)
        text = r.text
    except Exception:
        return []
    if "([" not in text or "])" not in text:
        return []
    try:
        payload = text[text.index("([") + 1:text.rindex("])") + 1]
        return json.loads(payload)
    except Exception:
        return []


def _em_klines(secid, klt=101, lmt=800):
    """东财历史日K兜底：tdx/新浪/百度都失败时仍可训练回测。"""
    fields2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    data = em_get(
        "https://push2his.eastmoney.com",
        "/api/qt/stock/kline/get",
        {
            "secid": secid,
            "klt": str(klt),
            "fqt": "1",
            "beg": "20180101",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": fields2,
        },
        timeout=12,
        retries=2,
    )
    lines = ((data or {}).get("data") or {}).get("klines") or []
    rows = []
    for line in lines[-lmt:]:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        try:
            rows.append({
                "date": parts[0],
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5]),
                "amount": float(parts[6]),
            })
        except (TypeError, ValueError):
            continue
    return rows


def baidu_kline(code, lmt=240):
    """百度股市通日 K 线兜底。"""
    code = str(code).zfill(6)
    url = "https://finance.pae.baidu.com/selfselect/getstockquotation"
    params = {
        "all": "1",
        "isIndex": "false",
        "isBk": "false",
        "isBlock": "false",
        "isFutures": "false",
        "isStock": "true",
        "newFormat": "1",
        "group": "quotation_kline_ab",
        "finClientType": "pc",
        "code": code,
        "start_time": "",
        "ktype": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/vnd.finance-web.v1+json",
        "Origin": "https://gushitong.baidu.com",
        "Referer": "https://gushitong.baidu.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10, proxies=NO_PROXY)
        r.raise_for_status()
        d = r.json()
        md = (d.get("Result") or {}).get("newMarketData") or {}
        keys = md.get("keys", [])
        raw = str(md.get("marketData", ""))
    except Exception:
        return []
    rows = []
    for line in raw.split(";"):
        parts = line.split(",")
        if len(parts) < 7:
            continue
        item = dict(zip(keys, parts))
        try:
            rows.append({
                "day": str(item.get("time", ""))[:10],
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0) or 0),
                "amount": float(item.get("amount", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    return rows[-lmt:]


_PERIOD = {
    101: Period.DAILY,
    102: Period.WEEKLY,
    5: Period.MIN_5,
    15: Period.MIN_15,
    60: Period.MIN_60,
}


def _tdx_klines(secid, klt, lmt):
    m, code = secid.split(".")
    df = _call_timeout(
        lambda: tdx.kline(m, code, period=_PERIOD.get(klt, Period.DAILY), count=lmt),
        8,
        None,
        "tdx.kline",
    )
    if df is None:
        raise RuntimeError("tdx.kline 超时/无数据")
    rows, prev = [], None
    for _, r in df.iterrows():
        o = float(r["open"]); c = float(r["close"])
        h = float(r["high"]); l = float(r["low"])
        v = float(r["vol"]); amt = float(r.get("amount") or 0.0)
        row = {"date": str(r["datetime"])[:10], "open": o, "close": c,
               "high": h, "low": l, "vol": v, "amount": amt,
               "amp": round((h - l) / c * 100, 2) if c else 0.0,
               "pct": 0.0, "change": 0.0, "turnover": 0.0}
        if prev:
            row["pct"] = round((c - prev) / prev * 100, 2)
            row["change"] = round(c - prev, 2)
        prev = c
        rows.append(row)
    return rows


def _klines(secid, klt, lmt):
    """统一 K 线入口：通达信优先，失败回退新浪/百度。"""
    if os.environ.get("ASTOCK_NO_TDX") == "1" and klt == 101:
        out = _sina_to_internal(_sina_k2(_sina_symbol(secid), 240, lmt))
        if out:
            return out
        out = _sina_to_internal(_em_klines(secid, klt, lmt))
        if out:
            return out
        out = _sina_to_internal(_sina_k(_sina_symbol(secid), 240, lmt))
        if out:
            return out
        return _sina_to_internal(baidu_kline(secid.split(".")[-1], lmt))
    try:
        if tdx.available():
            return _tdx_klines(secid, klt, lmt)
    except Exception as e:
        print("[tdx] klines fallback -> sina:", e)
    sym = _sina_symbol(secid)
    scale = {101: 240, 5: 5, 60: 60, 15: 15}.get(klt, 240)
    out = _sina_to_internal(_sina_k2(sym, scale, lmt))
    if not out:
        out = _sina_to_internal(_sina_k(sym, scale, lmt))
    if not out and klt == 101:
        out = _sina_to_internal(_em_klines(secid, klt, lmt))
        if not out:
            out = _sina_to_internal(baidu_kline(secid.split(".")[-1], lmt))
    return out

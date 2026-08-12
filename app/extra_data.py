# -*- coding: utf-8 -*-
"""SKILL.md 补充端点：一致预期/全市场龙虎榜/基本面/财报/公告/监控/异动/ETF期权。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from io import StringIO

import pandas as pd
import requests

from astock_data import UA, em_get, eastmoney_datacenter, _norm_code, _NO_PROXY

CN_TZ = timezone(timedelta(hours=8))


def cn_today_iso() -> str:
    return datetime.now(CN_TZ).date().isoformat()


def norm_code(code):
    return _norm_code(code)


# ---------------------------------------------------------------------------
# 同花顺一致预期 EPS
# ---------------------------------------------------------------------------
def ths_eps_forecast(code):
    code = norm_code(code)
    url = f"https://basic.10jqka.com.cn/new/{code}/worth.html"
    headers = {
        "User-Agent": UA,
        "Referer": "https://basic.10jqka.com.cn/",
    }
    r = requests.get(url, headers=headers, timeout=15, proxies=_NO_PROXY)
    r.encoding = "gbk"
    try:
        dfs = pd.read_html(StringIO(r.text))
    except Exception:
        return []
    for df in dfs:
        cols = [str(c) for c in df.columns]
        if any("每股收益" in c or "均值" in c for c in cols):
            return df.fillna("").to_dict("records")
    return dfs[0].fillna("").to_dict("records") if dfs else []


# ---------------------------------------------------------------------------
# 全市场龙虎榜
# ---------------------------------------------------------------------------
def daily_dragon_tiger(trade_date=None, min_net_buy=None):
    trade_date = trade_date or cn_today_iso()
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{trade_date}')(TRADE_DATE<='{trade_date}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
    )
    if not data:
        return {"date": trade_date, "total_records": 0, "stocks": []}
    actual_date = str(data[0].get("TRADE_DATE", ""))[:10] if data else trade_date
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({
            "code": row.get("SECURITY_CODE", ""),
            "name": row.get("SECURITY_NAME_ABBR", ""),
            "reason": row.get("EXPLANATION", ""),
            "close": row.get("CLOSE_PRICE") or 0,
            "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
            "net_buy_wan": round(net_buy, 1),
            "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
            "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
            "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2),
        })
    return {"date": actual_date, "total_records": len(stocks), "stocks": stocks}


# ---------------------------------------------------------------------------
# 东财个股基本面（push2delay 域名，避免主域空响应）
# ---------------------------------------------------------------------------
def eastmoney_stock_info(code):
    code = norm_code(code)
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2delay.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": f"{market_code}.{code}",
    }
    r = em_get(url, params=params, headers={"User-Agent": UA}, timeout=10)
    d = (r.json() or {}).get("data", {}) or {}
    return {
        "code": d.get("f57", ""),
        "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0),
        "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
    }


# ---------------------------------------------------------------------------
# 新浪财报三表
# ---------------------------------------------------------------------------
def sina_financial_report(code, report_type="lrb", num=8):
    code = norm_code(code)
    prefix = "sh" if code.startswith("6") else "sz"
    paper_code = f"{prefix}{code}"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {
        "paperCode": paper_code,
        "source": report_type,
        "type": "0",
        "page": "1",
        "num": str(num),
    }
    r = requests.get(url, params=params, headers={"User-Agent": UA},
                     timeout=15, proxies=_NO_PROXY)
    report_list = ((r.json() or {}).get("result") or {}).get("data", {}).get("report_list", {}) or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec = {"报告期": f"{period[:4]}-{period[4:6]}-{period[6:8]}"}
        for it in obj.get("data", []) or []:
            title = it.get("item_title", "")
            if not title or it.get("item_value") is None:
                continue
            rec[title] = it.get("item_value")
            tongbi = it.get("item_tongbi")
            if tongbi not in (None, ""):
                rec[title + "_同比"] = tongbi
        rows.append(rec)
    return rows


# ---------------------------------------------------------------------------
# 巨潮公告
# ---------------------------------------------------------------------------
_CNINFO_ORGID_MAP = {}


def _cninfo_ts_to_date(ts):
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    return str(ts)[:10] if ts else ""


def _cninfo_orgid(code):
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             headers={"User-Agent": UA}, timeout=15, proxies=_NO_PROXY)
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"]
                                 for s in r.json().get("stockList", [])}
        except Exception:
            pass
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    if code.startswith("6"):
        return f"gssh0{code}"
    if code.startswith(("8", "4")):
        return f"gsbj0{code}"
    return f"gssz0{code}"


def cninfo_announcements(code, page_size=30):
    code = norm_code(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    org_id = _cninfo_orgid(code)
    payload = {
        "stock": f"{code},{org_id}",
        "tabName": "fulltext",
        "pageSize": str(page_size),
        "pageNum": "1",
        "column": "",
        "category": "",
        "plate": "",
        "seDate": "",
        "searchkey": "",
        "secid": "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://www.cninfo.com.cn/new/disclosure",
        "Origin": "https://www.cninfo.com.cn",
    }
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=15, proxies=_NO_PROXY)
        d = r.json()
    except Exception:
        return []
    rows = []
    for item in d.get("announcements", []) or []:
        rows.append({
            "title": item.get("announcementTitle", ""),
            "type": item.get("announcementTypeName", ""),
            "date": _cninfo_ts_to_date(item.get("announcementTime")),
            "url": f"https://www.cninfo.com.cn/new/disclosure/detail?annoId={item.get('announcementId', '')}",
        })
    return rows


# ---------------------------------------------------------------------------
# 重点监控池 + 日内异动
# ---------------------------------------------------------------------------
MONITOR_URL = "https://mobappconfig.securities.eastmoney.com/emcfg/stock_monitor.json"
_MONITOR_MARKET = {"1": "SH", "0": "SZ", "B": "BJ"}


def em_stock_monitor(only_active=True):
    r = em_get(MONITOR_URL, headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    rows = r.json() or []
    today = cn_today_iso()
    out = []
    for x in rows:
        start, end = x.get("VALIDATESTARTDATE", ""), x.get("VALIDATEENDDATE", "")
        if only_active and not (start <= today <= end):
            continue
        raw_mkt = str(x.get("MARKET", "")).upper()
        out.append({
            "code": x.get("STKCODE", ""),
            "name": x.get("STKNAME", ""),
            "market": _MONITOR_MARKET.get(raw_mkt, f"?{raw_mkt}"),
            "start": start,
            "end": end,
            "link": x.get("LINK_URL", ""),
        })
    return out


ANOMALY_BASE = "https://dycalchis.eastmoney.com/price-anomaly"
HQ_PARAMS = {"team": "h5", "product": "EastMoney", "client": "WAP",
             "version": "9001", "name": "WAP", "user": "123"}
ANOMALY_RULES = {
    1: "主板连续10个交易日内4次出现同向异常波动",
    2: "创业板连续10个交易日内3次出现同向异常波动",
    3: "科创板连续10个交易日内3次出现同向异常波动",
    4: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+100%",
    5: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-50%",
    6: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到+200%",
    7: "连续三十个交易日内日收盘价涨跌幅偏离值累计达到-70%",
    8: "北交所连续10个交易日内3次出现同向异常波动",
    40: "连续十个交易日内日收盘价涨跌幅偏离值累计达到+150%",
    50: "连续十个交易日内日收盘价涨跌幅偏离值累计达到-60%",
    60: "连续30个交易日内日收盘价涨跌幅偏离值累计达到+300%",
    70: "连续30个交易日内日收盘价涨跌幅偏离值累计达到-75%",
}


def _anomaly_market(code, m, board=None):
    c = str(code or "")
    if c.startswith("920") or c[:2] in ("43", "83", "87") or board == 8:
        return "BJ"
    return "SH" if m == 1 else "SZ"


def _anomaly_get(path, page_size, page_no, **extra):
    params = {**HQ_PARAMS, "pageSize": str(page_size), "pageNo": str(page_no), **extra}
    r = em_get(f"{ANOMALY_BASE}/{path}", params=params,
               headers={"Referer": "https://vipmoney.eastmoney.com/"}, timeout=20)
    d = r.json()
    if d.get("result") != 0:
        raise RuntimeError(f"东财异动接口拒绝: result={d.get('result')} msg={d.get('msg')!r}")
    return d


def em_price_anomaly(page_size=200, page_no=1):
    d = _anomaly_get("list", page_size, page_no)
    items = []
    for x in d.get("data") or []:
        e = x.get("e")
        key = e * 10 if (x.get("s") == 6 and e in (4, 5, 6, 7)) else e
        items.append({
            "code": x.get("c"),
            "name": x.get("n"),
            "market": _anomaly_market(x.get("c"), x.get("m"), x.get("s")),
            "change_pct": x.get("a"),
            "deviation": x.get("x"),
            "days": x.get("d"),
            "board": x.get("s"),
            "rule_code": key,
            "rule": ANOMALY_RULES.get(key, f"未知规则码 {key}"),
            "is_today": x.get("o") != 2,
        })
    return {"date": str(d.get("date", "")), "pages": d.get("pages", 0), "items": items}


def em_price_anomaly_count(page_size=50, page_no=1):
    d = _anomaly_get("count", page_size, page_no, sortKey="", sortDir="")
    items = [{
        "code": x.get("c"),
        "name": x.get("n"),
        "market": _anomaly_market(x.get("c"), x.get("m"), x.get("s")),
        "price": x.get("p"),
        "change_pct": x.get("a"),
        "times": x.get("t"),
        "deviation": x.get("x"),
        "days": x.get("d"),
        "board": x.get("s"),
    } for x in d.get("data") or []]
    return {"date": str(d.get("date", "")), "pages": d.get("pages", 0), "items": items}


# ---------------------------------------------------------------------------
# ETF 期权
# ---------------------------------------------------------------------------
SINA_OPT_HDR = {"Referer": "https://stock.finance.sina.com.cn/", "User-Agent": UA}


def _opt_f(x):
    try:
        return float(x)
    except Exception:
        return x


def _sina_opt_list(param):
    r = requests.get(f"https://hq.sinajs.cn/list={param}", headers=SINA_OPT_HDR,
                     timeout=10, proxies=_NO_PROXY)
    r.encoding = "gbk"
    t = r.text
    return t.split('"')[1].split(",") if '"' in t else []


def sina_option_codes(underlying="510050", call=True):
    cate = {"510050": "50ETF", "510300": "300ETF",
            "588000": "科创50ETF", "510500": "500ETF"}.get(underlying, "50ETF")
    url = ("https://stock.finance.sina.com.cn/futures/api/openapi.php/"
           f"StockOptionService.getStockName?exchange=null&cate={cate}")
    try:
        months = requests.get(url, headers=SINA_OPT_HDR, timeout=10,
                              proxies=_NO_PROXY).json()["result"]["data"]["contractMonth"]
    except Exception:
        return {}
    months = [m.replace("-", "")[2:] for m in months[1:]]
    flag = "OP_UP_" if call else "OP_DOWN_"
    out = {}
    for m in months:
        codes = [c.replace("CON_OP_", "") for c in _sina_opt_list(f"{flag}{underlying}{m}")
                 if c.startswith("CON_OP_")]
        if codes:
            out[m] = codes
    return out


def sina_option_tquote(code):
    v = _sina_opt_list(f"CON_OP_{code}")
    if len(v) < 43:
        return {}
    return {
        "bid_vol": _opt_f(v[0]), "bid": _opt_f(v[1]), "last": _opt_f(v[2]),
        "ask": _opt_f(v[3]), "ask_vol": _opt_f(v[4]), "open_interest": _opt_f(v[5]),
        "pct": _opt_f(v[6]), "strike": _opt_f(v[7]), "prev_close": _opt_f(v[8]),
        "open": _opt_f(v[9]), "limit_up": _opt_f(v[10]), "limit_down": _opt_f(v[11]),
        "name": v[37], "amplitude": _opt_f(v[38]), "high": _opt_f(v[39]),
        "low": _opt_f(v[40]), "volume": _opt_f(v[41]), "amount": _opt_f(v[42]),
    }


def sina_option_greeks(code):
    raw = _sina_opt_list(f"CON_SO_{code}")
    if len(raw) < 16:
        return {}
    v = [raw[0]] + raw[4:]
    return {
        "name": v[0], "volume": _opt_f(v[1]), "delta": _opt_f(v[2]),
        "gamma": _opt_f(v[3]), "theta": _opt_f(v[4]), "vega": _opt_f(v[5]),
        "iv": _opt_f(v[6]), "high": _opt_f(v[7]), "low": _opt_f(v[8]),
        "trade_code": v[9], "strike": _opt_f(v[10]), "last": _opt_f(v[11]),
        "theory": _opt_f(v[12]),
    }

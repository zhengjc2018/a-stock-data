# -*- coding: utf-8 -*-
"""次日高开候选（移植 a-trade next_day_candidates 逻辑，数据源走 HKS 统一入口）。

口径：筛选今日未涨停、通过主板/ST/价格/基本面/行业硬过滤的个股，用
量价与板块热度因子等权评分，输出次日 T+1 开盘高开概率较高的 Top N。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd
import requests

import paths
import gap_model
import tail_model

# 数据层统一走 datahub（HKS server 的轻量子集）。
import datahub as server

MAIN_BOARD_PREFIXES = ("000", "001", "002", "600", "601", "603", "605")

BLOCKED_INDUSTRY_KEYWORDS = (
    "白酒",
    "证券",
    "地产",
    "消费",
    "房地产",
    "食品",
    "饮料",
    "零售",
    "商贸",
    "家电",
)

MAX_PRICE = 80.0
MAX_PE_TTM = 100.0
MAX_PB = 5.0
NOT_LIMIT_PCT = 9.8

SCORE_FACTORS_HIGH = (
    "amplitude_pct",
    "pos_ma20",
    "pos_ma60",
    "dist_low60",
    "amount_yi",
    "vol_ratio_5",
    "industry_limit_count",
)
SCORE_FACTORS_LOW = ("dist_high60",)

SNAPSHOT_PAGE_SIZE = 100
SNAPSHOT_MAX_PAGES = 60
HISTORY_BARS = 150
MIN_HISTORY_BARS = 65
CACHE_TTL = 600
TOP_N = 50
EXTRA_FEATURES = [
    "macd_dif", "macd_dea", "macd_hist", "macd_gold", "macd_dif_pos",
    "kdj_k", "kdj_d", "kdj_j", "kdj_gold", "rsi6", "bias5", "bias10",
    "bias20", "roc10", "boll_pos", "boll_width", "ma_bull", "atr14",
    "vol_shrink", "hammer", "long_upper", "engulfing", "gap_up_20",
    "morning_star", "three_white_soldiers", "bullish_harami", "piercing",
    "rising_three", "turtle_breakout", "ma_reclaim",
    "close_pos", "close_high_ratio",
    # Alpha101/Alpha158 风格扩展：波动率、量价相关、形态、动量、筹码特征。
    "volatility_10", "volatility_20", "volatility_ratio_20_60",
    "vol_price_corr10", "vol_price_corr20", "volume_std20",
    "ret_skew20", "ret_kurt20", "max_ret_20", "min_ret_20",
    "ma5_slope", "ma10_slope", "ma20_slope", "macd_slope", "rsi_slope",
    "up_days_10", "consecutive_up", "consecutive_down",
    "gap_avg_20", "high_breakout_count_60", "low_breakout_count_60",
    "volume_accel_10", "money_flow_ratio", "range_position_20",
    "price_accel_5", "price_accel_10", "atr_ratio_10_20",
    "boll_pct_change", "corr_high_low_20", "close_above_ma5_ratio_20",
    "volume_price_divergence", "limit_up_history_60",
]

RANK_SOURCE_FEATURES = [
    "pct_chg",
    "vol_ratio_5",
    "amplitude_pct",
    "amount_yi",
    "pos_ma20",
    "ret_5",
    "macd_hist",
    "rsi6",
    "industry_mean_prev",
]

_GAP_CACHE = {"ts": 0, "data": None, "computing": False, "last_err": None}
_GAP_LOCK = threading.Lock()


def _scope_key(scope=None):
    scope = scope or {}
    return "|".join(
        str(scope.get(k, ""))
        for k in ("main", "chi_next", "st", "price_min", "price_max", "mcap")
    )


def _to_float(value, default=0.0):
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_main_board(code: str) -> bool:
    return str(code).zfill(6).startswith(MAIN_BOARD_PREFIXES)


def _board_of(code: str) -> str:
    code = str(code).zfill(6)
    if code.startswith(("000", "001", "002", "003", "600", "601", "603", "605")):
        return "main"
    if code.startswith(("300", "301")) or code.startswith(("688", "689")):
        return "chi_next"
    return "other"


def _is_st_name(name: str) -> bool:
    upper = str(name or "").upper()
    return upper.startswith("ST") or upper.startswith("*ST") or "退" in upper


def _industry_allowed(industry: str) -> bool:
    return not any(keyword in str(industry) for keyword in BLOCKED_INDUSTRY_KEYWORDS)


def _price_ok(price, scope=None) -> bool:
    try:
        price = float(price)
        if not (0.0 < price <= MAX_PRICE):
            return False
        scope = scope or {}
        price_min = scope.get("price_min")
        price_max = scope.get("price_max")
        if price_min not in (None, ""):
            price_min = float(price_min)
            if price < price_min:
                return False
        if price_max not in (None, ""):
            price_max = float(price_max)
            if price > price_max:
                return False
        return True
    except (TypeError, ValueError):
        return False


def _fundamentals_ok(pe_ttm=None, pb=None) -> bool:
    if pe_ttm is not None:
        try:
            if float(pe_ttm) <= 0 or float(pe_ttm) > MAX_PE_TTM:
                return False
        except (TypeError, ValueError):
            return False
    if pb is not None:
        try:
            if float(pb) > MAX_PB:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _not_limit_up(pct_chg) -> bool:
    try:
        return float(pct_chg) < NOT_LIMIT_PCT
    except (TypeError, ValueError):
        return False


@lru_cache(maxsize=8192)
def _stock_industry(code: str) -> str:
    """东财 F10 取行业（EM2016），失败返回空串。"""
    code = str(code).zfill(6)
    market = "SH" if code.startswith("6") else "SZ"
    try:
        r = requests.get(
            "https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax",
            params={"code": f"{market}{code}"},
            headers={"User-Agent": "Mozilla/5.0",
                     "Referer": "https://emweb.securities.eastmoney.com/"},
            timeout=6,
            proxies=server.NO_PROXY,
        )
        r.raise_for_status()
        jbzl = (r.json() or {}).get("jbzl") or [{}]
        return str((jbzl[0] or {}).get("EM2016") or "").strip()
    except Exception:
        return ""


def fetch_market_snapshot(max_pages=SNAPSHOT_MAX_PAGES) -> pd.DataFrame:
    """东财全市场快照（push2delay，一次请求带行业，避免逐票 F10）。"""
    rows = []
    for pn in range(1, max_pages + 1):
        try:
            data = server.em_get(
                "https://push2delay.eastmoney.com",
                "/api/qt/clist/get",
                {
                    "pn": str(pn),
                    "pz": str(SNAPSHOT_PAGE_SIZE),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f12",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                    "fields": "f2,f3,f5,f6,f8,f9,f12,f14,f15,f16,f17,f18,f20,f21,f23,f100",
                },
                timeout=12,
                retries=2,
            )
            diff = ((data or {}).get("data") or {}).get("diff") or []
        except Exception as e:
            print(f"[gap_pick] snapshot page {pn} err: {e}", flush=True)
            break
        if not diff:
            break
        for it in diff:
            f20 = _to_float(it.get("f20"), None)
            f21 = _to_float(it.get("f21"), None)
            rows.append({
                "code": str(it.get("f12", "")).zfill(6),
                "name": it.get("f14", ""),
                "price": _to_float(it.get("f2"), None),
                "pct_chg": _to_float(it.get("f3"), None),
                "volume_lots": _to_float(it.get("f5"), None),
                "amount": _to_float(it.get("f6"), None),
                "turnover": _to_float(it.get("f8"), None),
                "high": _to_float(it.get("f15"), None),
                "low": _to_float(it.get("f16"), None),
                "pe_ttm": _to_float(it.get("f9"), None),
                "pb": _to_float(it.get("f23"), None),
                "mktcap": f20 / 1e8 if f20 else None,
                "float_mcap": f21 / 1e8 if f21 else None,
                "industry": str(it.get("f100") or "").strip(),
            })
        if len(diff) < SNAPSHOT_PAGE_SIZE:
            break
    return pd.DataFrame(rows)


def fetch_zt_pool(trade_date: str) -> pd.DataFrame:
    """东方财富涨停池；失败返回空 DataFrame。"""
    compact = str(trade_date).replace("-", "")
    data = server.em_get(
        "https://push2ex.eastmoney.com",
        "/getTopicZTPool",
        {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": 0,
            "pagesize": 300,
            "sort": "fbt:asc",
            "date": compact,
        },
        timeout=8,
        retries=2,
    )
    payload = (data or {}).get("data") or {}
    pool = payload.get("pool") or []
    rows = []
    for r in pool:
        rows.append({
            "代码": str(r.get("c", "")).zfill(6),
            "名称": r.get("n", ""),
            "涨跌幅": _to_float(r.get("zdp")),
            "连板数": r.get("lbc") or 0,
            "所属行业": r.get("hybk") or "未知行业",
        })
    return pd.DataFrame(rows)


def _add_limit_labels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["pre_close"] = out["close"].shift(1)
    out["is_limit_up"] = (
        (out["close"] >= out["pre_close"] * (1 + 0.099))
        & (out["pre_close"] > 0)
    )
    streak = []
    current = 0
    for flag in out["is_limit_up"].tolist():
        current = current + 1 if flag else 0
        streak.append(current)
    out["limit_streak"] = streak
    out["prev_limit_up"] = out["is_limit_up"].shift(1, fill_value=False).astype(int)
    out["limit_streak_prev"] = out["limit_streak"].shift(1, fill_value=0).astype(int)
    return out


def _add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "is_limit_up" not in out.columns:
        pre = out["close"].shift(1)
        out["pre_close"] = pre
        out["is_limit_up"] = (
            (out["close"] >= pre * 1.099) & (pre > 0)
        )
        out["limit_streak"] = 0
        out["prev_limit_up"] = 0
        out["limit_streak_prev"] = 0
    out["vol_ratio_5"] = out["volume"] / out["volume"].rolling(5).mean().shift(1)
    prev_close = out["pre_close"] if "pre_close" in out.columns else out["close"].shift(1)
    out["pct_chg"] = (out["close"] / prev_close - 1) * 100
    out["amplitude_pct"] = (out["high"] - out["low"]) / prev_close * 100
    span = out["high"] - out["low"]
    out["body_ratio"] = ((out["close"] - out["open"]) / span).where(span > 0, 1.0)
    out["pos_ma20"] = out["close"] / out["close"].rolling(20).mean() - 1
    out["pos_ma60"] = out["close"] / out["close"].rolling(60).mean() - 1
    high60 = out["high"].rolling(60).max().shift(1)
    low60 = out["low"].rolling(60).min().shift(1)
    out["dist_high60"] = out["close"] / high60 - 1
    out["dist_low60"] = out["close"] / low60 - 1
    amount = out["amount"] if "amount" in out.columns else out["close"] * out["volume"]
    out["amount_yi"] = amount / 1e8
    out["ret_5"] = out["close"] / out["close"].shift(5) - 1
    out["ret_10"] = out["close"] / out["close"].shift(10) - 1
    out["pos_ma5"] = out["close"] / out["close"].rolling(5).mean() - 1
    out["pos_ma10"] = out["close"] / out["close"].rolling(10).mean() - 1
    out["vol_ratio_10"] = out["volume"] / out["volume"].rolling(10).mean().shift(1)
    out["vol_20"] = out["close"].pct_change().rolling(20).std()
    out["gap_count_20"] = (out["open"] / prev_close - 1 > 0.005).rolling(20).sum()
    out["up_days_5"] = (out["close"] > prev_close).rolling(5).sum()
    ma5 = out["close"].rolling(5).mean()
    ma10 = out["close"].rolling(10).mean()
    ma20 = out["close"].rolling(20).mean()
    vol_ma20 = out["volume"].rolling(20).mean()
    prev_high20 = out["high"].rolling(20).max().shift(1)
    prev_high10 = out["high"].rolling(10).max().shift(1)
    recent_low5 = out["low"].rolling(5).min()
    out["vol_breakout"] = (
        (out["close"] >= prev_high20 * 0.995) & (out["volume"] > vol_ma20 * 1.5)
    ).astype(int)
    out["duck_head"] = (
        (ma5 > ma10) & (ma10 > ma20) & (out["close"] > ma20) &
        (recent_low5 > ma20 * 0.97) &
        (out["close"] >= prev_high10 * 0.99) &
        (out["volume"] > vol_ma20 * 1.2) & (out["pct_chg"] > 0)
    ).astype(int)
    close = out["close"]
    high = out["high"]
    low = out["low"]
    open_ = out["open"]
    volume = out["volume"]
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    out["macd_dif"] = dif
    out["macd_dea"] = dea
    out["macd_hist"] = (dif - dea) * 2
    out["macd_gold"] = ((dif > dea) & (dif.shift(1) <= dea.shift(1))).astype(int)
    out["macd_dif_pos"] = (dif > 0).astype(int)
    low9 = low.rolling(9).min()
    high9 = high.rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    out["kdj_k"] = k
    out["kdj_d"] = d
    out["kdj_j"] = j
    out["kdj_gold"] = ((k > d) & (k.shift(1) <= d.shift(1))).astype(int)
    delta6 = close.diff()
    gain6 = delta6.clip(lower=0).rolling(6).mean()
    loss6 = (-delta6.clip(upper=0)).rolling(6).mean()
    out["rsi6"] = 100 - 100 / (1 + gain6 / loss6.replace(0, np.nan))
    out["bias5"] = close / ma5 - 1
    out["bias10"] = close / ma10 - 1
    out["bias20"] = close / ma20 - 1
    out["roc10"] = close / close.shift(10) - 1
    out["upper"] = ma20 + 2 * close.rolling(20).std()
    out["lower"] = ma20 - 2 * close.rolling(20).std()
    boll_pos = (close - out["lower"]) / (out["upper"] - out["lower"])
    boll_width = (out["upper"] - out["lower"]) / ma20
    out["boll_pos"] = boll_pos
    out["boll_width"] = boll_width
    ma60 = close.rolling(60).mean()
    out["ma_bull"] = ((ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)).astype(int)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    out["atr14"] = tr.rolling(14).mean() / close
    out["vol_shrink"] = (volume < vol_ma20 * 0.8).astype(int)
    body = close - open_
    upper_shadow = high - np.maximum(close, open_)
    lower_shadow = np.minimum(close, open_) - low
    out["hammer"] = ((lower_shadow > 2 * np.abs(body)) & (upper_shadow < lower_shadow)).astype(int)
    out["long_upper"] = (upper_shadow > 2 * np.abs(body)).astype(int)
    prev_open = open_.shift(1)
    out["engulfing"] = (
        (close > open_) & (prev_close < prev_open) &
        (close >= prev_open) & (open_ <= prev_close)
    ).astype(int)
    out["gap_up_20"] = ((open_ / prev_close - 1) > 0.01).rolling(20).sum()
    c1 = close.shift(2)
    o1 = open_.shift(2)
    c2 = close.shift(1)
    o2 = open_.shift(1)
    body1 = (c1 - o1).abs()
    body2 = (c2 - o2).abs()
    out["morning_star"] = (
        (c1 < o1) & (body2 < 0.4 * body1) &
        (close > open_) & (close > (o1 + c1) / 2)
    ).astype(int)
    out["three_white_soldiers"] = (
        (close > open_) & (c2 > o2) & (c1 > o1) &
        (close > c2) & (c2 > c1)
    ).astype(int)
    out["bullish_harami"] = (
        (c1 < o1) & (body1 > 2 * (close - open_).abs()) &
        (open_ <= c1) & (close >= o1) & (close > open_)
    ).astype(int)
    prev_mid = (o1 + c1) / 2
    out["piercing"] = (
        (c1 < o1) & (open_ < c1) & (close > prev_mid) &
        (close < o1) & (close > open_)
    ).astype(int)
    f_o = open_.shift(4)
    f_c = close.shift(4)
    mid_high = high.shift(1).rolling(3).max()
    mid_low = low.shift(1).rolling(3).min()
    out["rising_three"] = (
        (f_c > f_o) & (close > open_) & (close > f_c) &
        (mid_low > f_o) & (mid_high < f_c)
    ).astype(int)
    out["turtle_breakout"] = (close >= prev_high20).astype(int)
    out["ma_reclaim"] = (
        (close > ma10) & (recent_low5 >= ma10 * 0.98) & (out["pct_chg"] > 0)
    ).astype(int)
    span_day = (high - low).replace(0, np.nan)
    out["close_pos"] = (close - low) / span_day
    out["close_high_ratio"] = close / high
    ret = close.pct_change()
    out["volatility_10"] = ret.rolling(10).std()
    out["volatility_20"] = ret.rolling(20).std()
    out["volatility_ratio_20_60"] = (
        ret.rolling(20).std() / ret.rolling(60).std()
    )
    out["vol_price_corr10"] = close.rolling(10).corr(volume)
    out["vol_price_corr20"] = close.rolling(20).corr(volume)
    out["volume_std20"] = volume.rolling(20).std() / vol_ma20
    out["ret_skew20"] = ret.rolling(20).skew()
    out["ret_kurt20"] = ret.rolling(20).kurt()
    out["max_ret_20"] = ret.rolling(20).max()
    out["min_ret_20"] = ret.rolling(20).min()
    out["ma5_slope"] = ma5 / ma5.shift(5) - 1
    out["ma10_slope"] = ma10 / ma10.shift(5) - 1
    out["ma20_slope"] = ma20 / ma20.shift(5) - 1
    out["macd_slope"] = dif / dif.shift(3) - 1
    out["rsi_slope"] = out["rsi6"] / out["rsi6"].shift(3) - 1
    out["up_days_10"] = (close > prev_close).rolling(10).sum()
    up_streak, down_streak, up_cur, down_cur = [], [], 0, 0
    for is_up in (close > prev_close).fillna(False).tolist():
        up_cur = up_cur + 1 if is_up else 0
        down_cur = 0 if is_up else down_cur + 1
        up_streak.append(up_cur)
        down_streak.append(down_cur)
    out["consecutive_up"] = up_streak
    out["consecutive_down"] = down_streak
    out["gap_avg_20"] = (open_ / prev_close - 1).abs().rolling(20).mean()
    prev_high60 = high.rolling(60).max().shift(1)
    prev_low60 = low.rolling(60).min().shift(1)
    out["high_breakout_count_60"] = (
        close >= prev_high60 * 0.995
    ).rolling(60).sum()
    out["low_breakout_count_60"] = (
        close <= prev_low60 * 1.005
    ).rolling(60).sum()
    out["volume_accel_10"] = volume / volume.shift(10) - 1
    amount_safe = amount.replace(0, np.nan)
    out["money_flow_ratio"] = ((close - open_) * volume) / amount_safe
    low20 = low.rolling(20).min()
    high20 = high.rolling(20).max()
    out["range_position_20"] = (close - low20) / (high20 - low20)
    out["price_accel_5"] = close.pct_change(5).diff()
    out["price_accel_10"] = close.pct_change(10).diff()
    out["atr_ratio_10_20"] = tr.rolling(10).mean() / tr.rolling(20).mean()
    out["boll_pct_change"] = boll_pos - boll_pos.shift(3)
    out["corr_high_low_20"] = high.rolling(20).corr(low)
    out["close_above_ma5_ratio_20"] = (close > ma5).rolling(20).mean()
    out["volume_price_divergence"] = (
        (close.pct_change() * volume.pct_change()).rolling(10).mean()
    )
    out["limit_up_history_60"] = out["is_limit_up"].rolling(60).sum()
    return out.replace([np.inf, -np.inf], np.nan)


def _history_df(secid: str, trade_date: str, price, high, low, volume_lots, amount=None) -> pd.DataFrame:
    try:
        rows = server._klines(secid, 101, HISTORY_BARS)
    except Exception:
        rows = []
    if not rows or len(rows) < MIN_HISTORY_BARS:
        return None
    records = []
    for r in rows:
        try:
            close = float(r["close"])
            vol = float(r.get("vol") or 0)
            amount = float(r.get("amount") or 0) or close * vol
            records.append({
                "date": str(r["date"])[:10],
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": close,
                "volume": vol,
                "amount": amount,
            })
        except (KeyError, TypeError, ValueError):
            continue
    if len(records) < MIN_HISTORY_BARS:
        return None
    df = pd.DataFrame(records).drop_duplicates("date").sort_values("date").reset_index(drop=True)
    last_date = str(df.iloc[-1]["date"])[:10]
    today = {
        "date": trade_date,
        "open": _to_float(price),
        "high": _to_float(high, price),
        "low": _to_float(low, price),
        "close": _to_float(price),
        "volume": int(_to_float(volume_lots, 0) * 100),
        "amount": _to_float(amount) or _to_float(price) * int(_to_float(volume_lots, 0) * 100),
    }
    if last_date == trade_date:
        for col in ("open", "high", "low", "close", "volume", "amount"):
            df.loc[df.index[-1], col] = today[col]
    else:
        df = pd.concat([df, pd.DataFrame([today])], ignore_index=True)
    df = _add_limit_labels(df)
    df = _add_features(df)
    return df


def build_candidates(
    snapshot_df: pd.DataFrame,
    zt_df: pd.DataFrame,
    trade_date: str,
    scope: dict | None = None,
    index_ret_prev: float = 0.0,
    industry_mean_map: dict | None = None,
    index_ma5_up: float = 0.0,
    industry_rank_map: dict | None = None,
) -> list[dict]:
    scope = scope or {}
    industry_mean_map = industry_mean_map or {}
    industry_rank_map = industry_rank_map or {}
    total = len(snapshot_df)
    progress = {"done": 0}
    progress_lock = threading.Lock()

    def _worker(row) -> dict | None:
        with progress_lock:
            progress["done"] += 1
            done = progress["done"]
        if done % 200 == 0:
            print(f"[gap_pick] 扫描 {done}/{total}", flush=True)
        code = str(row.get("code", "")).zfill(6)
        name = str(row.get("name", ""))
        board = _board_of(code)
        if board == "other":
            return None
        if board == "main" and not scope.get("main", True):
            return None
        if board == "chi_next" and not scope.get("chi_next", True):
            return None
        if not scope.get("st", False) and _is_st_name(name):
            return None
        if not _price_ok(row.get("price"), scope):
            return None
        if not _fundamentals_ok(row.get("pe_ttm"), row.get("pb")):
            return None
        if not _not_limit_up(row.get("pct_chg")):
            return None
        mcap = _to_float(row.get("mktcap"), None)
        mcap_mode = scope.get("mcap")
        if mcap is not None and mcap_mode:
            if mcap_mode == "small" and not mcap < 100:
                return None
            if mcap_mode == "mid" and not (100 <= mcap <= 500):
                return None
            if mcap_mode == "large" and not mcap > 500:
                return None
        try:
            industry = str(row.get("industry") or "").strip() or "未知行业"
            if not _industry_allowed(industry):
                return None
            hist = _history_df(
                f"{'1' if code[0] in '689' else '0'}.{code}",
                trade_date,
                row.get("price"),
                row.get("high"),
                row.get("low"),
                row.get("volume_lots"),
                row.get("amount"),
            )
            if hist is None:
                return None
            last = hist.iloc[-1]
            features = {
                "vol_ratio_5": _to_float(last.get("vol_ratio_5"), None),
                "vol_ratio_10": _to_float(last.get("vol_ratio_10"), None),
                "amplitude_pct": _to_float(last.get("amplitude_pct"), None),
                "dist_high60": _to_float(last.get("dist_high60"), None),
                "dist_low60": _to_float(last.get("dist_low60"), None),
                "pos_ma20": _to_float(last.get("pos_ma20"), None),
                "pos_ma60": _to_float(last.get("pos_ma60"), None),
                "pos_ma5": _to_float(last.get("pos_ma5"), None),
                "pos_ma10": _to_float(last.get("pos_ma10"), None),
                "amount_yi": _to_float(last.get("amount_yi"), None),
                "pct_chg": _to_float(last.get("pct_chg"), None),
                "body_ratio": _to_float(last.get("body_ratio"), None),
                "ret_5": _to_float(last.get("ret_5"), None),
                "ret_10": _to_float(last.get("ret_10"), None),
                "vol_20": _to_float(last.get("vol_20"), None),
                "gap_count_20": _to_float(last.get("gap_count_20"), None),
                "up_days_5": _to_float(last.get("up_days_5"), None),
                "prev_limit_up": int(_to_float(last.get("prev_limit_up"), 0)),
                "limit_streak_prev": int(_to_float(last.get("limit_streak_prev"), 0)),
                "industry_zt_count": 0,
                "index_ret_prev": index_ret_prev,
                "industry_mean_prev": 0,
                "vol_breakout": int(_to_float(last.get("vol_breakout"), 0)),
                "duck_head": int(_to_float(last.get("duck_head"), 0)),
                "index_ma5_up": index_ma5_up,
                "industry_rank_prev": 0,
            }
            for _f in EXTRA_FEATURES:
                features[_f] = _to_float(last.get(_f), None)
            if any(v is None or pd.isna(v) or not np.isfinite(v) for v in features.values()):
                return None
            return {
                "code": code,
                "name": name,
                "price": _to_float(row.get("price")),
                "change_pct": _to_float(row.get("pct_chg")),
                "turnover_pct": _to_float(row.get("turnover"), None),
                "industry": industry,
                **features,
            }
        except Exception as e:
            print(f"[gap_pick] skip {code}: {e}", flush=True)
            return None

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="gap-scan") as ex:
        out = [c for c in ex.map(_worker, snapshot_df.to_dict("records")) if c]
    if not out:
        return []
    heat = {}
    if not zt_df.empty:
        industry_col = "所属行业" if "所属行业" in zt_df.columns else "industry"
        heat = (
            zt_df.assign(_industry=zt_df[industry_col].fillna("未知行业").astype(str))
            .groupby("_industry")
            .size()
            .to_dict()
        )
    for candidate in out:
        candidate["industry_limit_count"] = int(heat.get(candidate["industry"], 0))
        candidate["industry_zt_count"] = candidate["industry_limit_count"]
        candidate["industry_mean_prev"] = float(industry_mean_map.get(candidate["industry"], 0.0))
        candidate["industry_rank_prev"] = float(industry_rank_map.get(candidate["industry"], 0.0))
    rank_df = pd.DataFrame(out)
    for name in RANK_SOURCE_FEATURES:
        if name in rank_df.columns:
            rank_df[f"rank_{name}"] = rank_df[name].rank(pct=True).fillna(0.5)
    out = rank_df.to_dict("records")
    return out


def score_candidates(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    df = pd.DataFrame(candidates)
    for column in SCORE_FACTORS_HIGH:
        df[f"{column}_hit"] = df[column] >= df[column].median()
    for column in SCORE_FACTORS_LOW:
        df[f"{column}_hit"] = df[column] <= df[column].median()
    hit_columns = [
        f"{column}_hit"
        for column in SCORE_FACTORS_HIGH + SCORE_FACTORS_LOW
    ]
    df["score"] = df[hit_columns].sum(axis=1).astype(int)
    df["reason"] = df.apply(_recommend_reason, axis=1)
    model_feats = gap_model.model_features()
    df["prob"] = np.nan
    if model_feats:
        def _prob(row):
            return gap_model.score({k: row.get(k) for k in model_feats})
        df["prob"] = df.apply(_prob, axis=1)
    tail_feats = tail_model.model_features()
    df["tail_prob"] = np.nan
    if tail_feats:
        def _tail(row):
            return tail_model.score({k: row.get(k) for k in tail_feats})
        df["tail_prob"] = df.apply(_tail, axis=1)
    primary = "tail_prob" if df["tail_prob"].notna().any() else "prob"
    if df[primary].notna().any():
        df = df.sort_values(
            [primary, "prob", "score", "industry_limit_count"],
            ascending=[False, False, False, False],
            na_position="last",
        )
    else:
        df = df.sort_values(["score", "industry_limit_count"], ascending=[False, False])
    records = df.to_dict("records")
    for rec in records:
        if rec.get("prob") is not None and pd.isna(rec["prob"]):
            rec["prob"] = None
        if rec.get("tail_prob") is not None and pd.isna(rec["tail_prob"]):
            rec["tail_prob"] = None
    return records


def _enhance_candidates(candidates, fast=False, lite=False):
    """用主力资金/热榜/龙虎榜/公告事件对 TopN 二次确认并重排。"""
    if not candidates:
        return []
    import astock_data as ad
    import extra_data as ex

    hot = {}
    try:
        for r in ad.em_hot_rank(50):
            hot[str(r.get("code"))] = r.get("rank")
    except Exception:
        pass

    def _worker(c):
        c = dict(c)
        fund = {}
        lhb = {}
        ann = []
        margin = []
        holder = []
        if not lite:
            try:
                rows = ad.stock_fund_flow_120d(c["code"])
                if rows:
                    fund = rows[-1]
            except Exception:
                pass
        if not fast and not lite:
            try:
                lhb = ad.dragon_tiger_board(c["code"], look_back=5)
            except Exception:
                pass
            try:
                ann = ex.cninfo_announcements(c["code"], 8)
            except Exception:
                pass
            try:
                margin = ad.margin_trading(c["code"], 5)
            except Exception:
                pass
            try:
                holder = ad.holder_num_change(c["code"], 3)
            except Exception:
                pass
        main_net_yi = float(fund.get("main_net") or 0) / 1e8
        lhb_count = len((lhb or {}).get("records") or [])
        lhb_inst_net = float((lhb or {}).get("institution", {}).get("net_amt") or 0)
        margin_chg = 0.0
        if len(margin) >= 2:
            prev_bal = float(margin[1].get("rzrqye") or 0)
            cur_bal = float(margin[0].get("rzrqye") or 0)
            if prev_bal:
                margin_chg = cur_bal / prev_bal - 1
        holder_chg = 0.0
        if holder:
            holder_chg = float(holder[0].get("change_ratio") or 0)
        hot_rank = hot.get(str(c["code"]))
        event_flag = 0
        event_note = ""
        for a in (ann or [])[:7]:
            t = str(a.get("title", ""))
            if any(k in t for k in ("解禁", "减持")):
                event_flag = -1
                event_note = "解禁/减持"
            elif any(k in t for k in ("业绩预增", "回购", "中标", "增持")):
                if event_flag >= 0:
                    event_flag = 1
                    event_note = "利好事件"
        boost = 0.0
        if main_net_yi > 0:
            boost += 0.03
        elif main_net_yi < 0:
            boost -= 0.05
        if hot_rank:
            boost += 0.02
        if lhb_count > 0:
            boost += 0.02
        if event_flag > 0:
            boost += 0.02
        if event_flag < 0:
            boost -= 0.03
        if margin_chg > 0.02:
            boost += 0.01
        elif margin_chg < -0.02:
            boost -= 0.01
        if holder_chg < 0:
            boost += 0.02
        elif holder_chg > 0.02:
            boost -= 0.01
        confirm_layers = {}
        if _to_float(c.get("index_ma5_up")) >= 0.5:
            confirm_layers["大盘"] = "指数站上MA5"
        if (_to_float(c.get("industry_rank_prev")) >= 0.6 or
                int(_to_float(c.get("industry_limit_count"))) >= 2):
            confirm_layers["板块"] = "板块排名靠前/有涨停"
        if (_to_float(c.get("vol_breakout")) >= 1 or
                _to_float(c.get("vol_ratio_5")) >= 1.2 or
                _to_float(c.get("turnover_pct")) >= 5):
            confirm_layers["量能"] = "放量/量比/换手抬升"
        if main_net_yi > 0:
            confirm_layers["资金"] = "主力净流入"
        if hot_rank or lhb_count > 0 or event_flag > 0:
            confirm_layers["情绪"] = "热榜/龙虎榜/利好"
        if (_to_float(c.get("pos_ma20")) > 0 and
                _to_float(c.get("close_high_ratio")) >= 0.5):
            confirm_layers["位置"] = "站上MA20且收在当日偏强区"
        confirm_score = len(confirm_layers)
        if confirm_score >= 3:
            boost += (confirm_score - 3) * 0.01
        if confirm_score <= 1 and main_net_yi < 0:
            boost -= 0.04
        c["main_net_yi"] = round(main_net_yi, 2)
        c["lhb_inst_net"] = round(lhb_inst_net, 1)
        c["margin_chg"] = round(margin_chg, 4)
        c["holder_chg"] = round(holder_chg, 3)
        c["hot_rank"] = hot_rank
        c["lhb_count_5"] = lhb_count
        c["confirm_score"] = confirm_score
        c["confirm_layers"] = confirm_layers
        if main_net_yi > 0.5 or lhb_inst_net > 0:
            c["main_intent"] = "吸筹"
        elif main_net_yi < -0.5 or lhb_inst_net < 0:
            c["main_intent"] = "流出"
        else:
            c["main_intent"] = "中性"
        c["event_note"] = event_note
        c["boost"] = round(boost, 4)
        prob = c.get("tail_prob") or c.get("prob") or 0
        c["enhanced_prob"] = round(max(min(prob + boost, 1.0), 0.0), 4)
        return c

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="gap-boost") as ex:
        out = list(ex.map(_worker, candidates))
    out.sort(key=lambda x: x.get("enhanced_prob") or 0, reverse=True)
    return out


def _recommend_reason(row) -> str:
    parts = []
    if row.get("vol_ratio_5_hit"):
        parts.append(f"量比{row['vol_ratio_5']:.2f}")
    if row.get("amplitude_pct_hit"):
        parts.append(f"振幅{row['amplitude_pct']:.1f}%")
    if row.get("pos_ma20_hit"):
        parts.append("站上MA20")
    if row.get("pos_ma60_hit"):
        parts.append("站上MA60")
    if row.get("dist_low60_hit"):
        parts.append("远离60日低点")
    if row.get("amount_yi_hit"):
        parts.append(f"成交{row['amount_yi']:.0f}亿")
    if row.get("industry_limit_count_hit"):
        parts.append(f"板块涨停{row['industry_limit_count']}家")
    if row.get("dist_high60_hit"):
        parts.append("贴近60日高点")
    if parts:
        return " · ".join(parts)
    return "无明显突出因子" if not row.get("score") else "综合评分靠前"


def _compute(scope=None):
    scope = scope or {}
    if not scope.get("main", True) and not scope.get("chi_next", True):
        return {
            "date": time.strftime("%Y-%m-%d"),
            "ts": int(time.time()),
            "elapsed_sec": 0.0,
            "total": 0,
            "candidates": [],
            "scope_key": _scope_key(scope),
        }
    t0 = time.time()
    trade_date = time.strftime("%Y-%m-%d")
    zt_df = fetch_zt_pool(trade_date)
    print(f"[gap_pick] 涨停池 {len(zt_df)} 只", flush=True)
    snapshot_df = fetch_market_snapshot()
    if snapshot_df.empty:
        raise RuntimeError("全市场快照为空")
    print(f"[gap_pick] 全市场快照 {len(snapshot_df)} 行，开始筛选", flush=True)
    allowed_boards = []
    if scope.get("main", True):
        allowed_boards.append("main")
    if scope.get("chi_next", True):
        allowed_boards.append("chi_next")
    scoped_df = snapshot_df[
        snapshot_df["code"].astype(str).str.zfill(6).map(_board_of).isin(allowed_boards)
    ].copy()
    industry_mean_map = (
        snapshot_df.assign(industry=snapshot_df["industry"].fillna("未知行业"))
        .groupby("industry")["pct_chg"].mean().to_dict()
    )
    industry_rank_map = (
        pd.Series(industry_mean_map).rank(pct=True).to_dict()
    )
    idx_rows = server._klines("1.000001", 101, 5)
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
    print(f"[gap_pick] 交易权限范围内 {len(scoped_df)} 行", flush=True)
    # 涨停池接口失败时，用快照中涨幅 >=9.8% 的票近似补行业热度，不阻塞主流程。
    if zt_df.empty:
        approx = snapshot_df[pd.to_numeric(snapshot_df["pct_chg"], errors="coerce") >= 9.8].copy()
        if not approx.empty:
            approx = approx.rename(columns={"code": "代码", "name": "名称", "pct_chg": "涨跌幅"})
            approx["连板数"] = 1
            approx["所属行业"] = approx["代码"].map(_stock_industry).fillna("未知行业")
            zt_df = approx[["代码", "名称", "涨跌幅", "连板数", "所属行业"]]
    candidates = build_candidates(
        scoped_df, zt_df, trade_date, scope,
        index_ret_prev, industry_mean_map, index_ma5_up, industry_rank_map)
    print(f"[gap_pick] 硬过滤后候选 {len(candidates)} 只，开始评分", flush=True)
    scored = score_candidates(candidates)
    scored = _enhance_candidates(scored[:10], fast=True, lite=True)
    ranking = "model" if any(pd.notna(c.get("prob")) for c in scored) else "rule"
    note = ""
    if scored:
        max_n = 3
        min_prob = 0.30
        if not index_ma5_up:
            if index_ret_prev >= -0.005:
                max_n = 2
                min_prob = 0.35
            else:
                max_n = 1
                min_prob = 0.45
        def _passes(c):
            if (c.get("enhanced_prob") or 0) < min_prob:
                return False
            if (c.get("industry_rank_prev") or 0) < 0.3:
                return False
            if (c.get("main_net_yi") or 0) < 0 and (c.get("confirm_score") or 0) < 4:
                return False
            return True

        filtered = [c for c in scored if _passes(c)]
        scored = filtered[:max_n]
    if not scored:
        note = "今日无推荐：候选未达置信度或市场/板块环境不满足"
    return {
        "date": trade_date,
        "ts": int(time.time()),
        "elapsed_sec": round(time.time() - t0, 1),
        "total": len(scored),
        "candidates": scored[:TOP_N],
        "ranking": ranking,
        "note": note,
        "scope_key": _scope_key(scope),
    }


def trigger_refresh(scope=None) -> bool:
    scope = scope or {}
    with _GAP_LOCK:
        if _GAP_CACHE["computing"]:
            return False
        _GAP_CACHE["computing"] = True

    def _run():
        try:
            data = _compute(scope)
            with _GAP_LOCK:
                _GAP_CACHE.update(ts=data["ts"], data=data, last_err=None)
            print(f"[gap_pick] 完成：候选 {data['total']} 只，用时 {data['elapsed_sec']}s", flush=True)
        except Exception as e:
            with _GAP_LOCK:
                _GAP_CACHE["last_err"] = str(e)
            print(f"[gap_pick] err: {e}", flush=True)
        finally:
            with _GAP_LOCK:
                _GAP_CACHE["computing"] = False

    threading.Thread(target=_run, daemon=True).start()
    return True


def get_cache(scope=None, trigger=True):
    key = _scope_key(scope)
    with _GAP_LOCK:
        data = _GAP_CACHE["data"]
        ts = _GAP_CACHE["ts"]
        computing = _GAP_CACHE["computing"]
    if data and data.get("scope_key") == key and time.time() - ts < CACHE_TTL:
        return data
    if not computing and trigger:
        trigger_refresh(scope)
    return data if data and data.get("scope_key") == key else None


def cache_ts():
    with _GAP_LOCK:
        return _GAP_CACHE["ts"]


def is_computing():
    with _GAP_LOCK:
        return _GAP_CACHE["computing"]


def last_err():
    with _GAP_LOCK:
        return _GAP_CACHE["last_err"]


if __name__ == "__main__":
    result = _compute()
    for c in result["candidates"]:
        print(c["code"], c["name"], c["score"])

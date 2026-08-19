# -*- coding: utf-8 -*-
"""次日高开 GBDT 训练（v2）：新增技术特征 + 行业涨停热度 + 概率校准。

产物 gap_model.json 由 App/EXE/APK 用纯 numpy 推理，运行时不依赖 sklearn。
样本口径与 gap_pick.py 对齐：主板、排除 ST/当日涨停/价格>80；
标签 = T+1 开盘 >= 信号日收盘 * (1 + label_gap)，默认 +3%。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from sklearn.calibration import IsotonicRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import datahub as server
import gap_pick
import paths

MODEL_FEATURES = [
    "pct_chg",
    "vol_ratio_5",
    "vol_ratio_10",
    "amplitude_pct",
    "body_ratio",
    "pos_ma5",
    "pos_ma10",
    "pos_ma20",
    "pos_ma60",
    "dist_high60",
    "dist_low60",
    "amount_yi",
    "ret_5",
    "ret_10",
    "vol_20",
    "gap_count_20",
    "up_days_5",
    "prev_limit_up",
    "limit_streak_prev",
    "index_ret_prev",
    "industry_mean_prev",
    "vol_breakout",
    "duck_head",
    "index_ma5_up",
    "industry_rank_prev",
] + [f for f in gap_pick.EXTRA_FEATURES]

DAILY_BARS = 800
_SAMPLE_INDUSTRY = {}
TRAIN_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "train_cache")


def _recompute_labels(df, reach, label_gap):
    df = df.copy()
    close = df["close"].astype(float)
    if reach:
        hit = pd.concat([df["next_open"], df["next_high"]], axis=1).max(axis=1) >= close * (1 + label_gap)
        hit_net = pd.concat([df["next_open"], df["next_high"]], axis=1).max(axis=1) >= close * (1 + label_gap + 0.003)
    else:
        hit = df["next_open"].astype(float) >= close * (1 + label_gap)
        hit_net = df["next_open"].astype(float) >= close * (1 + label_gap + 0.003)
    df["label"] = hit.astype(int)
    df["label_net"] = hit_net.astype(int)
    return df


def fetch_daily(code):
    secid = f"{'1' if code.startswith('6') else '0'}.{code}"
    try:
        rows = server._klines(secid, 101, DAILY_BARS)
    except Exception:
        return None
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if df.empty or "date" not in df.columns:
        return None
    if "volume" not in df.columns and "vol" in df.columns:
        df = df.rename(columns={"vol": "volume"})
    df = df[["date", "open", "high", "low", "close", "volume", "amount"]].copy()
    for col in ("open", "high", "low", "close", "volume", "amount"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).drop_duplicates("date")
    df = df.sort_values("date").reset_index(drop=True)
    if len(df) < gap_pick.MIN_HISTORY_BARS + 5:
        return None
    df = gap_pick._add_limit_labels(df)
    df = gap_pick._add_features(df)
    return df


def sample_codes(limit):
    global _SAMPLE_INDUSTRY
    snapshot = gap_pick.fetch_market_snapshot()
    if snapshot.empty:
        return []
    df = snapshot[snapshot["code"].astype(str).str.zfill(6).map(gap_pick._board_of) == "main"]
    _SAMPLE_INDUSTRY = {
        str(row["code"]).zfill(6): str(row.get("industry") or "")
        for row in df.to_dict("records")
    }
    codes = df["code"].astype(str).str.zfill(6).unique().tolist()
    if limit and limit < len(codes):
        rng = np.random.default_rng(42)
        codes = rng.choice(codes, size=limit, replace=False).tolist()
    return codes


def collect_samples(codes, start, end, index_ret, index_ma5_up, label_gap, reach, verbose=True):
    rows = []
    t0 = time.time()
    n_valid = 0

    def _load(code):
        code = str(code).zfill(6)
        industry = _SAMPLE_INDUSTRY.get(code) or gap_pick._stock_industry(code) or "未知行业"
        return code, fetch_daily(code), industry

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="gap-train") as ex:
        futures = [ex.submit(_load, c) for c in codes]
        done = 0
        for fut in futures:
            try:
                code, df, industry = fut.result(timeout=120)
            except Exception:
                continue
            done += 1
            if df is None:
                continue
            n_valid += 1
            _append_rows(code, df, industry, start, end, index_ret, index_ma5_up, label_gap, reach, rows)
            if verbose and done % 50 == 0:
                el = time.time() - t0
                print(f"[train] {done}/{len(codes)} 有效 {n_valid} 只 | 样本 {len(rows)} | 已用 {el:.0f}s", flush=True)
    return rows, n_valid


def _append_rows(code, df, industry, start, end, index_ret, index_ma5_up, label_gap, reach, rows):
    for j in range(len(df) - 1):
        r = df.iloc[j]
        nxt = df.iloc[j + 1]
        date = str(r["date"])[:10]
        if start and date < start:
            continue
        if end and date > end:
            continue
        price = float(r["close"])
        if not gap_pick._price_ok(price, None):
            continue
        if not gap_pick._not_limit_up(float(r.get("pct_chg") or 0.0)):
            continue
        feats = {}
        bad = False
        for name in MODEL_FEATURES:
            if name in ("industry_zt_count", "index_ret_prev", "industry_mean_prev",
                        "index_ma5_up", "industry_rank_prev"):
                feats[name] = 0.0
                continue
            if name == "industry_zt_count":
                feats[name] = 0.0
                continue
            v = r.get(name)
            try:
                fv = float(v)
            except (TypeError, ValueError):
                bad = True
                break
            if math.isnan(fv) or math.isinf(fv):
                bad = True
                break
            feats[name] = fv
        if bad:
            continue
        feats["index_ret_prev"] = index_ret.get(date, 0.0)
        feats["index_ma5_up"] = index_ma5_up.get(date, 0.0)
        if reach:
            label = 1 if float(max(nxt["open"], nxt["high"])) >= price * (1 + label_gap) else 0
            label_net = 1 if float(max(nxt["open"], nxt["high"])) >= price * (1 + label_gap + 0.003) else 0
        else:
            label = 1 if float(nxt["open"]) >= price * (1 + label_gap) else 0
            label_net = 1 if float(nxt["open"]) >= price * (1 + label_gap + 0.003) else 0
        rows.append({
            "date": date, "code": code, "industry": industry,
            "close": price,
            "volume": float(r.get("volume") or r.get("vol") or 0),
            "next_open": float(nxt["open"]),
            "next_high": float(nxt["high"]),
            "next_low": float(nxt["low"]),
            "next_close": float(nxt["close"]),
            **feats, "label": label, "label_net": label_net,
        })


def load_zt_heat(dates):
    heat = {}
    for i, date in enumerate(sorted(dates), 1):
        try:
            zt = gap_pick.fetch_zt_pool(date)
        except Exception:
            continue
        if zt is None or zt.empty:
            continue
        col = "所属行业" if "所属行业" in zt.columns else "industry"
        for industry, cnt in zt[col].fillna("未知行业").value_counts().items():
            heat[(date, str(industry))] = int(cnt)
        if i % 50 == 0:
            print(f"[train] zt heat {i}/{len(dates)}", flush=True)
    return heat


def topk_rates(df, col, ks=(1, 3, 10), label_col="label"):
    out = {k: [0, 0] for k in ks}
    for _, g in df.groupby("date", sort=True):
        g = g.sort_values(col, ascending=False)
        for k in ks:
            if len(g) < k:
                continue
            out[k][1] += 1
            if bool(g.iloc[:k][label_col].any()):
                out[k][0] += 1
    return {k: round(out[k][0] / max(1, out[k][1]), 4) for k in ks}


def split_by_date(df, train_frac=0.7, val_frac=0.15):
    dates = sorted(df["date"].unique())
    n = len(dates)
    train_end = dates[max(1, int(n * train_frac) - 1)]
    val_end = dates[max(1, int(n * (train_frac + val_frac)) - 1)]
    train = df[df["date"] <= train_end].copy()
    val = df[(df["date"] > train_end) & (df["date"] <= val_end)].copy()
    test = df[df["date"] > val_end].copy()
    return train, val, test


def load_outcomes(out_dir):
    """读取 outcomes/candidates_*.json + hits_*.json，拼成训练样本。"""
    import glob

    rows = []
    for cand_path in sorted(glob.glob(os.path.join(out_dir, "candidates_*.json"))):
        date = os.path.basename(cand_path).replace("candidates_", "").replace(".json", "")
        hit_path = os.path.join(out_dir, f"hits_{date}.json")
        if not os.path.isfile(hit_path):
            continue
        try:
            cand = json.load(open(cand_path, encoding="utf-8"))
            hits = json.load(open(hit_path, encoding="utf-8"))
        except Exception:
            continue
        hit_map = {h["code"]: h for h in hits.get("results", [])}
        for c in cand.get("candidates", []):
            h = hit_map.get(c.get("code"))
            if not h or c.get("date") != date:
                continue
            feats = {k: c.get(k) for k in MODEL_FEATURES}
            if any(v is None for v in feats.values()):
                continue
            rows.append({
                "date": date,
                "code": c.get("code"),
                "industry": c.get("industry", ""),
                **feats,
                "label": 1 if h.get("hit") else 0,
                "label_net": 1 if h.get("hit") else 0,
            })
    return rows


def export_gbdt(model, calib, features, metrics, start, end, n_samples):
    trees = []
    for est in model.estimators_[:, 0]:
        t = est.tree_
        trees.append({
            "left": t.children_left.tolist(),
            "right": t.children_right.tolist(),
            "feature": t.feature.tolist(),
            "threshold": t.threshold.tolist(),
            "value": t.value.reshape(-1).tolist(),
        })
    return {
        "type": "gbdt",
        "features": features,
        "init_score": 0.0 if isinstance(model.init_, str) else float(model.init_.prior),
        "learning_rate": float(model.learning_rate),
        "trees": trees,
        "calib": {
            "thresholds": calib.X_thresholds_.tolist(),
            "targets": calib.y_thresholds_.tolist(),
        },
        "version": 2,
        "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_samples": n_samples,
        "metrics": metrics,
        "date_range": [start, end],
    }


def prepare_data(args):
    """采集并构造训练数据集，返回 DataFrame；供训练和超参搜索复用。"""
    # 批量训练/回测时跳过通达信连接池，避免间歇失败拖慢全样本采集。
    os.environ.setdefault("ASTOCK_NO_TDX", "1")
    limit = getattr(args, "limit", 0) or 0
    reach = bool(getattr(args, "reach", False))
    label_gap = float(getattr(args, "label_gap", 0.03))
    exact_path = os.path.join(
        TRAIN_CACHE_DIR,
        f"train_{args.start}_{args.end}_gap{label_gap}_reach{int(reach)}_n{limit}.csv",
    )
    alt_path = os.path.join(
        TRAIN_CACHE_DIR,
        f"train_{args.start}_{args.end}_gap{label_gap}_reach{int(not reach)}_n{limit}.csv",
    )
    if os.path.isfile(exact_path):
        df = pd.read_csv(exact_path)
        print(f"[train] loaded cache {exact_path} ({len(df)} rows)", flush=True)
        return df
    if os.path.isfile(alt_path):
        df = _recompute_labels(pd.read_csv(alt_path), reach, label_gap)
        print(f"[train] loaded cache {alt_path} and recomputed {reach=} labels ({len(df)} rows)",
              flush=True)
        return df
    if args.codes:
        codes = [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    else:
        codes = sample_codes(args.limit)
    print(f"[train] codes: {len(codes)}", flush=True)

    idx_rows = server._klines("1.000001", 101, 800)
    index_ret = {}
    index_ma5_up = {}
    prev_close = None
    closes = []
    dates = []
    for r in idx_rows:
        date = str(r["date"])[:10]
        close = float(r["close"])
        if prev_close:
            index_ret[date] = close / prev_close - 1
        closes.append(close)
        dates.append(date)
        if len(closes) >= 5:
            ma5 = sum(closes[-5:]) / 5
            index_ma5_up[date] = 1.0 if close > ma5 else 0.0
        prev_close = close

    rows, n_valid = collect_samples(
        codes, args.start, args.end, index_ret, index_ma5_up, args.label_gap,
        getattr(args, "reach", False))
    if not rows:
        print("no samples")
        return None
    df = pd.DataFrame(rows)
    print(f"[train] raw samples {len(df)} valid_stocks {n_valid}", flush=True)

    if args.outcomes_dir:
        added = load_outcomes(args.outcomes_dir)
        if added:
            extra = pd.DataFrame(added)
            keep_cols = ["date", "code", "industry"] + MODEL_FEATURES + ["label", "label_net"]
            extra = extra[keep_cols]
            df = pd.concat([df, extra], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "code"], keep="first")
            print(f"[train] outcomes appended {len(added)} -> {len(df)}", flush=True)

    df["industry_mean_prev"] = df.groupby(["date", "industry"])["pct_chg"].transform("mean")
    industry_ret = df.groupby(["date", "industry"])["pct_chg"].mean().reset_index()
    industry_ret["rank_val"] = industry_ret.groupby("date")["pct_chg"].rank(pct=True)
    df = df.merge(industry_ret[["date", "industry", "rank_val"]],
                  on=["date", "industry"], how="left")
    df["industry_rank_prev"] = df["rank_val"].fillna(0).astype(float)
    df = df.drop(columns=["rank_val"])

    if not args.no_zt_heat:
        heat = load_zt_heat(df["date"].unique())
        df["industry_zt_count"] = df.apply(
            lambda r: heat.get((r["date"], r["industry"]), 0), axis=1)
        print("[train] zt heat joined", flush=True)
    os.makedirs(TRAIN_CACHE_DIR, exist_ok=True)
    df.to_csv(exact_path, index=False)
    print(f"[train] cached {exact_path} ({len(df)} rows)", flush=True)
    return df


def train_model(args):
    """进程内训练入口，返回模型 payload dict；CLI 和 auto_train 共用。"""
    df = prepare_data(args)
    if df is None:
        return None

    train, val, test = split_by_date(df)
    print(f"[train] train {len(train)} val {len(val)} test {len(test)}", flush=True)
    if len(train) < 2000 or len(val) < 500 or len(test) < 500:
        print("too few samples")
        return None

    Xtr = train[MODEL_FEATURES].values
    ytr = train["label"].values
    model = GradientBoostingClassifier(
        n_estimators=args.trees,
        max_depth=args.depth,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
        init="zero",
    )
    print("[train] fitting GBDT...", flush=True)
    t0 = time.time()
    model.fit(Xtr, ytr)
    print(f"[train] fit done {time.time() - t0:.0f}s", flush=True)

    def proba(df_in):
        return model.predict_proba(df_in[MODEL_FEATURES].values)[:, 1]

    val_p = proba(val)
    test_p = proba(test)
    val["prob"] = val_p
    test["prob"] = test_p
    auc = roc_auc_score(test["label"], test_p)
    metrics = {
        "base_rate": round(float(test["label"].mean()), 4),
        "base_rate_net": round(float(test["label_net"].mean()), 4),
        "test_auc": round(float(auc), 4),
        "test_top1": topk_rates(test, "prob", (1,))[1],
        "test_top3": topk_rates(test, "prob", (3,))[3],
        "test_top10": topk_rates(test, "prob", (10,))[10],
        "test_top1_net": topk_rates(test, "prob", (1,), "label_net")[1],
        "test_top3_net": topk_rates(test, "prob", (3,), "label_net")[3],
        "test_top10_net": topk_rates(test, "prob", (10,), "label_net")[10],
    }
    print("[train] metrics", metrics, flush=True)

    calib = IsotonicRegression(out_of_bounds="clip")
    calib.fit(val_p, val["label"].values)
    payload = export_gbdt(model, calib, MODEL_FEATURES, metrics, args.start, args.end, len(df))
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"[train] saved {args.out} ({(os.path.getsize(args.out) / 1024):.0f} KB)", flush=True)
    return payload


def main():
    ap = argparse.ArgumentParser(description="次日高开 GBDT 训练 v2")
    ap.add_argument("--codes", type=str, default=None)
    ap.add_argument("--limit", type=int, default=500, help="采样股票数")
    ap.add_argument("--trees", type=int, default=150)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--start", default="2024-08-01")
    ap.add_argument("--end", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--out", default="gap_model_v2.json")
    ap.add_argument("--label-gap", type=float, default=0.03,
                    help="次日高开标签阈值，默认 0.03 = +3%")
    ap.add_argument("--reach", action="store_true",
                    help="标签改为 T+1 开盘或盘中最高 ≥ +3%")
    ap.add_argument("--no-zt-heat", action="store_true")
    ap.add_argument("--outcomes-dir", type=str, default=None,
                    help="追加已验证的真实候选样本（outcomes 目录）")
    args = ap.parse_args()
    train_model(args)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""次日高开模型超参搜索：复用同一份数据集，按验证集择优，测试集复评。"""
from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd
from sklearn.calibration import IsotonicRegression
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score

import auto_train
import train_gap_v2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--label-gap", type=float, default=0.03)
    args = ap.parse_args()

    data_args = argparse.Namespace(
        codes=None,
        limit=args.limit,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out="/tmp/gap_tune_tmp.json",
        no_zt_heat=True,
        outcomes_dir=None,
        label_gap=args.label_gap,
        trees=150,
        depth=3,
    )
    df = train_gap_v2.prepare_data(data_args)
    if df is None:
        return
    train, val, test = train_gap_v2.split_by_date(df)
    print(f"[tune] train {len(train)} val {len(val)} test {len(test)}", flush=True)

    grid = [
        (150, 2, 0.04),
        (150, 2, 0.08),
        (150, 3, 0.04),
        (150, 3, 0.08),
        (250, 2, 0.04),
        (250, 2, 0.08),
        (250, 3, 0.04),
        (250, 3, 0.08),
    ]
    results = []
    for trees, depth, lr in grid:
        t0 = time.time()
        model = GradientBoostingClassifier(
            n_estimators=trees,
            max_depth=depth,
            learning_rate=lr,
            subsample=0.8,
            random_state=42,
            init="zero",
        )
        model.fit(train[train_gap_v2.MODEL_FEATURES].values, train["label"].values)
        val_p = model.predict_proba(val[train_gap_v2.MODEL_FEATURES].values)[:, 1]
        val_auc = roc_auc_score(val["label"], val_p)
        val_df = val.copy()
        val_df["prob"] = val_p
        top10 = train_gap_v2.topk_rates(val_df, "prob", (10,))[10]
        results.append({
            "trees": trees, "depth": depth, "lr": lr,
            "val_auc": round(float(val_auc), 4), "val_top10": top10,
            "model": model, "elapsed": round(time.time() - t0, 0),
        })
        print(f"[tune] trees={trees} depth={depth} lr={lr} "
              f"val_auc={val_auc:.4f} val_top10={top10} "
              f"elapsed={time.time() - t0:.0f}s", flush=True)

    results.sort(key=lambda r: (-r["val_top10"], -r["val_auc"]))
    best = results[0]
    print(f"[tune] best {best['trees']}/{best['depth']}/{best['lr']} "
          f"val_auc={best['val_auc']} val_top10={best['val_top10']}", flush=True)
    test_p = best["model"].predict_proba(test[train_gap_v2.MODEL_FEATURES].values)[:, 1]
    test_df = test.copy()
    test_df["prob"] = test_p
    test_auc = roc_auc_score(test["label"], test_p)
    metrics = {
        "base_rate": round(float(test["label"].mean()), 4),
        "test_auc": round(float(test_auc), 4),
        "test_top1": train_gap_v2.topk_rates(test_df, "prob", (1,))[1],
        "test_top3": train_gap_v2.topk_rates(test_df, "prob", (3,))[3],
        "test_top10": train_gap_v2.topk_rates(test_df, "prob", (10,))[10],
    }
    print("[tune] test", metrics, flush=True)
    val_p = best["model"].predict_proba(val[train_gap_v2.MODEL_FEATURES].values)[:, 1]
    calib = IsotonicRegression(out_of_bounds="clip")
    calib.fit(val_p, val["label"].values)
    payload = train_gap_v2.export_gbdt(
        best["model"], calib, train_gap_v2.MODEL_FEATURES, metrics,
        data_args.start, data_args.end, len(df))
    out_path = "/tmp/gap_model_tuned.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"[tune] saved {out_path}", flush=True)

    cur = auto_train.current_metrics()
    if not cur or metrics["test_auc"] > cur.get("test_auc", 0) + 0.003 or \
            metrics["test_top10"] > cur.get("test_top10", 0) + 0.01:
        auto_train.publish(out_path, metrics, "tuned hyperparameters improved")
        print("[tune] published", flush=True)
    else:
        auto_train.reject(metrics, "tuned model not better")
        print("[tune] rejected", flush=True)


if __name__ == "__main__":
    main()

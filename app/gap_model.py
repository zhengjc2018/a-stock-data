# -*- coding: utf-8 -*-
"""次日高开排序模型推理（纯 numpy，无 sklearn 依赖）。

支持两种模型格式：
1. 旧版逻辑回归：sigmoid(w·x+b)
2. GBDT：浅层回归树集成 + sigmoid + 可选 isotonic 概率校准
模型文件由 train_gap_v2.py 离线生成，App/EXE/APK 共用同一份 JSON。
"""
from __future__ import annotations

import json
import math
import os
import threading

import numpy as np

import paths

MODEL_FILE = "gap_model.json"

_LOCK = threading.Lock()
_CACHE = {"mtime": None, "model": None, "err": None}


def _candidate_paths():
    paths_list = [paths.data_path(MODEL_FILE)]
    bundled = paths.bundle_path(MODEL_FILE)
    if bundled not in paths_list:
        paths_list.append(bundled)
    return paths_list


def _validate(raw):
    if not isinstance(raw, dict):
        return None
    features = raw.get("features")
    if not (isinstance(features, list) and features):
        return None
    model = {
        "type": str(raw.get("type") or "logistic"),
        "features": [str(x) for x in features],
        "version": raw.get("version"),
        "trained_at": raw.get("trained_at"),
        "n_samples": raw.get("n_samples"),
        "metrics": raw.get("metrics"),
        "date_range": raw.get("date_range"),
    }
    try:
        if model["type"] == "gbdt":
            trees = raw.get("trees") or []
            if not isinstance(trees, list) or not trees:
                return None
            parsed = []
            for t in trees:
                node = {
                    "left": np.asarray(t["left"], dtype=np.int64),
                    "right": np.asarray(t["right"], dtype=np.int64),
                    "feature": np.asarray(t["feature"], dtype=np.int64),
                    "threshold": np.asarray(t["threshold"], dtype=float),
                    "value": np.asarray(t["value"], dtype=float),
                }
                if not (len(node["left"]) == len(node["right"]) == len(node["feature"]) ==
                        len(node["threshold"]) == len(node["value"])):
                    return None
                parsed.append(node)
            model["trees"] = parsed
            model["init_score"] = float(raw.get("init_score", 0.0))
            model["learning_rate"] = float(raw.get("learning_rate", 0.1))
            calib = raw.get("calib")
            if calib:
                model["calib"] = {
                    "thresholds": np.asarray(calib["thresholds"], dtype=float),
                    "targets": np.asarray(calib["targets"], dtype=float),
                }
        else:
            mean = raw.get("mean")
            std = raw.get("std")
            weights = raw.get("weights")
            intercept = raw.get("intercept")
            if not all(isinstance(x, list) for x in (mean, std, weights)):
                return None
            if not (len(features) == len(mean) == len(std) == len(weights)):
                return None
            model["mean"] = np.asarray(mean, dtype=float)
            model["std"] = np.asarray(std, dtype=float)
            model["std"][model["std"] < 1e-8] = 1.0
            model["weights"] = np.asarray(weights, dtype=float)
            model["intercept"] = float(intercept)
    except (TypeError, ValueError, KeyError):
        return None
    return model


def _load():
    for p in _candidate_paths():
        if not os.path.isfile(p):
            continue
        try:
            mtime = os.path.getmtime(p)
            with _LOCK:
                if _CACHE["model"] is not None and _CACHE["mtime"] == mtime:
                    return _CACHE["model"]
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
            model = _validate(raw)
            if model is None:
                continue
            with _LOCK:
                _CACHE.update(mtime=mtime, model=model, err=None)
            return model
        except Exception as e:
            with _LOCK:
                _CACHE["err"] = str(e)
            continue
    with _LOCK:
        _CACHE.update(model=None, mtime=None, err=None)
    return None


def model():
    return _load()


def meta():
    m = _load()
    if not m:
        return None
    return {
        "type": m.get("type"),
        "version": m.get("version"),
        "trained_at": m.get("trained_at"),
        "features": m.get("features"),
        "n_samples": m.get("n_samples"),
        "metrics": m.get("metrics"),
        "date_range": m.get("date_range"),
    }


def model_features():
    m = _load()
    return list(m["features"]) if m else []


def _sig(z):
    z = max(min(z, 50.0), -50.0)
    return 1.0 / (1.0 + math.exp(-z))


def _gbdt_raw(m, x):
    z = float(m["init_score"])
    for tree in m["trees"]:
        node = 0
        left = tree["left"]
        right = tree["right"]
        feat = tree["feature"]
        thr = tree["threshold"]
        while True:
            f = int(feat[node])
            if f < 0 or f >= len(x):
                break
            nxt = int(left[node]) if float(x[f]) <= float(thr[node]) else int(right[node])
            if nxt < 0:
                break
            node = nxt
        z += float(tree["value"][node]) * float(m["learning_rate"])
    return z


def score(features) -> float | None:
    """返回模型概率 [0,1]；特征缺失或模型不可用时返回 None。"""
    m = _load()
    if not m:
        return None
    return score_model(m, features)


def score_model(m, features) -> float | None:
    feats = m["features"]
    try:
        x = np.empty(len(feats), dtype=float)
        for i, name in enumerate(feats):
            v = features.get(name)
            if v is None:
                return None
            try:
                x[i] = float(v)
            except (TypeError, ValueError):
                return None
            if math.isnan(x[i]):
                return None
        if m["type"] == "gbdt":
            p = _sig(_gbdt_raw(m, x))
            calib = m.get("calib")
            if calib is not None:
                p = float(np.interp(p, calib["thresholds"], calib["targets"]))
                p = max(min(p, 1.0), 0.0)
            return p
        x = (x - m["mean"]) / m["std"]
        return _sig(float(x @ m["weights"] + m["intercept"]))
    except (KeyError, TypeError, ValueError, IndexError):
        return None

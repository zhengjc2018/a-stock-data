# -*- coding: utf-8 -*-
"""尾盘买入专属模型：T+1 开盘或盘中最高 >= +3% 的概率。"""
from __future__ import annotations

import json
import os
import threading

import gap_model
import paths

MODEL_FILE = "tail_reach_model.json"

_LOCK = threading.Lock()
_CACHE = {"mtime": None, "model": None}


def _candidate_paths():
    paths_list = [paths.data_path(MODEL_FILE)]
    bundled = paths.bundle_path(MODEL_FILE)
    if bundled not in paths_list:
        paths_list.append(bundled)
    return paths_list


def _load():
    for p in _candidate_paths():
        if not os.path.isfile(p):
            continue
        try:
            mtime = os.path.getmtime(p)
            with _LOCK:
                if _CACHE["model"] is not None and _CACHE["mtime"] == mtime:
                    return _CACHE["model"]
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            model = gap_model._validate(raw)
            if model is None:
                continue
            with _LOCK:
                _CACHE.update(mtime=mtime, model=model)
            return model
        except Exception:
            continue
    return None


def model():
    return _load()


def model_features():
    m = _load()
    return list(m["features"]) if m else []


def score(features):
    m = _load()
    if not m:
        return None
    return gap_model.score_model(m, features)


def meta():
    m = _load()
    if not m:
        return None
    return m.get("metrics")

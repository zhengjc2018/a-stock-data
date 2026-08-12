# -*- coding: utf-8 -*-
"""自动重训 + 模型择优发布/回滚。

流程：
1. 用全市场历史 + outcomes 真实验证样本重训 GBDT
2. 与当前线上模型比较测试集 Top10/AUC
3. 明显更优则发布（旧模型备份到 gap_model_prev.json），否则记录拒绝
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import paths

APP_DIR = paths.APP_DIR
OUT_DIR = paths.bundle_path("outcomes")
HISTORY_FILE = paths.bundle_path("model_history.json")
MODEL_FILE = paths.bundle_path("gap_model.json")
PREV_FILE = paths.bundle_path("gap_model_prev.json")
TRAIN_SCRIPT = paths.bundle_path("train_gap_v2.py")


def _file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:
        return ""


def _load_history():
    if os.path.isfile(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def _save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def current_metrics():
    if not os.path.isfile(MODEL_FILE):
        return None
    try:
        with open(MODEL_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw.get("metrics")
    except Exception:
        return None


def train_candidate(out_path):
    cmd = [
        sys.executable, TRAIN_SCRIPT,
        "--limit", "500",
        "--trees", "150",
        "--depth", "3",
        "--no-zt-heat",
        "--outcomes-dir", OUT_DIR,
        "--out", out_path,
    ]
    subprocess.run(cmd, check=True)
    with open(out_path, encoding="utf-8") as f:
        return json.load(f)


def should_publish(cur, new):
    if not cur:
        return True
    gain_top10 = float(new.get("test_top10") or 0) - float(cur.get("test_top10") or 0)
    gain_auc = float(new.get("test_auc") or 0) - float(cur.get("test_auc") or 0)
    return gain_top10 >= 0.005 or (gain_top10 >= 0 and gain_auc >= 0.003)


def publish(new_path, new_metrics, reason):
    if os.path.isfile(MODEL_FILE):
        shutil.copyfile(MODEL_FILE, PREV_FILE)
    shutil.copyfile(new_path, MODEL_FILE)
    history = _load_history()
    history.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "publish",
        "reason": reason,
        "new_hash": _file_hash(MODEL_FILE),
        "metrics": new_metrics,
    })
    _save_history(history)
    print(f"[auto_train] published, reason={reason}", flush=True)


def reject(new_metrics, reason):
    history = _load_history()
    history.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "reject",
        "reason": reason,
        "metrics": new_metrics,
    })
    _save_history(history)
    print(f"[auto_train] rejected, reason={reason}", flush=True)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    cur = current_metrics()
    print("[auto_train] current", cur, flush=True)
    new_path = os.path.join(OUT_DIR, "..", "gap_candidate.json")
    new_path = os.path.abspath(new_path)
    new_model = train_candidate(new_path)
    new_metrics = new_model.get("metrics") or {}
    print("[auto_train] candidate", new_metrics, flush=True)
    if should_publish(cur, new_metrics):
        reason = "first publish" if not cur else "top10/auc improved"
        publish(new_path, new_metrics, reason)
    else:
        reason = "not better than current model"
        reject(new_metrics, reason)


if __name__ == "__main__":
    main()

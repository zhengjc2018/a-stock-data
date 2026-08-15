# -*- coding: utf-8 -*-
"""自动重训 + 模型择优发布/回滚。

流程：
1. 用全市场历史 + outcomes 真实验证样本重训 GBDT
2. 与当前线上模型比较测试集 Top10/AUC
3. 明显更优则发布（旧模型备份到 gap_model_prev.json），否则记录拒绝
"""
from __future__ import annotations

import hashlib
import argparse
import json
import os
import shutil
import time

import requests

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
    import train_gap_v2

    args = argparse.Namespace(
        codes=None,
        limit=500,
        trees=150,
        depth=3,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out=out_path,
        no_zt_heat=True,
        outcomes_dir=OUT_DIR,
        label_gap=0.03,
    )
    payload = train_gap_v2.train_model(args)
    if payload is None:
        raise RuntimeError("训练样本不足或失败")
    return payload


def download_model():
    """从 GitHub 下载最新 gap_model.json 并更新本地（手机/打包版用）。"""
    raw_url = "https://raw.githubusercontent.com/zhengjc2018/a-stock-data/main/app/gap_model.json"
    urls = [raw_url, "https://gh-proxy.com/" + raw_url]
    target = paths.data_path("gap_model.json")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    for url in urls:
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                continue
            raw = r.json()
            if not (isinstance(raw, dict) and raw.get("type") == "gbdt"
                    and raw.get("features") and raw.get("trees")):
                continue
            with open(target, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
            history = _load_history()
            history.append({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "action": "remote_update",
                "reason": "downloaded from GitHub",
                "source": url,
                "new_hash": _file_hash(target),
                "metrics": raw.get("metrics"),
            })
            _save_history(history)
            return raw.get("metrics") or {}
        except Exception:
            continue
    raise RuntimeError("无法从 GitHub 下载模型，请检查网络")


def should_publish(cur, new):
    if not cur:
        return True
    gain_top1 = float(new.get("test_top1") or 0) - float(cur.get("test_top1") or 0)
    gain_top3 = float(new.get("test_top3") or 0) - float(cur.get("test_top3") or 0)
    gain_top10 = float(new.get("test_top10") or 0) - float(cur.get("test_top10") or 0)
    gain_auc = float(new.get("test_auc") or 0) - float(cur.get("test_auc") or 0)
    return (gain_top10 >= 0.005 or gain_top3 >= 0.02 or gain_top1 >= 0.03
            or (gain_top10 >= 0 and gain_auc >= 0.003))


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
        reason = "first publish" if not cur else "metrics improved"
        publish(new_path, new_metrics, reason)
        return {"action": "publish", "reason": reason, "metrics": new_metrics}
    else:
        reason = "not better than current model"
        reject(new_metrics, reason)
        return {"action": "reject", "reason": reason, "metrics": new_metrics}


if __name__ == "__main__":
    main()

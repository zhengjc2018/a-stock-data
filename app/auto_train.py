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

import gap_model
import paths

APP_DIR = paths.APP_DIR
OUT_DIR = paths.bundle_path("outcomes")
HISTORY_FILE = paths.bundle_path("model_history.json")
MODEL_FILE = paths.bundle_path("gap_model.json")
PREV_FILE = paths.bundle_path("gap_model_prev.json")
TAIL_MODEL_FILE = paths.bundle_path("tail_reach_model.json")
TAIL_PREV_FILE = paths.bundle_path("tail_reach_model_prev.json")
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


def current_tail_metrics():
    if not os.path.isfile(TAIL_MODEL_FILE):
        return None
    try:
        with open(TAIL_MODEL_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return raw.get("metrics")
    except Exception:
        return None


def _load_validated(path):
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return gap_model._validate(json.load(f))


def rolling_metrics_for_model(model_path, df):
    """用滚动窗口统计一个模型在样本外的 Top3 净命中稳定性。"""
    import train_gap_v2

    model = _load_validated(model_path)
    if model is None or df is None:
        return None
    dates = sorted(df["date"].unique())
    if len(dates) < 150:
        return None
    feature_names = model["features"]
    values = []
    for i in range(120, len(dates), 30):
        cutoff = dates[i - 1]
        end = dates[min(i - 1 + 30, len(dates) - 1)]
        test = df[(df["date"] > cutoff) & (df["date"] <= end)].copy()
        if len(test) < 30:
            continue
        probs = []
        for rec in test.to_dict("records"):
            feats = {k: rec.get(k) for k in feature_names}
            probs.append(gap_model.score_model(model, feats))
        test["prob"] = probs
        top3 = train_gap_v2.topk_rates(test, "prob", (3,), "label_net")[3]
        values.append(top3)
    if not values:
        return None
    return {
        "windows": len(values),
        "mean": round(float(sum(values)) / len(values), 4),
        "min": round(float(min(values)), 4),
        "max": round(float(max(values)), 4),
    }


def train_candidate(out_path, limit=500):
    import train_gap_v2

    args = argparse.Namespace(
        codes=None,
        limit=limit,
        trees=150,
        depth=3,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out=out_path,
        no_zt_heat=True,
        outcomes_dir=OUT_DIR,
        label_gap=0.03,
        reach=False,
    )
    payload = train_gap_v2.train_model(args)
    if payload is None:
        raise RuntimeError("训练样本不足或失败")
    return payload


def train_tail_candidate(out_path, limit=500):
    import train_gap_v2

    args = argparse.Namespace(
        codes=None,
        limit=limit,
        trees=150,
        depth=3,
        start="2024-08-01",
        end=time.strftime("%Y-%m-%d"),
        out=out_path,
        no_zt_heat=True,
        outcomes_dir=OUT_DIR,
        label_gap=0.03,
        reach=True,
    )
    payload = train_gap_v2.train_model(args)
    if payload is None:
        raise RuntimeError("尾盘模型训练样本不足或失败")
    return payload


def download_model():
    """从 GitHub 下载最新 gap_model/tail_reach_model 并更新本地（手机/打包版用）。"""
    remote_models = [
        ("gap_model.json", MODEL_FILE),
        ("tail_reach_model.json", TAIL_MODEL_FILE),
    ]
    os.makedirs(os.path.dirname(MODEL_FILE), exist_ok=True)
    downloaded = {}
    for remote_name, target in remote_models:
        raw_url = f"https://raw.githubusercontent.com/zhengjc2018/a-stock-data/main/app/{remote_name}"
        urls = [raw_url, "https://gh-proxy.com/" + raw_url]
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
                    "reason": f"downloaded {remote_name} from GitHub",
                    "source": url,
                    "new_hash": _file_hash(target),
                    "metrics": raw.get("metrics"),
                })
                _save_history(history)
                downloaded[remote_name] = raw.get("metrics") or {}
                break
            except Exception:
                continue
        if remote_name not in downloaded:
            raise RuntimeError(f"无法从 GitHub 下载 {remote_name}，请检查网络")
    return {"gap": downloaded.get("gap_model.json"),
            "tail": downloaded.get("tail_reach_model.json")}


def should_publish(cur, new):
    if not cur:
        return True
    raw_keys = ("test_top1", "test_top3", "test_top10")
    net_keys = ("test_top1_net", "test_top3_net", "test_top10_net")
    raw_gains = [
        float(new.get(k) or 0) - float(cur.get(k) or 0)
        for k in raw_keys
    ]
    gain_auc = float(new.get("test_auc") or 0) - float(cur.get("test_auc") or 0)
    has_net = all(k in cur and k in new for k in net_keys)
    net_gains = [
        float(new.get(k) or 0) - float(cur.get(k) or 0)
        for k in net_keys
    ] if has_net else [0.0, 0.0, 0.0]
    improved_raw = max(raw_gains) >= 0.01
    no_raw_regression = all(g >= -0.005 for g in raw_gains)
    improved_net = has_net and max(net_gains) >= 0.02
    no_net_regression = not has_net or all(g >= -0.01 for g in net_gains)
    auc_ok = gain_auc >= -0.002
    return (
        (improved_raw or improved_net)
        and no_raw_regression
        and no_net_regression
        and auc_ok
    )


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


def publish_tail(new_path, new_metrics, reason):
    if os.path.isfile(TAIL_MODEL_FILE):
        try:
            shutil.copyfile(TAIL_MODEL_FILE, TAIL_PREV_FILE)
        except Exception:
            pass
    shutil.copyfile(new_path, TAIL_MODEL_FILE)
    history = _load_history()
    history.append({
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "publish_tail",
        "reason": reason,
        "new_hash": _file_hash(TAIL_MODEL_FILE),
        "metrics": new_metrics,
    })
    _save_history(history)
    print(f"[auto_train] published tail model, reason={reason}", flush=True)


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=("gap", "tail", "both"), default="both")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--rolling", action="store_true",
                    help="发布前额外跑滚动样本外验证，要求稳定性达标")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    if args.model in ("gap", "both"):
        cur = current_metrics()
        print("[auto_train] current gap", cur, flush=True)
        new_path = os.path.abspath(os.path.join(OUT_DIR, "..", "gap_candidate.json"))
        new_model = train_candidate(new_path, args.limit)
        new_metrics = new_model.get("metrics") or {}
        print("[auto_train] gap candidate", new_metrics, flush=True)
        rolling_ok = True
        rolling = None
        if args.rolling:
            import train_gap_v2
            data_args = argparse.Namespace(
                codes=None, limit=args.limit, start="2024-08-01",
                end=time.strftime("%Y-%m-%d"), out="/tmp/auto_rolling_tmp.json",
                no_zt_heat=True, outcomes_dir=OUT_DIR, label_gap=0.03,
                trees=150, depth=3, reach=False)
            df = train_gap_v2.prepare_data(data_args)
            cur_roll = rolling_metrics_for_model(MODEL_FILE, df)
            new_roll = rolling_metrics_for_model(new_path, df)
            rolling = {"current": cur_roll, "candidate": new_roll}
            print("[auto_train] rolling", rolling, flush=True)
            if cur_roll and new_roll:
                rolling_ok = (new_roll["mean"] >= 0.75 and
                              new_roll["mean"] >= cur_roll["mean"] - 0.02)
        if should_publish(cur, new_metrics) and rolling_ok:
            reason = "first publish" if not cur else "metrics improved"
            if args.rolling and rolling and rolling.get("candidate"):
                reason += f" | rolling mean={rolling['candidate']['mean']}"
            publish(new_path, new_metrics, reason)
            results["gap"] = {"action": "publish", "reason": reason,
                              "metrics": new_metrics, "rolling": rolling}
        else:
            reason = "not better than current model"
            if args.rolling and rolling and not rolling_ok:
                reason += " or rolling stability not met"
            reject(new_metrics, reason)
            results["gap"] = {"action": "reject", "reason": reason,
                              "metrics": new_metrics, "rolling": rolling}
    if args.model in ("tail", "both"):
        cur = current_tail_metrics()
        print("[auto_train] current tail", cur, flush=True)
        new_path = os.path.abspath(os.path.join(OUT_DIR, "..", "tail_candidate.json"))
        new_model = train_tail_candidate(new_path, args.limit)
        new_metrics = new_model.get("metrics") or {}
        print("[auto_train] tail candidate", new_metrics, flush=True)
        rolling_ok = True
        rolling = None
        if args.rolling:
            import train_gap_v2
            data_args = argparse.Namespace(
                codes=None, limit=args.limit, start="2024-08-01",
                end=time.strftime("%Y-%m-%d"), out="/tmp/auto_rolling_tmp.json",
                no_zt_heat=True, outcomes_dir=OUT_DIR, label_gap=0.03,
                trees=150, depth=3, reach=True)
            df = train_gap_v2.prepare_data(data_args)
            cur_roll = rolling_metrics_for_model(TAIL_MODEL_FILE, df)
            new_roll = rolling_metrics_for_model(new_path, df)
            rolling = {"current": cur_roll, "candidate": new_roll}
            print("[auto_train] tail rolling", rolling, flush=True)
            if cur_roll and new_roll:
                rolling_ok = (new_roll["mean"] >= 0.75 and
                              new_roll["mean"] >= cur_roll["mean"] - 0.02)
        if should_publish(cur, new_metrics) and rolling_ok:
            reason = "first publish" if not cur else "tail metrics improved"
            if args.rolling and rolling and rolling.get("candidate"):
                reason += f" | rolling mean={rolling['candidate']['mean']}"
            publish_tail(new_path, new_metrics, reason)
            results["tail"] = {"action": "publish", "reason": reason,
                               "metrics": new_metrics, "rolling": rolling}
        else:
            reason = "not better than current tail model"
            if args.rolling and rolling and not rolling_ok:
                reason += " or rolling stability not met"
            reject(new_metrics, reason)
            results["tail"] = {"action": "reject", "reason": reason,
                               "metrics": new_metrics, "rolling": rolling}
    return results


if __name__ == "__main__":
    main()

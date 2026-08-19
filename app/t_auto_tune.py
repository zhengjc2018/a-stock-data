# -*- coding: utf-8 -*-
"""盘后自动重跑持仓个股做T参数优化。

用法：
  python t_auto_tune.py
"""
from __future__ import annotations

import json
import os
import time

import do_t
import paths
import t_strategy


def run(force=False):
    state = do_t.load_state()
    holdings = state.get("holdings") or []
    if not holdings:
        return {"updated": 0, "results": []}
    out_dir = do_t.T_PARAMS_DIR
    os.makedirs(out_dir, exist_ok=True)
    results = []
    updated = 0
    for h in holdings:
        code = str(h.get("code", "")).zfill(6)
        if not code:
            continue
        path = os.path.join(out_dir, f"t_params_{code}.json")
        if not force and os.path.isfile(path):
            age_hours = (time.time() - os.path.getmtime(path)) / 3600
            if age_hours < 6:
                results.append({"code": code, "skipped": True, "age_hours": round(age_hours, 1)})
                continue
        symbol, secid = do_t._symbol_secid(code)
        try:
            payload = t_strategy.optimize_code(code, symbol, secid, out_dir, write=False)
            if payload:
                if payload.get("improved"):
                    t_strategy.save_params(payload, out_dir)
                    updated += 1
                results.append({
                    "code": code,
                    "skipped": False,
                    "trend": payload.get("profile", {}).get("trend"),
                    "volatility": payload.get("profile", {}).get("volatility"),
                    "improved": payload.get("improved"),
                    "saved": bool(payload.get("improved")),
                })
            else:
                results.append({"code": code, "skipped": False, "error": "no payload"})
        except Exception as e:
            results.append({"code": code, "skipped": False, "error": str(e)})
    return {"updated": updated, "results": results}


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run(), ensure_ascii=False, indent=2))

"""Android 内置后端入口：准备数据目录并启动 Flask。"""

from __future__ import annotations

import os
import threading


def _seed_defaults() -> None:
    import paths

    data = os.environ.get("APANEL_DATA_DIR") or paths.data_dir()
    os.environ["APANEL_DATA_DIR"] = data
    paths.ensure_data_dir()


def start(host: str = "127.0.0.1", port: int = 5050, data_dir: str = None) -> None:
    if data_dir:
        os.environ["APANEL_DATA_DIR"] = data_dir
    _seed_defaults()

    from server import app, start_background

    start_background()

    def _run() -> None:
        app.run(host=host, port=int(port), threaded=True)

    threading.Thread(target=_run, daemon=True).start()

# A股高开雷达

基于 a-stock-data 数据端点的轻量单页仪表盘，核心功能为「次日个股高开推荐」。

## 本地运行

```bash
cd app
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
APANEL_PORT=5050 .venv/bin/python server.py
```

浏览器打开 `http://127.0.0.1:5050/`。

macOS 也可直接双击 `start.command`。

## 次日高开推荐

首次打开会在后台执行全市场扫描，约 1.5-3 分钟出结果。算法沿用 hks 的
`gap_pick.py + gap_model.json`：新浪/东财全市场快照 + 通达信日 K 特征 + 逻辑回归概率排序。

## 打包

### APK（本机）

```bash
cd app/android
JAVA_HOME=/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home \
  ./gradlew assembleDebug -PbuildPython=/opt/homebrew/bin/python3.11
```

产物：`app/android/app/build/outputs/apk/debug/app-debug.apk`

### EXE（GitHub Actions）

推送代码后手动运行 `.github/workflows/build-windows-exe.yml`，Windows 构建机会产出
`AStockDataApp.exe` 并上传 artifact。

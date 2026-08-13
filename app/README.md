# A股高开雷达

基于 a-stock-data 数据端点的轻量单页仪表盘，核心功能为「次日个股高开推荐」。

页面包含：市场总览、打板情绪（涨停/炸板/跌停/昨日涨停/重点监控/日内异动）、
板块资金流（行业/概念/地域 × 今日/5日/10日）、热榜与资讯、全市场龙虎榜、
次日高开推荐、个股雷达（估值/研报/两融/大宗/股东户数/分红/资金流/互动易/新闻/
概念/公告/财报）、ETF 期权 T 型报价（IV + 希腊字母）。

次日高开排序 v2：GBDT 150 棵树 + isotonic 概率校准，测试集 Top1 命中 45.9%、
Top3 66.2%、Top10 86.5%（基准 8.1%），模型由 `train_gap_v2.py` 离线训练，
可通过页面「策略健康」的“手动优化模型”按钮触发重训与择优发布。

## 自优化闭环

`daily_loop.py` 每天自动运行：

- 18:10 记录当日 Top50 候选到 `outcomes/candidates_*.json`
- 次日 09:35 用真实开盘价验证高开命中，写入 `outcomes/hits_*.json`
- 周日 18:10 自动重训，并在模型明显更优时发布（旧模型备份到
  `gap_model_prev.json`，历史记录写入 `model_history.json`）

macOS 安装定时任务：

```bash
cp app/com.astockdata.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.astockdata.daily.plist
```

页面「策略健康」页签展示当前模型指标、近 30 天真实命中率、模型发布历史和每日验证明细。

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

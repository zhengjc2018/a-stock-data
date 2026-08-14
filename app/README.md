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

后台常驻服务（网页 + 做T监控）：

```bash
cp app/com.astockdata.server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.astockdata.server.plist
```

服务由 launchd 保活，关闭终端后仍会运行，崩溃会自动重启。

做T监控说明：App/网页进程活着时每 1 分钟跑一轮；服务/App 重启后会自动恢复
“已启动监控”状态。Android 上如果进程被系统杀掉或强制停止，需要重新打开 App；
Android APK 已内置前台服务（常驻通知），App 退到后台时进程会被保活，
做T监控可继续每分钟运行；系统强杀或重启手机后仍需重新打开 App。
做T信号在安卓上通过前台服务轮询 `t_notify.json` 并弹出系统通知。

页面「策略健康」页签展示当前模型指标、近 30 天真实命中率、模型发布历史和每日验证明细。

次日高开推荐默认不自动计算：页面刷新不会触发扫描，需要点击「立即计算」手动运行；
每日 18:10 的定时任务仍会自行记录候选。

## 做T信号回测

- v1 指标共振（VWAP/RSI/BOLL/量能）：买胜率 51.4%、卖胜率 50.6%
- v2 强信号（竞价量价 + 主力资金流 + 大盘共振 + 量价背离）：
  买 141 次胜率 57.4%、卖 41 次胜率 53.7%

回测脚本：`backtest_t.py`（v1）、`backtest_t2.py`（v2），口径为 +0.5%/-0.5%、
60 分钟目标止损。v2 信号更少但胜率更高，仍建议先模拟盘观察再自动执行。

### 个股趋势画像与参数优化（二次优化回测）

`t_strategy.py` 对每只股票计算趋势/波动/量能画像，并在 5 分钟历史数据上做
“前 70% 选参、后 30% 验证”的参数网格优化。8 只样本汇总：

- 全局默认参数：775 次信号，胜率 56.1%
- 个股优化参数：372 次信号，胜率 58.6%

优化版胜率略高但信号少了一半，且茅台/平安银行等个股优化后反而变差；
建议采用“混合策略”：只有个股验证集提升明显且信号数足够时才启用优化参数，
否则回退全局默认参数。

## 做T模拟盘

页面「做T」可添加持仓（代码/名称/成本/数量），启动后每 1 分钟检查一次信号：

- 添加持仓后自动进行个股特征分析（趋势/波动/量能/参数/回测胜率），并在页面展示
  （先用纯量化快速算出趋势画像，参数优化在后台继续跑；不使用 LLM，无需配置大模型）
- 代码/名称输入框支持实时自动补全（腾讯搜索接口）
- 信号引擎：VWAP + RSI + BOLL + 量能 + 主力资金流 + 大盘共振
- 优先使用 `t_params/` 中个股优化参数；未优化或验证不达标的股票回退默认参数
- 触发信号后推送系统通知，并默认自动模拟成交（单次 1/3 仓位，单票每日最多 3 次）
- 持仓、信号、成交记录保存在 `t_holdings.json`（本地状态，不入库）

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

# 量化升级记录：吸收开源项目提升选股/做T胜率

目标：把“次日高开 +3%”选股与做T信号从“感觉可用”推进到“回测口径真实、滚动验证稳定、持续择优发布”。

## 评估过的开源项目

| 项目 | 可吸收点 | 结论 |
|------|----------|------|
| [microsoft/qlib](https://github.com/microsoft/qlib) | Alpha158 因子库、滚动重训、TopK 分层 | 吸收因子构建思路；不直接引入完整依赖 |
| [yupoet/aurumq-rl](https://github.com/yupoet/aurumq-rl) | Alpha101 + Alpha191 因子、涨停/ST/行业/北向/龙虎榜/筹码 | 吸收技术/量价因子子集，防过拟合 |
| [guoyaohua/limit-up-sniper](https://github.com/guoyaohua/limit-up-sniper) | 涨停基因评分、逐层确认漏斗、复盘验证 | 已吸收为 6 层确认漏斗 |
| [123quant/QMT-QuantLimit](https://github.com/123quant/QMT-QuantLimit) | 打板/隔日溢价回测 | 口径借鉴，不引入 QMT 实盘依赖 |
| [WolkenZhen/cn-stock-agent](https://github.com/WolkenZhen/cn-stock-agent) | 回测计入 0.15% 交易成本、支撑位止损 | 已吸收为成本修正与止损口径 |

以下项目因强依赖、策略差异或实盘通道不兼容，暂只作学习参考，未吸收：
`charliedream1/ai_quant_trade`、`maybeshewill-cv/financial-agent`、`quant-a-stock/*`、`008karan/*`。

## 已落地改动

1. **真实口径回测**：`app/realistic_backtest.py` 对每笔推荐按“止盈/止损/收盘退出”模拟，默认计入 0.3% 往返成本。
2. **Alpha 因子扩展**：在原有技术/形态/市场特征上新增波动率、量价相关、动量斜率、连涨连跌、突破次数、资金流向代理等 30+ 因子。
3. **6 层确认漏斗**：大盘、板块、量能、主力资金、情绪、位置逐层确认，得分不足且资金流出时强惩罚。
4. **尾盘模型纳入自优化**：自动重训同时覆盖 `gap_model`（次日开盘）和 `tail_reach_model`（盘中达 +3%），择优发布才生效。
5. **净口径择优**：模型发布比较同时看原始 TopK 与扣费后的净 TopK，避免“假提升”。
6. **滚动样本外验证**：`app/rolling_eval.py` 按交易日滚动训练，统计 Top3 净命中率的均值/中位数/波动。
7. **做T成本修正**：做T参数优化与回测统一扣除往返成本，筛选出的参数更贴近实盘。

## 使用方式

```bash
cd app
.venv/bin/python realistic_backtest.py --limit 500
.venv/bin/python rolling_eval.py --limit 300 --step 30 --horizon 30
.venv/bin/python auto_train.py --model both
```

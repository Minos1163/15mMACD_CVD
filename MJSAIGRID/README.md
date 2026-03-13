# AIGRID

AI-enhanced crypto futures trading system focused on directional moving grid execution, fund-flow based biasing, and layered risk control.

当前仓库的实盘主链已经不是旧版静态网格，而是 `MOVING_FUTURES_GRID`：资金流引擎负责判断开仓方向，执行层负责维护移动合约网格、持仓后的分批止盈梯子，以及统一风控与保护单。

## What This Repository Does

- 通过多周期市场数据、资金流、盘口与趋势因子判断开仓方向。
- 使用移动合约网格在趋势方向上挂入场单。
- 持仓后继续维护分批止盈单与同向补仓单。
- 使用统一风控、保护单 SLA、冲突退出与账户级熔断保护实盘。
- 输出结构化日志，方便回溯每一轮 K 线决策与执行行为。

## Current Strategy

当前默认运行模式来自 [config/trading_config_fund_flow.json](/d:/AIDCA/AIGRID/config/trading_config_fund_flow.json#L1)：

- `strategy.mode = MOVING_FUTURES_GRID`
- `symbol = DOGEUSDT`
- `leverage = 2x`
- `grid_type = arithmetic`
- `active_order_count = 2`
- `position_exit_order_count = 2`

这条策略链的核心形态是：

1. 资金流引擎在 [decision_engine.py](/d:/AIDCA/AIGRID/src/fund_flow/decision_engine.py#L656) 输出 `LONG_ONLY / SHORT_ONLY / BOTH`。
2. 实盘机器人在 [fund_flow_bot.py](/d:/AIDCA/AIGRID/src/app/fund_flow_bot.py#L4488) 同步移动网格挂单。
3. 持仓后，机器人继续维护分批止盈网格，而不是只保留固定止损。
4. 风控由 [risk_controller.py](/d:/AIDCA/AIGRID/src/risk/risk_controller.py#L58) 和执行层保护逻辑共同兜底。

## Architecture

- [main.py](/d:/AIDCA/AIGRID/src/main.py#L1)
  运行入口。读取配置并自动选择 `grid / fund_flow / moving_grid` 运行时。
- [fund_flow_bot.py](/d:/AIDCA/AIGRID/src/app/fund_flow_bot.py#L1)
  当前实盘主机器人。负责调度、风控接入、方向判断、移动网格同步、保护单维护和日志落盘。
- [decision_engine.py](/d:/AIDCA/AIGRID/src/fund_flow/decision_engine.py#L656)
  方向决策核心。根据多周期行情与资金流上下文判断允许的开仓方向。
- [risk_controller.py](/d:/AIDCA/AIGRID/src/risk/risk_controller.py#L58)
  统一风控控制器。负责启动检查、杠杆限制、下单前风控闸门等。
- [trading_config_fund_flow.json](/d:/AIDCA/AIGRID/config/trading_config_fund_flow.json#L1)
  当前实盘配置源。

## Project Structure

```text
AIGRID/
├─ config/                 # 策略与风控配置
├─ docs/                   # 设计文档、部署文档、日志说明
├─ src/
│  ├─ app/                 # 机器人运行时
│  ├─ fund_flow/           # 资金流与方向判断
│  ├─ grid_trading/        # 历史网格模块与模型
│  ├─ risk/                # 风控控制器与风控接入
│  └─ trading/             # 下单网关、交易执行、风控执行
└─ README.md               # GitHub 首页说明
```

## Quick Start

### 1. Prepare environment

- Python 3.10+
- Binance futures API credentials
- 实盘前确认配置文件、风控参数、交易对和杠杆设置正确

### 2. Main config

当前默认配置文件：

- [trading_config_fund_flow.json](/d:/AIDCA/AIGRID/config/trading_config_fund_flow.json#L1)

### 3. Run one cycle

```bash
python src/main.py --config config/trading_config_fund_flow.json --once
```

### 4. Run live loop

```bash
python src/main.py --config config/trading_config_fund_flow.json
```

更完整的 VPS 启动、systemd、巡检与日志说明见：

- [18_VPS实盘部署清单.md](/d:/AIDCA/AIGRID/docs/18_VPS实盘部署清单.md#L1)

## Important Config Knobs

在 [trading_config_fund_flow.json](/d:/AIDCA/AIGRID/config/trading_config_fund_flow.json#L1) 里，实盘最关键的参数通常是：

- `trading.default_leverage` / `trading.max_leverage`
- `risk.max_daily_loss_percent`
- `grid.grid_type`
- `grid.active_order_count`
- `grid.position_exit_order_count`
- `grid.budget_portion`
- `grid.auto_range_pct`
- `fund_flow.default_target_portion`
- `fund_flow.add_position_portion`
- `fund_flow.max_symbol_position_portion`
- `fund_flow.stop_loss_pct`

按当前默认配置，如果账户权益约为 `100 USDT` 且杠杆为 `2x`，单笔挂单目标名义价值大致在 `25 USDT` 左右。

## Logging

当前运行时会输出结构化调试日志，重点包括：

- `kline_debug_utc.jsonl`
- `kline_debug_utc.csv`
- `trade_fills_utc.csv`
- `deepseek_usage_utc.jsonl`
- `deepseek_usage_utc.csv`

日志路径与字段说明参考：

- [18_VPS实盘部署清单.md](/d:/AIDCA/AIGRID/docs/18_VPS实盘部署清单.md#L282)
- [ENHANCED_KLINE_LOGGER_GUIDE.md](/d:/AIDCA/AIGRID/docs/ENHANCED_KLINE_LOGGER_GUIDE.md#L1)
- [log_fields.md](/d:/AIDCA/AIGRID/docs/log_fields.md#L1)

## Documentation Map

- [01_项目概述.md](/d:/AIDCA/AIGRID/docs/01_项目概述.md#L1)
- [03_网格交易概念与术语.md](/d:/AIDCA/AIGRID/docs/03_网格交易概念与术语.md#L1)
- [05_AI信号模块设计.md](/d:/AIDCA/AIGRID/docs/05_AI信号模块设计.md#L1)
- [06_风控系统设计.md](/d:/AIDCA/AIGRID/docs/06_风控系统设计.md#L1)
- [07_交易执行引擎设计.md](/d:/AIDCA/AIGRID/docs/07_交易执行引擎设计.md#L1)
- [08_参数配置字典.md](/d:/AIDCA/AIGRID/docs/08_参数配置字典.md#L1)
- [策略逻辑梳理.md](/d:/AIDCA/AIGRID/docs/策略逻辑梳理.md#L1)
- [18_VPS实盘部署清单.md](/d:/AIDCA/AIGRID/docs/18_VPS实盘部署清单.md#L1)

## Safety Notes

- 这是实盘交易代码，不应在不了解风控参数的情况下直接启动。
- 改动杠杆、仓位比例、止损和网格层数前，应先理解当前配置的联动关系。
- 如果交易所已有旧仓位或旧杠杆设置，脚本配置不会自动修正交易所历史状态。
- 上线前至少先跑一次 `--once`，确认日志、保护单、方向判断和配置加载都正常。

## Current Status

这个仓库当前更准确的描述是：

- 不是传统静态双边现货网格。
- 是“资金流定方向 + 等差移动期货网格 + 持仓后退出梯子 + 统一风控”的实盘框架。
- 当前 README 按仓库现状撰写，适合直接作为 GitHub 首页说明继续迭代。
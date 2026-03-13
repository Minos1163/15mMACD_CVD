# VPS实盘部署清单

本文基于当前代码实现整理，只覆盖当前已经存在并可用的启动入口、环境变量、日志与巡检项。

## 1. 当前实盘入口

推荐入口：

```bash
python src/main.py --config config/trading_config_fund_flow.json
```

原因：

- `src/main.py` 会优先读取配置中的 `strategy.mode`
- 当前 `config/trading_config_fund_flow.json` 已设置为 `MOVING_FUTURES_GRID`
- 因此虽然文件名仍叫 `trading_config_fund_flow.json`，运行时已经进入移动合约网格分支

当前配置默认已关闭二次确认：

- `startup.live_confirmation_enabled=false`
- VPS 可直接启动，不再需要 `--confirm-live`
- 如果后续想临时恢复，再传 `--enable-live-confirmation`

## 2. 最小环境变量

### 必需

```bash
BINANCE_API_KEY=你的币安API Key
BINANCE_SECRET=你的币安API Secret
```

### 推荐

```bash
TRADING_BOT_ENV_FILE=/opt/aigrid/.env
TRADING_CONFIG_FILE=/opt/aigrid/config/trading_config_fund_flow.json
```

说明：

- `TRADING_BOT_ENV_FILE` 用于显式指定 `.env`
- `TRADING_CONFIG_FILE` 用于守护进程场景固定配置路径

### 条件可选

只有你启用了相应能力时才需要：

```bash
DEEPSEEK_API_KEY=你的DeepSeek Key
BINANCE_ACCOUNT_MODE=ONEWAY
BINANCE_PROXY=http://127.0.0.1:7890
BINANCE_HTTP_PROXY=http://127.0.0.1:7890
BINANCE_HTTPS_PROXY=http://127.0.0.1:7890
BINANCE_CLOSE_PROXY=http://127.0.0.1:7890
BINANCE_CLOSE_USE_PROXY=1
BINANCE_PROXY_FALLBACK=1
BINANCE_VERBOSE_REQ=1
BINANCE_VERBOSE_OPEN_RESULT=1
```

说明：

- 当前配置文件里 `network.force_direct=true` 且 `network.disable_proxy=true`
- 也就是说默认会强制直连并禁用环境代理
- 只有在 VPS 网络需要代理时，才建议重新调整 `config` 或临时改环境变量排查

## 3. VPS推荐目录

假设项目部署在：

```bash
/opt/aigrid
```

建议结构：

```bash
/opt/aigrid
  ├─ .venv
  ├─ .env
  ├─ config/
  ├─ docs/
  ├─ logs/
  └─ src/
```

## 4. 首次安装

```bash
cd /opt/aigrid
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

建议先做一次静态校验：

```bash
. .venv/bin/activate
python -m py_compile src/main.py src/app/fund_flow_bot.py src/fund_flow/decision_engine.py
```

## 5. 启动命令

### 5.1 交互式首轮巡检

先跑一轮单周期：

```bash
cd /opt/aigrid
. .venv/bin/activate
python src/main.py \
  --config config/trading_config_fund_flow.json \
  --once
```

说明：

- 这个命令适合首次上线前人工盯屏
- `--once` 只跑一个 cycle，方便检查日志、参数、挂单行为

### 5.2 交互式连续运行

```bash
cd /opt/aigrid
. .venv/bin/activate
python src/main.py \
  --config config/trading_config_fund_flow.json
```

### 5.3 非交互后台运行

```bash
cd /opt/aigrid
. .venv/bin/activate
python src/main.py --config config/trading_config_fund_flow.json
```

## 6. systemd服务模板

文件：

```bash
/etc/systemd/system/aigrid-live.service
```

内容：

```ini
[Unit]
Description=AIGRID Live Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/aigrid
Environment=TRADING_BOT_ENV_FILE=/opt/aigrid/.env
Environment=TRADING_CONFIG_FILE=/opt/aigrid/config/trading_config_fund_flow.json
ExecStart=/opt/aigrid/.venv/bin/python /opt/aigrid/src/main.py --config /opt/aigrid/config/trading_config_fund_flow.json
Restart=always
RestartSec=10
User=aigrid
Group=aigrid

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable aigrid-live
sudo systemctl start aigrid-live
sudo systemctl status aigrid-live
```

实时看日志：

```bash
sudo journalctl -u aigrid-live -f
```

## 7. 上线前巡检清单

### 7.1 账户与交易所

- API 已开启合约权限
- API 未开启提现权限
- VPS IP 已加入 Binance API 白名单
- 实盘账户模式与你策略预期一致
- 账户可用余额充足，且你接受当前 `budget_portion`

### 7.2 配置文件

重点检查 `config/trading_config_fund_flow.json`：

- `strategy.mode` 必须为 `MOVING_FUTURES_GRID`
- `symbols` 是否只保留你要交易的币种
- `grid.grid_count`
- `grid.active_order_count`
- `grid.budget_portion`
- `grid.leverage`
- `grid.neutral_action`
- `risk.max_daily_loss_percent`
- `risk.max_consecutive_losses`
- `risk.stop_loss_default_percent`
- `schedule.align_to_kline_close`
- `schedule.interval_seconds`
- `network.force_direct`
- `network.disable_proxy`
- `startup.live_confirmation_enabled=false`

### 7.3 策略认知确认

当前实现是：

- 资金流只负责判断开仓方向
- 执行层是单边移动入场网格
- 持仓后的退出主要依赖 TP/SL 与保护单修复逻辑

当前还不是：

- 经典成交后一格自动补反向止盈格的完整双向期货网格

如果你接受的是“方向型移动合约网格”，可以直接实盘。

## 8. 启动后巡检清单

### 8.1 启动输出必须看到

- `COMPAT RUNTIME MODE`
- `运行模式: MOVING_FUTURES_GRID`
- `日志目录`
- `成交回报日志(UTC)`
- `K线调试JSONL(UTC)`
- `K线调试CSV(UTC)`

### 8.2 首轮cycle必须确认

- 没有 `BLOCKED`
- 没有 `API凭证未配置`
- 没有 `config file not found`
- 没有连续的 Binance 连接异常
- 没有大量下单拒绝或最小名义价值报错

### 8.3 交易行为检查

跑完一个 `--once` 后检查：

- 是否按预期只在单边方向铺单
- 挂单数量是否接近 `active_order_count`
- 无方向时是否真的 `pause`
- 已有持仓时保护单是否齐全

## 9. 重点日志位置

### 9.1 运行日志

控制台日志会同时落盘到 6 小时分桶文件，根目录默认是：

```bash
logs/YYYY-MM/YYYY-MM-DD/runtime.out.00.log
logs/YYYY-MM/YYYY-MM-DD/runtime.err.00.log
```

### 9.2 策略与调试日志

默认在：

```bash
logs/YYYY-MM/YYYY-MM-DD/fund_flow/
```

重点看：

- `trade_fills_utc.csv`
- `kline_debug_utc.jsonl`
- `kline_debug_utc.csv`
- `deepseek_usage_utc.jsonl`
- `deepseek_usage_utc.csv`
- `fund_flow_risk_state.json`
- `protection_sla_alerts.log`

## 10. 推荐上线顺序

### 第一步

先用交互模式跑一次：

```bash
python src/main.py --config config/trading_config_fund_flow.json --once
```

### 第二步

确认：

- 启动摘要正确
- 市场数据正常
- 方向判定正常
- 没有异常撤单/重复挂单

### 第三步

再切连续运行：

```bash
python src/main.py --config config/trading_config_fund_flow.json
```

### 第四步

最后再挂到 `systemd`

## 11. 当前最值得盯的风险点

- 当前不是完整“成交后自动补反向止盈格”的经典网格
- `take_profit_default_percent=0.0` 时，更依赖保护单和风控退出，不是固定止盈模式
- 配置中当前交易对数量较多时，首日建议缩小 `symbols` 或降低 `budget_portion`
- 实盘首日建议先人工值守，至少观察 3 到 5 个完整 cycle

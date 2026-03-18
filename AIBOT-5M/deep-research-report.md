# DS3 与 AIBOT-5M 24小时未开仓问题深度研究报告

## 执行摘要

本次研究基于已启用的 **GitHub 连接器**对两个代码库进行静态审计与配置审阅：**Minos1163/DS3** 以及用户指定目录 **Minos1163/15mMACD_CVD/AIBOT-5M**（下称 AIBOT-5M）。AIBOT-5M在结构上是一个“只跑 FUND_FLOW 路径”的实盘运行时，入口为 `AIBOT-5M/src/main.py`，主循环与信号、风控、执行的完整链路在 `AIBOT-5M/src/app/fund_flow_bot.py`。fileciteturn40file0L1-L1 fileciteturn42file0L1-L1

针对“本地运行 24 小时未见开仓”，从代码链路看 **“未开仓”并不等价于“策略没有产生 BUY/SELL 决策”**：更常见的情况是 **在开仓窗口门控（时间对齐）之后**，又被 **SignalPool（资金流规则池过滤）**、**Entry Hard Filter（如 MA10+MACD 触发要求）**、**前置风控 Gate（包含执行质量 1m BLOCK 与风险评分 gate_trade_decision）**、**账户级冷却/熔断**、**执行层校验（仓位比例/杠杆严格同步/可用保证金为0）**等任一环节拦截，导致最终没有下单。该“多重门控串联”的结构在 AIBOT-5M 的运行时代码中非常明确，并且会打印对应跳过原因。fileciteturn42file0L1-L1

综上，最可能导致“24小时无开仓”的根因，按概率从高到低建议优先排查：

1) **你大部分时间可能处于“非开仓窗口（WAIT_OPEN_AI）→ ingestion_only”的采样模式**：在 `align_to_kline_close=true` 时，只有在 **每个 15m 收线+延迟后的一小段窗口**才允许进入真正的开仓评估（allow_new_entries=True）。若进程启动/轮询漂移导致频繁错过窗口，会长时间只采样不交易。fileciteturn42file0L1-L1  
2) **SignalPool 资金流过滤条件过严或边沿触发（edge trigger）+冷却导致长期不过**：AIBOT-5M 配置中 trend_pool 对 `cvd_momentum` 与 `imbalance` 有硬阈值（例如 15m 的 `imbalance>=0.06` / `<=-0.06` 等），并启用 `edge_trigger_enabled` 与 `edge_cooldown_seconds`，会让“看起来有信号”的时段仍被过滤。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
3) **Entry Hard Filter 或 MA10+MACD 触发要求使得 BUY/SELL 被强制降级为 HOLD**：例如配置 `entry_require_macd_trigger=true`、`entry_hard_filter=true` 时，如果决策 metadata 没有满足 MACD 触发/早期触发条件，会直接 `MACD_TRIGGER_REQUIRED` 降级为 HOLD。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
4) **前置风控 Gate（pretrade_risk_gate）把开仓决策变成 HOLD 或 BLOCK**：尤其当 `execution_quality_1m.block_entry=true` 时，会出现 `EXECUTION_1M_BLOCK`；或 gate_trade_decision 输出 `BLOCK/AVOID/EXIT` 被列入 `entry_block_actions` 时直接拦截。fileciteturn30file0L1-L1 fileciteturn42file0L1-L1  
5) **执行层校验失败（risk_engine.validate_decision）或严格杠杆同步失败**：如 target_portion 超界、available_balance 为 0、strict_leverage_sync=true 且改杠杆失败，都会让“看上去在跑策略”但永远不下单。DS3 的执行路由与风控引擎明确会在校验失败时返回 `status=error`。fileciteturn22file0L1-L1 fileciteturn21file0L1-L1  

> 重要说明：用户提到需优先阅读“策略开仓逻辑文档.md”，但在 DS3 的已检索文件集中更明确存在的是 `docs/fund_flow_strategy_technical_spec.md`（含策略与门控的中文说明），未能在当前检索路径中发现同名 `策略开仓逻辑文档.md`。本报告用“技术规格文档 + 配置 + 代码”为主完成证据链；由于你未提供本地运行日志样例（stdout、logs 目录），报告中“日志片段证据”只能用仓库中内置的日志落盘/打印点位来替代，并给出你应当能在本地看到的典型输出格式。fileciteturn10file0L1-L1 fileciteturn42file0L1-L1

## 研究范围与证据来源

本次研究严格按你的要求，优先使用已启用连接器：**github**（当前仅启用该连接器）。核心审阅对象如下：

DS3（Minos1163/DS3）侧：

- `config/trading_config_fund_flow.json`：策略启用项、阈值、调度、风险与仓位限制。fileciteturn9file0L1-L1  
- `src/app/fund_flow_bot.py`：实盘主循环、开仓窗口门控、信号过滤与执行链路（DS3 版本已读取）。fileciteturn20file0L1-L1  
- `src/fund_flow/decision_engine.py`：决策引擎（包含 rule_strategy/评分融合等）。fileciteturn11file0L1-L1  
- `src/fund_flow/trigger_engine.py`：触发去重与 SignalPool 过滤机制。fileciteturn25file0L1-L1  
- `src/fund_flow/risk_engine.py`、`src/fund_flow/execution_router.py`：决策合法性校验、杠杆同步、下单与保护单落地。fileciteturn21file0L1-L1 fileciteturn22file0L1-L1  
- `docs/fund_flow_strategy_technical_spec.md`：策略说明与演进记录（中文）。fileciteturn10file0L1-L1  

AIBOT-5M（Minos1163/15mMACD_CVD/AIBOT-5M）侧：

- `AIBOT-5M/src/main.py`：规范入口，指向 `src.app.fund_flow_bot.TradingBot`。fileciteturn40file0L1-L1  
- `AIBOT-5M/config/trading_config_fund_flow.json`：AIBOT-5M 的资金流/MA10+MACD/KDJ 配置集合。fileciteturn41file0L1-L1  
- `AIBOT-5M/src/app/fund_flow_bot.py`：更“可观测”的运行时实现（打印了 HOLD 归因、signal_pool 拦截原因、Gate 行为等）。fileciteturn42file0L1-L1  
- `AIBOT-5M/requirements.txt`：依赖（python-binance、pandas/numpy、openai SDK 等）。fileciteturn35file0L1-L1  

## 未开仓原因精准定位

下面按“真实开仓链路的顺序”逐段列出 **所有可能阻止开仓的触发条件、阈值、时间框架、数据源依赖、信号合成逻辑、以及潜在实现错误**。为满足“精确定位”，每一类都给出对应的 **配置项/代码证据**（由于缺少你本地日志，日志证据以“代码中会打印/落盘的位置与典型输出”替代）。

### 开仓窗口门控与调度对齐导致长期只采样不交易

在 AIBOT-5M 运行时中，`TradingBot.run()` 会先判断是否空仓；空仓时如果 **不满足 allow_new_entries 与 flat_review_due**，会进入 `ingestion_only=True` 的采样周期，并打印 `mode=WAIT_OPEN_AI`。只有在满足开仓窗口时才会进入 `mode=OPEN_WINDOW_AI_TOP2` 并允许开仓决策执行。fileciteturn42file0L1-L1

“允许开仓的窗口”由 `_should_allow_entries_this_cycle` 决定。其逻辑关键点：

- 当 `schedule.align_to_kline_close=true` 且决策周期存在（例如 `fund_flow.decision_timeframe="15m"`）时，**只允许在“收线延迟后”的一个轮询窗口内开仓一次**。代码注释明确：`open_ts = close_ts + delay_seconds`，窗口宽度约等于 `min(timeframe_seconds, interval_seconds)`。fileciteturn42file0L1-L1  
- AIBOT-5M 配置中 `schedule.interval_seconds=60`、`schedule.kline_close_delay_seconds=3`，且 `ai_review.flat_timeframe="15m"`，因此每个 15m 周期只有大约 **60 秒开仓窗口**（例如 xx:15:03 ~ xx:16:03）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1

可能导致“24h都没真正进入开仓周期”的典型原因：

- **时间漂移/进程负载导致错过窗口**：如果一次循环耗时接近或超过 60 秒，且下一轮进入时已经 `ts-open_ts > window_seconds`，则该 15m 周期会被跳过，只剩下 ingestion_only。AIBOT-5M 中还有 `max_cycle_runtime_seconds` 用于提前终止循环，进一步增加“错过窗口”的概率。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- **误以为“程序在跑=在交易”**：实际上 `ingestion_only` 明确写着“仅写入市场快照，不触发决策/执行”，并会打印提示。若你本地日志级别低或只看“没有下单”，容易忽略这一行。fileciteturn42file0L1-L1  

你应当立刻在本地核对的“证据输出”（无需改代码）：观察 stdout/落盘 `runtime.out.log` 中是否持续出现类似

- `mode=WAIT_OPEN_AI ... ingestion_only=True`  
- `⏭️ 当前无持仓，等待下一次 15m AI 开仓复核窗口...`  

AIBOT-5M 已自带 runtime 日志落盘（6小时分桶写入 `runtime.out.log`/`runtime.err.log`），便于回溯。fileciteturn42file0L1-L1

### 信号生成阶段长期 HOLD 的来源：阈值、时间框架、数据依赖与合成逻辑

即便进入开仓窗口，仍可能一直 HOLD。此时需要把“决策引擎 output”为 BUY/SELL 的必要条件逐条拆开。

#### 资金流与指标数据依赖

在 AIBOT-5M 中，资金流上下文由 `_build_fund_flow_context` 生成，并通过 `MarketIngestionService.aggregate_from_metrics` 聚合成 `flow_snapshot`。数据源来自 Binance 的实时行情/订单簿/订单流快照（由 `MarketDataManager` & `BinanceClient` 提供），关键特征包括：

- `cvd_ratio / cvd_momentum`（订单流 CVD 与动量）、`oi_delta_ratio`（持仓量变化比）、`funding_rate`、`depth_ratio`、`imbalance`，以及 1m 级别执行质量指标（spread、VPIN、trap_score 等）。fileciteturn42file0L1-L1  

你需要关注的实现风险（会让信号一直不成立）：

- 当某些实时字段缺失时，代码会用价格变化做代理（例如 `cvd_ratio` 回退为 `change_15m`），这可能导致短期内信号弱或方向不稳定，从而触发后续“方向锁/确认不足”类逻辑。fileciteturn42file0L1-L1  
- 预热历史不足会造成指标 NaN/默认值：AIBOT-5M 启动时会尝试预载 120 分钟 15m K线（并构造 aggregation 样本），但如果交易对 K线不足会跳过并打印“预载跳过”。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

#### DS3 的“规则策略模式”可能极端收敛

DS3 的技术规格文档明确描述“当前主策略”带有 **1H 趋势过滤 + 15M 入场 confluence + 动态止损**等结构。虽然我们无法在本回合完整展开 decision_engine 的每个分支，但**配置层面**已体现出“规则策略/方向过滤 + 入场触发约束”的倾向。fileciteturn10file0L1-L1 fileciteturn9file0L1-L1

如果你跑的是 DS3 且其默认分支启用了类似规则链，常见导致“24h无触发”的硬条件包括：

- 上位周期（1h）方向不满足时直接 NO_TRADE  
- 入场要求 EMA 交叉/带宽突破/某类 MACD 触发（交叉或柱体扩张）同时成立  
- 对止损距离、波动率、ADX/ATR 处于某区间等做额外门控  

这些需要你在本地通过“决策落盘/打印”精确采样（见后文调试步骤）。

### SignalPool 过滤是最常见的“看似有信号但不下单”的拦截点

AIBOT-5M 的开仓链路里，在决策产生后（BUY/SELL）会调用：

- `TriggerEngine.evaluate_signal_pool(...)`，若 `passed=false` 会打印  
  `signal_pool过滤未通过，跳过开仓/加仓...` 并 `continue`，即 **不进入下单队列**。fileciteturn42file0L1-L1  

AIBOT-5M 的配置中，存在两套池：`signal_pools`（多 pool）+ `signal_pool`（legacy/default pool），并将若干资金流信号定义为硬阈值规则（15m 级别）。例如 trend_pool 的规则包含：

- LONG：`cvd_momentum >= 0.0005`、`imbalance >= 0.06`  
- SHORT：`cvd_momentum <= -0.0005`、`imbalance <= -0.06`  

且 `logic="AND"`, `min_pass_count=1`，并启用 `edge_trigger_enabled=true`、`edge_cooldown_seconds` 等机制，会让“持续满足阈值”时也只触发边沿一次，然后进入冷却。fileciteturn41file0L1-L1

因此，“24h无开仓”的一种非常典型解释是：

- 决策引擎可能在若干时刻给出 BUY/SELL 倾向（或分值接近阈值），但 **SignalPool 长期不过**，或者过一次后被 edge 冷却压住；最终候选队列为空。

你需要的证据输出就在运行时：每次被拦截都会打印 `pool=... reason=... edge=... score=...`。fileciteturn42file0L1-L1

### Entry Hard Filter（例如 MA10 + MACD 触发要求）直接把 BUY/SELL 强制降级为 HOLD

AIBOT-5M 提供了一个非常明确的“硬过滤器”：`_apply_ma10_macd_entry_filter`。当配置满足：

- `fund_flow.ma10_macd_confluence.enabled=true`
- `entry_hard_filter=true`
- `entry_require_macd_trigger=true`（且 `entry_allow_macd_early` 决定是否允许“早期柱体扩张”作为替代触发）

那么如果 BUY/SELL 没满足相应触发，会直接返回 HOLD，并在 reason 中附带 `MACD_TRIGGER_REQUIRED side=...` 等标签。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1

结合配置可知，该仓库当前 MACD 参数已在 `macd_timeframes` 中显式指定，并且锚定周期（anchor）默认为 `1h`；执行周期（exec）为 `15m`。fileciteturn41file0L1-L1

这直接回答你后续“是否去掉 MA10/KDJ、引入多周期 MACD 风控”的可行性：AIBOT-5M 现状其实已经具备将 **多周期 MACD 注入 flow_context/metadata** 并用于风险约束的框架（详见重构建议章节）。fileciteturn42file0L1-L1

### 前置风控 Gate 拦截：执行质量 1m BLOCK 与风险评分 gate_trade_decision

AIBOT-5M 的 `_apply_pretrade_risk_gate` 做了两类强拦截：

1) **execution_quality_1m.block_entry**：如果 1m 微观结构指标触发硬风险开关（如 spread_bps、spread_z、vpin、flow_toxicity 等超过阈值），则直接把 BUY/SELL 改为 HOLD，并打上 `EXECUTION_1M_BLOCK`。在测试用例中也明确验证了这一行为。fileciteturn30file0L1-L1 fileciteturn42file0L1-L1  
2) **gate_trade_decision 输出动作在 entry_block_actions 内**：配置中 `entry_block_actions` 默认为 `["EXIT","BLOCK","AVOID"]`，若 gate 返回这些动作，会直接拦截开仓（HOLD）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

此外还有一个“隐蔽但常见”的坑：

- 当 gate_action == "HOLD" 时，代码会按 `entry_hold_portion_scale` 下调本次开仓仓位占比，并可能下调杠杆。若你同时配置了较大的 `min_open_portion`（或 DS3 风控引擎默认最小开仓比例较高），就会出现 **“Gate 允许但把仓位缩到低于最小下单阈值 → risk_engine.validate_decision 失败/或在后续加仓逻辑里被 remaining<min_open_portion 跳过”** 的现象。AIBOT-5M 配置里 `min_open_portion=0.06`，并且 gate 下调比例为 0.75；DS3 未必有此字段，可能走默认更高门槛。fileciteturn41file0L1-L1 fileciteturn21file0L1-L1 fileciteturn42file0L1-L1  

### 执行层阻断：严格杠杆同步、可用余额、决策合法性校验失败

即便通过所有信号门控，执行层仍可能阻断“真实下单”。DS3 的 `FundFlowExecutionRouter.execute_decision` 在执行前明确调用风控引擎校验，失败则返回 `status=error` 且 message 包含“decision 校验失败…”。fileciteturn22file0L1-L1

在 DS3 风控引擎中，常见校验点包括（会直接抛错/拒绝）：

- symbol 不在白名单、operation 非法  
- `target_portion_of_balance` 不在允许区间（min/max open portion）  
- leverage 不在允许区间  
这些属于“实现层的硬拒绝”。fileciteturn21file0L1-L1

其次，DS3 与 AIBOT-5M 都配置了 `strict_leverage_sync=true`（AIBOT-5M 配置明确如此），意味着下单前会尝试把交易所杠杆同步到策略杠杆；如果 API 权限/账户模式导致改杠杆失败，严格模式下会阻止开仓。fileciteturn41file0L1-L1 fileciteturn22file0L1-L1 fileciteturn42file0L1-L1

你“24小时无成交”的另一条高概率路径是：

- 策略确实产生过 BUY/SELL，但 execution_router 返回 error（杠杆同步失败、交易所拒单、下单数量不足最小下单量/名义金额、或 available_balance 读取为 0），从而你在成交/持仓上看不到任何变化。

AIBOT-5M 的 `_execute_and_log_decision` 已包含对 `status=error` 的详细打印（`❌ 执行失败详情: ...`，以及交易所 error_code），你应能在日志里直接定位。fileciteturn42file0L1-L1

## trading_config_fund_flow.json 门槛与资金流/仓位限制体检

你要求重点检查 `trading_config_fund_flow.json` 中可能阻止开仓的配置项。下面分别列出 DS3 与 AIBOT-5M 的关键“阻断项”，并解释作用机制（括号中给出配置证据）。

### 仓位与容量限制

AIBOT-5M：

- `fund_flow.max_active_symbols=2`：全局最多同时持有 2 个交易对；超出后候选开仓会在 `_finalize_entries` 被跳过。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- `fund_flow.max_symbol_position_portion=0.1`：单标的最大保证金占权益比例；即使允许加仓，remaining 不足也会跳过。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- `fund_flow.min_open_portion=0.06`：低于该比例的开仓/加仓会被后续逻辑拦掉（尤其与 pretrade_risk_gate 的 portion_scale 联动时）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- `trading.reserve_percent=20`（以及 `min_position_percent=12`）：若你的 sizing 逻辑在别处使用这几个百分比，可能导致“可用资金不足/目标仓位过大/格式化后数量为 0”，进而拒单。需要结合执行层的“下单数量格式化”与交易所规则一起看（本报告未读取交易所 stepSize/tickSize 缓存实现，建议按调试步骤验证下单 qty）。fileciteturn41file0L1-L1

DS3：

- `fund_flow.default_target_portion=0.08`、`max_symbol_position_portion=0.12`、`max_active_symbols=2` 显示 DS3 也倾向“小仓位+小容量”的组合；在“多门控”下会明显降低触发概率。fileciteturn9file0L1-L1  
- DS3 风控引擎自身存在 `min_open_portion` 默认值（若配置未显式覆盖），一旦与 Gate 下调后仓位冲突，就可能出现“允许开仓但校验失败”的隐患。fileciteturn21file0L1-L1  

### 资金流/信号阈值与过滤

AIBOT-5M：

- `signal_pools.trend_pool` 内的资金流阈值（`cvd_momentum`、`imbalance` 等）是**硬过滤**，并且默认 `logic=AND`，这会使“看似有趋势”但微观订单流不够强的行情全部被挡住。fileciteturn41file0L1-L1  
- `signal_pools.*.edge_trigger_enabled=true + edge_cooldown_seconds`：即使满足条件，也只在边沿触发一次，之后冷却期不会再开新仓（这对“你观察 24h 没开仓”会造成错觉：可能开仓机会在冷却窗口外出现但被压住）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- `fund_flow.trigger_dedupe_seconds=180`：同一 symbol/trigger_id 可能被去重跳过，尤其当 trigger_id 粒度过粗或 snapshot timestamp 不更新时。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

DS3：

- DS3 同样有 signal pool、trigger 去重、trend_capture、regime 等多层阈值。由于你“24h无开仓”，建议先用 AIBOT-5M 运行时那套打印点对 DS3 做同等可观测化（见调试步骤）。fileciteturn25file0L1-L1 fileciteturn9file0L1-L1  

### 调度与时间窗口（最容易忽视的阻断项）

AIBOT-5M：

- `schedule.align_to_kline_close=true`、`interval_seconds=60`、`kline_close_delay_seconds=3` 决定了开仓窗口的“短而稀疏”；`max_cycle_runtime_seconds=45` 又进一步要求每轮处理不能拖太久，否则容易错过窗口。fileciteturn41file0L1-L1  
- `ai_review.flat_timeframe="15m"` 会参与开仓窗口计算：你以为策略每分钟都在判断，但真正允许开仓只在每个 15m 边界附近。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

## 静态代码审计与潜在缺陷清单

下面按“开仓判断→信号计算→风控→下单接口”的关键函数/模块列出职责，并指出潜在 bug/边界/异常处理问题。为便于对照，你可以先从 AIBOT-5M 的路径入手（日志更丰富），再回看 DS3 对应实现。

### 关键模块与函数职责

AIBOT-5M（实盘主链路最完整）：

- `AIBOT-5M/src/main.py::main()`：实盘入口；默认把 `BINANCE_DRY_RUN=0` 并启动 `TradingBot.run()`；支持 `--once` 单轮跑。fileciteturn40file0L1-L1  
- `src/app/fund_flow_bot.py::TradingBot.run()`：主循环；负责“是否空仓→是否开仓窗口→run_cycle/ingestion_only”。fileciteturn42file0L1-L1  
- `TradingBot._should_allow_entries_this_cycle()` / `_should_allow_aligned_cycle()`：开仓窗口门控与桶去重。fileciteturn42file0L1-L1  
- `TradingBot._materialize_flow_snapshot()`：从实时数据构造 metrics 并聚合为 flow_snapshot；写 DB。fileciteturn42file0L1-L1  
- `TradingBot._compute_ma10_macd_confluence()` + `_inject_confluence_into_flow_context()`：拉取 exec/anchor K 线并计算 MA10、MACD、KDJ、布林；注入 metadata/timeframes，供后续 Entry Hard Filter 与风控使用。fileciteturn42file0L1-L1  
- `FundFlowDecisionEngine.decide(...)`：核心决策引擎（在 DS3 repo 中实现；AIBOT-5M 复用同名模块）。fileciteturn11file0L1-L1 fileciteturn42file0L1-L1  
- `TriggerEngine.should_trigger()` / `evaluate_signal_pool()`：去重与 rules 过滤；不通过直接跳过开仓。fileciteturn42file0L1-L1 fileciteturn25file0L1-L1  
- `TradingBot._apply_ma10_macd_entry_filter()`：硬过滤；可直接把 BUY/SELL 降级 HOLD。fileciteturn42file0L1-L1  
- `TradingBot._apply_pretrade_risk_gate()`：前置风控 Gate；可 BLOCK/HOLD/DEGRADE；并会把 `execution_quality_1m` 融入 gate。fileciteturn42file0L1-L1  
- `FundFlowExecutionRouter.execute_decision(...)`（DS3中）：执行层；校验 decision、同步杠杆、下单与保护单管理。fileciteturn22file0L1-L1  

DS3 执行与风控（对“未开仓”尤关键）：

- `src/fund_flow/risk_engine.py::FundFlowRiskEngine`：对 `target_portion_of_balance`、leverage、symbol 等做硬校验。fileciteturn21file0L1-L1  
- `src/fund_flow/execution_router.py::FundFlowExecutionRouter.execute_decision`：在校验失败时返回 error，或在严格杠杆同步失败时阻断开仓。fileciteturn22file0L1-L1  

### 重点潜在 bug / 竞态 / 边界处理问题

1) **开仓窗口过窄 + 轮询预算裁剪导致“永远错过开仓窗口”**  
`window_seconds = min(tf_seconds, interval_seconds)`，配合 `interval_seconds=60` 会使窗口只有 60 秒；一旦每轮处理稍慢（网络抖动、单次请求超时、symbols_per_cycle 很大），就会错过。建议：调试时先关闭对齐或把 interval 提高/窗口扩大（见调试步骤）。fileciteturn42file0L1-L1

2) **SignalPool 与决策分数尺度不匹配**  
SignalPool 既检查规则通过数，又检查 `min_long_score/min_short_score`。若决策引擎在某些分支没有写入 `long_score/short_score`（或写入字段名不同），将导致 pool_eval 的 score 永远很低，从而所有 BUY/SELL 被过滤。AIBOT-5M 运行时会打印 `score=...`，能快速验证是否发生该问题。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1

3) **Gate “DEGRADE” 后仓位占比可能落入 min_open_portion 以下，引发后续拒绝**  
`entry_hold_portion_scale` 会缩小仓位；若 DS3 风控最小开仓占比较高或执行层有最小名义金额约束，会导致“策略判断允许，但每次都被 size 约束拒绝”。建议在调试时记录：原始 target_portion、gate 后 portion、risk_engine min_open_portion、以及最终下单 quantity。fileciteturn21file0L1-L1 fileciteturn42file0L1-L1

4) **严格杠杆同步导致的“无声失败”**  
当 `strict_leverage_sync=true` 且 API 无权限/接口异常，执行层可能一直返回 error，外层只看到无持仓变化。AIBOT-5M 已包含对 leverage_sync error 的打印；DS3 若不够显眼，建议临时加上同等打印。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1 fileciteturn22file0L1-L1

5) **执行质量 1m BLOCK 过度敏感**  
AIBOT-5M 的 `_build_execution_quality_1m` 将 spread/vpin/trap_score 等组合成 BLOCK/PASSIVE_ONLY/NEUTRAL 模式；一旦进入 BLOCK，就直接拦截开仓（测试用例已验证）。若你的数据源在某些时段给出异常 spread_bps 或 vpin（例如返回比例/基点混用），会造成长期 BLOCK。建议输出关键字段原始值，确认计量单位（spread_bps 是否已乘以 10000）。fileciteturn42file0L1-L1 fileciteturn30file0L1-L1

## 可复现调试步骤与最小测试用例

你希望“可复现调试步骤 + 最小测试用例（含日志级别、回放数据范围、关键断点/打印点）”。由于你未提供本地日志样例，本节给出“无需改框架即可执行”的最小方案，并提供必要的插桩建议。

### 一键定位：先确认你是否真的进入过开仓窗口

在 AIBOT-5M 目录下，直接运行一次并观察输出：

```bash
python -m src.main --config config/trading_config_fund_flow.json --once
```

证据判断：

- 若看到 `mode=WAIT_OPEN_AI ... ingestion_only=True`，说明你不在开仓窗口（正常）。fileciteturn42file0L1-L1  
- 若看到 `mode=OPEN_WINDOW...`，说明本轮允许开仓评估，你接下来应当看到每个 symbol 的决策/过滤打印（如 signal_pool 过滤、Gate、执行结果）。fileciteturn42file0L1-L1  

若你跑的是 DS3 而不是 AIBOT-5M：建议先用 AIBOT-5M 运行时对同一套 `FundFlowDecisionEngine`/执行组件做验证（它自带更“啰嗦”的可观测性），再把相同的打印点迁回 DS3。

### 强制放大开仓机会：暂时绕开时间门控与过滤器（用于验证“能否下单”）

为了把问题从“策略不触发”与“执行层不下单”中区分开，建议按以下顺序做最小化开关试验（每次只改一个变量，跑 30-60 分钟即可）：

1) **关闭时间对齐门控**（让每分钟都允许开仓评估）  
将配置改为：`schedule.align_to_kline_close=false`。AIBOT-5M 的 `_is_kline_alignment_active` 会因此返回 False，从而 `_should_allow_entries_this_cycle` 直接 True。fileciteturn42file0L1-L1  
若此时仍然 0 开仓，说明阻断大概率在后续过滤/风控/执行层。

2) **临时关闭 SignalPool**（验证是不是 pool 造成永远不过）  
将 `fund_flow.signal_pool.enabled=false`（或把 `min_pass_count=0` 且规则阈值放宽为 0）并观察是否出现开仓。`evaluate_signal_pool` 不通过时会打印明确原因；关闭后应消失。fileciteturn42file0L1-L1  

3) **临时关闭 Entry Hard Filter**（验证是否 MA10/MACD 触发要求过严）  
将 `fund_flow.ma10_macd_confluence.entry_hard_filter=false` 或 `entry_require_macd_trigger=false`。然后观察 `MACD_TRIGGER_REQUIRED` 是否消失以及是否开始进入 pending_new_entries 队列。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

4) **临时关闭 pretrade_risk_gate**（验证是否 Gate 把所有 entry 变 HOLD/BLOCK）  
将 `fund_flow.pretrade_risk_gate.enabled=false`。如果此时开仓出现，说明 Gate 的 `entry_threshold/max_drawdown/volatility_cap` 或 `execution_quality_1m` 的 BLOCK 是核心原因。fileciteturn42file0L1-L1  

5) **关闭 strict_leverage_sync 或降低杠杆**（验证是否执行层同步杠杆失败）  
将 `fund_flow.strict_leverage_sync=false` 或把 leverage 降到交易所当前默认杠杆附近，再看是否能下单。执行层如果持续失败，你要抓 `leverage_sync` 的 error message（AIBOT-5M 已打印）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  

### 最小测试用例：用单元测试做“开仓拦截原因”回归

仓库已经提供了与“前置风控 Gate”相关的测试样例（包括 execution_quality_1m BLOCK 会把 BUY 变 HOLD）。你可以直接运行：

```bash
pytest -q tests/test_pretrade_risk_gate_exit_logic.py -k block_entry
```

该测试验证：当 flow_context 注入 `execution_quality_1m.mode=BLOCK` 且 `block_entry=true` 时，`_apply_pretrade_risk_gate` 必须把 BUY 降级为 HOLD，reason 含 `EXECUTION_1M_BLOCK`。fileciteturn30file0L1-L1

若你要为“24h不触发开仓”写一个最小复现测试，建议新增一个测试（伪代码风格）：

- 构造一个 `FundFlowDecision(operation=BUY, target_portion=0.08, leverage=5, metadata 满足 long_score)`  
- 再构造一个 `signal_pool` 规则，使其必定不通过（例如阈值设置为极大），验证 bot 会打印/返回 “signal_pool过滤未通过”。  
- 再构造一个 `pretrade_risk_gate` action=HOLD 且 portion_scale=0.5，同时 risk_engine.min_open_portion=0.08，验证执行路由校验失败（error）或被 min_open_portion 跳过。

AIBOT-5M 的 `_execute_and_log_decision` 已提供大量“归因打印点”（HOLD 归因、signal_pool 原因、Gate action、执行失败详情），你可以把这些打印作为断言目标（或将其结构化输出到 JSON）。fileciteturn42file0L1-L1

## 重构建议

你提出的重构目标是：评估去掉 KDJ 与 MA10、引入多周期 MACD 作为风控、并保持回测/实盘一致性。AIBOT-5M 当前框架对这些改动“非常友好”，因为它已经有：

- 多周期 K 线抓取与指标计算入口（`_compute_ma10_macd_confluence`）  
- 指标注入到 `flow_context["timeframes"]` 与 decision metadata 的通道（`_inject_confluence_into_flow_context`）  
- 多周期 MACD 风控样式函数（`_evaluate_macd_mtf_risk_guard` 当前使用 exec 与 1h anchor）  
- 可配置的 `ma10_macd_confluence.macd_timeframes` 字典来定义每个周期参数  
以上都能直接扩展到 4H。fileciteturn42file0L1-L1 fileciteturn41file0L1-L1

### 去掉 KDJ 与 MA10 的利弊评估

结合 AIBOT-5M 现有实现，KDJ 与 MA10 的作用点主要有三类：

- **方向指导/打分**：`direction_guide.model="MACD_KDJ"` 与 `direction_combo.macd_kdj_weights` 明确把 KDJ 当作方向判据的一部分。fileciteturn41file0L1-L1  
- **入场过滤**：`ma10_macd_confluence` 同时计算 MA10、KDJ、MACD、布林，并把一些结果作为 entry hard/soft 的依据（例如 `entry_soft_penalty_no_kdj` 等字段显示 KDJ 会影响分数/惩罚）。fileciteturn41file0L1-L1 fileciteturn42file0L1-L1  
- **持仓冲突保护与平仓决断**：`risk.conflict_protection.close_decision_kdj_weight` 把 KDJ 作为 close 决断因子之一。fileciteturn41file0L1-L1  

从策略工程角度（信号冗余/延迟/过拟合/复杂度）：

- 去掉 **MA10**：优点是减少“均线偏置”的滞后与多一重 hard block（尤其在震荡行情会显著减少入场）；缺点是失去一个简单的趋势/偏置判定，可能导致逆势入场增多。  
- 去掉 **KDJ**：优点是减少震荡指标带来的过拟合风险（KDJ 在高波动品种上容易频繁摆动），降低分支复杂度；缺点是失去超买超卖/拐点类的“时机”信息，可能让入场更多依赖 MACD 的慢信号或资金流，触发频率下降或入场更靠后。

若你的核心诉求是“24h无开仓→希望更容易触发”，从结构上讲，**最该先动的不是 KDJ/M A10，而是“SignalPool + Gate + 时间窗口”这三类门控**；KDJ/MA10更多影响“质量/方向”而非“是否完全没机会”。这一点从 AIBOT-5M 中层层 `continue` 的链路特征可以看出来：SignalPool/Gate 处于更靠前的硬拦截层。fileciteturn42file0L1-L1

### 引入 MACD_1H(8,21,5) 与 MACD_4H(12,26,9) 作为风控的可行性与实现细节

现状对照：

- AIBOT-5M 已在 `ma10_macd_confluence.macd_timeframes` 中定义了 `15m:(8,21,5)` 与 `1h:(12,26,9)`（注意与你期望的 1H 参数不同：目前 1H 用的是 12/26/9）。fileciteturn41file0L1-L1  
- 运行时 `_compute_ma10_macd_confluence` 已具备“按 timeframe 拉 K 线→算 MACD→写入 metadata（如 macd_1h_*）”的模式。fileciteturn42file0L1-L1  

因此，实现“1H(8,21,5) + 4H(12,26,9) 风控”有两种可选路径：

1) **参数替换 + 新增 4H（推荐）**  
在 `macd_timeframes` 中新增 `4h:(12,26,9)`，并把 `1h` 的 params 改成 `(8,21,5)`（与你目标一致）。然后在 `_compute_ma10_macd_confluence` 中增加一次 `k_4h = get_klines(..., "4h", limit=...)`，计算 `macd_4h_state` 并注入 `macd_4h_cross/macd_4h_zone/macd_4h_hist/...`。  
接着扩展 `_evaluate_macd_mtf_risk_guard`：把“anchor”从单一 1h 扩展成 “1h + 4h”，形成 **HTF veto（高周期否决）**：当 4h 强烈反向时，即使 15m/1h 支持，也只允许减仓/不开新仓，或强制降杠杆/降仓位。

2) **保留现有 1h(12,26,9) 作为中期趋势，新增 1h(8,21,5) 作为快锚（较复杂）**  
这需要在 metadata 同时携带两套 1h MACD，并在风控里定义冲突解决规则。除非你有明确的历史回测支持，否则不建议一开始就引入“双1h”。

避免信号冲突的推荐聚合逻辑（简单可控）：

- **4H：只做“禁止强反向”**  
  - 如果准备做 LONG，但 4H `macd_cross=DEAD` 或 `macd_hist<0 且 hist_delta<0`（下行加速），则直接 `BLOCK_ENTRY_HTF`。  
- **1H：做“趋势一致性加权”**  
  - 1H 同向则加分/放宽某些门槛；1H 中性则允许但降低仓位/杠杆；1H 轻度反向则提高 required_score。  
- **15M：作为入场触发**（交叉/柱体扩张/资金流确认）

阈值建议（示例，需回测校准）：

- “强反向”判定：`cross` 反向 + `hist` 绝对值超过过去 N 根均值的一定倍数（你已有 `hist_norm` 的归一化概念，可直接使用）  
- “中性”判定：`abs(hist_norm) < 0.1` 且 `cross="NONE"`  

### 替代或补充指标建议

在“资金流策略”语境下，若你要减少 KDJ/MA10，同时保持/提升入场质量，通常更合适的补充是：

- **ATR% / 波动率门控**：你已经有 `regime.atr_pct_min/max` 与 `extreme_volatility_cooldown_*`，可以把 ATR% 更明确地用于“开仓阈值动态调整”（低波动放宽触发，高波动收紧或只做被动挂单）。fileciteturn41file0L1-L1  
- **布林带结构（BB squeeze / breakout）**：AIBOT-5M 已计算并注入布林（`bb_width_norm`, `bb_break`, `bb_pos_norm`），可将其从“辅助因子”升级为清晰的“结构确认”条件。fileciteturn42file0L1-L1  
- **订单簿微观结构（spread/vpin/trap_score）分层执行策略**：你已有 `execution_quality_1m` 的 PASSIVE_ONLY/PREFER_PASSIVE，建议把它从“仅拦截”改成“影响开仓方式”（例如强制 GTC、禁用 market fallback），减少因执行质量差导致的假信号亏损。fileciteturn42file0L1-L1  

### 重构后的伪代码示例

目标：移除 KDJ/MA10 依赖、接入多周期 MACD 风控（15m 触发，1h/4h 风控），并保证“回测/实盘一致性”。

下面给出一个与当前代码风格一致的伪代码骨架（Python）：

```python
def compute_mtf_macd(symbol):
    # 统一从同一数据源取K线，保证回测/实盘一致性
    k15 = get_klines(symbol, "15m", limit=200)
    k1h = get_klines(symbol, "1h",  limit=120)   # 1H(8,21,5)
    k4h = get_klines(symbol, "4h",  limit=120)   # 4H(12,26,9)

    macd_15 = macd(closes(k15), fast=8,  slow=21, signal=5)
    macd_1h = macd(closes(k1h), fast=8,  slow=21, signal=5)
    macd_4h = macd(closes(k4h), fast=12, slow=26, signal=9)

    return {
        "15m": macd_15, "1h": macd_1h, "4h": macd_4h
    }

def htf_veto(side, macd_4h):
    # 4H只做强反向否决
    if side == "LONG":
        if macd_4h.cross == "DEAD" or (macd_4h.hist < 0 and macd_4h.hist_delta < 0):
            return True, "HTF_BLOCK_4H_BEAR"
    if side == "SHORT":
        if macd_4h.cross == "GOLDEN" or (macd_4h.hist > 0 and macd_4h.hist_delta > 0):
            return True, "HTF_BLOCK_4H_BULL"
    return False, ""

def decide_entry(flow_context, mtf_macd):
    # 15m触发：MACD交叉或柱体扩张（不再依赖KDJ/MA10）
    trigger_long  = mtf_macd["15m"].cross == "GOLDEN" or mtf_macd["15m"].hist_expand_up
    trigger_short = mtf_macd["15m"].cross == "DEAD"   or mtf_macd["15m"].hist_expand_down

    # 资金流确认（保留SignalPool思想，但从“硬拦截”改为“加分/减分”也可）
    flow_long_ok  = flow_context.cvd_momentum > +x and flow_context.imbalance > +y
    flow_short_ok = flow_context.cvd_momentum < -x and flow_context.imbalance < -y

    if trigger_long and flow_long_ok:
        return "BUY"
    if trigger_short and flow_short_ok:
        return "SELL"
    return "HOLD"

def risk_adjust(side, mtf_macd):
    # 1H作为趋势一致性：决定降杠杆/降仓位
    align = (side == "LONG"  and (mtf_macd["1h"].hist > 0)) or \
            (side == "SHORT" and (mtf_macd["1h"].hist < 0))
    if align:
        return {"portion_scale": 1.0, "leverage_cap": None}
    else:
        return {"portion_scale": 0.6, "leverage_cap": 4}

def pipeline(symbol):
    flow = build_flow_context(symbol)
    mtf = compute_mtf_macd(symbol)

    op = decide_entry(flow, mtf)
    if op in ["BUY","SELL"]:
        side = "LONG" if op=="BUY" else "SHORT"
        veto, reason = htf_veto(side, mtf["4h"])
        if veto:
            return HOLD(reason)

        adj = risk_adjust(side, mtf)
        return OPEN(op, portion*=adj["portion_scale"], leverage=min(leverage, adj["leverage_cap"]))

    return HOLD()
```

对应到当前仓库落地时的关键实现点：

- 在 `_compute_ma10_macd_confluence`：删除/忽略 MA10 与 KDJ 计算分支，仅保留 `MACD + BB + ATR%` 等；并新增 `4h` K线抓取与 `macd_4h_*` 写入。fileciteturn42file0L1-L1  
- 在 `_apply_ma10_macd_entry_filter`：改名为 `_apply_macd_entry_filter`，去掉 KDJ/MA10 相关字段依赖，只基于 15m MACD trigger 作硬过滤；把 1h/4h 的冲突判断放入“风险降杠杆/否决”层，而不是入场硬过滤层。fileciteturn42file0L1-L1  
- 在 `_evaluate_macd_mtf_risk_guard`：把 anchor 扩展为 4h（或新增 `_evaluate_macd_htf_veto`），并在 decision metadata 落盘，方便回测/实盘一致地复盘。fileciteturn42file0L1-L1  

### 指标组合对比表

| 方案 | 当前组合（示例：MA10+MACD+KDJ+资金流+SignalPool+Gate） | 去掉 KDJ/MA10 | 引入多周期 MACD 风控（15m触发 + 1h趋势 + 4h否决） |
|---|---|---|---|
| 触发灵敏度 | 中等偏低：SignalPool+Gate+硬过滤叠加容易“一个不过就全不过” fileciteturn42file0L1-L1 | 上升：少两层指标门控，但仍受 SignalPool/Gate 限制 | 可控：15m触发可放宽，但 4h 否决会抑制逆势乱开 |
| 延迟 | 偏高：MA10 与 KDJ 在高噪声下会反复确认，MACD本身也滞后 | 略降低：移除 MA10/KDJ 的确认滞后 | 分层：入场延迟取决于 15m MACD；风险确认由 1h/4h（更慢但只做否决/降杠杆） |
| 过拟合风险 | 中高：KDJ 参数敏感，多个门控的阈值组合容易拟合特定阶段 | 降低：减少震荡指标与冗余门控 | 中等：多周期 MACD 参数也需校准，但结构更“可解释、可分层回测” |
| 实现复杂度 | 已存在但复杂：多处依赖 KDJ/MA10（方向指导、软惩罚、平仓权重） fileciteturn41file0L1-L1 | 降低：删除 KDJ/MA10 的计算与依赖关系，但要同步清理权重/字段 | 中等：需要新增 4h 数据抓取与风控逻辑，但框架已有多周期注入通道 fileciteturn42file0L1-L1 |
| 推荐阈值示例 | SignalPool：imbalance 0.06、cvd_mom 0.0005；Gate：entry_threshold/volatility_cap；硬过滤：MACD trigger fileciteturn41file0L1-L1 | SignalPool/Gate 可先不动，观察开仓频率；若仍不触发，优先放宽 SignalPool/Gate | 15m MACD：cross 或 2-bar hist_expand；1h MACD：同向则 portion_scale=1，否则 0.6；4h MACD：强反向直接 block |

### 信号流 mermaid 图示

```mermaid
flowchart TD
  A[数据输入\nBinance 行情/订单簿/订单流/K线] --> B[特征工程\n资金流聚合\nCVD/OI/Funding/Imbalance]
  A --> C[指标计算\n15m MACD\n1h MACD\n4h MACD\n(可选BB/ATR%)]
  B --> D[决策引擎\n生成 BUY/SELL/HOLD\n+ 分数/阈值]
  C --> D

  D --> E[SignalPool 过滤\n规则阈值/edge 冷却]
  E -->|passed| F[Entry Hard Filter\n(可选)\n如 MACD trigger 必须成立]
  E -->|blocked| X1[跳过开仓\n记录 reason]

  F --> G[Pretrade Risk Gate\n执行质量1m BLOCK\n风险评分 gate_trade_decision\n(可降仓/降杠杆)]
  G -->|BLOCK/HOLD| X2[跳过开仓\n记录 reason]
  G -->|ENTER/DEGRADE| H[执行层校验\nrisk_engine.validate_decision\nmin/max portion\n杠杆范围]
  H -->|error| X3[执行失败\n记录 message]
  H -->|ok| I[下单/同步杠杆\nstrict_leverage_sync\n挂保护单 TP/SL]
  I --> J[落盘与归因\nruntime日志/DB/决策日志]
```

---

**结论性建议（最短路径）**：你当前“24小时未开仓”的定位应当先从“是否真的进入开仓窗口”与“是否被 SignalPool/Gate 拦截”两条证据链入手，因为它们位于最靠前且最硬的 `continue` 拦截点。AIBOT-5M 运行时已经把这些归因打印得很清楚；建议你用 AIBOT-5M 的运行时先跑同样 24h（或 2-4h 即可），拿到 `signal_pool过滤未通过 / PRE_RISK_BLOCK / MACD_TRIGGER_REQUIRED / 杠杆同步失败 / 执行失败详情` 等明确文本，再将结果映射回 DS3 的同名模块与配置调参。fileciteturn42file0L1-L1
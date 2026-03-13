# 增强K线日志系统使用指南

## 概述

增强K线日志系统提供了一套完整的工具,用于记录、解析和分析FUND_FLOW策略的K线操作日志。该系统能够完整输出每次操作K线的信息,包括方向、持仓、AI策略、风控、K线OPEN和CLOSE等关键信息,便于调试策略BUG和优化参数。

## 核心组件

### 1. 增强日志记录器 (enhanced_kline_logger.py)

提供结构化的日志记录功能,支持多种输出格式。

**主要类:**

- `KlineInfo`: K线信息数据类
- `AIStrategyInfo`: AI策略信息数据类
- `RiskControlInfo`: 风控信息数据类
- `DecisionInfo`: 决策信息数据类
- `KlineOperationLog`: 完整的K线操作日志数据类
- `EnhancedKlineLogger`: 增强日志记录器

### 2. 日志解析器 (log_parser.py)

解析现有的FUND_FLOW日志文件,提取关键信息并转换为增强格式。

**主要类:**

- `FundFlowLogParser`: FUND_FLOW日志解析器

## 使用方法

### 方法1: 解析现有日志文件

解析现有的runtime日志文件,生成增强格式的日志、JSON和CSV文件:

```bash
# 切换到src/fund_flow目录
cd D:\AIDCA\AIGRID\src\fund_flow

# 解析日志文件
python log_parser.py D:\AIDCA\AIGRID\logs\2026-03\2026-03-12\runtime.out.00.log

# 指定输出目录
python log_parser.py D:\AIDCA\AIGRID\logs\2026-03\2026-03-12\runtime.out.00.log D:\AIDCA\AIGRID\output
```

**输出文件:**

1. `runtime.out.00_enhanced_[timestamp].log` - 增强格式的可读日志
2. `runtime.out.00_[timestamp].jsonl` - JSON Lines格式日志
3. `runtime.out.00_[timestamp].csv` - CSV格式日志

### 方法2: 在代码中集成增强日志

在你的策略代码中集成增强日志记录:

```python
from datetime import datetime
from src.fund_flow.enhanced_kline_logger import (
    EnhancedKlineLogger, KlineOperationLog, KlineInfo, 
    AIStrategyInfo, RiskControlInfo, DecisionInfo,
    DecisionAction, MarketRegime, Direction
)

# 初始化日志记录器
logger = EnhancedKlineLogger("D:/AIDCA/AIGRID/logs/enhanced_kline.log")

# 创建K线操作日志
log_entry = KlineOperationLog(
    timestamp=datetime.now(),
    cycle=112,
    mode="MIXED_AI_REVIEW",
    kline=KlineInfo(
        symbol="BNBUSDT",
        open_time=1678886400000,
        close_time=1678886699999,
        open_price=651.40,
        high_price=652.00,
        low_price=651.00,
        close_price=651.63,
        volume=1000.0,
        timeframe="5m"
    ),
    ai_strategy=AIStrategyInfo(
        regime=MarketRegime.TREND,
        direction=Direction.LONG,
        signal_long=0.213,
        signal_short=0.000,
        score_15m_long=0.142,
        score_15m_short=0.000,
        score_5m_long=0.218,
        score_5m_short=0.000,
        confidence=0.750,
        ai_weights={
            "cvd": 0.22,
            "cvd_momentum": 0.11,
            "oi_delta": 0.27,
            "funding": 0.09,
            "depth_ratio": 0.19,
            "imbalance": 0.04,
            "liquidity_delta": 0.06,
            "micro_delta": 0.02
        }
    ),
    risk_control=RiskControlInfo(
        gate_score=0.235,
        gate_action="BUY",
        risk_level="LOW",
        position_size=0.08,
        target_size=0.10,
        leverage=5,
        margin_used=100.0,
        margin_available=900.0,
        is_protected=True
    ),
    decision=DecisionInfo(
        action=DecisionAction.BUY,
        reason="TREND | mode=TREND_STD | long=0.434",
        signal_source="signal",
        trigger_type="MACD_TRIGGER_REQUIRED"
    )
)

# 记录日志
logger.log_operation(log_entry)

# 导出为JSON
logger.log_json(log_entry, "D:/AIDCA/AIGRID/logs/kline_ops.json")

# 导出为CSV
logger.export_to_csv("D:/AIDCA/AIGRID/logs/kline_ops.csv")

# 获取统计信息
stats = logger.get_statistics()
print(stats)
```

### 方法3: 查询和分析日志

```python
from src.fund_flow.enhanced_kline_logger import EnhancedKlineLogger

# 加载日志
logger = EnhancedKlineLogger()

# 查询特定交易对的日志
bnb_logs = logger.get_logs_by_symbol("BNBUSDT")
for log in bnb_logs:
    print(log.kline)

# 查询特定动作的日志
buy_logs = logger.get_logs_by_action(DecisionAction.BUY)
print(f"共有 {len(buy_logs)} 次BUY操作")

# 查询特定周期的日志
cycle_log = logger.get_logs_by_cycle(112)
if cycle_log:
    print(cycle_log.decision)
```

## 日志输出格式

### 控制台/文件格式

```
====================================================================================================
📊 K线操作日志 - Cycle 112 @ 2026-03-12 00:00:03 UTC
====================================================================================================

🎯 K线信息:
   BNBUSDT | O:651.400000 H:652.000000 L:651.000000 C:651.630000 | Change:+0.04% | TF:5m

🤖 AI策略:
   Regime:TREND | Dir:LONG | Signal(L/S):0.213/0.000 | Score15m(L/S):0.142/0.000 | Score5m(L/S):0.218/0.000 | Conf:0.75

🛡️  风控:
   Gate:BUY(+0.235) | Level:LOW | Pos:0.08/0.10 | Leverage:5x | Margin:100.00/900.00 | Protected:True

📝 决策:
   Action:BUY | Reason:TREND | Source:signal | Trigger:MACD_TRIGGER_REQUIRED

⚖️  AI权重:
   cvd:0.22, cvd_momentum:0.11, oi_delta:0.27, funding:0.09, depth_ratio:0.19, imbalance:0.04, liquidity_delta:0.06, micro_delta:0.02

💼 持仓:
   side:LONG, entry_price:653.09, current_price:651.63, pnl:-0.22%

📈 性能:
   win_rate:0.65, sharpe_ratio:1.85, max_drawdown:-0.08

====================================================================================================
```

### JSON格式

```json
{
  "timestamp": "2026-03-12T00:00:03+00:00",
  "cycle": 112,
  "mode": "MIXED_AI_REVIEW",
  "kline": {
    "symbol": "BNBUSDT",
    "open_time": 1678886400000,
    "close_time": 1678886699999,
    "open_price": 651.4,
    "high_price": 652.0,
    "low_price": 651.0,
    "close_price": 651.63,
    "volume": 1000.0,
    "timeframe": "5m",
    "change_pct": 0.0352
  },
  "ai_strategy": {
    "regime": "TREND",
    "direction": "LONG",
    "signal_long": 0.213,
    "signal_short": 0.0,
    "score_15m_long": 0.142,
    "score_15m_short": 0.0,
    "score_5m_long": 0.218,
    "score_5m_short": 0.0,
    "confidence": 0.75,
    "ai_weights": {
      "cvd": 0.22,
      "cvd_momentum": 0.11,
      "oi_delta": 0.27,
      "funding": 0.09,
      "depth_ratio": 0.19,
      "imbalance": 0.04,
      "liquidity_delta": 0.06,
      "micro_delta": 0.02
    },
    "fallback_used": false,
    "model_version": "v1.0"
  },
  "risk_control": {
    "gate_score": 0.235,
    "gate_action": "BUY",
    "risk_level": "LOW",
    "position_size": 0.08,
    "target_size": 0.10,
    "leverage": 5,
    "margin_used": 100.0,
    "margin_available": 900.0,
    "stop_loss": null,
    "take_profit": null,
    "max_drawdown": 0.0,
    "consecutive_losses": 0,
    "is_protected": true
  },
  "decision": {
    "action": "BUY",
    "reason": "TREND | mode=TREND_STD | long=0.434",
    "hold_attribution": "",
    "signal_source": "signal",
    "trigger_type": "MACD_TRIGGER_REQUIRED",
    "additional_info": null
  },
  "position_info": null,
  "performance": null
}
```

### CSV格式

CSV文件包含以下字段:

- timestamp, cycle, mode, symbol, timeframe
- open_price, high_price, low_price, close_price, volume, change_pct
- regime, direction, signal_long, signal_short, confidence
- gate_score, gate_action, risk_level, position_size, target_size, leverage
- action, reason, signal_source, trigger_type

## 数据分析示例

### Python示例: 加载和分析CSV日志

```python
import pandas as pd
import matplotlib.pyplot as plt

# 加载CSV文件
df = pd.read_csv('D:/AIDCA/AIGRID/logs/runtime.out.00_20260312_120000.csv')

# 基本统计
print(f"总日志条数: {len(df)}")
print(f"\n动作分布:")
print(df['action'].value_counts())

print(f"\n市场状态分布:")
print(df['regime'].value_counts())

print(f"\n交易对分布:")
print(df['symbol'].value_counts())

# 分析特定交易对
bnb_df = df[df['symbol'] == 'BNBUSDT']

# 绘制价格走势
plt.figure(figsize=(12, 6))
plt.plot(bnb_df['timestamp'], bnb_df['close_price'])
plt.title('BNBUSDT Price Over Time')
plt.xlabel('Time')
plt.ylabel('Price')
plt.xticks(rotation=45)
plt.tight_layout()
plt.savefig('bnb_price_chart.png')

# 分析信号与价格变化的关系
bnb_df['signal_strength'] = bnb_df['signal_long'] - bnb_df['signal_short']
bnb_df['price_change'] = bnb_df['close_price'].pct_change()

plt.figure(figsize=(10, 6))
plt.scatter(bnb_df['signal_strength'], bnb_df['price_change'], alpha=0.5)
plt.xlabel('Signal Strength (Long - Short)')
plt.ylabel('Price Change')
plt.title('Signal Strength vs Price Change')
plt.axhline(y=0, color='r', linestyle='--')
plt.axvline(x=0, color='r', linestyle='--')
plt.savefig('signal_vs_price_change.png')

# 分析风控决策
buy_df = df[df['action'] == 'BUY']
print(f"\nBUY操作统计:")
print(f"平均gate_score: {buy_df['gate_score'].mean():.3f}")
print(f"平均confidence: {buy_df['confidence'].mean():.3f}")

sell_df = df[df['action'] == 'SELL']
print(f"\nSELL操作统计:")
print(f"平均gate_score: {sell_df['gate_score'].mean():.3f}")
print(f"平均confidence: {sell_df['confidence'].mean():.3f}")
```

### Python示例: 分析JSON日志

```python
import json
import pandas as pd

# 加载JSON Lines文件
logs = []
with open('D:/AIDCA/AIGRID/logs/runtime.out.00_20260312_120000.jsonl', 'r') as f:
    for line in f:
        logs.append(json.loads(line))

# 转换为DataFrame
df = pd.json_normalize(logs)

# 分析AI权重
if 'ai_strategy.ai_weights' in df.columns:
    weights_df = df['ai_strategy.ai_weights'].apply(pd.Series)
    print("AI权重平均值:")
    print(weights_df.mean())

# 分析决策原因
print("\n决策原因分布:")
print(df['decision.reason'].value_counts().head(10))
```

## 调试和优化建议

### 1. 识别异常模式

查找连续的HOLD决策:

```python
# 查找连续HOLD决策
df['is_hold'] = df['action'] == 'HOLD'
df['hold_streak'] = df.groupby('symbol')['is_hold'].transform(
    lambda x: x.cumsum() - x.cumsum().mask(~x).ffill().fillna(0)
)

long_hold_streaks = df[df['hold_streak'] > 10]
print("连续HOLD决策超过10次的K线:")
print(long_hold_streaks[['timestamp', 'symbol', 'hold_streak', 'reason']])
```

### 2. 分析信号阈值效果

```python
# 分析不同信号阈值下的决策效果
thresholds = [0.2, 0.3, 0.4, 0.5, 0.6]

for threshold in thresholds:
    buy_signals = df[df['signal_long'] > threshold]
    if len(buy_signals) > 0:
        # 计算后续K线的价格变化
        future_returns = []
        for idx, row in buy_signals.iterrows():
            if idx < len(df) - 5:
                future_price = df.iloc[idx+5]['close_price']
                current_price = row['close_price']
                ret = (future_price - current_price) / current_price
                future_returns.append(ret)
        
        if future_returns:
            avg_return = sum(future_returns) / len(future_returns)
            win_rate = sum(1 for r in future_returns if r > 0) / len(future_returns)
            print(f"阈值 {threshold}: 信号数={len(buy_signals)}, 平均收益={avg_return:.2%}, 胜率={win_rate:.2%}")
```

### 3. 风控有效性分析

```python
# 分析风控决策的有效性
protected_trades = df[df['risk_control.is_protected'] == True]
unprotected_trades = df[df['risk_control.is_protected'] == False]

if len(protected_trades) > 0:
    print("受保护的交易:")
    print(f"平均gate_score: {protected_trades['gate_score'].mean():.3f}")
    print(f"平均max_drawdown: {protected_trades['risk_control.max_drawdown'].mean():.3f}")

if len(unprotected_trades) > 0:
    print("\n未保护的交易:")
    print(f"平均gate_score: {unprotected_trades['gate_score'].mean():.3f}")
    print(f"平均max_drawdown: {unprotected_trades['risk_control.max_drawdown'].mean():.3f}")
```

## 集成到现有系统

### 在fund_flow_bot.py中集成

```python
from src.fund_flow.enhanced_kline_logger import EnhancedKlineLogger, KlineOperationLog

class FundFlowBot:
    def __init__(self):
        self.kline_logger = EnhancedKlineLogger(
            "D:/AIDCA/AIGRID/logs/enhanced_kline.log"
        )
    
    def process_kline(self, kline_data, decision, risk_info):
        """处理K线并记录日志"""
        log_entry = KlineOperationLog(
            timestamp=datetime.now(),
            cycle=self.current_cycle,
            mode=self.mode,
            kline=KlineInfo(
                symbol=kline_data['symbol'],
                open_time=kline_data['open_time'],
                close_time=kline_data['close_time'],
                open_price=kline_data['open'],
                high_price=kline_data['high'],
                low_price=kline_data['low'],
                close_price=kline_data['close'],
                volume=kline_data['volume'],
                timeframe=kline_data.get('timeframe', '5m')
            ),
            ai_strategy=AIStrategyInfo(
                regime=MarketRegime(kline_data['regime']),
                direction=Direction(kline_data['direction']),
                signal_long=kline_data.get('signal_long', 0.0),
                signal_short=kline_data.get('signal_short', 0.0),
                # ... 其他字段
            ),
            risk_control=RiskControlInfo(
                gate_score=risk_info['gate_score'],
                gate_action=risk_info['gate_action'],
                # ... 其他字段
            ),
            decision=DecisionInfo(
                action=DecisionAction(decision['action']),
                reason=decision['reason'],
                # ... 其他字段
            )
        )
        
        self.kline_logger.log_operation(log_entry)
```

## 常见问题

### Q1: 如何解析历史日志文件?

A: 使用log_parser.py脚本:
```bash
python log_parser.py D:\AIDCA\AIGRID\logs\2026-03\2026-03-12\runtime.out.00.log
```

### Q2: 如何实时记录K线操作?

A: 在策略代码中初始化EnhancedKlineLogger,并在每次处理K线后调用log_operation()方法。

### Q3: 支持哪些输出格式?

A: 支持三种格式:
- 增强格式日志(.log) - 人类可读
- JSON Lines(.jsonl) - 机器可读
- CSV(.csv) - 便于数据分析

### Q4: 如何分析特定交易对的表现?

A: 使用get_logs_by_symbol()方法或加载CSV后过滤:
```python
bnb_logs = logger.get_logs_by_symbol("BNBUSDT")
# 或
bnb_df = df[df['symbol'] == 'BNBUSDT']
```

### Q5: 如何优化参数?

A: 通过分析日志中的信号评分、决策原因和后续价格变化,可以:
1. 调整信号阈值
2. 优化AI权重配置
3. 改进风控阈值
4. 调整市场状态识别参数

## 总结

增强K线日志系统提供了完整的日志记录、解析和分析功能,能够帮助您:

1. ✅ 完整记录每次K线操作的详细信息
2. ✅ 解析现有日志文件,提取关键信息
3. ✅ 导出多种格式,便于分析
4. ✅ 查询和过滤日志
5. ✅ 进行数据分析和可视化
6. ✅ 调试策略BUG
7. ✅ 优化策略参数

通过系统化的日志记录和分析,您可以更好地理解策略行为,发现潜在问题,并持续优化策略性能。

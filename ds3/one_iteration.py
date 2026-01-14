import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
sys.path.insert(0, '.')
import jianhua_backtest as jb

# 生成35行数据
dates = pd.date_range(start=datetime.now() - timedelta(hours=5), periods=35, freq='15min')
np.random.seed(42)
returns = np.random.normal(0.001, 0.01, 35)
price = 50000 * np.exp(np.cumsum(returns))
df = pd.DataFrame({
    'timestamp': dates,
    'open': price * (1 + np.random.uniform(-0.005, 0.005, 35)),
    'high': price * (1 + np.random.uniform(0, 0.03, 35)),
    'low': price * (1 - np.random.uniform(0, 0.03, 35)),
    'close': price,
    'volume': np.random.uniform(100, 1000, 35)
})
df['high'] = df[['open', 'high', 'close']].max(axis=1)
df['low'] = df[['open', 'low', 'close']].min(axis=1)
df['sma20'] = df['close'].rolling(window=20).mean()

i = 20
current_price = df['close'].iloc[i]
sma20 = df['sma20'].iloc[i]
timestamp = df['timestamp'].iloc[i]
print(f'i={i}, price={current_price:.2f}, sma20={sma20:.2f}')

# 模拟AI信号
price_data = jb.simulate_price_data_for_backtest(df, i)
signal_data = jb.analyze_with_deepseek_for_backtest(price_data)
signal = signal_data['signal']
print(f'AI信号: {signal}')

# 创建回测器实例
backtester = jb.SimpleBacktester(initial_balance=10000.0, use_deepseek=True)
print(f'初始position: {backtester.position}')

# 手动执行交易逻辑
if signal == 'SELL' and backtester.position is None:
    print('条件满足：开空仓')
    trade_amount = backtester.balance * 0.5
    backtester.position = {
        'side': 'short',
        'entry_price': current_price,
        'amount': trade_amount,
        'timestamp': timestamp,
        'entry_index': i
    }
    backtester.balance -= trade_amount
    print(f'开空仓成功，仓位: {backtester.position}, 余额: {backtester.balance:.2f}')
else:
    print('条件不满足')
    print(f'signal={signal}, position={backtester.position}')
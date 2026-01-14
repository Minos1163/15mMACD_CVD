import sys
sys.path.insert(0, '.')
import jianhua_backtest as jb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# 生成小数据集，确保有足够的行数
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

print('数据准备完成，形状:', df.shape)
print('前5行:')
print(df.head())
print('\n开始回测')
backtester = jb.SimpleBacktester(initial_balance=10000.0, use_deepseek=True)
backtester.run_backtest(df, use_deepseek=True)
print('\n交易次数:', len(backtester.trades))
if backtester.trades:
    for i, trade in enumerate(backtester.trades):
        print(f'交易{i+1}: {trade}')
else:
    print('没有交易发生')
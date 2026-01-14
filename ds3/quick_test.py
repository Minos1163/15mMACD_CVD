import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
sys.path.insert(0, '.')
import jianhua_backtest as jb

# 创建只有25行的数据，这样循环只有5次迭代
dates = pd.date_range(start=datetime.now() - timedelta(hours=2), periods=25, freq='15min')
np.random.seed(42)
price = 50000 + np.cumsum(np.random.randn(25) * 100)
df = pd.DataFrame({
    'timestamp': dates,
    'open': price,
    'high': price + np.random.uniform(0, 200, 25),
    'low': price - np.random.uniform(0, 200, 25),
    'close': price + np.random.randn(25) * 50,
    'volume': np.random.uniform(100, 1000, 25)
})
df['high'] = df[['open', 'high', 'close']].max(axis=1)
df['low'] = df[['open', 'low', 'close']].min(axis=1)

print('Data shape:', df.shape)
print('Columns:', df.columns.tolist())
print('SMA20计算前:', df['close'].iloc[20:25].values)

backtester = jb.SimpleBacktester(initial_balance=10000.0, use_deepseek=False)
print('开始回测')
backtester.run_backtest(df, use_deepseek=False)
print('交易次数:', len(backtester.trades))
print('权益曲线长度:', len(backtester.equity_curve))
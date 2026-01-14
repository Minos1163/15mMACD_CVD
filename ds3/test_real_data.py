#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试获取真实BTC数据"""
import requests
import pandas as pd
from datetime import datetime, timedelta


def test_fetch_btc_data():
    """测试获取BTC真实数据"""
    print("=" * 60)
    print("测试获取真实BTC历史数据")
    print("=" * 60)

    # 交易对
    symbol = "BTCUSDT"
    interval = "15m"
    limit = 100

    # 计算时间范围
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = int((datetime.now() - timedelta(days=7)).timestamp() * 1000)

    print(f"\n请求参数:")
    print(f"  交易对: {symbol}")
    print(f"  时间周期: {interval}")
    print(f"  K线数量: {limit}")
    print(f"  时间范围: {datetime.fromtimestamp(start_time/1000)} 到 {datetime.fromtimestamp(end_time/1000)}")

    # 使用币安公共API
    url = "https://api.binance.com/api/v3/klines"
    params = {
        'symbol': symbol,
        'interval': interval,
        'startTime': start_time,
        'endTime': end_time,
        'limit': limit
    }

    print(f"\n正在请求API...")
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        klines = response.json()

        print(f"✓ API请求成功")
        print(f"✓ 获取到 {len(klines)} 根K线数据")

        if len(klines) == 0:
            print("✗ 数据为空")
            return

        # 转换为DataFrame
        ohlcv_list = []
        for k in klines:
            ohlcv_list.append([
                int(k[0]),
                float(k[1]),
                float(k[2]),
                float(k[3]),
                float(k[4]),
                float(k[5])
            ])
        df = pd.DataFrame(ohlcv_list, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 显示统计信息
        print(f"\n数据统计:")
        print(f"  时间范围: {df['timestamp'].iloc[0]} 到 {df['timestamp'].iloc[-1]}")
        print(f"  价格范围: {df['low'].min():.2f} - {df['high'].max():.2f} USDT")
        print(f"  平均价格: {df['close'].mean():.2f} USDT")
        print(f"  价格变化: {((df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100):+.2f}%")
        print(f"  总成交量: {df['volume'].sum():.2f} BTC")

        # 显示前10根K线
        print(f"\n前10根K线:")
        print(df[['timestamp', 'open', 'high', 'low', 'close']].head(10).to_string(index=False))

        print(f"\n最后10根K线:")
        print(df[['timestamp', 'open', 'high', 'low', 'close']].tail(10).to_string(index=False))

        # 保存数据
        output_file = "btc_real_data.csv"
        df.to_csv(output_file, index=False, encoding='utf-8')
        print(f"\n✓ 数据已保存到: {output_file}")

        return df

    except requests.exceptions.Timeout:
        print("✗ API请求超时")
    except requests.exceptions.RequestException as e:
        print(f"✗ API请求失败: {e}")
    except Exception as e:
        print(f"✗ 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_fetch_btc_data()

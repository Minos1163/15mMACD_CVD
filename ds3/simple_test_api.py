#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单测试 - 获取BTC真实数据
"""
import requests
import json
from datetime import datetime, timedelta

def test_api():
    print("="*60)
    print("测试币安API连接")
    print("="*60)

    url = "https://api.binance.com/api/v3/klines"
    params = {
        'symbol': 'BTCUSDT',
        'interval': '1h',
        'limit': 24
    }

    print(f"\n请求URL: {url}")
    print(f"参数: {params}")

    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"\n状态码: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"数据条数: {len(data)}")

            if len(data) > 0:
                print(f"\n第一根K线:")
                print(json.dumps(data[0], indent=2))
                print(f"\n最后一根K线:")
                print(json.dumps(data[-1], indent=2))
        else:
            print("返回数据为空")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_api()

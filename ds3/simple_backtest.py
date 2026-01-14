#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版回测系统 - 专注于AI信号生成和盈利能力测试
"""

import os
import sys
import time
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import requests
from typing import List, Dict, Any, Optional

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 尝试导入ds3模块
try:
    from ds3 import (
        calculate_technical_indicators,
        get_support_resistance_levels,
        get_market_trend,
        generate_technical_analysis_text,
        deepseek_client,
        TRADE_CONFIG
    )
    DEEPSEEK_AVAILABLE = True
except Exception as e:
    print(f"警告: 导入ds3模块失败 - {e}")
    DEEPSEEK_AVAILABLE = False
    import random
    
    # 创建模拟函数和配置
    def calculate_technical_indicators(df):
        return df
    def get_market_trend(df):
        return {'overall': '震荡整理'}
    def generate_technical_analysis_text(price_data) -> str:
        return "技术分析不可用"
    def get_support_resistance_levels(df, lookback=20):
        return {}
    
    # 模拟DeepSeek客户端
    class MockDeepSeekClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    # 随机生成交易信号（40% BUY, 40% SELL, 20% HOLD）
                    rand_val = random.random()
                    if rand_val < 0.4:
                        signal = "BUY"
                        reason = "模拟买入信号：技术指标显示上涨趋势"
                        confidence = random.choice(["MEDIUM", "HIGH"])
                    elif rand_val < 0.8:
                        signal = "SELL"
                        reason = "模拟卖出信号：技术指标显示下跌趋势"
                        confidence = random.choice(["MEDIUM", "HIGH"])
                    else:
                        signal = "HOLD"
                        reason = "模拟持有信号：市场震荡整理"
                        confidence = "LOW"
                    
                    # 生成合理的止损和止盈价格
                    price = 50000  # 假设价格
                    stop_loss = price * (0.98 if signal == "BUY" else 1.02)
                    take_profit = price * (1.02 if signal == "BUY" else 0.98)
                    
                    content = f'{{"signal": "{signal}", "reason": "{reason}", "stop_loss": {stop_loss}, "take_profit": {take_profit}, "confidence": "{confidence}"}}'
                    
                    class Message:
                        def __init__(self):
                            self.content = content
                    class Choice:
                        def __init__(self):
                            self.message = Message()
                    class MockResponse:
                        def __init__(self):
                            self.choices = [Choice()]
                    return MockResponse()
    
    deepseek_client = MockDeepSeekClient()
    
    # 默认交易配置
    TRADE_CONFIG = {
        'symbol': 'BTC/USDT:USDT',
        'leverage': 10,
        'timeframe': '15m',
        'test_mode': True,
        'data_points': 96,
        'analysis_periods': {
            'short_term': 20,
            'medium_term': 50,
            'long_term': 96
        },
        'position_management': {
            'enable_intelligent_position': True,
            'base_usdt_amount': 100,
            'high_confidence_multiplier': 1.5,
            'medium_confidence_multiplier': 1.0,
            'low_confidence_multiplier': 0.5,
            'max_position_ratio': 10,
            'trend_strength_multiplier': 1.2
        },
        # 新增交易风格基因参数
        'trading_style_genes': {
            'market_bias': '趋势跟随',          # 市场偏好: 趋势跟随、反转交易、震荡策略
            'risk_attitude': '中等风险',        # 风险态度: 保守、中等风险、激进
            'position_style': '分批建仓',       # 仓位风格: 全仓进出、分批建仓、金字塔加仓
            'add_position_logic': '盈利加仓',   # 加仓逻辑: 盈利加仓、亏损加仓、等额加仓
            'stop_loss_style': '移动止损',      # 止损方式: 固定止损、移动止损、时间止损
            'coin_filtering': '中等筛选',       # 币种筛选强度: 宽松筛选、中等筛选、严格筛选
            'timeframe_focus': '多周期共振'     # 时间周期偏好: 短线周期、中线周期、多周期共振
        },
        'risk_control': {
            'max_daily_loss_pct': 5.0,
            'max_single_loss_pct': 2.0,
            'max_consecutive_losses': 3,
            'max_daily_trades': 10,
            'circuit_breaker_enabled': True,
            'max_circuit_breaker_tries': 5,
            'circuit_breaker_cooldown': 300,
            'stop_loss_default_pct': 2.0,
            'take_profit_default_pct': 4.0
        },
        'order_execution': {
            'max_order_retries': 3,
            'retry_delay_base': 1.0,
            'cancel_order_retries': 2,
            'order_timeout': 30,
            'verify_order_status': True,
            'allow_partial_fills': True
        }
    }


def fetch_historical_klines(symbol: str, interval: str, 
                          start_time: datetime, 
                          end_time: datetime,
                          limit: int = 1000) -> pd.DataFrame:
    """
    从币安API获取历史K线数据
    
    Args:
        symbol: 交易对，如 'BTCUSDT'
        interval: 时间间隔，如 '15m'
        start_time: 开始时间
        end_time: 结束时间
        limit: 每次请求的最大K线数量
        
    Returns:
        DataFrame: K线数据
    """
    print(f"[数据] 获取历史数据: {symbol} {interval}")
    print(f"   时间范围: {start_time} 到 {end_time}")
    
    all_klines = []
    current_start = start_time
    
    # 币安API端点
    base_url = "https://api.binance.com/api/v3/klines"
    
    while current_start < end_time:
        # 计算本次请求的结束时间
        current_end = min(current_start + timedelta(hours=limit*15/60), end_time)
        
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit,
            'startTime': int(current_start.timestamp() * 1000),
            'endTime': int(current_end.timestamp() * 1000)
        }
        
        try:
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            klines = response.json()
            
            if not klines:
                print(f"   已获取 {len(all_klines)} 根K线")
                break
            
            all_klines.extend(klines)
            
            # 更新开始时间为最后一条K线的时间+1ms
            last_time = klines[-1][0]
            current_start = datetime.fromtimestamp(last_time / 1000 + 0.001)
            
            # 显示进度
            progress = (current_start - start_time) / (end_time - start_time) * 100
            print(f"   进度: {progress:.1f}%", end='\r')
            
            # 避免频繁请求
            time.sleep(0.1)
            
        except Exception as e:
            print(f"[错误] 获取数据失败: {e}")
            break
    
    print(f"\n[成功] 成功获取 {len(all_klines)} 根K线")
    
    # 转换为DataFrame
    columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume',
               'close_time', 'quote_asset_volume', 'number_of_trades',
               'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume',
               'ignore']
    
    df = pd.DataFrame(all_klines, columns=columns)
    
    # 转换数据类型
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 
                   'quote_asset_volume', 'taker_buy_base_asset_volume',
                   'taker_buy_quote_asset_volume']
    
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col])
    
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # 保留必要列
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    
    return df


def analyze_with_deepseek_for_backtest(price_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    使用DeepSeek API进行回测分析
    
    Args:
        price_data: 价格数据
        
    Returns:
        dict: 交易信号
    """
    if not DEEPSEEK_AVAILABLE:
        return create_fallback_signal(price_data)
    
    try:
        # 添加详细调试信息
        print(f"[API] 开始API调用 - 时间: {price_data['timestamp']}")
        
        # 生成技术分析文本
        technical_analysis = generate_technical_analysis_text(price_data)
        
        # 构建K线数据文本
        timeframe = TRADE_CONFIG.get('timeframe', '15m')
        kline_text = f"【最近5根{timeframe}K线数据】\n"
        for i, kline in enumerate(price_data['kline_data'][-5:]):
            trend = "阳线" if kline['close'] > kline['open'] else "阴线"
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            kline_text += f"K线{i + 1}: {trend} 开盘:{kline['open']:.2f} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"
        
        # 构建提示词 - 针对回测优化，集成交易风格基因
        genes = TRADE_CONFIG['trading_style_genes']
        prompt = f"""
        你是一个专业的加密货币量化交易AI。
        
        你的交易风格基因如下：
        - 市场偏好: {genes['market_bias']}
        - 风险态度: {genes['risk_attitude']}
        - 仓位风格: {genes['position_style']}
        - 加仓逻辑: {genes['add_position_logic']}
        - 止损方式: {genes['stop_loss_style']}
        - 币种筛选强度: {genes['coin_filtering']}
        - 时间周期偏好: {genes['timeframe_focus']}
        
        你必须：
        1. 严格控制回撤
        2. 避免过度交易
        3. 只在高置信度时出手
        
        【数据详情】
        {kline_text}
        
        {technical_analysis}
        
        【当前行情】
        - 当前价格: ${price_data['price']:,.2f}
        - 时间: {price_data['timestamp']}
        - 价格变化: {price_data['price_change']:+.2f}%
        
        请用以下JSON格式回复：
        {{
            "signal": "BUY|SELL|HOLD",
            "reason": "简要分析理由(基于技术分析和交易风格基因)",
            "stop_loss": 具体价格,
            "take_profit": 具体价格, 
            "confidence": "HIGH|MEDIUM|LOW"
        }}
        """
        
        # 调用DeepSeek API
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是一个专业的加密货币交易分析师，正在进行历史数据回测分析。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=500
        )
        
        # 解析响应 - 检查response及嵌套属性是否存在
        if not response or not response.choices:
            print("[错误] API响应为空或无choices数据")
            return create_fallback_signal(price_data)
        
        choice = response.choices[0]
        if not choice or not choice.message or choice.message.content is None:
            print("[错误] API响应中message.content为空")
            return create_fallback_signal(price_data)
        
        content = choice.message.content
        
        # 添加API调用完成的调试信息
        print(f"[API] API调用完成 - 响应内容: {content[:100]}...")  # 只打印前100字符
        
        # 提取JSON部分
        import re
        if content is None:
            # 如果content为None，使用备用信号
            signal_data = create_fallback_signal(price_data)
        else:
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                signal_data = json.loads(json_str)
            else:
                # 如果无法解析JSON，使用备用信号
                signal_data = create_fallback_signal(price_data)
        
        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)
        
        # 添加时间戳
        signal_data['timestamp'] = price_data['timestamp']
        
        return signal_data
        
    except Exception as e:
        print(f"[错误] DeepSeek分析失败: {e}")
        return create_fallback_signal(price_data)


def create_fallback_signal(price_data: Dict[str, Any]) -> Dict[str, Any]:
    """创建备用交易信号"""
    price_change = price_data.get('price_change', 0)
    
    # 基于价格变化的简单策略
    if price_change > 0.5:  # 上涨超过0.5%
        signal = "BUY"
        reason = f"价格上涨{price_change:.2f}%，采取买入策略"
        confidence = "MEDIUM"
    elif price_change < -0.5:  # 下跌超过0.5%
        signal = "SELL"
        reason = f"价格下跌{price_change:.2f}%，采取卖出策略"
        confidence = "MEDIUM"
    else:
        signal = "HOLD"
        reason = f"价格震荡({price_change:.2f}%)，采取保守策略"
        confidence = "LOW"
    
    return {
        "signal": signal,
        "reason": reason,
        "stop_loss": price_data['price'] * 0.98,
        "take_profit": price_data['price'] * 1.02,
        "confidence": confidence,
        "is_fallback": True,
        "timestamp": price_data['timestamp']
    }


def simulate_price_data_for_backtest(df: pd.DataFrame, idx: int, 
                                   timeframe: str = '15m') -> Dict[str, Any]:
    """
    为回测模拟price_data结构
    
    Args:
        df: 完整的K线DataFrame
        idx: 当前K线索引
        timeframe: 时间周期
        
    Returns:
        dict: 模拟的price_data
    """
    # 添加计算开始的调试信息
    print(f"[数据] 开始计算技术指标 (当前K线索引: {idx})")
    
    # 获取截止到当前idx的数据
    df_slice = df.iloc[:idx+1].copy()
    
    # 计算技术指标
    df_with_indicators = calculate_technical_indicators(df_slice)
    
    # 添加计算完成的调试信息
    print(f"[数据] 技术指标计算完成 (当前价格: {df_with_indicators.iloc[-1]['close']:.2f})")
    
    # 获取当前数据
    current_data = df_with_indicators.iloc[-1]
    # 修复：确保不会出现负索引的情况
    previous_data = df_with_indicators.iloc[-2] if len(df_with_indicators) > 1 else current_data
    
    # 获取趋势分析
    trend_analysis = get_market_trend(df_with_indicators)
    
    # 获取支撑阻力位
    levels_analysis = get_support_resistance_levels(df_with_indicators)
    
    # 构建price_data
    price_data = {
        'price': float(current_data['close']),
        'timestamp': current_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
        'high': float(current_data['high']),
        'low': float(current_data['low']),
        'volume': float(current_data['volume']),
        'timeframe': timeframe,
        'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
        'kline_data': df_slice[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
        'technical_data': {
            'sma_5': float(current_data.get('sma_5', 0)),
            'sma_20': float(current_data.get('sma_20', 0)),
            'sma_50': float(current_data.get('sma_50', 0)),
            'rsi': float(current_data.get('rsi', 0)),
            'macd': float(current_data.get('macd', 0)),
            'macd_signal': float(current_data.get('macd_signal', 0)),
            'macd_histogram': float(current_data.get('macd_histogram', 0)),
            'bb_upper': float(current_data.get('bb_upper', 0)),
            'bb_lower': float(current_data.get('bb_lower', 0)),
            'bb_position': float(current_data.get('bb_position', 0)),
            'volume_ratio': float(current_data.get('volume_ratio', 0))
        },
        'trend_analysis': trend_analysis,
        'levels_analysis': levels_analysis,
        'full_data': df_with_indicators
    }
    
    return price_data


class SimpleBacktester:
    """简单回测器"""
    
    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.daily_balances = {}  # 添加每日余额记录，用于计算每日盈亏
        
        print(f"[初始化] 初始化回测器")
        print(f"   初始资金: {initial_balance:.2f} USDT")
    
    def execute_trade(self, signal: str, price: float, timestamp: str, 
                     reason: str, confidence: str, current_equity: float) -> Optional[Dict[str, Any]]:
        """
        执行模拟交易
        
        Args:
            signal: 交易信号 (BUY/SELL/HOLD)
            price: 当前价格
            timestamp: 时间戳
            reason: 交易理由
            confidence: 信心程度
            current_equity: 当前总权益（余额+浮动盈亏）
            
        Returns:
            dict: 交易记录，无交易时返回None
        """
        if signal == 'HOLD':
            return None
        
        # 使用总权益计算风险比例（实现盈利再投资）
        risk_percentage = 0.02  # 2%的总权益风险
        
        # 根据信心程度调整仓位
        confidence_multiplier = {
            'HIGH': 1.5,
            'MEDIUM': 1.0,
            'LOW': 0.5
        }.get(confidence, 1.0)
        
        # 基于总权益计算交易金额，实现盈利再投资
        trade_amount = current_equity * risk_percentage * confidence_multiplier
        
        # 检查资金是否充足（确保不超过总权益）
        if trade_amount > current_equity:
            return None
        
        # 计算手续费（0.05%）
        commission = trade_amount * 0.0005
        
        # 更新余额（扣除手续费）
        self.balance -= commission
        
        # 创建交易记录
        trade_record = {
            'timestamp': timestamp,
            'signal': signal,
            'entry_price': price,
            'trade_amount': trade_amount,
            'commission': commission,
            'balance_before': self.balance + commission,
            'balance_after': self.balance,
            'reason': reason,
            'confidence': confidence
        }
        
        # 更新持仓，添加amount字段确保后续计算正确
        self.position = {
            'side': 'long' if signal == 'BUY' else 'short',
            'entry_price': price,
            'timestamp': timestamp,
            'amount': trade_amount  # 记录持仓金额，用于后续盈亏计算
        }
        
        pnl = self.balance - self.initial_balance
        pnl_pct = (pnl / self.initial_balance) * 100
        print(f"  [执行] {timestamp} - 执行{signal} @ {price:.2f} USDT, 金额: {trade_amount:.2f} USDT")
        print(f"        余额: {self.balance:.2f} USDT, 累计盈亏: {pnl:+.2f} USDT ({pnl_pct:+.2f}%)")
        
        return trade_record
    
    def close_position(self, price: float, timestamp: str, reason: str, confidence: str, current_equity: float):
        """平仓"""
        if self.position:
            # 计算盈亏
            entry_price = self.position['entry_price']
            amount = self.position['amount']
            
            if self.position['side'] == 'long':
                pnl = (price - entry_price) / entry_price * amount
            else:
                pnl = (entry_price - price) / entry_price * amount
            
            # 更新余额
            self.balance += pnl
            
            # 计算总权益（此时已平仓，总权益等于余额）
            total_equity = self.balance
            total_pnl = total_equity - self.initial_balance
            total_pnl_pct = (total_pnl / self.initial_balance) * 100
            
            print(f"  [平仓] {timestamp} - 平仓{self.position['side']}仓，盈亏: {pnl:+.2f} USDT")
            print(f"        余额: {self.balance:.2f} USDT, 累计盈亏: {total_pnl:+.2f} USDT ({total_pnl_pct:+.2f}%)")
            
            # 记录交易
            trade_record = {
                'timestamp': timestamp,
                'signal': f"CLOSE_{self.position['side'].upper()}",
                'entry_price': entry_price,
                'exit_price': price,
                'pnl': pnl,
                'balance_after': self.balance,
                'reason': reason,
                'confidence': confidence
            }
            self.trades.append(trade_record)
            
            # 重置持仓
            self.position = None

    def run_backtest(self, df: pd.DataFrame, use_ai: bool = True):
        """
        运行回测
        
        Args:
            df: K线数据DataFrame
            use_ai: 是否使用AI分析
        """
        print(f"\n[启动] 开始回测")
        print(f"   数据范围: {df['timestamp'].iloc[0]} 到 {df['timestamp'].iloc[-1]}")
        print(f"   K线数量: {len(df)}")
        print(f"   AI分析: {'启用' if use_ai else '禁用'}")
        print("-" * 60)
        
        # 重置状态
        self.balance = self.initial_balance
        self.position = None
        self.trades = []
        self.equity_curve = []
        
        total_kline = len(df)
        
        # 从第100根K线开始，确保有足够数据计算指标
        for idx in range(100, total_kline):
            # 初始化变量，避免"可能未绑定"警告
            signal = 'HOLD'
            trade_amount = 0.0
            pnl = 0.0
            
            # 添加详细调试信息，确保用户知道回测已开始
            if idx == 100:
                print(f"[启动] 回测已正式启动，开始处理第100根K线...")

            # 修改进度提示条件，使输出更频繁（每24根K线一次）
            if idx % 24 == 0:
                progress = idx / total_kline * 100
                print(f"[进度] 处理进度: {idx}/{total_kline} ({progress:.1f}%)")
            
            # 模拟当前时间点的price_data
            price_data = simulate_price_data_for_backtest(df, idx)
            current_price = price_data['price']
            timestamp = price_data['timestamp']
            
            # 计算当前权益（包括未平仓浮动盈亏）
            current_equity = self.balance
            if self.position:
                entry_price = self.position['entry_price']
                amount = self.position['amount']
                if self.position['side'] == 'long':
                    floating_pnl = (current_price - entry_price) / entry_price * amount
                else:
                    floating_pnl = (entry_price - current_price) / entry_price * amount
                current_equity += floating_pnl

            # ===== 新增：每日盈亏统计逻辑 =====
            # 提取日期部分 (YYYY-MM-DD)
            current_date = timestamp[:10]
            
            # 如果是新的一天，记录开始余额
            if current_date not in self.daily_balances:
                self.daily_balances[current_date] = {
                    'start_balance': current_equity,
                    'end_balance': current_equity,
                    'daily_pnl': 0.0,
                    'trades': []
                }
                print(f"[每日] {current_date} 开始 - 余额: {current_equity:.2f} USDT")
            
            # 记录当天的交易信息
            if signal != 'HOLD':
                self.daily_balances[current_date]['trades'].append({
                    'timestamp': timestamp,
                    'signal': signal,
                    'price': current_price,
                    'trade_amount': trade_amount,
                    'pnl': pnl
                })
            # ===============================
            
            # 生成交易信号
            if use_ai and DEEPSEEK_AVAILABLE:
                # 添加API调用前的调试信息
                print(f"[调试] 调用DeepSeek API处理时间: {timestamp}")
                signal_data = analyze_with_deepseek_for_backtest(price_data)
                # 添加API调用完成的调试信息
                print(f"[调试] DeepSeek API响应: {signal_data.get('signal')} @ {current_price:.2f}")
            else:
                signal_data = create_fallback_signal(price_data)
            
            signal = signal_data['signal']
            reason = signal_data['reason']
            confidence = signal_data['confidence']
            
            # 如果当前有持仓，且信号与持仓方向相反，则平仓
            if self.position:
                if (self.position['side'] == 'long' and signal == 'SELL') or \
                   (self.position['side'] == 'short' and signal == 'BUY'):
                    # 平仓
                    self.close_position(current_price, timestamp, f"AI信号转{signal.lower()}: {reason}", confidence, current_equity)
            
            # 只有在没有持仓或信号与当前持仓方向不冲突时才执行新交易
            if signal != 'HOLD' and (
                not self.position or 
                (self.position['side'] == 'long' and signal == 'BUY') or
                (self.position['side'] == 'short' and signal == 'SELL')
            ):
                # 执行交易
                if signal in ['BUY', 'SELL']:
                    # ===== 修改：基于总权益计算交易金额 =====
                    # 根据配置决定仓位大小
                    config = TRADE_CONFIG.get('position_management', {})
                    risk_percentage = 0.02  # 2%的总权益风险
                    
                    # 根据信心程度调整仓位
                    confidence_multiplier = {
                        'HIGH': config.get('high_confidence_multiplier', 1.5),
                        'MEDIUM': config.get('medium_confidence_multiplier', 1.0),
                        'LOW': config.get('low_confidence_multiplier', 0.5)
                    }.get(confidence, 1.0)
                    
                    # 计算实际交易金额（基于总权益，不超过当前权益）
                    trade_amount = min(current_equity * risk_percentage * confidence_multiplier, current_equity)
                    
                    # 如果交易金额太小，忽略交易
                    if trade_amount < 1:  # 至少1 USDT
                        continue
                    # =====================================
                    
                    # 计算手续费（0.05%）
                    commission = trade_amount * 0.0005
                    
                    # 更新余额
                    self.balance -= commission
                    
                    # 更新持仓
                    self.position = {
                        'side': 'long' if signal == 'BUY' else 'short',
                        'entry_price': current_price,
                        'timestamp': timestamp,
                        'amount': trade_amount  # 记录持仓金额
                    }
                    
                    # 计算盈亏
                    pnl = self.balance - self.initial_balance
                    
                    print(f"  [执行] {timestamp} - 开仓{signal} @ {current_price:.2f} USDT, 金额: {trade_amount:.2f} USDT")
                    print(f"        余额: {self.balance:.2f} USDT, 累计盈亏: {pnl:+.2f} USDT ({pnl:.2f}%)")
                    
                    # 记录交易
                    trade_record = {
                        'timestamp': timestamp,
                        'signal': signal,
                        'entry_price': current_price,
                        'trade_amount': trade_amount,
                        'commission': commission,
                        'balance_after': self.balance,
                        'reason': reason,
                        'confidence': confidence
                    }
                    self.trades.append(trade_record)
            
            # 更新当天的结束余额
            if current_date in self.daily_balances:
                self.daily_balances[current_date]['end_balance'] = current_equity
                start_balance = self.daily_balances[current_date]['start_balance']
                self.daily_balances[current_date]['daily_pnl'] = current_equity - start_balance
            
            # 检查是否是当天最后一根K线（简化处理，假设每天有96根15分钟K线）
            if (idx + 1) % 96 == 0 or idx == total_kline - 1:
                # 当天结束
                data = self.daily_balances[current_date]
                daily_pnl = data['daily_pnl']
                daily_pnl_pct = (daily_pnl / data['start_balance']) * 100
                
                print(f"[每日] {current_date} 结束 - 余额: {data['end_balance']:.2f} USDT")
                print(f"      当日盈亏: {daily_pnl:+.2f} USDT ({daily_pnl_pct:+.2f}%)")
        
        # 处理剩余持仓
        if self.position:
            print(f"[结束] 回测结束时仍有持仓，强制平仓")
            
            # 计算current_equity
            current_equity = self.balance
            if self.position:
                entry_price = self.position['entry_price']
                amount = self.position['amount']
                if self.position['side'] == 'long':
                    floating_pnl = (df['close'].iloc[-1] - entry_price) / entry_price * amount
                else:
                    floating_pnl = (entry_price - df['close'].iloc[-1]) / entry_price * amount
                current_equity += floating_pnl
            
            # 调用close_position方法
            self.close_position(df['close'].iloc[-1], str(df['timestamp'].iloc[-1]), "回测结束强制平仓", "N/A", current_equity)
        
        print("\n✅ 回测完成!")
    
    def generate_report(self):
        """生成简单报告"""
        if not self.trades:
            print("[警告] 没有交易记录")
            return
        
        total_trades = len(self.trades)
        winning_trades = 0
        total_profit = 0
        
        for trade in self.trades:
            if 'pnl' in trade:
                profit = trade['pnl']
                total_profit += profit
                
                if profit > 0:
                    winning_trades += 1
        
        win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0
        final_equity = self.equity_curve[-1]['equity'] if self.equity_curve else self.balance
        total_pnl = final_equity - self.initial_balance
        return_pct = total_pnl / self.initial_balance * 100
        
        print("\n" + "="*60)
        print("[图表] 回测报告")
        print("="*60)
        print(f"\n[资金] 资金情况:")
        print(f"   初始资金: {self.initial_balance:.2f} USDT")
        print(f"   最终权益: {final_equity:.2f} USDT")
        print(f"   总盈亏: {total_pnl:+.2f} USDT ({return_pct:+.2f}%)")
        
        print(f"\n[交易] 交易统计:")
        print(f"   总交易次数: {total_trades}")
        print(f"   胜率: {win_rate:.1f}%")
        print(f"   盈利交易: {winning_trades}次")
        print(f"   总盈利: {total_profit:.2f} USDT")
        
        # 计算最大回撤
        if self.equity_curve:
            equity_values = [e['equity'] for e in self.equity_curve]
            peak = equity_values[0]
            max_drawdown = 0
            
            for equity in equity_values:
                if equity > peak:
                    peak = equity
                drawdown = (peak - equity) / peak * 100
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
            
            print(f"\n[风险] 风险指标:")
            print(f"   最大回撤: {max_drawdown:.2f}%")
        
        # ===== 新增：每日盈亏统计显示 =====
        print("\n[每日] 每日盈亏明细:")
        total_days = len(self.daily_balances)
        profitable_days = 0
        total_daily_pnl = 0
        
        for date, data in self.daily_balances.items():
            start = data['start_balance']
            end = data['end_balance']
            daily_pnl = end - start
            daily_pnl_pct = (daily_pnl / start) * 100
            total_daily_pnl += daily_pnl
            
            if daily_pnl > 0:
                profitable_days += 1
                
            print(f"   {date}: {daily_pnl:+.2f} USDT ({daily_pnl_pct:+.2f}%)")
        
        # 计算胜率
        win_rate = profitable_days / total_days * 100 if total_days > 0 else 0
        print(f"\n[统计] 盈利日胜率: {profitable_days}/{total_days} ({win_rate:.1f}%)")
        print(f"      平均每日盈亏: {total_daily_pnl/total_days:.2f} USDT")
        # =================================
        
        print("\n" + "="*60)
        
        return {
            'initial_balance': self.initial_balance,
            'final_equity': final_equity,
            'total_pnl': total_pnl,
            'return_pct': return_pct,
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_profit': total_profit,
            'daily_win_rate': win_rate,
            'average_daily_pnl': total_daily_pnl/total_days
        }


def main():
    """主函数"""
    print("="*60)
    print("简化版AI交易策略回测系统")
    print("="*60)
    
    # 检查DeepSeek可用性
    if DEEPSEEK_AVAILABLE:
        print("[成功] DeepSeek API可用")
    else:
        print("[警告] DeepSeek API不可用，将使用备用信号")
    
    # 设置参数
    symbol = "BTCUSDT"  # 币安格式
    interval = "15m"
    days_back = 1  # 简化回测，只测1天
    
    # 计算时间范围
    end_time = datetime.now()
    start_time = end_time - timedelta(days=days_back)
    
    print(f"\n[参数] 回测参数:")
    print(f"   交易对: {symbol}")
    print(f"   时间周期: {interval}")
    print(f"   回测天数: {days_back}天")
    print(f"   开始时间: {start_time}")
    print(f"   结束时间: {end_time}")
    
    # 获取历史数据
    print(f"\n1. 获取历史数据...")
    try:
        df = fetch_historical_klines(symbol, interval, start_time, end_time)
        if len(df) < 100:
            print(f"❌ 数据不足，只有{len(df)}根K线")
            return
    except Exception as e:
        print(f"❌ 获取数据失败: {e}")
        print("   将使用模拟数据")
        # 生成模拟数据
        dates = pd.date_range(start=start_time, end=end_time, freq='15min')
        n = len(dates)
        np.random.seed(42)
        returns = np.random.normal(0.0001, 0.01, n)
        price = 50000 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            'timestamp': dates,
            'open': price * (1 + np.random.uniform(-0.001, 0.001, n)),
            'high': price * (1 + np.random.uniform(0, 0.02, n)),
            'low': price * (1 - np.random.uniform(0, 0.02, n)),
            'close': price,
            'volume': np.random.uniform(100, 1000, n)
        })
        df['high'] = df[['open', 'high', 'close']].max(axis=1)
        df['low'] = df[['open', 'low', 'close']].min(axis=1)
    
    # 创建回测器
    backtester = SimpleBacktester(initial_balance=10000.0)
    
    # 运行回测
    print(f"\n2. 运行回测...")
    backtester.run_backtest(df, use_ai=False)
    
    # 生成报告
    print(f"\n3. 生成报告...")
    backtester.generate_report()
    
    # 保存结果
    print(f"\n💾 保存回测结果...")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"backtest_result_{timestamp}.json"
    
    result_data = {
        'config': {
            'symbol': symbol,
            'interval': interval,
            'days_back': days_back,
            'initial_balance': backtester.initial_balance
        },
        'trades': backtester.trades,
        'summary': {
            'total_trades': len(backtester.trades),
            'final_equity': backtester.equity_curve[-1]['equity'] if backtester.equity_curve else backtester.balance
        }
    }
    
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"✅ 结果已保存到: {filename}")
    print(f"\n🎯 回测完成!")
    print("="*60)


if __name__ == "__main__":
    main()
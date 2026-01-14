#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建华回测 - 简化版回测系统
支持DeepSeek AI信号和传统SMA20策略
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import json
import sys
import os
import random
from typing import Optional
import requests
import time

# 尝试导入ds3模块中的DeepSeek客户端
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
    print("[信息] DeepSeek API可用")
except Exception as e:
    print(f"[警告] 导入ds3模块失败 - {e}")
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
                    
                    # 模拟API响应
                    class MockResponse:
                        def __init__(self):
                            self.choices = [MockChoice()]
                    class MockChoice:
                        def __init__(self):
                            self.message = MockMessage()
                    class MockMessage:
                        def __init__(self):
                            self.content = f'{{"signal": "{signal}", "reason": "{reason}", "stop_loss": 0, "take_profit": 0, "confidence": "{confidence}"}}'
                    
                    return MockResponse()
    
    deepseek_client = MockDeepSeekClient()
    TRADE_CONFIG = {
        'timeframe': '15m',
        'symbol': 'ETH/USDT:USDT',
        'trading_style_genes': {
            'market_bias': '趋势跟随（激进）',
            'risk_attitude': '高风险',
            'position_style': '动态仓位',
            'add_position_logic': '金字塔加仓',
            'stop_loss_style': '移动止损',
            'coin_filtering': '严格筛选',
            'timeframe_focus': '多周期共振'
        },
        'position_management': {
            'enable_intelligent_position': True,
            'base_usdt_amount': 100,
            'high_confidence_multiplier': 1.7,
            'medium_confidence_multiplier': 1.1,
            'low_confidence_multiplier': 0.6,
            'position_usage_pct': 85.0,
            'min_position_usdt': 25,
            'enable_pyramiding': True,
            'pyramid_max_layers': 3,
            'pyramid_step_gain_pct': 0.30,   # 浮盈达到该百分比再加仓
            'pyramid_size_multiplier': 0.6,  # 每次加仓为当前仓位的倍率
            'partial_tp_pct': 5.5,           # 浮盈达到该百分比先分批止盈
            'partial_tp_fraction': 0.5       # 分批止盈比例
        },
        'risk_control': {
            'max_daily_loss_pct': 8.0,
            'max_single_loss_pct': 2.5,
            'max_position_pct': 85.0,
            'stop_loss_default_pct': 2.4,
            'take_profit_default_pct': 10.0,
            'max_consecutive_losses': 5,
            'cooldown_bars_after_circuit': 2
        },
        'signal_filters': {
            'min_confidence': 'MEDIUM',
            'scale_with_confidence': True
        },
        'trailing_stop': {
            'enable': True,
            'trigger_pct': 1.0,   # 浮盈达到1.0%开始移动
            'callback_pct': 0.4   # 回吐0.4%触发止盈保护
        }
    }

def simulate_price_data_for_backtest(df: pd.DataFrame, idx: int, 
                                   timeframe: str = '15m') -> dict:
    """
    为回测模拟price_data结构
    """
    # 获取截止到当前idx的数据
    df_slice = df.iloc[:idx+1].copy()
    
    # 计算技术指标
    df_with_indicators = calculate_technical_indicators(df_slice)
    
    # 获取当前数据
    current_data = df_with_indicators.iloc[-1]
    previous_data = df_with_indicators.iloc[-2] if len(df_with_indicators) > 1 else current_data
    
    # 获取趋势分析
    trend_analysis = get_market_trend(df_with_indicators)
    
    # 获取支撑阻力位
    levels_analysis = get_support_resistance_levels(df_with_indicators)
    
    # 构建price_data
    price_data = {
        'price': round(float(current_data['close']), 6),
        'timestamp': current_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
        'high': round(float(current_data['high']), 6),
        'low': round(float(current_data['low']), 6),
        'volume': float(current_data['volume']),
        'timeframe': timeframe,
        'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
        'kline_data': df_slice[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10)
            .round({'open': 6, 'high': 6, 'low': 6, 'close': 6})
            .to_dict('records'),
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

def analyze_with_deepseek_for_backtest(price_data: dict, position_state: dict | None = None) -> dict:
    """
    使用DeepSeek API进行回测分析
    """
    print(f"[调试] analyze_with_deepseek_for_backtest被调用，价格: {price_data['price']:.6f}")
    if not DEEPSEEK_AVAILABLE:
        print("[调试] DEEPSEEK_AVAILABLE为False，使用备用信号")
        return create_fallback_signal(price_data)
    
    try:
        print(f"[调试] 进入try块，开始生成技术分析文本")
        # 生成技术分析文本
        technical_analysis = generate_technical_analysis_text(price_data)
        print(f"[调试] 技术分析文本生成完成，长度: {len(technical_analysis)}")
        
        # 构建K线数据文本
        timeframe = TRADE_CONFIG.get('timeframe', '15m')
        kline_text = f"【最近5根{timeframe}K线数据】\n"
        for i, kline in enumerate(price_data['kline_data'][-5:]):
            trend = "阳线" if kline['close'] > kline['open'] else "阴线"
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            kline_text += f"K线{i + 1}: {trend} 开盘:{kline['open']:.2f} 收盘:{kline['close']:.2f} 涨跌:{change:+.2f}%\n"
        
        # 构建提示词
        genes = TRADE_CONFIG['trading_style_genes']
        position_text = "无持仓"
        if position_state:
            pos_side = position_state.get('side', 'none')
            pos_entry = position_state.get('entry_price')
            pos_stop = position_state.get('stop_loss')
            pos_take = position_state.get('take_profit')
            position_text = f"当前持仓: {pos_side}, 开仓价: {pos_entry}, 止损: {pos_stop}, 止盈: {pos_take}"
        prompt = f"""
        你是一个专业的加密货币量化交易AI，正在进行历史数据回测分析。
        
        你的交易风格基因如下：
        - 市场偏好: {genes['market_bias']}
        - 风险态度: {genes['risk_attitude']}
        - 仓位风格: {genes['position_style']}
        - 加仓逻辑: {genes['add_position_logic']}
        - 止损方式: {genes['stop_loss_style']}
        - 币种筛选强度: {genes['coin_filtering']}
        - 时间周期偏好: {genes['timeframe_focus']}
        
        【核心指令 - 必须严格遵守】
        1. 你必须给出明确的交易信号，禁止过度使用HOLD信号。
        2. 在明确趋势中必须给出方向性信号：
           - 强势上涨趋势 → BUY信号
           - 强势下跌趋势 → SELL信号
        3. 仅在以下情况可以使用HOLD信号：
           - 价格在极窄范围内震荡（波动<0.5%）
           - 技术指标相互矛盾，无明确方向
        4. 你的风险态度为高风险，这意味着你应该在趋势中积极行动，不要因短期波动而犹豫。
        
        【增强盈利策略】
        1. 趋势持续性判断：
           - 如果价格连续3根K线上涨且成交量放大 → 强势看多，BUY信号
           - 如果价格连续3根K线下跌且成交量放大 → 强势看空，SELL信号
        2. 突破策略：
           - 价格突破近期高点（最近5根K线最高点） → 突破买入，BUY信号
           - 价格跌破近期低点（最近5根K线最低点） → 突破卖出，SELL信号
        3. 动量策略：
           - 价格变化>1%且MACD金叉 → 动量买入，BUY信号
           - 价格变化<-1%且MACD死叉 → 动量卖出，SELL信号
        4. 均线策略：
           - 价格站在所有均线上方且均线发散 → 强势多头，BUY信号
           - 价格跌破所有均线且均线发散 → 强势空头，SELL信号
        
        【数据详情】
        {kline_text}
        
        {technical_analysis}
        
        【当前行情】
        - 当前价格: ${price_data['price']:,.2f}
        - 时间: {price_data['timestamp']}
        - 本K线最高: ${price_data['high']:,.2f}
        - 本K线最低: ${price_data['low']:,.2f}
        - 本K线成交量: {price_data['volume']:.2f} BTC
        - 价格变化: {price_data['price_change']:+.2f}%
        
        【趋势判断规则】
        1. 强势上涨趋势判断标准：
           - 价格高于所有主要均线（SMA5, SMA20, SMA50）
           - 均线呈多头排列（SMA5 > SMA20 > SMA50）
           - MACD柱状图为正
           - 成交量放大
        2. 强势下跌趋势判断标准：
           - 价格低于所有主要均线
           - 均线呈空头排列（SMA5 < SMA20 < SMA50）
           - MACD柱状图为负
           - 成交量放大
        
        【交易信号生成规则】
        1. 如果满足强势上涨趋势标准 → 给出BUY信号，信心HIGH
        2. 如果满足强势下跌趋势标准 → 给出SELL信号，信心HIGH
        3. 如果满足突破策略 → 给出对应方向信号，信心HIGH
        4. 如果满足动量策略 → 给出对应方向信号，信心MEDIUM
        5. 如果趋势不明确 → 可以HOLD，但必须说明理由
        
        【重要提醒】
        你现在正在进行历史数据回测，需要根据技术分析给出明确信号。
        不要因为短期超买超卖而过度保守，趋势是你的朋友。
        记住：在强势趋势中，持续持有仓位比频繁交易更能获利。
        如果已经持有仓位且趋势继续，继续保持持仓（HOLD）。
        只有在趋势反转或达到止盈目标时才平仓（给出反向信号）。

        当前持仓状态：{position_text}
        
        请用以下JSON格式回复：
        {{
            "signal": "BUY|SELL|HOLD",
            "action": "OPEN_BUY|OPEN_SELL|CLOSE|HOLD",
            "reason": "简要分析理由(基于技术分析和交易风格基因)",
            "stop_loss": 具体价格,
            "take_profit": 具体价格, 
            "confidence": "HIGH|MEDIUM|LOW"
        }}
        """
        
        # 调用DeepSeek API
        print(f"[调试] 准备调用DeepSeek API，prompt长度: {len(prompt)}")
        try:
            response = deepseek_client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system", "content": "你是一个专业的加密货币交易分析师，正在进行历史数据回测分析。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )
            print(f"[调试] DeepSeek API调用完成，response类型: {type(response)}")
        except Exception as api_error:
            print(f"[错误] DeepSeek API调用异常: {api_error}")
            return create_fallback_signal(price_data)
        
        # 解析响应
        if not response or not response.choices:
            print("[错误] API响应为空或无choices数据")
            return create_fallback_signal(price_data)
        
        choice = response.choices[0]
        if not choice or not choice.message or choice.message.content is None:
            print("[错误] API响应中message.content为空")
            return create_fallback_signal(price_data)
        
        content = choice.message.content
        
        # 提取JSON部分
        import re
        if content is None:
            signal_data = create_fallback_signal(price_data)
        else:
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                signal_data = json.loads(json_str)
            else:
                signal_data = create_fallback_signal(price_data)
        
        # 验证必需字段
        required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence', 'action']
        if not all(field in signal_data for field in required_fields):
            signal_data = create_fallback_signal(price_data)
        
        # 调试：打印信号信息
        print(f"[调试] AI信号: {signal_data['signal']}, 理由: {signal_data['reason'][:50]}..., 信心: {signal_data['confidence']}")
        
        # 添加时间戳
        signal_data['timestamp'] = price_data['timestamp']
        
        return signal_data
        
    except Exception as e:
        print(f"[错误] DeepSeek分析失败: {e}")
        return create_fallback_signal(price_data)

def create_fallback_signal(price_data: dict) -> dict:
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

    # 与信号保持一致的动作输出
    action_map = {
        "BUY": "OPEN_BUY",
        "SELL": "OPEN_SELL",
        "HOLD": "HOLD"
    }
    action = action_map.get(signal, "HOLD")
    
    return {
        "signal": signal,
        "action": action,
        "reason": reason,
        "stop_loss": price_data['price'] * 0.98,
        "take_profit": price_data['price'] * 1.02,
        "confidence": confidence,
        "is_fallback": True,
        "timestamp": price_data['timestamp']
    }


CONFIDENCE_ORDER = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}

class SimpleBacktester:
    """简单回测器"""
    
    def __init__(self, initial_balance: float = 10000.0, use_deepseek: bool = False):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.use_deepseek = use_deepseek
        self.risk_config = TRADE_CONFIG.get('risk_control') or {}
        self.position_config = TRADE_CONFIG.get('position_management') or {}
        self.signal_filters = TRADE_CONFIG.get('signal_filters') or {}
        self.trailing_config = TRADE_CONFIG.get('trailing_stop') or {}
        self.stop_loss_default_pct = self.risk_config.get('stop_loss_default_pct', 2.0)
        self.take_profit_default_pct = self.risk_config.get('take_profit_default_pct', 4.0)
        self.max_consecutive_losses = self.risk_config.get('max_consecutive_losses', 3)
        self.cooldown_bars_after_circuit = self.risk_config.get('cooldown_bars_after_circuit', 0)
        self.consecutive_losses = 0
        self.cooldown_bars = 0
        self.pyramid_enabled = self.position_config.get('enable_pyramiding', False)
        self.pyramid_max_layers = self.position_config.get('pyramid_max_layers', 1)
        self.pyramid_step_gain_pct = self.position_config.get('pyramid_step_gain_pct', 0.6)
        self.pyramid_size_multiplier = self.position_config.get('pyramid_size_multiplier', 0.5)
        self.partial_tp_pct = self.position_config.get('partial_tp_pct', 0)
        self.partial_tp_fraction = self.position_config.get('partial_tp_fraction', 0.5)
        # AI调用节流缓存
        self._last_ai_price = None
        self._last_ai_indicators = None
        self._last_ai_signal = None
        
        print(f"[初始化] 初始资金: {initial_balance:.2f} USDT")
        if use_deepseek:
            print(f"[信息] DeepSeek AI信号: 启用")

    def _normalize_confidence(self, confidence: str) -> str:
        if not confidence:
            return 'LOW'
        confidence_upper = str(confidence).upper()
        return confidence_upper if confidence_upper in CONFIDENCE_ORDER else 'LOW'

    def _confidence_rank(self, confidence: str) -> int:
        return CONFIDENCE_ORDER.get(self._normalize_confidence(confidence), 0)

    def _should_trade(self, confidence: str) -> bool:
        min_conf = self.signal_filters.get('min_confidence', 'LOW')
        return self._confidence_rank(confidence) >= self._confidence_rank(min_conf)

    def _resolve_stop_take(self, side: str, entry_price: float,
                           stop_loss_value: Optional[float], take_profit_value: Optional[float]) -> tuple[float, float]:
        def _to_float(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        stop_loss = _to_float(stop_loss_value)
        take_profit = _to_float(take_profit_value)
        default_stop = max(self.stop_loss_default_pct, 0.01) / 100
        default_take = max(self.take_profit_default_pct, 0.01) / 100
        if side == 'long':
            if not stop_loss or stop_loss >= entry_price:
                stop_loss = entry_price * (1 - default_stop)
            if not take_profit or take_profit <= entry_price:
                take_profit = entry_price * (1 + default_take)
        else:
            if not stop_loss or stop_loss <= entry_price:
                stop_loss = entry_price * (1 + default_stop)
            if not take_profit or take_profit >= entry_price:
                take_profit = entry_price * (1 - default_take)
        return stop_loss, take_profit

    def _calculate_trade_amount(self, entry_price: float, stop_loss_price: float,
                                confidence: str) -> float:
        balance = self.balance
        if balance <= 0:
            return 0.0
        risk_pct = max(self.risk_config.get('max_single_loss_pct', 2.0), 0) / 100
        usage_pct = max(self.position_config.get('position_usage_pct', self.risk_config.get('max_position_pct', 60.0)), 0) / 100
        loss_pct = abs(entry_price - stop_loss_price) / entry_price if stop_loss_price else self.stop_loss_default_pct / 100
        loss_pct = max(loss_pct, 1e-4)
        risk_amount_cap = balance * risk_pct if risk_pct > 0 else balance
        amount_by_risk = risk_amount_cap / loss_pct if loss_pct > 0 else balance
        balance_cap = balance * usage_pct if usage_pct > 0 else balance
        base_amount = balance_cap  # 使用可用资金上限为基准，实现“盈利全部再投入”
        conf = self._normalize_confidence(confidence)
        if self.signal_filters.get('scale_with_confidence', True):
            multipliers = {
                'HIGH': self.position_config.get('high_confidence_multiplier', 1.5),
                'MEDIUM': self.position_config.get('medium_confidence_multiplier', 1.0),
                'LOW': self.position_config.get('low_confidence_multiplier', 0.5)
            }
            base_amount *= multipliers.get(conf, 1.0)
            base_amount = min(base_amount, balance_cap)  # 不突破80%仓位上限
        # 若已有持仓，则限制总持仓不超过最大仓位比例
        if self.position:
            existing = self.position.get('amount', 0)
            base_amount = max(0.0, min(base_amount, balance_cap - existing))
        min_position_usdt = self.position_config.get('min_position_usdt', 25.0)
        trade_amount = min(balance, balance_cap, amount_by_risk, base_amount)
        if trade_amount < min_position_usdt:
            return 0.0
        return trade_amount

    def _snapshot_indicators(self, price_data: dict) -> dict:
        tech = price_data.get('technical_data') or {}
        keys = ['sma_5', 'sma_20', 'sma_50', 'macd', 'macd_signal', 'macd_histogram', 'rsi']
        snap = {k: float(tech.get(k, 0) or 0) for k in keys}
        snap['price'] = float(price_data.get('price', 0) or 0)
        return snap

    def _is_market_stable(self, price_data: dict) -> bool:
        """判断市场是否平稳以节流AI调用：价格变动<0.2%且指标变化极小"""
        if self._last_ai_price is None or self._last_ai_indicators is None:
            return False
        price = float(price_data.get('price', 0) or 0)
        if price <= 0 or self._last_ai_price <= 0:
            return False
        price_move_pct = abs(price - self._last_ai_price) / self._last_ai_price
        if price_move_pct >= 0.001:
            return False
        current_snap = self._snapshot_indicators(price_data)
        last_snap = self._last_ai_indicators
        diffs = []
        for k, v in current_snap.items():
            last_v = last_snap.get(k, 0)
            if last_v == 0:
                continue
            diffs.append(abs(v - last_v) / abs(last_v))
        max_drift = max(diffs) if diffs else 0
        return max_drift < 0.0005  # 指标漂移 <0.05%

    def _maybe_add_position(self, side: str, current_price: float, idx: int, timestamp, reason: str, confidence: str):
        if not self.pyramid_enabled:
            return False
        if not self.position or self.position['side'] != side:
            return False
        layers = self.position.get('layers', 1)
        if layers >= self.pyramid_max_layers:
            return False
        entry_price = self.position['entry_price']
        amount = self.position['amount']
        if amount <= 0:
            return False
        # 浮盈百分比
        if side == 'long':
            gain_pct = (current_price - entry_price) / entry_price * 100
        else:
            gain_pct = (entry_price - current_price) / entry_price * 100
        if gain_pct < self.pyramid_step_gain_pct:
            return False
        # 以现有止损计算风险
        stop_loss_price = self.position.get('stop_loss')
        if not stop_loss_price:
            stop_loss_price, _ = self._resolve_stop_take(side, current_price, None, None)
        add_amount = amount * self.pyramid_size_multiplier
        # 不突破风险/资金上限
        sized_amount = min(add_amount, self._calculate_trade_amount(current_price, stop_loss_price, confidence))
        if sized_amount <= 0:
            return False
        new_total_amount = amount + sized_amount
        weighted_entry = (entry_price * amount + current_price * sized_amount) / new_total_amount
        # 调整止损: 多头不降低，空头不抬高（方向相反）
        if side == 'long':
            new_stop = max(stop_loss_price or 0, weighted_entry * (1 - self.stop_loss_default_pct / 100)) if stop_loss_price else weighted_entry * (1 - self.stop_loss_default_pct / 100)
        else:
            new_stop = min(stop_loss_price or float('inf'), weighted_entry * (1 + self.stop_loss_default_pct / 100)) if stop_loss_price else weighted_entry * (1 + self.stop_loss_default_pct / 100)
        # 更新仓位
        self.position['amount'] = new_total_amount
        self.position['entry_price'] = weighted_entry
        self.position['stop_loss'] = new_stop
        self.position['layers'] = layers + 1
        # 资金扣减
        self.balance -= sized_amount
        print(f"  [加仓] {timestamp} @ {current_price:.2f}, 追加: {sized_amount:.2f}, 总仓: {new_total_amount:.2f}, 止损调整为 {new_stop:.2f}")
        return True

    def _on_trade_closed(self, pnl: float):
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if self.max_consecutive_losses and self.consecutive_losses >= self.max_consecutive_losses:
            self.cooldown_bars = max(self.cooldown_bars, self.cooldown_bars_after_circuit)
            print(f"[熔断] 连续亏损达到{self.consecutive_losses}次，启动冷却{self.cooldown_bars}根K线")

    def _decrement_cooldown(self):
        if self.cooldown_bars > 0:
            self.cooldown_bars -= 1
            if self.cooldown_bars == 0:
                print("[熔断] 冷却结束，恢复开仓")

    def _can_open_new_position(self) -> bool:
        return self.cooldown_bars <= 0

    def _close_position(self, exit_price: float, timestamp, reason: str,
                        signal_label: str, index: int, original_reason: str,
                        confidence: str):
        if not self.position:
            return
        entry_price = self.position['entry_price']
        amount = self.position['amount']
        side = self.position['side']
        if side == 'long':
            pnl = (exit_price - entry_price) / entry_price * amount
        else:
            pnl = (entry_price - exit_price) / entry_price * amount
        self.balance += amount + pnl
        self._on_trade_closed(pnl)
        print(f"  [{reason}] {timestamp} @ {exit_price:.2f}, 盈亏: {pnl:+.2f}, 余额: {self.balance:.2f}")
        self.trades.append({
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'amount': amount,
            'pnl': pnl,
            'entry_time': self.position['timestamp'],
            'exit_time': timestamp,
            'hold_bars': index - self.position.get('entry_index', index),
            'reason': original_reason,
            'confidence': confidence
        })
        self.position = None
        self.equity_curve.append({
            'timestamp': timestamp,
            'equity': self.balance,
            'signal': signal_label,
            'reason': original_reason,
            'confidence': confidence
        })

    def _partial_take_profit(self, exit_price: float, timestamp, index: int,
                             reason: str, confidence: str) -> bool:
        if not self.position or self.position.get('partial_tp_done'):
            return False
        frac = float(self.partial_tp_fraction or 0)
        if frac <= 0:
            return False
        frac = min(max(frac, 0.05), 0.9)
        entry_price = self.position['entry_price']
        amount = self.position['amount']
        if amount <= 0:
            return False
        close_amount = amount * frac
        side = self.position['side']
        if side == 'long':
            pnl = (exit_price - entry_price) / entry_price * close_amount
        else:
            pnl = (entry_price - exit_price) / entry_price * close_amount
        self.balance += close_amount + pnl
        remaining = amount - close_amount
        self.position['amount'] = remaining
        self.position['partial_tp_done'] = True
        print(f"  [{reason}] {timestamp} @ {exit_price:.2f}, 分批止盈: {close_amount:.2f}, 盈亏: {pnl:+.2f}, 余额: {self.balance:.2f}")
        self.trades.append({
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'amount': close_amount,
            'pnl': pnl,
            'entry_time': self.position['timestamp'],
            'exit_time': timestamp,
            'hold_bars': index - self.position.get('entry_index', index),
            'reason': reason,
            'confidence': confidence
        })
        min_position_usdt = self.position_config.get('min_position_usdt', 25.0)
        if remaining < min_position_usdt:
            self._close_position(
                exit_price,
                timestamp,
                'PARTIAL_TP_FULL',
                'PARTIAL_TP_FULL',
                index,
                '分批止盈后余量不足，全部平仓',
                confidence
            )
        return True

    def _check_stop_take_exit(self, df: pd.DataFrame, idx: int) -> bool:
        if not self.position:
            return False
        current_timestamp = df['timestamp'].iloc[idx]
        current_high = df['high'].iloc[idx]
        current_low = df['low'].iloc[idx]
        self._update_trailing_stop(current_high, current_low)
        side = self.position['side']
        stop_loss = self.position.get('stop_loss')
        take_profit = self.position.get('take_profit')
        triggered = None
        exit_price = None
        if side == 'long':
            if stop_loss and current_low <= stop_loss:
                triggered = 'STOP_LOSS'
                exit_price = stop_loss
            elif take_profit and current_high >= take_profit:
                triggered = 'TAKE_PROFIT'
                exit_price = take_profit
        else:
            if stop_loss and current_high >= stop_loss:
                triggered = 'STOP_LOSS'
                exit_price = stop_loss
            elif take_profit and current_low <= take_profit:
                triggered = 'TAKE_PROFIT'
                exit_price = take_profit
        if triggered and exit_price is not None:
            self._close_position(
                exit_price,
                current_timestamp,
                triggered,
                triggered,
                idx,
                self.position.get('reason', ''),
                self.position.get('confidence', 'N/A')
            )
            return True
        if self.partial_tp_pct and not self.position.get('partial_tp_done'):
            partial_pct = max(float(self.partial_tp_pct), 0) / 100
            entry_price = self.position['entry_price']
            if side == 'long':
                if current_high >= entry_price * (1 + partial_pct):
                    self._partial_take_profit(entry_price * (1 + partial_pct), current_timestamp, idx, 'PARTIAL_TP', self.position.get('confidence', 'N/A'))
            else:
                if current_low <= entry_price * (1 - partial_pct):
                    self._partial_take_profit(entry_price * (1 - partial_pct), current_timestamp, idx, 'PARTIAL_TP', self.position.get('confidence', 'N/A'))
        return False

    def _update_trailing_stop(self, current_high: float, current_low: float):
        if not self.position:
            return
        if not self.trailing_config.get('enable', False):
            return
        trigger_pct = max(self.trailing_config.get('trigger_pct', 0.7), 0) / 100
        callback_pct = max(self.trailing_config.get('callback_pct', 0.3), 0) / 100
        side = self.position['side']
        entry_price = self.position['entry_price']
        stop_loss = self.position.get('stop_loss')
        if side == 'long':
            highest = self.position.get('highest_price', entry_price)
            highest = max(highest, current_high)
            gain_pct = (highest - entry_price) / entry_price
            if gain_pct >= trigger_pct:
                candidate_stop = highest * (1 - callback_pct)
                if stop_loss is None or candidate_stop > stop_loss:
                    self.position['stop_loss'] = candidate_stop
            self.position['highest_price'] = highest
        else:
            lowest = self.position.get('lowest_price', entry_price)
            lowest = min(lowest, current_low)
            gain_pct = (entry_price - lowest) / entry_price
            if gain_pct >= trigger_pct:
                candidate_stop = lowest * (1 + callback_pct)
                if stop_loss is None or candidate_stop < stop_loss:
                    self.position['stop_loss'] = candidate_stop
            self.position['lowest_price'] = lowest
    
    def run_backtest(self, df: pd.DataFrame, use_deepseek: Optional[bool] = None):
        """
        运行回测 - 支持DeepSeek AI信号和SMA20策略
        
        Args:
            df: K线数据
            use_deepseek: 是否使用DeepSeek AI信号，None时使用初始化设置
        """
        if use_deepseek is None:
            use_deepseek = self.use_deepseek
            
        print(f"[回测] 开始回测，K线数量: {len(df)}")
        print(f"[信息] DeepSeek AI信号: {'启用' if use_deepseek else '禁用'}")
        
        # 计算基础技术指标（无论是否使用DeepSeek都需要）
        df['sma20'] = df['close'].rolling(window=20).mean()
        df['sma5'] = df['close'].rolling(window=5).mean()
        df['mom_pct'] = df['close'].pct_change() * 100
        df['vol_ma10'] = df['volume'].rolling(window=10).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma10']
        df['vol_ratio'] = df['vol_ratio'].fillna(1.0).replace([np.inf, -np.inf], 1.0)
        
        print(f"[调试] 回测循环：i从20到{len(df)-1}，共{len(df)-20}次迭代")
        for i in range(20, len(df)):
            current_price = df['close'].iloc[i]
            current_high = df['high'].iloc[i]
            current_low = df['low'].iloc[i]
            sma20 = df['sma20'].iloc[i]
            sma5 = df['sma5'].iloc[i]
            mom_pct = df['mom_pct'].iloc[i]
            vol_ratio = df['vol_ratio'].iloc[i]
            timestamp = df['timestamp'].iloc[i]
            print(f"[调试] i={i}, 价格={current_price:.2f}, sma20={sma20:.2f}")

            if self._check_stop_take_exit(df, i):
                print(f"[调试] 止损/止盈在第{i}根K线触发，跳过当期信号")
                continue
            
            self._decrement_cooldown()

            # 生成交易信号
            signal = 'HOLD'
            action = 'HOLD'
            reason = ''
            confidence = 'LOW'
            signal_stop_loss = None
            signal_take_profit = None
            
            # 如果已有持仓，优先考虑平仓信号，避免重复开仓
            if self.position is not None and self.position['side'] == 'long':
                # 已有多仓，关注卖出信号
                pass
            elif self.position is not None and self.position['side'] == 'short':
                # 已有空仓，关注买入信号
                pass
            
            if use_deepseek and DEEPSEEK_AVAILABLE:
                # 准备价格数据
                price_data = simulate_price_data_for_backtest(df, i)
                position_state = None
                if self.position:
                    position_state = {
                        'side': self.position.get('side'),
                        'entry_price': self.position.get('entry_price'),
                        'stop_loss': self.position.get('stop_loss'),
                        'take_profit': self.position.get('take_profit')
                    }

                price_change_abs = abs(price_data.get('price_change', 0))
                reuse_last = price_change_abs < 0.2 and self._is_market_stable(price_data) and self._last_ai_signal is not None
                if reuse_last:
                    signal_data = dict(self._last_ai_signal or {})
                    signal_data['timestamp'] = price_data['timestamp']
                    print(f"[节流] 价格/指标变化<0.2%，复用上一条AI信号: {signal_data.get('signal')}")
                    self._last_ai_price = price_data.get('price')
                    self._last_ai_indicators = self._snapshot_indicators(price_data)
                else:
                    # 调用DeepSeek API
                    signal_data = analyze_with_deepseek_for_backtest(price_data, position_state=position_state)
                    self._last_ai_price = price_data.get('price')
                    self._last_ai_indicators = self._snapshot_indicators(price_data)
                    self._last_ai_signal = dict(signal_data) if signal_data else None
                signal = signal_data['signal']
                reason = signal_data['reason']
                confidence_raw = signal_data.get('confidence') if signal_data else 'LOW'
                confidence = self._normalize_confidence(str(confidence_raw or 'LOW'))
                signal_stop_loss = signal_data.get('stop_loss')
                signal_take_profit = signal_data.get('take_profit')
                action = str(signal_data.get('action', 'HOLD') or 'HOLD').upper()
                if signal in ('BUY', 'SELL') and not self._should_trade(confidence):
                    print(f"[过滤] 信号信心{confidence} 低于阈值，忽略交易")
                    signal = 'HOLD'
                    action = 'HOLD'
                print(f"[循环 {i}] AI信号: {signal}, 理由: {reason[:50]}..., 信心: {confidence}")
            else:
                # 使用强化版技术面策略（无DeepSeek时更激进）
                prev_close = df['close'].iloc[i-1]
                prev_sma20 = df['sma20'].iloc[i-1]
                bullish_breakout = (current_price > sma20 and current_price > sma5 and mom_pct > 0.6 and vol_ratio > 1.0)
                bearish_breakdown = (current_price < sma20 and current_price < sma5 and mom_pct < -0.6 and vol_ratio > 1.0)
                golden_cross = (current_price > sma20 and prev_close <= prev_sma20)
                dead_cross = (current_price < sma20 and prev_close >= prev_sma20)

                if self.position is None:
                    if bullish_breakout or golden_cross:
                        signal = 'BUY'
                        reason = f"动量多头: 价>MA5/MA20 且动量{mom_pct:+.2f}% vol_ratio {vol_ratio:.2f}"
                    elif bearish_breakdown or dead_cross:
                        signal = 'SELL'
                        reason = f"动量空头: 价<MA5/MA20 且动量{mom_pct:+.2f}% vol_ratio {vol_ratio:.2f}"
                else:
                    # 趋势持仓延长规则：趋势未破坏时避免反手
                    trend_hold_long = current_price >= sma20 and sma5 >= sma20
                    trend_hold_short = current_price <= sma20 and sma5 <= sma20
                    # 多头保护：趋势被破坏且动量转弱才反手
                    if self.position['side'] == 'long' and (not trend_hold_long) and (mom_pct < -1.1 and current_price < sma20):
                        signal = 'SELL'
                        reason = f"多头转弱: 跌破MA20且动量{mom_pct:+.2f}%"
                    # 空头保护：趋势被破坏且动量转强才反手
                    elif self.position['side'] == 'short' and (not trend_hold_short) and (mom_pct > 1.1 and current_price > sma20):
                        signal = 'BUY'
                        reason = f"空头转弱: 突破MA20且动量{mom_pct:+.2f}%"

                confidence = 'HIGH'
                stop_guess = current_price * (1 - self.stop_loss_default_pct / 100)
                take_guess = current_price * (1 + self.take_profit_default_pct / 100)
                if signal == 'BUY':
                    signal_stop_loss, signal_take_profit = self._resolve_stop_take('long', current_price, stop_guess, take_guess)
                elif signal == 'SELL':
                    signal_stop_loss, signal_take_profit = self._resolve_stop_take('short', current_price, current_price * (1 + self.stop_loss_default_pct / 100), current_price * (1 - self.take_profit_default_pct / 100))
                if signal == 'BUY':
                    action = 'OPEN_BUY'
                elif signal == 'SELL':
                    action = 'OPEN_SELL'
                else:
                    action = 'HOLD'
                print(f"[循环 {i}] Agg信号: {signal}, 价: {current_price:.2f}, MA5: {sma5:.2f}, MA20: {sma20:.2f}, mom: {mom_pct:+.2f}%, vol_ratio: {vol_ratio:.2f}")
            
            # 执行交易逻辑
            print(f"[调试] 循环{i}信号={signal}, action={action}, position={self.position}, 余额={self.balance:.2f}, 冷却={self.cooldown_bars}")
            if action == 'CLOSE':
                if self.position:
                    close_reason = reason or 'ACTION_CLOSE'
                    self._close_position(current_price, timestamp, 'ACTION_CLOSE', 'ACTION_CLOSE', i, close_reason, confidence)
                # 无持仓则忽略
                continue
            if action == 'OPEN_BUY':
                if self.position is not None and self.position['side'] == 'short':
                    close_reason = reason or '反手做多'
                    self._close_position(current_price, timestamp, 'BUY平空', 'CLOSE_SHORT', i, close_reason, confidence)
                    # 关闭后进入下一步开多
                if self.position is None:
                    if not self._can_open_new_position():
                        print("  [熔断] 冷却中，禁止开多")
                        continue
                    stop_loss_price, take_profit_price = self._resolve_stop_take('long', current_price, signal_stop_loss, signal_take_profit)
                    trade_amount = self._calculate_trade_amount(current_price, stop_loss_price, confidence)
                    if trade_amount <= 0:
                        print("  [风控] 仓位计算结果不足以开多，跳过")
                    else:
                        self.position = {
                            'side': 'long',
                            'entry_price': current_price,
                            'amount': trade_amount,
                            'timestamp': timestamp,
                            'entry_index': i,
                            'stop_loss': stop_loss_price,
                            'take_profit': take_profit_price,
                            'reason': reason,
                            'confidence': confidence,
                            'layers': 1,
                            'partial_tp_done': False
                        }
                        self.balance -= trade_amount
                        print(f"  [买入开多] {timestamp} @ {current_price:.2f}, 金额: {trade_amount:.2f}")
                        print(f"    止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}, 信心: {confidence}")
                elif self.position is not None and self.position['side'] == 'long':
                    self._maybe_add_position('long', current_price, i, timestamp, reason, confidence)
            elif action == 'OPEN_SELL':
                if self.position is not None and self.position['side'] == 'long':
                    close_reason = reason or '反手做空'
                    self._close_position(current_price, timestamp, 'SELL平多', 'CLOSE_LONG', i, close_reason, confidence)
                if self.position is None:
                    if not self._can_open_new_position():
                        print("  [熔断] 冷却中，禁止开空")
                        continue
                    stop_loss_price, take_profit_price = self._resolve_stop_take('short', current_price, signal_stop_loss, signal_take_profit)
                    trade_amount = self._calculate_trade_amount(current_price, stop_loss_price, confidence)
                    if trade_amount <= 0:
                        print("  [风控] 仓位计算结果不足以开空，跳过")
                    else:
                        self.position = {
                            'side': 'short',
                            'entry_price': current_price,
                            'amount': trade_amount,
                            'timestamp': timestamp,
                            'entry_index': i,
                            'stop_loss': stop_loss_price,
                            'take_profit': take_profit_price,
                            'reason': reason,
                            'confidence': confidence,
                            'layers': 1,
                            'partial_tp_done': False
                        }
                        self.balance -= trade_amount
                        print(f"  [卖出开空] {timestamp} @ {current_price:.2f}, 金额: {trade_amount:.2f}")
                        print(f"    止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}, 信心: {confidence}")
                elif self.position is not None and self.position['side'] == 'short':
                    self._maybe_add_position('short', current_price, i, timestamp, reason, confidence)
            
            # 记录权益曲线
            current_equity = self.balance
            if self.position:
                entry_price = self.position['entry_price']
                amount = self.position['amount']
                # amount 表示为本次开仓投入的保证金/名义USD，不应在权益中被扣除
                current_equity += amount
                if self.position['side'] == 'long':
                    floating_pnl = (current_price - entry_price) / entry_price * amount
                else:  # short
                    floating_pnl = (entry_price - current_price) / entry_price * amount
                current_equity += floating_pnl
            # 无持仓时 current_equity 即为余额

            self.equity_curve.append({
                'timestamp': timestamp,
                'equity': current_equity,
                'signal': signal,
                'reason': reason,
                'confidence': confidence
            })
        
        # 强制平仓
        if self.position:
            final_idx = len(df) - 1
            final_timestamp = df['timestamp'].iloc[-1]
            self._close_position(
                df['close'].iloc[-1],
                final_timestamp,
                'FORCE_EXIT',
                'FORCE_EXIT',
                final_idx,
                '回测结束强制平仓',
                'N/A'
            )
        
        print(f"[回测] 完成，最终余额: {self.balance:.2f} USDT")
    
    def generate_report(self):
        """生成回测报告"""
        if not self.equity_curve:
            return {'error': '无权益曲线数据'}

        # 使用实际余额而不是权益曲线的最后一个值
        final_equity = self.balance
        total_pnl = final_equity - self.initial_balance
        return_pct = (total_pnl / self.initial_balance) * 100
        
        # 计算最大回撤
        equity_values = [e['equity'] for e in self.equity_curve]
        peak = equity_values[0]
        max_drawdown = 0
        
        for equity in equity_values:
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        # 计算交易统计
        total_trades = len(self.trades)
        winning_trades = [t for t in self.trades if t['pnl'] > 0]
        losing_trades = [t for t in self.trades if t['pnl'] <= 0]
        win_rate = len(winning_trades) / total_trades * 100 if total_trades > 0 else 0
        
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf') if avg_win > 0 else 0
        
        # 计算夏普比率（基于权益曲线收益率）
        equity_series = pd.Series(equity_values)
        daily_returns = equity_series.pct_change().dropna()
        if len(daily_returns) > 1:
            sharpe_ratio = np.sqrt(365) * daily_returns.mean() / daily_returns.std() if daily_returns.std() != 0 else 0
        else:
            sharpe_ratio = 0
        
        return {
            'initial_balance': self.initial_balance,
            'final_equity': final_equity,
            'total_pnl': total_pnl,
            'return_pct': return_pct,
            'total_trades': total_trades,
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate_pct': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown_pct': max_drawdown
        }

def fetch_real_btc_klines(days: int = 3) -> pd.DataFrame:
    """
    获取真实的BTC历史K线数据

    Args:
        days: 获取最近多少天的数据

    Returns:
        包含OHLCV数据的DataFrame
    """
    try:
        # 转换交易对符号为币安格式
        symbol_raw = TRADE_CONFIG['symbol']
        if '/' in symbol_raw:
            # 格式: BTC/USDT -> BTCUSDT
            base_quote = symbol_raw.split('/')[0] + symbol_raw.split('/')[1].split(':')[0]
        else:
            base_quote = symbol_raw

        # 计算时间范围
        end_time = int(datetime.now().timestamp() * 1000)
        start_time = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)

        # 使用币安API获取K线数据
        import requests
        cache_dir = os.path.dirname(__file__)
        cache_file = os.path.join(
            cache_dir,
            f"data_cache_{base_quote}_{TRADE_CONFIG['timeframe']}_{days}d.csv"
        )

        base_urls = [
            "https://api.binance.com",
            "https://api1.binance.com",
            "https://api2.binance.com",
            "https://api3.binance.com",
            "https://api.binance.us"
        ]
        params = {
            'symbol': base_quote,
            'interval': TRADE_CONFIG['timeframe'],
            'startTime': start_time,
            'endTime': end_time,
            'limit': 1000
        }

        session = requests.Session()
        session.trust_env = False
        klines = None
        last_error = None
        for base_url in base_urls:
            url = f"{base_url}/api/v3/klines"
            for attempt in range(2):
                try:
                    response = session.get(
                        url,
                        params=params,
                        timeout=12,
                        proxies={}
                    )
                    response.raise_for_status()
                    klines = response.json()
                    break
                except Exception as e:
                    last_error = e
                    print(f"[警告] 获取K线失败({base_url}, 尝试{attempt + 1}/2): {e}")
                    time.sleep(1.0)
            if klines is not None:
                break
        if klines is None:
            if os.path.exists(cache_file):
                print(f"[警告] 使用本地缓存数据: {cache_file}")
                df = pd.read_csv(cache_file, parse_dates=['timestamp'])
                df = calculate_technical_indicators(df)
                return df
            raise (last_error or RuntimeError("获取K线数据失败，且无缓存可用"))

        if not klines:
            raise ValueError(f"获取K线数据失败，API返回空数据")

        # 转换为DataFrame
        ohlcv = [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # 计算技术指标
        df = calculate_technical_indicators(df)
        try:
            df.to_csv(cache_file, index=False)
            print(f"[缓存] 已保存K线数据到 {cache_file}")
        except Exception as e:
            print(f"[警告] 保存K线缓存失败: {e}")

        print(f"[成功] 获取到 {len(df)} 根真实K线数据")
        print(f"数据范围: {df['timestamp'].iloc[0]} 到 {df['timestamp'].iloc[-1]}")
        print(f"价格范围: {df['low'].min():.2f} - {df['high'].max():.2f} USDT")

        return df

    except Exception as e:
        print(f"[错误] 获取真实K线数据失败: {e}")
        raise


def load_backtest_params(file_path: str) -> dict:
    """从JSON文件加载回测参数，失败时返回空字典"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[警告] 无法从{file_path}加载参数: {e}")
        return {}


def main():
    print("="*60)
    print("建华回测系统 - 简化版 (使用真实历史数据)")
    print("支持DeepSeek AI信号和传统SMA20策略")
    print("="*60)

    # 加载指定参数文件
    param_file = "jianhua_backtest_real_data_20260115_011112.json"
    params = load_backtest_params(param_file)
    initial_balance = float(params.get('initial_balance', 10000.0) or 10000.0)
    print(f"[信息] 使用参数文件 {param_file} 中的初始资金: {initial_balance:.2f} USDT")

    # 检查DeepSeek可用性
    if DEEPSEEK_AVAILABLE:
        print("[信息] DeepSeek API可用，启用AI信号")
    else:
        print("[警告] DeepSeek API不可用，使用备用信号或SMA20策略")

    # 获取真实历史数据（最近3天）
    print(f"\n[步骤1] 正在获取真实的{TRADE_CONFIG['symbol']}历史K线数据...")
    try:
        df = fetch_real_btc_klines(days=3)
    except Exception as e:
        print(f"[致命错误] 无法获取真实数据，程序终止: {e}")
        return

    # 限制数据量以减少API调用
    # 3天的15分钟K线 = 3 * 24 * 4 = 288根，限制为240根（15小时数据）
    max_rows = 240
    if len(df) > max_rows:
        print(f"\n[步骤2] 限制回测数据量为前{max_rows}行（约{max_rows*15/60:.0f}小时数据）")
        df = df.iloc[:max_rows].copy()
    else:
        print(f"\n[步骤2] 使用全部 {len(df)} 行数据")

    print(f"\n[步骤3] 创建回测器 - 初始资金: {initial_balance:.2f} USDT")
    # 创建回测器 - 目前禁用DeepSeek，使用强化版技术因子
    backtester = SimpleBacktester(initial_balance=initial_balance, use_deepseek=False)

    print(f"\n[步骤4] 开始回测，使用强化版技术因子（无DeepSeek）...")
    # 运行回测 - 禁用DeepSeek
    backtester.run_backtest(df, use_deepseek=False)

    print(f"\n[步骤5] 生成回测报告...")
    # 生成报告
    report = backtester.generate_report()

    print("\n" + "="*60)
    print("回测报告 - 强化版技术因子（无DeepSeek） (真实数据)")
    print("="*60)
    print(f"交易对: {TRADE_CONFIG['symbol']}")
    print(f"时间周期: {TRADE_CONFIG['timeframe']}")
    print(f"初始资金: {report['initial_balance']:.2f} USDT")
    print(f"最终权益: {report['final_equity']:.2f} USDT")
    print(f"总盈亏: {report['total_pnl']:+.2f} USDT ({report['return_pct']:+.2f}%)")
    print(f"交易次数: {report['total_trades']} (盈利: {report['winning_trades']}, 亏损: {report['losing_trades']})")
    print(f"胜率: {report['win_rate_pct']:.2f}%")
    print(f"平均盈利: {report['avg_win']:+.2f} USDT")
    print(f"平均亏损: {report['avg_loss']:+.2f} USDT")
    if report['profit_factor'] == float('inf'):
        print(f"盈亏比: ∞")
    else:
        print(f"盈亏比: {report['profit_factor']:.2f}")
    print(f"夏普比率: {report['sharpe_ratio']:.2f}")
    print(f"最大回撤: {report['max_drawdown_pct']:.2f}%")
    print("="*60)

    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"jianhua_backtest_real_data_{timestamp}.json"
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"结果已保存到: {filename}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[致命错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
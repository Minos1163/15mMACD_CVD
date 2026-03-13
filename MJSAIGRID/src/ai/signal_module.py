"""
AI Signal Module - Rule Engine Version
基于文档 05_AI信号模块设计说明书 实现

V1版本: 规则引擎实现市场状态识别
- 使用技术指标进行市场状态分类
- 输出模式建议和风险评分
- 提供基础参数推荐
- 输出决策解释
"""

from typing import Optional, Dict, List, Tuple
from datetime import datetime, timedelta
import numpy as np

from src.grid_trading.models import (
    MarketState,
    GridMode,
    RiskLevel,
    AIDecision,
    GridConfig,
    StrategyState,
    RiskStatus,
)


class RuleBasedAIEngine:
    """规则引擎版本的AI信号模块"""

    def __init__(self, config: GridConfig):
        """
        初始化AI引擎

        Args:
            config: 网格配置
        """
        self.config = config
        self.last_decision: Optional[AIDecision] = None
        self.last_update_time: Optional[datetime] = None
        self.market_state_history: List[Tuple[datetime, MarketState]] = []
        self.mode_switch_history: List[datetime] = []

    def generate_signal(
        self,
        klines: List[dict],
        current_price: float,
        strategy_state: StrategyState,
        risk_status: RiskStatus
    ) -> AIDecision:
        """
        生成AI信号

        Args:
            klines: K线数据列表(按时间排序,最新的在最后)
            current_price: 当前价格
            strategy_state: 策略状态
            risk_status: 风险状态

        Returns:
            AI决策对象
        """
        timestamp = datetime.now()

        # 1. 计算技术指标
        indicators = self._calculate_indicators(klines)

        # 2. 识别市场状态
        market_state, confidence = self._detect_market_state(indicators, klines, current_price)

        # 3. 计算风险评分
        risk_score = self._calculate_risk_score(
            indicators, strategy_state, risk_status, market_state
        )

        # 4. 推荐网格模式
        recommended_mode = self._recommend_mode(
            market_state, confidence, risk_score, strategy_state
        )

        # 5. 推荐参数
        (recommended_leverage,
         recommended_price_lower,
         recommended_price_upper,
         recommended_grid_count) = self._recommend_params(
            market_state, indicators, current_price, risk_score
        )

        # 6. 生成决策解释
        reason, reason_codes, explanations = self._build_explanations(
            market_state, confidence, risk_score, indicators, strategy_state
        )

        # 7. 确定建议动作
        action = self._decide_action(
            recommended_mode, risk_score, confidence, strategy_state
        )

        # 创建决策对象
        decision = AIDecision(
            market_state=market_state,
            recommended_mode=recommended_mode,
            recommended_leverage=recommended_leverage,
            recommended_price_lower=recommended_price_lower,
            recommended_price_upper=recommended_price_upper,
            recommended_grid_count=recommended_grid_count,
            risk_score=risk_score,
            confidence=confidence,
            reason=reason,
            timestamp=timestamp
        )

        # 更新历史记录
        self.last_decision = decision
        self.last_update_time = timestamp
        self.market_state_history.append((timestamp, market_state))

        # 记录模式切换
        if (strategy_state.current_mode != recommended_mode and
            self._can_switch_mode(strategy_state, confidence, risk_score)):
            self.mode_switch_history.append(timestamp)

        return decision

    def _calculate_indicators(self, klines: List[dict]) -> Dict[str, float]:
        """
        计算技术指标

        Args:
            klines: K线数据列表

        Returns:
            技术指标字典
        """
        if len(klines) < 50:
            # 数据不足,返回默认值
            return {
                'ema_short': 0.0,
                'ema_medium': 0.0,
                'ema_long': 0.0,
                'rsi': 50.0,
                'macd': 0.0,
                'macd_signal': 0.0,
                'macd_hist': 0.0,
                'atr': 0.0,
                'adx': 0.0,
                'bb_upper': 0.0,
                'bb_middle': 0.0,
                'bb_lower': 0.0,
                'bb_width': 0.0,
                'price_change_1h': 0.0,
                'price_change_4h': 0.0,
                'price_change_24h': 0.0,
            }

        closes = np.array([k['close'] for k in klines])
        highs = np.array([k['high'] for k in klines])
        lows = np.array([k['low'] for k in klines])

        # EMA (7, 25, 99)
        ema_short = self._calculate_ema(closes, 7)
        ema_medium = self._calculate_ema(closes, 25)
        ema_long = self._calculate_ema(closes, 99)

        # RSI (14)
        rsi = self._calculate_rsi(closes, 14)

        # MACD (12, 26, 9)
        macd, macd_signal, macd_hist = self._calculate_macd(closes, 12, 26, 9)

        # ATR (14)
        atr = self._calculate_atr(highs, lows, closes, 14)

        # ADX (14)
        adx = self._calculate_adx(highs, lows, closes, 14)

        # Bollinger Bands (20, 2)
        bb_upper, bb_middle, bb_lower, bb_width = self._calculate_bollinger_bands(closes, 20, 2)

        # Price changes
        current_price = closes[-1]
        price_change_1h = 0.0 if len(closes) < 2 else (current_price - closes[-2]) / closes[-2]
        price_change_4h = 0.0 if len(closes) < 5 else (current_price - closes[-5]) / closes[-5]
        price_change_24h = 0.0 if len(closes) < 25 else (current_price - closes[-25]) / closes[-25]

        return {
            'ema_short': ema_short,
            'ema_medium': ema_medium,
            'ema_long': ema_long,
            'rsi': rsi,
            'macd': macd,
            'macd_signal': macd_signal,
            'macd_hist': macd_hist,
            'atr': atr,
            'adx': adx,
            'bb_upper': bb_upper,
            'bb_middle': bb_middle,
            'bb_lower': bb_lower,
            'bb_width': bb_width,
            'price_change_1h': price_change_1h,
            'price_change_4h': price_change_4h,
            'price_change_24h': price_change_24h,
        }

    def _detect_market_state(
        self,
        indicators: Dict[str, float],
        klines: List[dict],
        current_price: float
    ) -> Tuple[MarketState, float]:
        """
        识别市场状态

        Args:
            indicators: 技术指标
            klines: K线数据
            current_price: 当前价格

        Returns:
            (市场状态, 置信度)
        """
        ema_short = indicators['ema_short']
        ema_medium = indicators['ema_medium']
        ema_long = indicators['ema_long']
        rsi = indicators['rsi']
        macd_hist = indicators['macd_hist']
        adx = indicators['adx']
        bb_width = indicators['bb_width']
        price_change_24h = indicators['price_change_24h']

        # 异常高波动检测
        if abs(price_change_24h) > 0.20:  # 24h波动超过20%
            return MarketState.ABNORMAL, 0.7

        # 数据不足
        if adx == 0:
            return MarketState.UNKNOWN, 0.3

        # 置信度计算
        confidence = 0.0

        # 震荡状态判定
        range_signals = 0
        if adx < 25:  # ADX低表示趋势弱
            range_signals += 1
            confidence += 0.2
        if ema_medium == ema_long or abs(ema_medium - ema_long) / ema_long < 0.01:
            range_signals += 1
            confidence += 0.15
        if 40 <= rsi <= 60:  # RSI在中性区间
            range_signals += 1
            confidence += 0.15
        if abs(price_change_24h) < 0.05:  # 24h波动小于5%
            range_signals += 1
            confidence += 0.2

        if range_signals >= 3:
            return MarketState.RANGE, min(confidence, 0.85)

        # 上涨趋势判定
        uptrend_signals = 0
        confidence = 0.0
        if ema_short > ema_medium > ema_long:  # 均线多头排列
            uptrend_signals += 1
            confidence += 0.25
        if ema_short > ema_long:  # 短期均线高于长期均线
            uptrend_signals += 1
            confidence += 0.15
        if adx >= 25 and adx > 0:  # 趋势强度足够
            uptrend_signals += 1
            confidence += 0.15
        if rsi > 50 and rsi < 75:  # RSI强势但未超买
            uptrend_signals += 1
            confidence += 0.15
        if price_change_24h > 0.05:  # 正向收益
            uptrend_signals += 1
            confidence += 0.15

        if uptrend_signals >= 4:
            return MarketState.UPTREND, min(confidence, 0.85)

        # 下跌趋势判定
        downtrend_signals = 0
        confidence = 0.0
        if ema_short < ema_medium < ema_long:  # 均线空头排列
            downtrend_signals += 1
            confidence += 0.25
        if ema_short < ema_long:  # 短期均线低于长期均线
            downtrend_signals += 1
            confidence += 0.15
        if adx >= 25 and adx > 0:  # 趋势强度足够
            downtrend_signals += 1
            confidence += 0.15
        if rsi < 50 and rsi > 25:  # RSI弱势但未超卖
            downtrend_signals += 1
            confidence += 0.15
        if price_change_24h < -0.05:  # 负向收益
            downtrend_signals += 1
            confidence += 0.15

        if downtrend_signals >= 4:
            return MarketState.DOWNTREND, min(confidence, 0.85)

        # 不确定状态
        return MarketState.UNKNOWN, 0.4

    def _calculate_risk_score(
        self,
        indicators: Dict[str, float],
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        market_state: MarketState
    ) -> int:
        """
        计算风险评分

        Args:
            indicators: 技术指标
            strategy_state: 策略状态
            risk_status: 风险状态
            market_state: 市场状态

        Returns:
            风险评分 (0-100)
        """
        score = 0

        # 1. 市场波动风险
        atr_ratio = indicators['atr'] / strategy_state.current_price if strategy_state.current_price > 0 else 0
        if atr_ratio > 0.05:  # ATR超过5%
            score += 25
        elif atr_ratio > 0.03:
            score += 15
        elif atr_ratio > 0.02:
            score += 5

        # 2. 趋势不确定性风险
        if market_state == MarketState.UNKNOWN:
            score += 15
        elif market_state == MarketState.ABNORMAL:
            score += 30

        # 3. 杠杆风险
        if strategy_state.current_leverage >= 10:
            score += 20
        elif strategy_state.current_leverage >= 5:
            score += 10
        elif strategy_state.current_leverage >= 3:
            score += 5

        # 4. 回撤风险
        drawdown_ratio = strategy_state.max_drawdown
        if drawdown_ratio > 0.10:
            score += 20
        elif drawdown_ratio > 0.05:
            score += 10
        elif drawdown_ratio > 0.03:
            score += 5

        # 5. 保证金占用风险
        margin_ratio = risk_status.margin_usage
        if margin_ratio > 0.8:
            score += 20
        elif margin_ratio > 0.6:
            score += 10
        elif margin_ratio > 0.4:
            score += 5

        # 6. 模式错配风险
        if (market_state == MarketState.DOWNTREND and
            strategy_state.current_mode == GridMode.LONG):
            score += 15
        elif (market_state == MarketState.UPTREND and
              strategy_state.current_mode == GridMode.SHORT):
            score += 15

        # 限制在0-100范围
        return min(max(score, 0), 100)

    def _recommend_mode(
        self,
        market_state: MarketState,
        confidence: float,
        risk_score: int,
        strategy_state: StrategyState
    ) -> GridMode:
        """
        推荐网格模式

        Args:
            market_state: 市场状态
            confidence: 置信度
            risk_score: 风险评分
            strategy_state: 策略状态

        Returns:
            推荐的网格模式
        """
        # 高风险时保持保守
        if risk_score >= 80:
            return strategy_state.current_mode  # 保持当前模式

        # 置信度过低时不切换
        if confidence < 0.5:
            return strategy_state.current_mode

        # 在冷静期内不切换
        if (strategy_state.last_mode_switch_time and
            datetime.now() - strategy_state.last_mode_switch_time <
            timedelta(minutes=self.config.mode_switch_cooldown_minutes)):
            return strategy_state.current_mode

        # 根据市场状态推荐模式
        if market_state == MarketState.RANGE:
            return GridMode.NEUTRAL
        elif market_state == MarketState.UPTREND:
            return GridMode.LONG
        elif market_state == MarketState.DOWNTREND:
            return GridMode.SHORT
        elif market_state == MarketState.UNKNOWN:
            # 不确定时,如果当前风险评分高,保持中性
            if risk_score >= 60:
                return GridMode.NEUTRAL
            return strategy_state.current_mode
        else:  # ABNORMAL
            # 异常时,根据当前持仓决定
            return strategy_state.current_mode

    def _recommend_params(
        self,
        market_state: MarketState,
        indicators: Dict[str, float],
        current_price: float,
        risk_score: int
    ) -> Tuple[int, float, float, int]:
        """
        推荐参数

        Args:
            market_state: 市场状态
            indicators: 技术指标
            current_price: 当前价格
            risk_score: 风险评分

        Returns:
            (推荐杠杆, 推荐下限, 推荐上限, 推荐网格数)
        """
        # 推荐杠杆
        if risk_score >= 70:
            recommended_leverage = 2
        elif risk_score >= 50:
            recommended_leverage = 3
        else:
            if market_state in [MarketState.UPTREND, MarketState.DOWNTREND]:
                recommended_leverage = 4
            else:
                recommended_leverage = 3

        # 推荐价格区间
        atr = indicators['atr']
        if atr > 0:
            # 基于ATR动态调整区间
            range_factor = min(atr * 2 / current_price, 0.15)  # 最大15%区间
            price_lower = current_price * (1 - range_factor)
            price_upper = current_price * (1 + range_factor)
        else:
            # 默认10%区间
            price_lower = current_price * 0.95
            price_upper = current_price * 1.05

        # 根据市场状态调整区间
        if market_state == MarketState.UPTREND:
            # 上涨趋势,整体上移
            price_upper = current_price * 1.15
            price_lower = max(price_lower, current_price * 0.95)
        elif market_state == MarketState.DOWNTREND:
            # 下跌趋势,整体下移
            price_lower = current_price * 0.85
            price_upper = min(price_upper, current_price * 1.05)

        # 推荐网格数量
        volatility = abs(indicators['price_change_24h'])
        if volatility > 0.10:  # 高波动,减少网格数
            recommended_grid_count = 8
        elif volatility > 0.05:
            recommended_grid_count = 10
        else:  # 低波动,增加网格数
            recommended_grid_count = 12

        return recommended_leverage, price_lower, price_upper, recommended_grid_count

    def _build_explanations(
        self,
        market_state: MarketState,
        confidence: float,
        risk_score: int,
        indicators: Dict[str, float],
        strategy_state: StrategyState
    ) -> Tuple[str, List[str], List[str]]:
        """
        构建决策解释

        Args:
            market_state: 市场状态
            confidence: 置信度
            risk_score: 风险评分
            indicators: 技术指标
            strategy_state: 策略状态

        Returns:
            (原因摘要, 原因码列表, 解释列表)
        """
        reason_codes = []
        explanations = []

        # 市场状态解释
        if market_state == MarketState.RANGE:
            reason_codes.append("LOW_TREND_STRENGTH")
            reason_codes.append("MEDIUM_VOLATILITY")
            explanations.append(f"ADX={indicators['adx']:.1f}, 趋势强度不足")
            explanations.append(f"RSI={indicators['rsi']:.1f}, 处于中性区间")
        elif market_state == MarketState.UPTREND:
            reason_codes.append("UPTREND_CONFIRMED")
            explanations.append("短期、中期、长期均线呈多头排列")
            explanations.append(f"ADX={indicators['adx']:.1f}, 趋势明确")
        elif market_state == MarketState.DOWNTREND:
            reason_codes.append("DOWNTREND_CONFIRMED")
            explanations.append("短期、中期、长期均线呈空头排列")
            explanations.append(f"ADX={indicators['adx']:.1f}, 下跌趋势确认")
        elif market_state == MarketState.ABNORMAL:
            reason_codes.append("HIGH_VOLATILITY")
            explanations.append(f"24h波动率={abs(indicators['price_change_24h'])*100:.1f}%, 异常")

        # 风险评分解释
        if risk_score >= 60:
            reason_codes.append("RISK_SCORE_HIGH")
            explanations.append(f"当前风险评分={risk_score}, 建议降低杠杆")

        # 置信度解释
        if confidence < 0.5:
            reason_codes.append("LOW_CONFIDENCE")
            explanations.append(f"置信度={confidence:.2f}, 建议保持当前模式")

        # 模式建议
        mode_map = {
            MarketState.RANGE: "中性网格",
            MarketState.UPTREND: "做多网格",
            MarketState.DOWNTREND: "做空网格",
        }
        if market_state in mode_map:
            explanations.append(f"市场状态为{market_state.value}, 推荐使用{mode_map[market_state]}")

        # 生成摘要
        if explanations:
            reason = " ".join(explanations[:2])  # 取前两条作为摘要
        else:
            reason = "市场分析完成,无特殊情况"

        return reason, reason_codes, explanations

    def _decide_action(
        self,
        recommended_mode: GridMode,
        risk_score: int,
        confidence: float,
        strategy_state: StrategyState
    ) -> str:
        """
        决定建议动作

        Args:
            recommended_mode: 推荐模式
            risk_score: 风险评分
            confidence: 置信度
            strategy_state: 策略状态

        Returns:
            动作: RUN/KEEP/SWITCH_TO_LONG/SWITCH_TO_SHORT/SWITCH_TO_NEUTRAL/PAUSE
        """
        # 极高风险建议暂停
        if risk_score >= 90:
            return "PAUSE"

        # 如果AI自动切换未启用,保持运行
        if not self.config.ai_auto_switch_enabled:
            return "RUN"

        # 检查是否可以切换模式
        if not self._can_switch_mode(strategy_state, confidence, risk_score):
            return "KEEP"

        # 模式相同,保持运行
        if recommended_mode == strategy_state.current_mode:
            return "RUN"

        # 建议切换模式
        if recommended_mode == GridMode.NEUTRAL:
            return "SWITCH_TO_NEUTRAL"
        elif recommended_mode == GridMode.LONG:
            return "SWITCH_TO_LONG"
        elif recommended_mode == GridMode.SHORT:
            return "SWITCH_TO_SHORT"

        return "RUN"

    def _can_switch_mode(
        self,
        strategy_state: StrategyState,
        confidence: float,
        risk_score: int
    ) -> bool:
        """
        判断是否可以切换模式

        Args:
            strategy_state: 策略状态
            confidence: 置信度
            risk_score: 风险评分

        Returns:
            是否可以切换
        """
        # 置信度过低
        if confidence < self.config.ai_confidence_threshold:
            return False

        # 风险评分过高
        if risk_score >= 80:
            return False

        # 冷却期未过
        if (strategy_state.last_mode_switch_time and
            datetime.now() - strategy_state.last_mode_switch_time <
            timedelta(minutes=self.config.mode_switch_cooldown_minutes)):
            return False

        return True

    # ==================== 技术指标计算辅助函数 ====================

    def _calculate_ema(self, data: np.ndarray, period: int) -> float:
        """计算EMA"""
        if len(data) < period:
            return data[-1] if len(data) > 0 else 0.0

        multiplier = 2 / (period + 1)
        ema = data[0]
        for price in data[1:]:
            ema = (price * multiplier) + (ema * (1 - multiplier))
        return ema

    def _calculate_rsi(self, closes: np.ndarray, period: int) -> float:
        """计算RSI"""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_macd(
        self, closes: np.ndarray, fast: int, slow: int, signal: int
    ) -> Tuple[float, float, float]:
        """计算MACD"""
        ema_fast = self._calculate_ema(closes, fast)
        ema_slow = self._calculate_ema(closes, slow)

        macd = ema_fast - ema_slow
        macd_signal = macd * 0.8  # 简化计算
        macd_hist = macd - macd_signal

        return macd, macd_signal, macd_hist

    def _calculate_atr(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int
    ) -> float:
        """计算ATR"""
        if len(highs) < period + 1:
            return 0.0

        tr_list = []
        for i in range(1, len(highs)):
            high = highs[i]
            low = lows[i]
            prev_close = closes[i - 1]

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            tr_list.append(tr)

        if len(tr_list) >= period:
            return np.mean(tr_list[-period:])
        return np.mean(tr_list)

    def _calculate_adx(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int
    ) -> float:
        """计算ADX (简化版本)"""
        if len(highs) < period * 2:
            return 0.0

        # 简化实现: 计算价格变动幅度
        changes = []
        for i in range(1, len(closes)):
            change = abs(closes[i] - closes[i-1]) / closes[i-1]
            changes.append(change)

        if len(changes) >= period:
            avg_change = np.mean(changes[-period:])
            # 简化ADX: 基于平均波动率
            adx = min(avg_change * 1000, 100)  # 转换为0-100范围
            return adx

        return 0.0

    def _calculate_bollinger_bands(
        self, closes: np.ndarray, period: int, std_dev: float
    ) -> Tuple[float, float, float, float]:
        """计算布林带"""
        if len(closes) < period:
            return closes[-1], closes[-1], closes[-1], 0.0

        middle = np.mean(closes[-period:])
        std = np.std(closes[-period:])

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        width = (upper - lower) / middle if middle > 0 else 0.0

        return upper, middle, lower, width

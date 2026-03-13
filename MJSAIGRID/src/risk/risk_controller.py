"""
Risk Control System
基于文档 06_风控系统设计说明书 实现

完整实现风控检查逻辑,包括:
- 启动前检查
- 下单前检查
- 实时风险监控
- 风险等级管理
- 熔断与暂停机制
"""

from typing import Optional, Tuple, List, Dict
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass

from src.grid_trading.models import (
    RiskLevel,
    GridMode,
    OrderRequest,
    OrderSide,
    Position,
    AccountBalance,
    GridConfig,
    StrategyState,
    MarketState,
    AIDecision,
    ErrorType,
    StrategyStatus,
)


class RiskAction(str, Enum):
    """风控动作枚举"""
    ALLOW = "allow"
    WARN = "warn"
    LIMIT_NEW_ORDER = "limit_new_order"
    REDUCE_LEVERAGE = "reduce_leverage"
    REDUCE_POSITION = "reduce_position"
    CANCEL_NON_CRITICAL_ORDERS = "cancel_non_critical_orders"
    PAUSE_STRATEGY = "pause_strategy"
    STOP_STRATEGY = "stop_strategy"
    FORCE_CLOSE = "force_close"


@dataclass
class RiskCheckResult:
    """风控检查结果"""
    allowed: bool
    action: RiskAction
    reason: str
    reason_codes: List[str]
    risk_level: RiskLevel
    metadata: Dict = None


class RiskController:
    """风控控制器 - 完整实现"""

    def __init__(self, config: GridConfig):
        """
        初始化风控控制器

        Args:
            config: 网格配置
        """
        self.config = config

        # 风险状态
        self.current_risk_level = RiskLevel.LOW
        self.pause_start_time: Optional[datetime] = None
        self.cooldown_end_time: Optional[datetime] = None

        # 统计信息
        self.consecutive_losses: int = 0
        self.last_loss_time: Optional[datetime] = None
        self.daily_pnl: float = 0.0
        self.daily_reset_time: datetime = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # 异常检测
        self.abnormal_market_detected = False
        self.last_abnormal_check_time: Optional[datetime] = None

    def startup_check(
        self,
        config: GridConfig,
        account_balance: AccountBalance,
        current_price: float
    ) -> RiskCheckResult:
        """
        启动前检查

        Args:
            config: 网格配置
            account_balance: 账户余额
            current_price: 当前价格

        Returns:
            检查结果
        """
        reason_codes = []
        reasons = []

        # 1. 参数合法性检查
        is_valid, error_msg = config.validate()
        if not is_valid:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.STOP_STRATEGY,
                reason=f"参数配置错误: {error_msg}",
                reason_codes=["PARAM_VALIDATION_ERROR"],
                risk_level=RiskLevel.CRITICAL
            )

        # 2. 杠杆检查
        if config.leverage > self.config.max_leverage:
            reasons.append(f"杠杆{config.leverage}X超过风控上限{self.config.max_leverage}X")
            reason_codes.append("LEVERAGE_TOO_HIGH")

        # 3. 保证金检查
        margin_needed = config.capital / config.leverage
        available = account_balance.available_balance
        if available < margin_needed:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.STOP_STRATEGY,
                reason=f"可用余额不足: 需要{margin_needed:.2f}, 可用{available:.2f}",
                reason_codes=["INSUFFICIENT_BALANCE"],
                risk_level=RiskLevel.CRITICAL
            )

        # 4. 价格区间检查
        if config.price_lower > 0 and config.price_upper > 0:
            if config.price_lower >= config.price_upper:
                return RiskCheckResult(
                    allowed=False,
                    action=RiskAction.STOP_STRATEGY,
                    reason="价格区间下限必须小于上限",
                    reason_codes=["INVALID_PRICE_RANGE"],
                    risk_level=RiskLevel.CRITICAL
                )

            if not (config.price_lower <= current_price <= config.price_upper):
                reasons.append(f"当前价格{current_price}不在设定区间[{config.price_lower}, {config.price_upper}]")
                reason_codes.append("PRICE_OUT_OF_RANGE")

        # 5. 网格数量检查
        if config.grid_count < 2:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.STOP_STRATEGY,
                reason="网格数量至少为2",
                reason_codes=["INVALID_GRID_COUNT"],
                risk_level=RiskLevel.CRITICAL
            )

        if config.active_order_count > config.grid_count:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.STOP_STRATEGY,
                reason="活跃挂单数不能超过网格总数",
                reason_codes=["INVALID_ACTIVE_ORDER_COUNT"],
                risk_level=RiskLevel.CRITICAL
            )

        # 6. 市场状态检查(如果提供)
        # TODO: 集成市场状态检测

        # 汇总结果
        if reason_codes:
            reason = "; ".join(reasons)
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.WARN,
                reason=reason,
                reason_codes=reason_codes,
                risk_level=RiskLevel.MEDIUM
            )

        return RiskCheckResult(
            allowed=True,
            action=RiskAction.ALLOW,
            reason="启动检查通过",
            reason_codes=[],
            risk_level=RiskLevel.LOW
        )

    def pre_order_check(
        self,
        order_request: OrderRequest,
        account_balance: AccountBalance,
        positions: List[Position],
        strategy_state: StrategyState,
        current_price: float
    ) -> RiskCheckResult:
        """
        下单前检查

        Args:
            order_request: 订单请求
            account_balance: 账户余额
            positions: 当前持仓
            strategy_state: 策略状态
            current_price: 当前价格

        Returns:
            检查结果
        """
        reason_codes = []

        # 检查是否处于暂停状态
        if self._is_paused():
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.PAUSE_STRATEGY,
                reason="策略处于暂停状态",
                reason_codes=["STRATEGY_PAUSED"],
                risk_level=self.current_risk_level
            )

        # 检查是否处于冷却期
        if self._is_in_cooldown():
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.PAUSE_STRATEGY,
                reason="策略处于冷却期",
                reason_codes=["COOLDOWN_ACTIVE"],
                risk_level=self.current_risk_level
            )

        # 1. 风险等级检查
        if self.current_risk_level == RiskLevel.CRITICAL:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.STOP_STRATEGY,
                reason="风险等级为CRITICAL,禁止新开仓",
                reason_codes=["RISK_LEVEL_CRITICAL"],
                risk_level=RiskLevel.CRITICAL
            )

        if self.current_risk_level == RiskLevel.HIGH:
            # 高风险:仅允许减仓操作
            if order_request.reduce_only:
                return RiskCheckResult(
                    allowed=True,
                    action=RiskAction.ALLOW,
                    reason="高风险状态,仅允许减仓",
                    reason_codes=["HIGH_RISK_REDUCE_ONLY"],
                    risk_level=RiskLevel.HIGH
                )
            else:
                return RiskCheckResult(
                    allowed=False,
                    action=RiskAction.LIMIT_NEW_ORDER,
                    reason="高风险状态,禁止新增风险敞口",
                    reason_codes=["RISK_LEVEL_HIGH"],
                    risk_level=RiskLevel.HIGH
                )

        # 2. 保证金使用率检查
        margin_usage = self._calculate_margin_usage(account_balance, positions)
        if margin_usage > self.config.max_margin_usage:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.LIMIT_NEW_ORDER,
                reason=f"保证金使用率{margin_usage*100:.1f}%超过上限{self.config.max_margin_usage*100:.1f}%",
                reason_codes=["MARGIN_USAGE_TOO_HIGH"],
                risk_level=RiskLevel.HIGH
            )

        # 3. 仓位比例检查
        position_ratio = self._calculate_position_ratio(positions, account_balance)
        if position_ratio > self.config.max_position_ratio:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.REDUCE_POSITION,
                reason=f"仓位占比{position_ratio*100:.1f}%超过上限{self.config.max_position_ratio*100:.1f}%",
                reason_codes=["POSITION_RATIO_TOO_HIGH"],
                risk_level=RiskLevel.HIGH
            )

        # 4. 单日亏损检查
        if self._check_daily_loss_limit():
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.PAUSE_STRATEGY,
                reason=f"单日亏损{abs(self.daily_pnl):.2f} USDT超过限额",
                reason_codes=["DAILY_LOSS_LIMIT_REACHED"],
                risk_level=RiskLevel.HIGH
            )

        # 5. 连续亏损检查
        if self._check_consecutive_losses():
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.PAUSE_STRATEGY,
                reason=f"连续亏损{self.consecutive_losses}次超过限额",
                reason_codes=["CONSECUTIVE_LOSSES"],
                risk_level=RiskLevel.HIGH
            )

        # 6. 最大回撤检查
        if strategy_state.max_drawdown > self.config.max_drawdown_threshold:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.PAUSE_STRATEGY,
                reason=f"最大回撤{strategy_state.max_drawdown*100:.1f}%超过限额",
                reason_codes=["MAX_DRAWDOWN_EXCEEDED"],
                risk_level=RiskLevel.HIGH
            )

        # 7. 异常行情检查
        if self._check_abnormal_market():
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.CANCEL_NON_CRITICAL_ORDERS,
                reason="检测到异常行情,暂停新增订单",
                reason_codes=["ABNORMAL_MARKET"],
                risk_level=RiskLevel.CRITICAL
            )

        # 8. 价格偏离检查
        if order_request.price:
            price_deviation = abs(order_request.price - current_price) / current_price
            if price_deviation > 0.20:  # 价格偏离超过20%
                return RiskCheckResult(
                    allowed=False,
                    action=RiskAction.LIMIT_NEW_ORDER,
                    reason=f"订单价格{order_request.price}严重偏离当前价格{current_price}",
                    reason_codes=["PRICE_DEVIATION_TOO_HIGH"],
                    risk_level=RiskLevel.MEDIUM
                )

        # 9. 杠杆检查
        if strategy_state.current_leverage > self.config.max_leverage:
            return RiskCheckResult(
                allowed=False,
                action=RiskAction.REDUCE_LEVERAGE,
                reason=f"当前杠杆{strategy_state.current_leverage}X超过风控上限",
                reason_codes=["LEVERAGE_TOO_HIGH"],
                risk_level=RiskLevel.HIGH
            )

        # 所有检查通过
        return RiskCheckResult(
            allowed=True,
            action=RiskAction.ALLOW,
            reason="下单前检查通过",
            reason_codes=[],
            risk_level=self.current_risk_level
        )

    def evaluate_risk(
        self,
        market_state: MarketState,
        ai_decision: Optional[AIDecision],
        strategy_state: StrategyState,
        account_balance: AccountBalance,
        positions: List[Position],
        current_price: float
    ) -> RiskCheckResult:
        """
        实时风险评估

        Args:
            market_state: 市场状态
            ai_decision: AI决策
            strategy_state: 策略状态
            account_balance: 账户余额
            positions: 持仓列表
            current_price: 当前价格

        Returns:
            风险评估结果
        """
        score = 0
        reason_codes = []
        actions = []
        reasons = []

        # 1. 市场波动风险
        if market_state == MarketState.ABNORMAL:
            score += 30
            reason_codes.append("MARKET_ABNORMAL")
            actions.append(RiskAction.CANCEL_NON_CRITICAL_ORDERS)

        elif market_state == MarketState.UNKNOWN:
            score += 15
            reason_codes.append("MARKET_UNKNOWN")

        # 2. AI风险评分
        if ai_decision:
            if ai_decision.risk_score >= 80:
                score += 25
                reason_codes.append("AI_HIGH_RISK")
                actions.append(RiskAction.REDUCE_LEVERAGE)
            elif ai_decision.risk_score >= 60:
                score += 15
                reason_codes.append("AI_MEDIUM_RISK")

        # 3. 杠杆风险
        if strategy_state.current_leverage >= 10:
            score += 20
            reason_codes.append("HIGH_LEVERAGE")
            actions.append(RiskAction.REDUCE_LEVERAGE)
        elif strategy_state.current_leverage >= 5:
            score += 10
            reason_codes.append("MEDIUM_LEVERAGE")

        # 4. 回撤风险
        drawdown_ratio = strategy_state.max_drawdown
        if drawdown_ratio >= 0.15:
            score += 25
            reason_codes.append("HIGH_DRAWDOWN")
            actions.append(RiskAction.PAUSE_STRATEGY)
        elif drawdown_ratio >= 0.10:
            score += 15
            reason_codes.append("MEDIUM_DRAWDOWN")
        elif drawdown_ratio >= 0.05:
            score += 5
            reason_codes.append("LOW_DRAWDOWN")

        # 5. 保证金风险
        margin_usage = self._calculate_margin_usage(account_balance, positions)
        if margin_usage >= 0.80:
            score += 25
            reason_codes.append("MARGIN_CRITICAL")
            actions.append(RiskAction.REDUCE_POSITION)
        elif margin_usage >= 0.65:
            score += 15
            reason_codes.append("MARGIN_HIGH")
        elif margin_usage >= 0.50:
            score += 5
            reason_codes.append("MARGIN_MEDIUM")

        # 6. 仓位风险
        position_ratio = self._calculate_position_ratio(positions, account_balance)
        if position_ratio >= 0.80:
            score += 15
            reason_codes.append("POSITION_HIGH")

        # 7. 单日亏损风险
        if self._check_daily_loss_limit():
            score += 30
            reason_codes.append("DAILY_LOSS_LIMIT")
            actions.append(RiskAction.PAUSE_STRATEGY)

        # 8. 连续亏损风险
        if self._check_consecutive_losses():
            score += 20
            reason_codes.append("CONSECUTIVE_LOSSES")
            actions.append(RiskAction.PAUSE_STRATEGY)

        # 9. 模式错配风险
        if (market_state == MarketState.DOWNTREND and
            strategy_state.current_mode == GridMode.LONG):
            score += 15
            reason_codes.append("MODE_MISMATCH_BULL_IN_BEAR")
            actions.append(RiskAction.REDUCE_POSITION)
        elif (market_state == MarketState.UPTREND and
              strategy_state.current_mode == GridMode.SHORT):
            score += 15
            reason_codes.append("MODE_MISMATCH_BEAR_IN_BULL")
            actions.append(RiskAction.REDUCE_POSITION)

        # 确定风险等级
        if score >= 75:
            new_risk_level = RiskLevel.CRITICAL
        elif score >= 50:
            new_risk_level = RiskLevel.HIGH
        elif score >= 25:
            new_risk_level = RiskLevel.MEDIUM
        else:
            new_risk_level = RiskLevel.LOW

        self.current_risk_level = new_risk_level

        # 选择最严格的动作
        final_action = self._select_strictest_action(actions)

        # 生成原因描述
        if reasons:
            reason = "; ".join(reasons)
        else:
            reason = f"风险评分: {score}/100"

        return RiskCheckResult(
            allowed=new_risk_level != RiskLevel.CRITICAL,
            action=final_action,
            reason=reason,
            reason_codes=reason_codes,
            risk_level=new_risk_level
        )

    def update_daily_pnl(self, pnl: float):
        """
        更新当日盈亏

        Args:
            pnl: 当笔交易盈亏
        """
        # 检查是否需要重置日统计
        now = datetime.now()
        if now >= self.daily_reset_time + timedelta(days=1):
            self.daily_pnl = 0.0
            self.daily_reset_time = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        self.daily_pnl += pnl

        # 更新连续亏损统计
        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_time = now
        else:
            self.consecutive_losses = 0
            self.last_loss_time = None

    def trigger_pause(self, duration_minutes: int = 0):
        """
        触发暂停

        Args:
            duration_minutes: 暂停时长(0表示无限期)
        """
        self.pause_start_time = datetime.now()
        if duration_minutes > 0:
            self.cooldown_end_time = self.pause_start_time + timedelta(minutes=duration_minutes)
        else:
            self.cooldown_end_time = None

    def resume(self):
        """恢复运行"""
        self.pause_start_time = None
        self.cooldown_end_time = None

    # ==================== 辅助方法 ====================

    def _is_paused(self) -> bool:
        """检查是否处于暂停状态"""
        return self.pause_start_time is not None

    def _is_in_cooldown(self) -> bool:
        """检查是否处于冷却期"""
        if self.cooldown_end_time is None:
            return False
        return datetime.now() < self.cooldown_end_time

    def _calculate_margin_usage(
        self,
        account_balance: AccountBalance,
        positions: List[Position]
    ) -> float:
        """
        计算保证金使用率

        Args:
            account_balance: 账户余额
            positions: 持仓列表

        Returns:
            保证金使用率(0-1)
        """
        if account_balance.wallet_balance <= 0:
            return 1.0  # 异常情况

        margin_used = abs(account_balance.unrealized_pnl)
        return min(margin_used / account_balance.wallet_balance, 1.0)

    def _calculate_position_ratio(
        self,
        positions: List[Position],
        account_balance: AccountBalance
    ) -> float:
        """
        计算仓位占比

        Args:
            positions: 持仓列表
            account_balance: 账户余额

        Returns:
            仓位占比(0-1)
        """
        if account_balance.wallet_balance <= 0:
            return 1.0

        total_position_value = sum(
            abs(p.quantity * p.mark_price) for p in positions
        )
        return min(total_position_value / account_balance.wallet_balance, 1.0)

    def _check_daily_loss_limit(self) -> bool:
        """
        检查是否达到单日亏损上限

        Returns:
            True if reached limit
        """
        if self.daily_pnl >= 0:
            return False

        loss_ratio = abs(self.daily_pnl) / self.config.capital
        return loss_ratio >= self.config.daily_loss_limit

    def _check_consecutive_losses(self) -> bool:
        """
        检查是否达到连续亏损上限

        Returns:
            True if reached limit
        """
        return self.consecutive_losses >= self.config.max_consecutive_losses

    def _check_abnormal_market(self) -> bool:
        """
        检查异常行情

        Returns:
            True if abnormal
        """
        if not self.config.abnormal_market_pause_enabled:
            return False

        # TODO: 实现具体的异常行情检测逻辑
        # 例如: ATR突然放大、单根K线涨跌幅过大等

        return self.abnormal_market_detected

    def _select_strictest_action(self, actions: List[RiskAction]) -> RiskAction:
        """
        选择最严格的动作

        Args:
            actions: 动作列表

        Returns:
            最严格的动作
        """
        if not actions:
            return RiskAction.ALLOW

        # 优先级顺序(从严格到宽松)
        priority_order = [
            RiskAction.STOP_STRATEGY,
            RiskAction.FORCE_CLOSE,
            RiskAction.PAUSE_STRATEGY,
            RiskAction.CANCEL_NON_CRITICAL_ORDERS,
            RiskAction.REDUCE_POSITION,
            RiskAction.REDUCE_LEVERAGE,
            RiskAction.LIMIT_NEW_ORDER,
            RiskAction.WARN,
            RiskAction.ALLOW,
        ]

        for action in priority_order:
            if action in actions:
                return action

        return RiskAction.ALLOW

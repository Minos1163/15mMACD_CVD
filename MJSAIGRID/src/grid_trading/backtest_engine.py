"""
回测引擎
基于历史数据验证网格交易策略

功能:
1. 撮合逻辑(限价单、市价单)
2. 收益计算(已实现盈亏、浮动盈亏、手续费)
3. 绩效统计(收益率、最大回撤、夏普比率等)
4. 可视化输出
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.grid_trading.models import (
    OrderSide,
    OrderType,
    OrderStatus,
    GridLevel,
    GridConfig,
    StrategyState,
    Trade,
    Position,
)


logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """回测配置"""
    symbol: str = "BTCUSDT"
    start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc) - timedelta(days=30))
    end_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    initial_capital: float = 10000.0  # USDT
    commission_rate: float = 0.0002  # 0.02% 交易所手续费
    slippage_rate: float = 0.0001  # 0.01% 滑点
    enable_short: bool = True  # 是否支持做空


@dataclass
class BacktestResult:
    """回测结果"""
    config: BacktestConfig
    
    # 交易统计
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    
    # 收益统计
    final_balance: float = 0.0
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_percent: float = 0.0
    
    # 风险指标
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_consecutive_losses: int = 0
    
    # 交易记录
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    
    # 绩效详情
    daily_returns: pd.DataFrame = field(default_factory=pd.DataFrame)
    monthly_returns: pd.DataFrame = field(default_factory=pd.DataFrame)


@dataclass
class LimitOrder:
    """限价订单"""
    order_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    create_time: datetime
    kline_time: datetime  # 订单创建时的K线时间


class BacktestEngine:
    """
    回测引擎
    
    支持网格交易策略回测
    """
    
    def __init__(
        self,
        config: BacktestConfig,
        grid_config: GridConfig,
    ):
        """
        初始化回测引擎
        
        Args:
            config: 回测配置
            grid_config: 网格配置
        """
        self.config = config
        self.grid_config = grid_config
        
        # 账户状态
        self.balance = config.initial_capital
        self.position_qty = 0.0  # 持仓数量(正数做多,负数做空)
        self.position_value = 0.0  # 持仓价值(USDT)
        self.entry_price = 0.0  # 平均开仓价格
        self.unrealized_pnl = 0.0
        self.realized_pnl = 0.0
        
        # 订单管理
        self._orders: Dict[str, LimitOrder] = {}  # 待成交订单
        self._order_id_counter = 0
        self._trades: List[Trade] = []
        self._equity_curve: List[Tuple[datetime, float]] = []
        
        # 网格订单
        self._grid_levels: List[GridLevel] = []
        self._active_orders: Dict[str, LimitOrder] = {}  # 活跃订单
        
    def _generate_order_id(self) -> str:
        """生成订单ID"""
        self._order_id_counter += 1
        return f"BT_{self._order_id_counter}"
    
    def _create_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: float,
        quantity: float,
        kline_time: datetime,
    ) -> LimitOrder:
        """创建限价订单"""
        order_id = self._generate_order_id()
        order = LimitOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            create_time=kline_time,
            kline_time=kline_time,
        )
        self._orders[order_id] = order
        return order
    
    def _match_order(
        self,
        order: LimitOrder,
        kline_high: float,
        kline_low: float,
        kline_close: float,
        kline_time: datetime,
    ) -> bool:
        """
        检查订单是否成交
        
        Args:
            order: 限价订单
            kline_high: K线最高价
            kline_low: K线最低价
            kline_close: K线收盘价
            kline_time: K线时间
            
        Returns:
            是否成交
        """
        if order.side == OrderSide.BUY:
            # 买单: 价格 <= K线最低价时成交
            if order.price <= kline_low:
                return True
            # 或者 价格在K线内,优先用close价撮合
            if order.price >= kline_low and order.price <= kline_high:
                return True
        else:  # SELL
            # 卖单: 价格 >= K线最高价时成交
            if order.price >= kline_high:
                return True
            # 或者 价格在K线内,优先用close价撮合
            if order.price >= kline_low and order.price <= kline_high:
                return True
        
        return False
    
    def _execute_trade(
        self,
        order: LimitOrder,
        fill_price: float,
        kline_time: datetime,
    ) -> Trade:
        """
        执行交易
        
        Args:
            order: 订单
            fill_price: 成交价格(包含滑点)
            kline_time: K线时间
            
        Returns:
            交易记录
        """
        # 计算手续费
        commission = order.quantity * fill_price * self.config.commission_rate
        
        # 更新持仓
        if order.side == OrderSide.BUY:
            # 买入
            old_position_value = abs(self.position_qty) * self.entry_price if self.position_qty != 0 else 0.0
            
            # 计算新的平均开仓价格
            if self.position_qty >= 0:
                # 加多仓或开多仓
                new_qty = self.position_qty + order.quantity
                if new_qty != 0:
                    self.entry_price = (
                        old_position_value + order.quantity * fill_price
                    ) / new_qty
                self.position_qty = new_qty
            else:
                # 平空仓(或减空)
                close_qty = min(abs(self.position_qty), order.quantity)
                close_pnl = close_qty * (self.entry_price - fill_price)
                self.realized_pnl += close_pnl
                self.position_qty += close_qty
                
                # 剩余部分开多仓
                remaining_qty = order.quantity - close_qty
                if remaining_qty > 0:
                    self.entry_price = fill_price
                    self.position_qty = remaining_qty
            
        else:  # SELL
            # 卖出
            if self.position_qty > 0:
                # 平多仓
                close_qty = min(self.position_qty, order.quantity)
                close_pnl = close_qty * (fill_price - self.entry_price)
                self.realized_pnl += close_pnl
                self.position_qty -= close_qty
                
                # 剩余部分开空仓
                remaining_qty = order.quantity - close_qty
                if remaining_qty > 0 and self.config.enable_short:
                    self.entry_price = fill_price
                    self.position_qty = -remaining_qty
            elif self.position_qty <= 0:
                # 加空仓或开空仓
                if self.config.enable_short:
                    old_position_value = abs(self.position_qty) * self.entry_price if self.position_qty != 0 else 0.0
                    new_qty = abs(self.position_qty) + order.quantity
                    if new_qty != 0:
                        self.entry_price = (
                            old_position_value + order.quantity * fill_price
                        ) / new_qty
                    self.position_qty = -new_qty
        
        # 更新持仓价值
        self.position_value = abs(self.position_qty) * fill_price
        
        # 创建交易记录
        trade = Trade(
            trade_id=self._generate_order_id(),
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            price=fill_price,
            quantity=order.quantity,
            commission=commission,
            time=kline_time,
        )
        
        self._trades.append(trade)
        
        return trade
    
    def _calculate_unrealized_pnl(self, current_price: float) -> float:
        """计算浮动盈亏"""
        if self.position_qty == 0:
            return 0.0
        
        if self.position_qty > 0:
            return self.position_qty * (current_price - self.entry_price)
        else:
            return abs(self.position_qty) * (self.entry_price - current_price)
    
    def _calculate_equity(self, current_price: float) -> float:
        """计算权益"""
        unrealized_pnl = self._calculate_unrealized_pnl(current_price)
        return self.config.initial_capital + self.realized_pnl + unrealized_pnl
    
    def run(
        self,
        klines: List[Dict[str, Any]],
        grid_levels: List[GridLevel],
    ) -> BacktestResult:
        """
        运行回测
        
        Args:
            klines: K线数据列表
            grid_levels: 网格层级列表
            
        Returns:
            回测结果
        """
        self._grid_levels = grid_levels
        
        # 初始化网格订单
        for level in grid_levels:
            if level.side in [OrderSide.BUY, None]:
                # 买单
                order = self._create_limit_order(
                    symbol=self.config.symbol,
                    side=OrderSide.BUY,
                    price=level.price,
                    quantity=0.0,  # 将在运行时计算
                    kline_time=klines[0]["datetime"] if klines else datetime.now(timezone.utc),
                )
                self._active_orders[level.price] = order
            if level.side in [OrderSide.SELL, None]:
                # 卖单
                order = self._create_limit_order(
                    symbol=self.config.symbol,
                    side=OrderSide.SELL,
                    price=level.price,
                    quantity=0.0,
                    kline_time=klines[0]["datetime"] if klines else datetime.now(timezone.utc),
                )
                self._active_orders[f"{level.price}_sell"] = order
        
        # 运行回测
        for kline in klines:
            kline_time = kline["datetime"]
            kline_high = kline["high"]
            kline_low = kline["low"]
            kline_close = kline["close"]
            
            # 检查订单成交
            filled_orders = []
            for order_id, order in list(self._active_orders.items()):
                if self._match_order(order, kline_high, kline_low, kline_close, kline_time):
                    # 计算成交价格(包含滑点)
                    fill_price = order.price * (1.0 + self.config.slippage_rate if order.side == OrderSide.BUY else 1.0 - self.config.slippage_rate)
                    
                    # 执行交易
                    self._execute_trade(order, fill_price, kline_time)
                    filled_orders.append(order_id)
            
            # 移除已成交订单
            for order_id in filled_orders:
                if order_id in self._active_orders:
                    del self._active_orders[order_id]
            
            # 补单逻辑
            self._replenish_orders(kline_close, kline_time)
            
            # 更新权益曲线
            equity = self._calculate_equity(kline_close)
            self._equity_curve.append((kline_time, equity))
            
            # 更新浮动盈亏
            self.unrealized_pnl = self._calculate_unrealized_pnl(kline_close)
        
        # 计算最终结果
        return self._calculate_result()
    
    def _replenish_orders(self, current_price: float, kline_time: datetime) -> None:
        """
        补单逻辑
        
        当价格接近某个网格层级时,补上该层级的订单
        """
        # 计算每个层级的价格
        for level in self._grid_levels:
            # 检查是否需要补买单
            if level.side in [OrderSide.BUY, None]:
                buy_order_key = level.price
                if buy_order_key not in self._active_orders:
                    # 价格低于当前价格时,补买单
                    if level.price < current_price:
                        # 计算订单数量
                        quantity = self._calculate_order_quantity(level, current_price)
                        if quantity > 0:
                            order = self._create_limit_order(
                                symbol=self.config.symbol,
                                side=OrderSide.BUY,
                                price=level.price,
                                quantity=quantity,
                                kline_time=kline_time,
                            )
                            self._active_orders[buy_order_key] = order
            
            # 检查是否需要补卖单
            if level.side in [OrderSide.SELL, None]:
                sell_order_key = f"{level.price}_sell"
                if sell_order_key not in self._active_orders:
                    # 价格高于当前价格时,补卖单
                    if level.price > current_price:
                        # 计算订单数量
                        quantity = self._calculate_order_quantity(level, current_price)
                        if quantity > 0:
                            order = self._create_limit_order(
                                symbol=self.config.symbol,
                                side=OrderSide.SELL,
                                price=level.price,
                                quantity=quantity,
                                kline_time=kline_time,
                            )
                            self._active_orders[sell_order_key] = order
    
    def _calculate_order_quantity(self, level: GridLevel, current_price: float) -> float:
        """计算订单数量"""
        # 简化计算: 每个层级使用固定数量
        # 实际应该基于网格配置和账户余额计算
        base_quantity = self.config.initial_capital * 0.01 / current_price  # 1%的仓位
        return base_quantity
    
    def _calculate_result(self) -> BacktestResult:
        """计算回测结果"""
        # 最终权益
        final_equity = self._equity_curve[-1][1] if self._equity_curve else self.config.initial_capital
        
        # 总盈亏
        total_pnl = final_equity - self.config.initial_capital
        total_pnl_percent = total_pnl / self.config.initial_capital * 100
        
        # 最大回撤
        max_equity = self.config.initial_capital
        max_drawdown = 0.0
        max_drawdown_percent = 0.0
        
        for _, equity in self._equity_curve:
            if equity > max_equity:
                max_equity = equity
            
            drawdown = max_equity - equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown
                max_drawdown_percent = drawdown / max_equity * 100 if max_equity > 0 else 0.0
        
        # 计算收益率序列
        if len(self._equity_curve) > 1:
            equity_df = pd.DataFrame(self._equity_curve, columns=["datetime", "equity"])
            equity_df.set_index("datetime", inplace=True)
            
            # 日收益率
            daily_returns = equity_df.resample("D").last().pct_change().dropna()
            
            # 计算夏普比率
            if len(daily_returns) > 1:
                excess_returns = daily_returns["equity"] - 0.0001  # 假设无风险利率
                sharpe_ratio = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0.0
                
                # 计算Sortino比率
                downside_returns = excess_returns[excess_returns < 0]
                sortino_ratio = np.sqrt(252) * excess_returns.mean() / downside_returns.std() if len(downside_returns) > 0 and downside_returns.std() > 0 else 0.0
            else:
                sharpe_ratio = 0.0
                sortino_ratio = 0.0
            
            # 月度收益率
            monthly_returns = equity_df.resample("M").last().pct_change().dropna()
        else:
            sharpe_ratio = 0.0
            sortino_ratio = 0.0
            daily_returns = pd.DataFrame()
            monthly_returns = pd.DataFrame()
        
        # 交易统计
        total_trades = len(self._trades)
        winning_trades = sum(1 for t in self._trades if (t.side == OrderSide.SELL and t.price > self.entry_price) or (t.side == OrderSide.BUY and t.price < self.entry_price))
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        
        # 最大连续亏损
        max_consecutive_losses = 0
        current_consecutive_losses = 0
        
        for trade in self._trades:
            # 简化判断: 实际应该基于交易对来判断盈亏
            if trade.side == OrderSide.SELL and trade.price < self.entry_price:
                current_consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
            else:
                current_consecutive_losses = 0
        
        return BacktestResult(
            config=self.config,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            final_balance=final_equity,
            total_pnl=total_pnl,
            total_pnl_percent=total_pnl_percent,
            max_drawdown=max_drawdown,
            max_drawdown_percent=max_drawdown_percent,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_consecutive_losses=max_consecutive_losses,
            trades=self._trades,
            equity_curve=self._equity_curve,
            daily_returns=daily_returns,
            monthly_returns=monthly_returns,
        )


# 导出
__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "BacktestEngine",
]

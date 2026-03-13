"""Grid engine for generating and managing grid levels."""

from typing import Optional
import math
from src.grid_trading.models import (
    GridLevel,
    GridConfig,
    GridType,
    GridMode,
    OrderSide,
    OrderStatus,
)


class GridEngine:
    """Core grid generation and management engine."""
    
    def __init__(self, config: GridConfig):
        self.config = config
        self.grid_levels: list[GridLevel] = []
    
    def generate_grid_levels(self, current_price: float) -> list[GridLevel]:
        """Generate grid levels based on configuration and current price.
        
        Args:
            current_price: Current market price
            
        Returns:
            List of GridLevel objects
        """
        # Auto-calculate price range if not set
        price_lower = self.config.price_lower
        price_upper = self.config.price_upper
        
        if price_lower <= 0 or price_upper <= 0:
            price_lower, price_upper = self._calculate_auto_range(current_price)
        
        # Generate grid levels based on type
        if self.config.grid_type == GridType.GEOMETRIC:
            levels = self._generate_geometric_grid(price_lower, price_upper)
        else:
            levels = self._generate_arithmetic_grid(price_lower, price_upper)
        
        # Assign sides based on mode
        self._assign_sides(levels)
        
        self.grid_levels = levels
        return levels
    
    def _calculate_auto_range(self, current_price: float) -> tuple[float, float]:
        """Calculate price range automatically based on current price.
        
        Default: +/- 10% range for DOGE
        """
        # TODO: Integrate with AI or volatility-based calculation
        volatility_factor = 0.10  # 10% range default
        
        price_lower = current_price * (1 - volatility_factor)
        price_upper = current_price * (1 + volatility_factor)
        
        return price_lower, price_upper
    
    def _generate_arithmetic_grid(
        self, 
        price_lower: float, 
        price_upper: float
    ) -> list[GridLevel]:
        """Generate arithmetic (equal difference) grid levels.
        
        Args:
            price_lower: Lower price bound
            price_upper: Upper price bound
            
        Returns:
            List of grid levels with equal price differences
        """
        grid_count = self.config.grid_count
        step = (price_upper - price_lower) / (grid_count - 1)
        
        levels = []
        for i in range(grid_count):
            price = price_lower + (i * step)
            level = GridLevel(
                level_id=i,
                price=round(price, self._get_price_precision(price))
            )
            levels.append(level)
        
        return levels
    
    def _generate_geometric_grid(
        self, 
        price_lower: float, 
        price_upper: float
    ) -> list[GridLevel]:
        """Generate geometric (equal ratio) grid levels.
        
        This is preferred for high-volatility assets like DOGE.
        
        Args:
            price_lower: Lower price bound
            price_upper: Upper price bound
            
        Returns:
            List of grid levels with equal price ratios
        """
        grid_count = self.config.grid_count
        
        # Calculate ratio factor: ratio^n = upper/lower
        ratio = math.pow(price_upper / price_lower, 1.0 / (grid_count - 1))
        
        levels = []
        for i in range(grid_count):
            price = price_lower * math.pow(ratio, i)
            level = GridLevel(
                level_id=i,
                price=round(price, self._get_price_precision(price))
            )
            levels.append(level)
        
        return levels
    
    def _get_price_precision(self, price: float) -> int:
        """Get appropriate price precision based on price level.
        
        Args:
            price: Price value
            
        Returns:
            Number of decimal places
        """
        if price >= 1:
            return 4
        elif price >= 0.01:
            return 5
        else:
            return 6
    
    def _assign_sides(self, levels: list[GridLevel]) -> None:
        """Assign buy/sell sides to grid levels based on mode.
        
        Args:
            levels: List of grid levels
        """
        mode = self.config.grid_mode
        current_price = self._get_current_price_estimate(levels)
        
        if mode == GridMode.NEUTRAL:
            # Neutral mode: buy below current, sell above current
            for level in levels:
                if level.price < current_price:
                    level.side = OrderSide.BUY
                else:
                    level.side = OrderSide.SELL
        
        elif mode == GridMode.LONG:
            # Long mode: mostly buy orders, some sell for taking profit
            sell_ratio = 0.3  # 30% sell orders for profit taking
            sell_count = int(len(levels) * sell_ratio)
            
            for i, level in enumerate(levels):
                if i >= len(levels) - sell_count:
                    level.side = OrderSide.SELL
                else:
                    level.side = OrderSide.BUY
        
        elif mode == GridMode.SHORT:
            # Short mode: mostly sell orders, some buy for covering
            buy_ratio = 0.3  # 30% buy orders for covering
            buy_count = int(len(levels) * buy_ratio)
            
            for i, level in enumerate(levels):
                if i < buy_count:
                    level.side = OrderSide.BUY
                else:
                    level.side = OrderSide.SELL
    
    def _get_current_price_estimate(self, levels: list[GridLevel]) -> float:
        """Estimate current price from grid levels.
        
        Args:
            levels: List of grid levels
            
        Returns:
            Estimated current price (middle of grid)
        """
        if not levels:
            return 0.0
        
        mid_idx = len(levels) // 2
        return levels[mid_idx].price
    
    def get_active_levels(
        self, 
        current_price: float
    ) -> list[GridLevel]:
        """Get active (pending) grid levels near current price.
        
        This implements the active order mechanism from docs:
        - Only maintain orders closest to current price
        - Reduces margin usage and improves capital efficiency
        
        Args:
            current_price: Current market price
            
        Returns:
            List of active grid levels
        """
        if not self.grid_levels:
            return []
        
        # Filter out already filled or cancelled levels
        available_levels = [
            level for level in self.grid_levels
            if level.status in [OrderStatus.PENDING, OrderStatus.NEW]
        ]
        
        if not available_levels:
            return []
        
        # Sort levels by distance from current price
        sorted_levels = sorted(
            available_levels,
            key=lambda x: abs(x.price - current_price)
        )
        
        # Return nearest levels up to active_order_count
        active_count = min(
            self.config.active_order_count,
            len(sorted_levels)
        )
        
        return sorted_levels[:active_count]
    
    def calculate_order_quantity(
        self,
        grid_level: GridLevel,
        total_capital: float,
        leverage: int
    ) -> float:
        """Calculate order quantity for a grid level.
        
        Args:
            grid_level: Grid level information
            total_capital: Total capital available
            leverage: Leverage multiplier
            
        Returns:
            Order quantity
        """
        # Calculate position size per grid level
        # Formula: capital / (grid_count * 2) * leverage
        # / 2 to account for both buy and sell orders
        position_per_level = (total_capital / self.config.grid_count) * leverage / 2
        
        # Calculate quantity
        quantity = position_per_level / grid_level.price
        
        return round(quantity, self._get_quantity_precision(grid_level.price))
    
    def _get_quantity_precision(self, price: float) -> int:
        """Get appropriate quantity precision based on price.
        
        Args:
            price: Price value
            
        Returns:
            Number of decimal places for quantity
        """
        if price >= 1:
            return 2
        elif price >= 0.01:
            return 3
        else:
            return 4
    
    def get_next_level_to_fill(
        self,
        current_price: float,
        direction: str = "down"
    ) -> Optional[GridLevel]:
        """Get the next grid level that would fill given price movement.
        
        Args:
            current_price: Current market price
            direction: "up" or "down"
            
        Returns:
            Next grid level or None
        """
        if not self.grid_levels:
            return None
        
        if direction == "down":
            # Price going down, find highest level below current price
            below_levels = [
                level for level in self.grid_levels
                if level.price < current_price and
                level.side in [OrderSide.BUY, None]
            ]
            if below_levels:
                return max(below_levels, key=lambda x: x.price)
        else:
            # Price going up, find lowest level above current price
            above_levels = [
                level for level in self.grid_levels
                if level.price > current_price and
                level.side in [OrderSide.SELL, None]
            ]
            if above_levels:
                return min(above_levels, key=lambda x: x.price)
        
        return None
    
    def should_replenish_order(
        self,
        grid_level: GridLevel,
        current_price: float
    ) -> bool:
        """Check if an order at this level should be replenished.
        
        Args:
            grid_level: Grid level
            current_price: Current market price
            
        Returns:
            True if order should be replenished
        """
        # Should replenish if:
        # 1. Level is in active range (close to current price)
        # 2. Order is filled or cancelled
        # 3. Level status is not already pending
        
        if grid_level.status not in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
            return False
        
        active_levels = self.get_active_levels(current_price)
        active_prices = [level.price for level in active_levels]
        
        # Check if this level is in the active range
        distance = abs(grid_level.price - current_price) / current_price
        active_max_distance = max(
            abs(level.price - current_price) / current_price
            for level in active_levels
        ) if active_levels else 0.05  # Default 5% range
        
        return distance <= active_max_distance
    
    def calculate_total_margin_required(
        self,
        current_price: float,
        total_capital: float,
        leverage: int
    ) -> float:
        """Calculate total margin required for all active orders.
        
        Args:
            current_price: Current market price
            total_capital: Total capital
            leverage: Leverage multiplier
            
        Returns:
            Margin required (in USDT)
        """
        active_levels = self.get_active_levels(current_price)
        total_value = 0.0
        
        for level in active_levels:
            quantity = self.calculate_order_quantity(level, total_capital, leverage)
            total_value += quantity * level.price
        
        # Margin required = total_value / leverage
        margin_required = total_value / leverage
        
        return margin_required
    
    def calculate_grid_profit(
        self, 
        buy_price: float, 
        sell_price: float
    ) -> float:
        """Calculate profit from a grid round-trip.
        
        Args:
            buy_price: Buy order price
            sell_price: Sell order price
            
        Returns:
            Profit as a ratio (e.g., 0.01 = 1%)
        """
        if buy_price <= 0:
            return 0.0
        
        return (sell_price - buy_price) / buy_price
    
    def calculate_theoretical_profit_per_grid(
        self,
        price_lower: float,
        price_upper: float
    ) -> float:
        """Calculate theoretical profit per grid level.
        
        Args:
            price_lower: Lower bound
            price_upper: Upper bound
            
        Returns:
            Profit ratio per grid
        """
        grid_count = self.config.grid_count
        
        if self.config.grid_type == GridType.GEOMETRIC:
            ratio = math.pow(price_upper / price_lower, 1.0 / (grid_count - 1))
            return ratio - 1
        else:
            step = (price_upper - price_lower) / (grid_count - 1)
            return step / price_lower
    
    def is_price_in_range(self, price: float) -> bool:
        """Check if price is within grid range.
        
        Args:
            price: Price to check
            
        Returns:
            True if price is in range
        """
        if not self.grid_levels:
            return True  # No range defined yet
        
        lower = min(l.price for l in self.grid_levels)
        upper = max(l.price for l in self.grid_levels)
        
        return lower <= price <= upper
    
    def should_trigger_tp_sl(self, current_price: float) -> tuple[bool, str]:
        """Check if take profit or stop loss should trigger.
        
        Args:
            current_price: Current market price
            
        Returns:
            Tuple of (should_trigger, reason)
        """
        # Check stop loss
        if self.config.stop_loss_price > 0:
            if self.config.grid_mode == GridMode.LONG:
                if current_price <= self.config.stop_loss_price:
                    return True, "stop_loss_long"
            elif self.config.grid_mode == GridMode.SHORT:
                if current_price >= self.config.stop_loss_price:
                    return True, "stop_loss_short"
        
        # Check take profit
        if self.config.take_profit_price > 0:
            if self.config.grid_mode == GridMode.LONG:
                if current_price >= self.config.take_profit_price:
                    return True, "take_profit_long"
            elif self.config.grid_mode == GridMode.SHORT:
                if current_price <= self.config.take_profit_price:
                    return True, "take_profit_short"
        
        return False, ""
    
    def get_grid_spacing_ratio(self) -> float:
        """Get the grid spacing as a ratio.
        
        Returns:
            Grid spacing ratio
        """
        if not self.grid_levels or len(self.grid_levels) < 2:
            return 0.0
        
        # Calculate average spacing
        total = 0.0
        for i in range(len(self.grid_levels) - 1):
            diff = self.grid_levels[i + 1].price - self.grid_levels[i].price
            ratio = diff / self.grid_levels[i].price
            total += ratio
        
        return total / (len(self.grid_levels) - 1)

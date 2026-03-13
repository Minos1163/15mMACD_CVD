"""
Order Manager - Handles order lifecycle management
Based on 交易执行引擎设计.md documentation

Implements:
- Order placement and cancellation
- Order status synchronization
- Order retry logic
- Idempotency guarantees
"""

import time
from typing import Optional, List, Dict
from datetime import datetime
from dataclasses import dataclass, field

from src.grid_trading.models import (
    Order,
    OrderRequest,
    OrderStatus,
    OrderSide,
    OrderType,
    TimeInForce,
    GridLevel,
    GridConfig,
)
from src.grid_trading.structured_logger import get_logger


@dataclass
class OrderResult:
    """Result of an order operation"""
    success: bool
    order: Optional[Order] = None
    error_message: str = ""
    error_code: Optional[str] = None
    retryable: bool = False


class OrderManager:
    """Manages order lifecycle for grid trading"""

    def __init__(self, config: GridConfig):
        """
        Initialize order manager

        Args:
            config: Grid trading configuration
        """
        self.config = config
        self.logger = get_logger(name="OrderManager")

        # Order storage
        self.active_orders: Dict[str, Order] = {}  # client_order_id -> Order
        self.order_history: List[Order] = []  # All orders (filled, cancelled, etc.)

        # Idempotency tracking
        self.order_requests: Dict[str, OrderRequest] = {}  # client_order_id -> request

    def generate_order_id(self, level_id: int, timestamp: Optional[int] = None) -> str:
        """
        Generate unique client order ID

        Args:
            level_id: Grid level ID
            timestamp: Unix timestamp (defaults to current time)

        Returns:
            Unique client order ID
        """
        if timestamp is None:
            timestamp = int(time.time())

        return f"grid_{self.config.symbol}_{level_id}_{timestamp}"

    def create_order_request(
        self,
        grid_level: GridLevel,
        strategy_id: str,
        leverage: int
    ) -> OrderRequest:
        """
        Create an order request from a grid level

        Args:
            grid_level: Grid level information
            strategy_id: Strategy ID
            leverage: Leverage multiplier

        Returns:
            Order request object
        """
        # Generate unique client order ID
        client_order_id = self.generate_order_id(grid_level.level_id)

        # Calculate order quantity
        # Formula: capital / grid_count * leverage / 2 / price
        # / 2 to account for both buy and sell orders
        quantity = (self.config.capital / self.config.grid_count) * leverage / 2 / grid_level.price

        # Create request
        request = OrderRequest(
            client_order_id=client_order_id,
            symbol=self.config.symbol,
            market_type="futures",
            side=grid_level.side or OrderSide.BUY,
            position_side="both",
            order_type=OrderType.LIMIT,
            price=grid_level.price,
            quantity=round(quantity, 6),  # 6 decimal places for DOGE
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            post_only=False,
            strategy_id=strategy_id,
            metadata={
                "level_id": grid_level.level_id,
                "grid_mode": self.config.grid_mode.value,
            }
        )

        # Store request for idempotency
        self.order_requests[client_order_id] = request

        self.logger.log_order_event(
            strategy_id=strategy_id,
            order_id=client_order_id,
            event_type="ORDER_REQUEST_CREATED",
            side=request.side,
            price=request.price,
            quantity=request.quantity,
            status=OrderStatus.PENDING,
        )

        return request

    async def place_order(self, request: OrderRequest) -> OrderResult:
        """
        Place an order with the exchange

        Args:
            request: Order request

        Returns:
            Order result
        """
        # Check idempotency
        if request.client_order_id in self.active_orders:
            existing_order = self.active_orders[request.client_order_id]
            if existing_order.status in [OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED]:
                return OrderResult(
                    success=True,
                    order=existing_order,
                    error_message="Order already exists and is active",
                    retryable=False
                )

        # Log order placement attempt
        self.logger.log_exchange_interaction(
            api_name="placeOrder",
            method="POST",
            latency_ms=0,
            status="PENDING",
        )

        # TODO: Implement actual exchange API call
        # For now, simulate placement
        try:
            order = await self._simulate_place_order(request)

            # Store order
            self.active_orders[request.client_order_id] = order

            self.logger.log_order_event(
                strategy_id=request.strategy_id or "unknown",
                order_id=request.client_order_id,
                event_type="ORDER_PLACED",
                side=order.side,
                price=order.price,
                quantity=order.quantity,
                status=order.status,
            )

            self.logger.log_exchange_interaction(
                api_name="placeOrder",
                method="POST",
                latency_ms=100,  # Simulated latency
                status="SUCCESS",
            )

            return OrderResult(success=True, order=order)

        except Exception as e:
            error_msg = f"Failed to place order: {str(e)}"
            self.logger.log_exchange_interaction(
                api_name="placeOrder",
                method="POST",
                latency_ms=100,
                status="FAILED",
                error_code="PLACE_FAILED",
            )
            return OrderResult(
                success=False,
                error_message=error_msg,
                error_code="PLACE_FAILED",
                retryable=True  # Assume retryable for network errors
            )

    async def cancel_order(self, client_order_id: str) -> OrderResult:
        """
        Cancel an order

        Args:
            client_order_id: Client order ID

        Returns:
            Order result
        """
        # Check if order exists
        if client_order_id not in self.active_orders:
            return OrderResult(
                success=False,
                error_message=f"Order {client_order_id} not found in active orders",
                error_code="ORDER_NOT_FOUND",
                retryable=False
            )

        order = self.active_orders[client_order_id]

        # Check if order can be cancelled
        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            return OrderResult(
                success=True,
                order=order,
                error_message=f"Order {client_order_id} already in final state {order.status}",
                retryable=False
            )

        # Log cancellation attempt
        self.logger.log_exchange_interaction(
            api_name="cancelOrder",
            method="DELETE",
            latency_ms=0,
            status="PENDING",
        )

        # TODO: Implement actual exchange API call
        try:
            cancelled_order = await self._simulate_cancel_order(order)

            # Update order in active orders
            self.active_orders[client_order_id] = cancelled_order

            # Move to history if final state
            if cancelled_order.status in [OrderStatus.CANCELLED, OrderStatus.FILLED]:
                self.order_history.append(canceled_order)
                del self.active_orders[client_order_id]

            self.logger.log_order_event(
                strategy_id=order.metadata.get("strategy_id", "unknown"),
                order_id=client_order_id,
                event_type="ORDER_CANCELLED",
                side=order.side,
                price=order.price,
                quantity=order.quantity,
                status=cancelled_order.status,
            )

            self.logger.log_exchange_interaction(
                api_name="cancelOrder",
                method="DELETE",
                latency_ms=50,  # Simulated latency
                status="SUCCESS",
            )

            return OrderResult(success=True, order=cancelled_order)

        except Exception as e:
            error_msg = f"Failed to cancel order {client_order_id}: {str(e)}"
            self.logger.log_exchange_interaction(
                api_name="cancelOrder",
                method="DELETE",
                latency_ms=50,
                status="FAILED",
                error_code="CANCEL_FAILED",
            )
            return OrderResult(
                success=False,
                error_message=error_msg,
                error_code="CANCEL_FAILED",
                retryable=True
            )

    async def sync_order_status(self, client_order_id: str) -> OrderResult:
        """
        Synchronize order status with exchange

        Args:
            client_order_id: Client order ID

        Returns:
            Order result with updated status
        """
        if client_order_id not in self.active_orders:
            return OrderResult(
                success=False,
                error_message=f"Order {client_order_id} not found in active orders",
                error_code="ORDER_NOT_FOUND",
                retryable=False
            )

        order = self.active_orders[client_order_id]

        # TODO: Implement actual exchange API call
        # For now, simulate status check
        try:
            updated_order = await self._simulate_order_status(order)

            # Update order if status changed
            if updated_order.status != order.status:
                self.active_orders[client_order_id] = updated_order

                # Log status change
                self.logger.log_order_event(
                    strategy_id=order.metadata.get("strategy_id", "unknown"),
                    order_id=client_order_id,
                    event_type="ORDER_STATUS_UPDATED",
                    side=order.side,
                    price=order.price,
                    quantity=order.quantity,
                    status=updated_order.status,
                )

            return OrderResult(success=True, order=updated_order)

        except Exception as e:
            error_msg = f"Failed to sync order {client_order_id}: {str(e)}"
            return OrderResult(
                success=False,
                error_message=error_msg,
                error_code="SYNC_FAILED",
                retryable=True
            )

    async def get_order(self, client_order_id: str) -> Optional[Order]:
        """
        Get order by client order ID

        Args:
            client_order_id: Client order ID

        Returns:
            Order object or None
        """
        # Check active orders first
        if client_order_id in self.active_orders:
            return self.active_orders[client_order_id]

        # Check order history
        for order in reversed(self.order_history):
            if order.client_order_id == client_order_id:
                return order

        return None

    def get_active_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Get all active orders

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of active orders
        """
        orders = list(self.active_orders.values())

        if symbol:
            orders = [o for o in orders if o.symbol == symbol]

        return orders

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> List[OrderResult]:
        """
        Cancel all active orders

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of cancellation results
        """
        orders = self.get_active_orders(symbol)
        results = []

        for order in orders:
            result = await self.cancel_order(order.client_order_id)
            results.append(result)

        return results

    async def handle_fill(self, client_order_id: str, fill_price: float, fill_quantity: float) -> Optional[Order]:
        """
        Handle order fill event

        Args:
            client_order_id: Client order ID
            fill_price: Fill price
            fill_quantity: Filled quantity

        Returns:
            Updated order or None
        """
        if client_order_id not in self.active_orders:
            return None

        order = self.active_orders[client_order_id]

        # Update filled quantity
        order.filled_quantity += fill_quantity
        order.avg_fill_price = (
            (order.avg_fill_price * (order.filled_quantity - fill_quantity) + fill_price * fill_quantity)
            / order.filled_quantity
            if order.filled_quantity > 0
            else fill_price
        )

        # Update status
        if order.filled_quantity >= order.quantity:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        order.update_time = datetime.now()

        # Log fill
        self.logger.log_trade_fill(
            strategy_id=order.metadata.get("strategy_id", "unknown"),
            trade_id=f"trade_{int(time.time())}",
            order_id=client_order_id,
            side=order.side,
            price=fill_price,
            quantity=fill_quantity,
            fee=fill_price * fill_quantity * 0.0004,  # 0.04% maker fee
        )

        # Move to history if filled
        if order.status == OrderStatus.FILLED:
            self.order_history.append(order)
            del self.active_orders[client_order_id]

        return order

    # ==================== Simulation methods (TODO: Replace with real API) ====================

    async def _simulate_place_order(self, request: OrderRequest) -> Order:
        """Simulate order placement (replace with real API)"""
        # Simulate API latency
        await asyncio.sleep(0.1)  # 100ms latency

        return Order(
            exchange_order_id=f"EXCH_{int(time.time())}",
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side,
            position_side=request.position_side,
            order_type=request.order_type,
            price=request.price,
            quantity=request.quantity,
            filled_quantity=0.0,
            avg_fill_price=0.0,
            status=OrderStatus.NEW,
            reduce_only=request.reduce_only,
            post_only=request.post_only,
            create_time=datetime.now(),
            update_time=datetime.now(),
            metadata=request.metadata.copy(),
        )

    async def _simulate_cancel_order(self, order: Order) -> Order:
        """Simulate order cancellation (replace with real API)"""
        await asyncio.sleep(0.05)  # 50ms latency

        order.status = OrderStatus.CANCELLED
        order.update_time = datetime.now()

        return order

    async def _simulate_order_status(self, order: Order) -> Order:
        """Simulate order status check (replace with real API)"""
        await asyncio.sleep(0.02)  # 20ms latency

        # In simulation, just return the order as-is
        return order


# Need to import asyncio for simulation methods
import asyncio

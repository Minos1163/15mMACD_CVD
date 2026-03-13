"""
Exchange Adapter - Base class for exchange integration
Based on 接口设计与交易所适配.md documentation

This module provides:
- Abstract base class for exchange adapters
- Unified data models for cross-exchange compatibility
- Error code mapping
- Precision handling
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field

from src.grid_trading.models import (
    SymbolRule,
    MarketTicker,
    Kline,
    AccountBalance,
    Position,
    OrderRequest,
    Order,
    OrderStatus,
    TradeFill,
    OrderSide,
    OrderType,
    ErrorType,
    ExchangeError,
)
from src.grid_trading.structured_logger import get_logger


class ExchangeAdapter(ABC):
    """Abstract base class for exchange adapters"""

    def __init__(self, api_key: str, api_secret: str, testnet: bool = True):
        """
        Initialize exchange adapter

        Args:
            api_key: Exchange API key
            api_secret: Exchange API secret
            testnet: Whether to use testnet
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.logger = get_logger(name=f"ExchangeAdapter_{self.__class__.__name__}")

    @abstractmethod
    async def connect(self) -> bool:
        """
        Connect to exchange

        Returns:
            True if connection successful
        """
        pass

    @abstractmethod
    async def disconnect(self) -> bool:
        """
        Disconnect from exchange

        Returns:
            True if disconnection successful
        """
        pass

    @abstractmethod
    async def get_symbol_rules(self, symbol: str) -> SymbolRule:
        """
        Get symbol trading rules

        Args:
            symbol: Trading pair symbol

        Returns:
            Symbol rule information
        """
        pass

    @abstractmethod
    async def get_ticker(self, symbol: str) -> MarketTicker:
        """
        Get market ticker

        Args:
            symbol: Trading pair symbol

        Returns:
            Market ticker
        """
        pass

    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500
    ) -> List[Kline]:
        """
        Get Kline data

        Args:
            symbol: Trading pair symbol
            interval: Kline interval (e.g., "1m", "5m", "1h")
            limit: Number of Klines to fetch

        Returns:
            List of Kline data
        """
        pass

    @abstractmethod
    async def get_account_balance(self) -> List[AccountBalance]:
        """
        Get account balance

        Returns:
            List of account balances
        """
        pass

    @abstractmethod
    async def get_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Get current positions

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of positions
        """
        pass

    @abstractmethod
    async def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol

        Args:
            symbol: Trading pair symbol
            leverage: Leverage multiplier

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    async def place_order(self, order_request: OrderRequest) -> Order:
        """
        Place an order

        Args:
            order_request: Order request details

        Returns:
            Order information

        Raises:
            ExchangeError: If order placement fails
        """
        pass

    @abstractmethod
    async def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> bool:
        """
        Cancel an order

        Args:
            symbol: Trading pair symbol
            order_id: Exchange order ID
            client_order_id: Client order ID

        Returns:
            True if cancellation successful

        Raises:
            ExchangeError: If cancellation fails
        """
        pass

    @abstractmethod
    async def get_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> Order:
        """
        Get order details

        Args:
            symbol: Trading pair symbol
            order_id: Exchange order ID
            client_order_id: Client order ID

        Returns:
            Order information

        Raises:
            ExchangeError: If order not found
        """
        pass

    @abstractmethod
    async def list_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        List all open orders

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of open orders
        """
        pass

    @abstractmethod
    async def cancel_all_orders(self, symbol: str) -> int:
        """
        Cancel all open orders for a symbol

        Args:
            symbol: Trading pair symbol

        Returns:
            Number of orders cancelled
        """
        pass

    # ==================== Helper Methods ====================

    def normalize_price(self, price: float, symbol_rules: SymbolRule) -> float:
        """
        Normalize price to match exchange precision

        Args:
            price: Original price
            symbol_rules: Symbol trading rules

        Returns:
            Normalized price
        """
        precision = self._count_decimal_places(symbol_rules.price_tick_size)
        return round(price, precision)

    def normalize_quantity(self, quantity: float, symbol_rules: SymbolRule) -> float:
        """
        Normalize quantity to match exchange precision

        Args:
            quantity: Original quantity
            symbol_rules: Symbol trading rules

        Returns:
            Normalized quantity
        """
        precision = self._count_decimal_places(symbol_rules.quantity_step_size)
        return round(quantity, precision)

    def _count_decimal_places(self, value: float) -> int:
        """
        Count decimal places in a value

        Args:
            value: Value to analyze

        Returns:
            Number of decimal places
        """
        str_value = str(value)
        if '.' not in str_value:
            return 0
        return len(str_value.split('.')[1])

    def map_order_status(self, exchange_status: str) -> OrderStatus:
        """
        Map exchange order status to internal status

        Args:
            exchange_status: Exchange-specific status string

        Returns:
            Internal order status
        """
        # Default implementation - override in subclasses
        status_map = {
            "NEW": OrderStatus.NEW,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        return status_map.get(exchange_status, OrderStatus.UNKNOWN)

    def create_exchange_error(
        self,
        message: str,
        error_type: ErrorType,
        raw_code: Optional[int] = None,
        raw_message: Optional[str] = None,
        retryable: bool = True
    ) -> ExchangeError:
        """
        Create standardized exchange error

        Args:
            message: Error message
            error_type: Error type enum
            raw_code: Raw error code from exchange
            raw_message: Raw error message from exchange
            retryable: Whether error is retryable

        Returns:
            Standardized exchange error
        """
        return ExchangeError(
            error_code=error_type.value,
            error_type=error_type,
            message=message,
            retryable=retryable,
            raw_code=raw_code,
            raw_message=raw_message,
            timestamp=datetime.now(),
        )


class WebSocketManager:
    """Base class for WebSocket connection management"""

    def __init__(self, base_url: str):
        """
        Initialize WebSocket manager

        Args:
            base_url: WebSocket base URL
        """
        self.base_url = base_url
        self.connected = False
        self.logger = get_logger(name="WebSocketManager")

    async def connect(self):
        """Connect to WebSocket"""
        # TODO: Implement WebSocket connection
        self.connected = True
        self.logger.log_system_event(event_type="WS_CONNECTED", level="INFO")

    async def disconnect(self):
        """Disconnect from WebSocket"""
        # TODO: Implement WebSocket disconnection
        self.connected = False
        self.logger.log_system_event(event_type="WS_DISCONNECTED", level="INFO")

    async def subscribe_ticker(self, symbol: str, callback):
        """
        Subscribe to ticker updates

        Args:
            symbol: Trading pair symbol
            callback: Callback function for updates
        """
        # TODO: Implement ticker subscription
        pass

    async def subscribe_kline(self, symbol: str, interval: str, callback):
        """
        Subscribe to Kline updates

        Args:
            symbol: Trading pair symbol
            interval: Kline interval
            callback: Callback function for updates
        """
        # TODO: Implement Kline subscription
        pass

    async def subscribe_order_updates(self, symbol: str, callback):
        """
        Subscribe to order updates

        Args:
            symbol: Trading pair symbol
            callback: Callback function for updates
        """
        # TODO: Implement order update subscription
        pass

    async def subscribe_account_updates(self, callback):
        """
        Subscribe to account updates

        Args:
            callback: Callback function for updates
        """
        # TODO: Implement account update subscription
        pass


class StateSyncManager:
    """Base class for state synchronization"""

    def __init__(self, exchange_adapter: ExchangeAdapter):
        """
        Initialize state sync manager

        Args:
            exchange_adapter: Exchange adapter instance
        """
        self.adapter = exchange_adapter
        self.logger = get_logger(name="StateSyncManager")

    async def sync_positions(self, symbol: Optional[str] = None) -> List[Position]:
        """
        Synchronize positions from exchange

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of synchronized positions
        """
        try:
            positions = await self.adapter.get_positions(symbol)
            self.logger.log_state_sync(
                sync_type="POSITIONS",
                object_type="position",
                success=True,
                records_count=len(positions),
            )
            return positions
        except Exception as e:
            self.logger.log_state_sync(
                sync_type="POSITIONS",
                object_type="position",
                success=False,
                records_count=0,
            )
            raise

    async def sync_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """
        Synchronize orders from exchange

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            List of synchronized orders
        """
        try:
            orders = await self.adapter.list_open_orders(symbol)
            self.logger.log_state_sync(
                sync_type="ORDERS",
                object_type="order",
                success=True,
                records_count=len(orders),
            )
            return orders
        except Exception as e:
            self.logger.log_state_sync(
                sync_type="ORDERS",
                object_type="order",
                success=False,
                records_count=0,
            )
            raise

    async def sync_balance(self) -> List[AccountBalance]:
        """
        Synchronize account balance from exchange

        Returns:
            List of synchronized balances
        """
        try:
            balances = await self.adapter.get_account_balance()
            self.logger.log_state_sync(
                sync_type="BALANCE",
                object_type="balance",
                success=True,
                records_count=len(balances),
            )
            return balances
        except Exception as e:
            self.logger.log_state_sync(
                sync_type="BALANCE",
                object_type="balance",
                success=False,
                records_count=0,
            )
            raise

    async def full_sync(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Perform full state synchronization

        Args:
            symbol: Filter by symbol (optional)

        Returns:
            Dictionary with all synchronized data
        """
        return {
            "positions": await self.sync_positions(symbol),
            "orders": await self.sync_orders(symbol),
            "balances": await self.sync_balance(),
        }

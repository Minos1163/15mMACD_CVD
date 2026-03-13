"""Grid trading data models - aligned with 接口设计与交易所适配文档."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any
from datetime import datetime


class GridMode(str, Enum):
    """Grid mode enumeration."""
    NEUTRAL = "neutral"
    LONG = "long"
    SHORT = "short"


class GridType(str, Enum):
    """Grid type enumeration."""
    ARITHMETIC = "arithmetic"
    GEOMETRIC = "geometric"


class OrderSide(str, Enum):
    """Order side enumeration - aligned with 附录A."""
    BUY = "buy"
    SELL = "sell"


class PositionSide(str, Enum):
    """Position side enumeration - aligned with 附录A."""
    LONG = "long"
    SHORT = "short"
    BOTH = "both"


class OrderType(str, Enum):
    """Order type enumeration - aligned with 附录A."""
    LIMIT = "limit"
    MARKET = "market"
    STOP = "stop"
    STOP_MARKET = "stop_market"
    TAKE_PROFIT = "take_profit"
    TAKE_PROFIT_MARKET = "take_profit_market"


class TimeInForce(str, Enum):
    """Time in force enumeration - aligned with 附录A."""
    GTC = "gtc"  # Good Till Cancel
    IOC = "ioc"  # Immediate or Cancel
    FOK = "fok"  # Fill or Kill
    POST_ONLY = "post_only"


class OrderStatus(str, Enum):
    """Order status enumeration - aligned with 10.1 统一内部状态."""
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    PENDING = "pending"
    UNKNOWN = "unknown"


class MarketState(str, Enum):
    """Market state enumeration for AI analysis."""
    RANGE = "range"
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    ABNORMAL = "abnormal"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    """Risk level enumeration."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StrategyStatus(str, Enum):
    """Strategy status enumeration."""
    INIT = "init"
    WAIT_TRIGGER = "wait_trigger"
    RUNNING = "running"
    REBALANCING = "rebalancing"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class ErrorType(str, Enum):
    """Internal error types - aligned with 16.2."""
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    NETWORK_ERROR = "network_error"
    EXCHANGE_TEMP_ERROR = "exchange_temp_error"
    ORDER_REJECTED = "order_rejected"
    ORDER_UNKNOWN = "order_unknown"
    STATE_SYNC_ERROR = "state_sync_error"
    UNSUPPORTED_OPERATION = "unsupported_operation"


# ==================== 统一数据模型 ====================

@dataclass
class SymbolRule:
    """Symbol rule - aligned with 6.1."""
    symbol: str
    market_type: str = "futures"
    price_tick_size: float = 0.0001
    quantity_step_size: float = 1.0
    min_quantity: float = 1.0
    min_notional: float = 5.0
    max_leverage: int = 125
    supported_order_types: list[str] = field(default_factory=lambda: ["limit", "market"])
    supported_time_in_force: list[str] = field(default_factory=lambda: ["gtc", "ioc", "fok"])


@dataclass
class MarketTicker:
    """Market ticker - aligned with 6.2."""
    symbol: str
    last_price: float = 0.0
    mark_price: float = 0.0
    index_price: float = 0.0
    bid_price: float = 0.0
    ask_price: float = 0.0
    timestamp: Optional[datetime] = None


@dataclass
class Kline:
    """Kline data - aligned with 6.3."""
    symbol: str
    interval: str
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class AccountBalance:
    """Account balance - aligned with 6.4."""
    asset: str
    wallet_balance: float = 0.0
    available_balance: float = 0.0
    margin_balance: float = 0.0
    unrealized_pnl: float = 0.0
    update_time: Optional[datetime] = None


@dataclass
class TradeFill:
    """Trade fill - aligned with 6.8."""
    trade_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    price: float = 0.0
    quantity: float = 0.0
    fee: float = 0.0
    fee_asset: str = "USDT"
    fill_time: Optional[datetime] = None


# ==================== 原有模型更新 ====================

@dataclass
class OrderRequest:
    """Order request - aligned with 6.6."""
    client_order_id: str
    symbol: str
    market_type: str = "futures"
    side: OrderSide = OrderSide.BUY
    position_side: PositionSide = PositionSide.BOTH
    order_type: OrderType = OrderType.LIMIT
    price: Optional[float] = None
    quantity: float = 0.0
    time_in_force: TimeInForce = TimeInForce.GTC
    reduce_only: bool = False
    post_only: bool = False
    trigger_price: Optional[float] = None
    strategy_id: Optional[str] = None
    signal_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Order:
    """Order information - aligned with 6.7."""
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    position_side: PositionSide = PositionSide.BOTH
    order_type: OrderType = OrderType.LIMIT
    price: float = 0.0
    quantity: float = 0.0
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    status: OrderStatus = OrderStatus.NEW
    reduce_only: bool = False
    post_only: bool = False
    create_time: Optional[datetime] = None
    update_time: Optional[datetime] = None
    raw_status: Optional[str] = None
    raw_response: dict = field(default_factory=dict)


@dataclass
class Position:
    """Position information - aligned with 6.5."""
    symbol: str = ""
    side: PositionSide = PositionSide.BOTH
    quantity: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: int = 1
    margin_mode: str = "cross"
    liquidation_price: float = 0.0
    update_time: Optional[datetime] = None


@dataclass
class GridLevel:
    """Single grid level definition."""
    level_id: int
    price: float
    quantity: float = 0.0
    order_id: Optional[str] = None
    side: Optional[OrderSide] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    created_at: Optional[datetime] = None


@dataclass
class GridConfig:
    """Grid trading configuration."""
    symbol: str = "DOGEUSDT"
    capital: float = 100.0
    leverage: int = 3
    grid_mode: GridMode = GridMode.NEUTRAL
    grid_type: GridType = GridType.GEOMETRIC
    
    # Price range
    price_lower: float = 0.0
    price_upper: float = 0.0
    entry_trigger_price: float = 0.0
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0
    
    # Grid settings
    grid_count: int = 12
    active_order_count: int = 6
    moving_grid_enabled: bool = False
    
    # AI settings
    ai_enabled: bool = True
    ai_auto_switch_enabled: bool = False
    ai_recalc_interval: int = 300
    mode_switch_cooldown_minutes: int = 60
    risk_score_threshold: int = 70
    
    # Risk settings
    max_drawdown_threshold: float = 0.12
    daily_loss_limit: float = 0.05
    max_consecutive_losses: int = 2
    consecutive_loss_cooldown: int = 1800
    daily_loss_cooldown: int = 28800
    max_margin_usage: float = 0.8
    max_position_ratio: float = 1.0
    max_leverage: int = 20
    high_volatility_threshold: float = 2.5
    force_close_margin_threshold: float = 0.9
    abnormal_market_pause_enabled: bool = True
    pause_cooldown_minutes: int = 60
    
    def validate(self) -> tuple[bool, str]:
        """Validate configuration."""
        if self.capital <= 0:
            return False, "capital must be positive"
        if not 2 <= self.leverage <= 20:
            return False, "leverage must be between 2 and 20"
        if self.grid_count < 2:
            return False, "grid_count must be at least 2"
        if self.active_order_count > self.grid_count:
            return False, "active_order_count cannot exceed grid_count"
        if self.price_lower > 0 and self.price_upper > 0:
            if self.price_lower >= self.price_upper:
                return False, "price_lower must be less than price_upper"
        return True, ""


@dataclass
class AIDecision:
    """AI decision output."""
    market_state: MarketState = MarketState.UNKNOWN
    recommended_mode: GridMode = GridMode.NEUTRAL
    recommended_leverage: int = 3
    recommended_price_lower: float = 0.0
    recommended_price_upper: float = 0.0
    recommended_grid_count: int = 12
    risk_score: int = 50
    confidence: float = 0.5
    reason: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class StrategyState:
    """Current strategy state."""
    status: StrategyStatus = StrategyStatus.INIT
    current_mode: GridMode = GridMode.NEUTRAL
    current_leverage: int = 3
    current_price: float = 0.0
    grid_levels: list[GridLevel] = field(default_factory=list)
    positions: list[Position] = field(default_factory=list)
    total_pnl: float = 0.0
    daily_pnl: float = 0.0
    max_drawdown: float = 0.0
    consecutive_losses: int = 0
    last_mode_switch_time: Optional[datetime] = None
    last_ai_update_time: Optional[datetime] = None
    error_message: Optional[str] = None


@dataclass
class RiskStatus:
    """Risk status information."""
    level: RiskLevel = RiskLevel.LOW
    score: int = 0
    margin_usage: float = 0.0
    drawdown: float = 0.0
    daily_loss: float = 0.0
    consecutive_losses: int = 0
    can_open_position: bool = True
    can_increase_leverage: bool = True
    should_stop: bool = False
    reason: str = ""


@dataclass
class ExchangeError:
    """Exchange error - aligned with 16.2."""
    error_code: str = ""
    error_type: ErrorType = ErrorType.NETWORK_ERROR
    message: str = ""
    retryable: bool = True
    raw_code: Optional[int] = None
    raw_message: Optional[str] = None
    request_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

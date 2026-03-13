"""
Binance WebSocket Manager - 实时数据订阅
基于交易所适配层接口设计文档实现

功能:
1. WebSocket连接管理
2. K线数据流
3. 订单薄数据流
4. 交易数据流
5. 账户数据流
6. 自动重连
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timezone

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, ConnectionClosedError
except ImportError:
    raise ImportError("websockets is required: pip install websockets")

from src.grid_trading.models import OrderInfo, Trade


logger = logging.getLogger(__name__)


class BinanceWebSocketManager:
    """
    Binance WebSocket管理器
    
    支持多订阅、自动重连、心跳检测
    """
    
    # WebSocket端点
    WS_BASE_URL = "wss://fstream.binance.com/ws"
    WS_TESTNET_URL = "wss://stream.binancefuture.com/ws"
    
    # 订阅类型
    STREAM_KLINE = "kline"
    STREAM_DEPTH = "depth"
    STREAM_AGG_TRADE = "aggTrade"
    STREAM_USER_DATA = "userData"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        ping_interval: int = 30,
        ping_timeout: int = 10,
        reconnect_delay: int = 5,
    ):
        """
        初始化WebSocket管理器
        
        Args:
            api_key: API密钥(用于用户数据流)
            api_secret: API密钥(用于用户数据流)
            testnet: 是否使用测试网
            ping_interval: 心跳间隔(秒)
            ping_timeout: 心跳超时(秒)
            reconnect_delay: 重连延迟(秒)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.reconnect_delay = reconnect_delay
        
        # WebSocket连接
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._ws_url = self.WS_TESTNET_URL if testnet else self.WS_BASE_URL
        
        # 订阅管理
        self._streams: Set[str] = set()
        self._subscriptions: Dict[str, List[Callable]] = {}
        
        # 回调函数
        self._on_error: Optional[Callable[[Exception], None]] = None
        self._on_connect: Optional[Callable[[], None]] = None
        self._on_disconnect: Optional[Callable[[], None]] = None
        
        # 状态
        self._running = False
        self._connected = False
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # 用户数据流监听key
        self._listen_key: Optional[str] = None
        self._listen_key_task: Optional[asyncio.Task] = None
    
    def set_error_handler(self, handler: Callable[[Exception], None]) -> None:
        """设置错误处理器"""
        self._on_error = handler
    
    def set_connect_handler(self, handler: Callable[[], None]) -> None:
        """设置连接处理器"""
        self._on_connect = handler
    
    def set_disconnect_handler(self, handler: Callable[[], None]) -> None:
        """设置断开处理器"""
        self._on_disconnect = handler
    
    def subscribe(self, stream: str, callback: Callable[[Dict[str, Any]], None]) -> None:
        """
        订阅数据流
        
        Args:
            stream: 流名称 (例如: "btcusdt@kline_1m")
            callback: 回调函数
        """
        if stream not in self._subscriptions:
            self._subscriptions[stream] = []
        self._subscriptions[stream].append(callback)
        
        if stream not in self._streams:
            self._streams.add(stream)
            logger.info(f"Subscribed to stream: {stream}")
    
    def unsubscribe(self, stream: str, callback: Optional[Callable] = None) -> None:
        """
        取消订阅
        
        Args:
            stream: 流名称
            callback: 回调函数(可选,如果为None则取消该流的所有回调)
        """
        if stream in self._subscriptions:
            if callback:
                self._subscriptions[stream].remove(callback)
                if not self._subscriptions[stream]:
                    del self._subscriptions[stream]
                    self._streams.discard(stream)
            else:
                del self._subscriptions[stream]
                self._streams.discard(stream)
            
            logger.info(f"Unsubscribed from stream: {stream}")
    
    def _subscribe_kline(
        self,
        symbol: str,
        interval: str,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        订阅K线数据
        
        Args:
            symbol: 交易对
            interval: K线间隔 (1m, 5m, 15m, 1h, 4h, 1d)
            callback: 回调函数
        """
        stream = f"{symbol.lower()}@kline_{interval}"
        self.subscribe(stream, callback)
    
    def _subscribe_depth(
        self,
        symbol: str,
        level: int = 20,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        订阅订单薄数据
        
        Args:
            symbol: 交易对
            level: 深度层级 (5, 10, 20)
            callback: 回调函数
        """
        stream = f"{symbol.lower()}@depth{level}"
        self.subscribe(stream, callback)
    
    def _subscribe_agg_trade(
        self,
        symbol: str,
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        订阅聚合交易数据
        
        Args:
            symbol: 交易对
            callback: 回调函数
        """
        stream = f"{symbol.lower()}@aggTrade"
        self.subscribe(stream, callback)
    
    async def _create_listen_key(self) -> str:
        """创建用户数据流监听key"""
        if not self.api_key or not self.api_secret:
            raise ValueError("API key and secret required for user data stream")
        
        # 使用REST API创建listen key
        # 这里简化处理,实际应该使用Binance REST API
        # 暂时返回模拟key
        return "test_listen_key"
    
    async def _keep_listen_key_alive(self) -> None:
        """保持listen key活跃"""
        while self._running:
            await asyncio.sleep(1800)  # 每30分钟刷新一次
            if self._listen_key:
                # 调用REST API刷新listen key
                pass
    
    async def _connect(self) -> None:
        """连接WebSocket"""
        if self._streams:
            streams_str = "/".join(self._streams)
            url = f"{self._ws_url}/{streams_str}"
        else:
            url = f"{self._ws_url}"
        
        logger.info(f"Connecting to WebSocket: {url}")
        
        try:
            self._ws = await websockets.connect(
                url,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout,
            )
            self._connected = True
            
            if self._on_connect:
                self._on_connect()
            
            logger.info("WebSocket connected")
            
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            self._connected = False
            raise
    
    async def _disconnect(self) -> None:
        """断开WebSocket连接"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            finally:
                self._ws = None
                self._connected = False
                
                if self._on_disconnect:
                    self._on_disconnect()
                
                logger.info("WebSocket disconnected")
    
    async def _handle_message(self, message: str) -> None:
        """处理WebSocket消息"""
        try:
            data = json.loads(message)
            
            # 检查错误
            if "error" in data:
                error_msg = data.get("error", {}).get("msg", "Unknown error")
                logger.error(f"WebSocket error: {error_msg}")
                if self._on_error:
                    self._on_error(Exception(error_msg))
                return
            
            # 检查数据流
            stream = data.get("stream", "")
            if not stream:
                logger.warning("Received message without stream")
                return
            
            # 获取回调函数
            callbacks = self._subscriptions.get(stream, [])
            if not callbacks:
                return
            
            # 调用所有回调
            payload = data.get("data", {})
            for callback in callbacks:
                try:
                    await asyncio.create_task(self._call_callback(callback, payload))
                except Exception as e:
                    logger.error(f"Callback error: {e}")
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _call_callback(self, callback: Callable, payload: Dict[str, Any]) -> None:
        """调用回调函数"""
        # 检查是否是协程
        if asyncio.iscoroutinefunction(callback):
            await callback(payload)
        else:
            callback(payload)
    
    async def _receive_loop(self) -> None:
        """接收消息循环"""
        while self._running and self._connected:
            try:
                message = await self._ws.recv()
                await self._handle_message(message)
            except ConnectionClosedError as e:
                logger.warning(f"WebSocket connection closed: {e}")
                self._connected = False
                break
            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                break
    
    async def _reconnect_loop(self) -> None:
        """重连循环"""
        while self._running:
            if not self._connected:
                try:
                    await self._connect()
                    await self._receive_loop()
                except Exception as e:
                    logger.error(f"Connection error: {e}")
                    if self._on_error:
                        self._on_error(e)
                    
                    # 等待后重连
                    await asyncio.sleep(self.reconnect_delay)
            else:
                await asyncio.sleep(1)
    
    async def start(self) -> None:
        """启动WebSocket管理器"""
        if self._running:
            logger.warning("WebSocket manager already running")
            return
        
        self._running = True
        logger.info("Starting WebSocket manager")
        
        # 创建listen key (如果需要)
        if self.api_key and self.api_secret:
            self._listen_key = await self._create_listen_key()
            self._listen_key_task = asyncio.create_task(self._keep_listen_key_alive())
        
        # 启动重连循环
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
    
    async def stop(self) -> None:
        """停止WebSocket管理器"""
        if not self._running:
            return
        
        logger.info("Stopping WebSocket manager")
        self._running = False
        
        # 取消任务
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        
        if self._listen_key_task:
            self._listen_key_task.cancel()
            try:
                await self._listen_key_task
            except asyncio.CancelledError:
                pass
        
        # 断开连接
        await self._disconnect()
        
        logger.info("WebSocket manager stopped")
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected
    
    def get_subscribed_streams(self) -> List[str]:
        """获取已订阅的流列表"""
        return list(self._streams)


# 便捷函数
def create_kline_callback(
    on_kline: Callable[[str, str, float, float, float, float, float], None],
) -> Callable[[Dict[str, Any]], None]:
    """
    创建K线回调函数
    
    Args:
        on_kline: K线处理函数 (symbol, interval, open, high, low, close, volume)
        
    Returns:
        WebSocket回调函数
    """
    def callback(payload: Dict[str, Any]) -> None:
        k = payload.get("k", {})
        if k.get("x", False):  # 只处理已完成的K线
            on_kline(
                symbol=k.get("s", ""),
                interval=k.get("i", ""),
                open=float(k.get("o", 0)),
                high=float(k.get("h", 0)),
                low=float(k.get("l", 0)),
                close=float(k.get("c", 0)),
                volume=float(k.get("v", 0)),
            )
    
    return callback


def create_trade_callback(
    on_trade: Callable[[str, float, float, int, int], None],
) -> Callable[[Dict[str, Any]], None]:
    """
    创建交易回调函数
    
    Args:
        on_trade: 交易处理函数 (symbol, price, quantity, time, trade_id)
        
    Returns:
        WebSocket回调函数
    """
    def callback(payload: Dict[str, Any]) -> None:
        on_trade(
            symbol=payload.get("s", ""),
            price=float(payload.get("p", 0)),
            quantity=float(payload.get("q", 0)),
            time=int(payload.get("T", 0)),
            trade_id=int(payload.get("a", 0)),
        )
    
    return callback


# 导出
__all__ = [
    "BinanceWebSocketManager",
    "create_kline_callback",
    "create_trade_callback",
]

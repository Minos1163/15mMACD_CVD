"""
Binance Exchange Adapter - 期货交易适配器实现
基于交易所适配层接口设计文档实现

功能:
1. REST API封装 (账户、订单、K线、订单薄)
2. 签名和认证
3. 错误处理和重试
4. 限流控制
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

from src.grid_trading.exchange_adapter import (
    ExchangeAdapter,
    OrderInfo,
    AccountInfo,
    Ticker,
    OrderBook,
    Kline,
    Position,
    Balance,
    ExchangeError,
    NetworkError,
)


logger = logging.getLogger(__name__)


class BinanceFuturesAdapter(ExchangeAdapter):
    """
    Binance期货交易所适配器
    
    支持币安合约(USDT-M)
    """
    
    # API端点
    BASE_URL = "https://fapi.binance.com"
    TESTNET_URL = "https://testnet.binancefuture.com"
    
    # API路径
    PATH_SERVER_TIME = "/fapi/v1/time"
    PATH_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
    PATH_ACCOUNT = "/fapi/v2/account"
    PATH_POSITION_RISK = "/fapi/v2/positionRisk"
    PATH_BALANCE = "/fapi/v2/balance"
    PATH_ORDER = "/fapi/v1/order"
    PATH_OPEN_ORDERS = "/fapi/v1/openOrders"
    PATH_ALL_ORDERS = "/fapi/v1/allOrders"
    PATH_CANCEL_ORDER = "/fapi/v1/order"
    PATH_CANCEL_ALL = "/fapi/v1/allOpenOrders"
    PATH_KLINES = "/fapi/v1/klines"
    PATH_DEPTH = "/fapi/v1/depth"
    PATH_TICKER = "/fapi/v1/ticker/price"
    
    # 错误代码
    ERROR_INVALID_SYMBOL = -1121
    ERROR_INVALID_INTERVAL = -1120
    ERROR_TIMESTAMP = -1021
    ERROR_API_KEY = -2014
    ERROR_SIGNATURE = -1022
    ERROR_INSUFFICIENT_BALANCE = -2019
    ERROR_REJECT_NEW_ORDER = -2013
    
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = False,
        timeout: int = 30,
        max_retries: int = 3,
        request_weight_limit: int = 1200,  # 每分钟请求权重
    ):
        """
        初始化Binance适配器
        
        Args:
            api_key: API密钥
            api_secret: API密钥
            testnet: 是否使用测试网
            timeout: 请求超时(秒)
            max_retries: 最大重试次数
            request_weight_limit: 请求权重限制
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_weight_limit = request_weight_limit
        
        # 基础URL
        self.base_url = self.TESTNET_URL if testnet else self.BASE_URL
        
        # 限流追踪
        self._request_weights: List[float] = []
        
        # 会话
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "X-MBX-APIKEY": api_key,
        })
        
        # 时间校准偏移(服务器时间与本地时间的差)
        self._time_offset = 0.0
        self._last_time_sync = 0.0
        
    def _get_timestamp(self) -> int:
        """获取时间戳(毫秒)"""
        return int(time.time() * 1000) + int(self._time_offset)
    
    def _sync_server_time(self) -> None:
        """同步服务器时间"""
        try:
            response = self._request("GET", self.PATH_SERVER_TIME, signed=False)
            server_time = response.get("serverTime", 0)
            local_time = int(time.time() * 1000)
            self._time_offset = server_time - local_time
            self._last_time_sync = time.time()
            logger.info(f"Server time synced, offset: {self._time_offset}ms")
        except Exception as e:
            logger.warning(f"Failed to sync server time: {e}")
    
    def _sign(self, params: Dict[str, Any]) -> str:
        """
        签名参数
        
        Args:
            params: 请求参数
            
        Returns:
            签名字符串
        """
        # 添加时间戳
        params["timestamp"] = self._get_timestamp()
        
        # 按字母顺序排序
        query_string = urlencode(params, doseq=True)
        
        # HMAC SHA256签名
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        
        return signature
    
    def _check_rate_limit(self) -> None:
        """检查请求限流"""
        now = time.time()
        # 移除1分钟前的记录
        self._request_weights = [t for t in self._request_weights if now - t < 60]
        
        if len(self._request_weights) >= self.request_weight_limit:
            # 等待直到有容量
            wait_time = 60 - (now - self._request_weights[0]) + 0.1
            logger.warning(f"Rate limit reached, sleeping {wait_time:.1f}s")
            time.sleep(wait_time)
    
    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        weight: int = 1,
    ) -> Dict[str, Any]:
        """
        发送HTTP请求
        
        Args:
            method: HTTP方法
            path: API路径
            params: 请求参数
            signed: 是否需要签名
            weight: 请求权重
            
        Returns:
            响应数据
        """
        # 定期同步时间
        if time.time() - self._last_time_sync > 300:  # 5分钟
            self._sync_server_time()
        
        # 检查限流
        self._check_rate_limit()
        
        # 准备参数
        if params is None:
            params = {}
        
        url = self.base_url + path
        
        # 签名
        if signed:
            signature = self._sign(params.copy())
            params["signature"] = signature
        
        # 构建查询字符串
        if method == "GET" and params:
            url += "?" + urlencode(params, doseq=True)
        
        # 重试逻辑
        last_error = None
        for attempt in range(self.max_retries):
            try:
                if method == "GET":
                    response = self._session.get(url, timeout=self.timeout)
                elif method == "POST":
                    response = self._session.post(url, json=params, timeout=self.timeout)
                elif method == "DELETE":
                    response = self._session.delete(url, params=params, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported method: {method}")
                
                # 记录请求权重
                self._request_weights.append(time.time())
                
                # 检查响应
                if response.status_code == 200:
                    return response.json()
                
                # 错误处理
                error_data = response.json()
                code = error_data.get("code", response.status_code)
                msg = error_data.get("msg", "Unknown error")
                
                # 特殊错误处理
                if code == self.ERROR_TIMESTAMP:
                    # 时间戳错误,重新同步
                    self._sync_server_time()
                    continue
                elif code == self.ERROR_API_KEY or code == self.ERROR_SIGNATURE:
                    # 认证错误,不重试
                    raise ExchangeError(f"Authentication failed: {msg}")
                elif response.status_code == 429:
                    # 限流,等待后重试
                    retry_after = int(response.headers.get("Retry-After", 1))
                    logger.warning(f"Rate limited, retrying after {retry_after}s")
                    time.sleep(retry_after)
                    continue
                elif response.status_code >= 500:
                    # 服务器错误,重试
                    logger.warning(f"Server error {response.status_code}, retrying...")
                    time.sleep(1 + attempt)
                    continue
                else:
                    # 其他错误
                    raise ExchangeError(f"API error {code}: {msg}")
                    
            except requests.exceptions.Timeout:
                last_error = NetworkError("Request timeout")
                logger.warning(f"Request timeout (attempt {attempt + 1})")
                if attempt < self.max_retries - 1:
                    time.sleep(1 + attempt)
            except requests.exceptions.RequestException as e:
                last_error = NetworkError(f"Request failed: {str(e)}")
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(1 + attempt)
        
        # 所有重试失败
        if last_error:
            raise last_error
        raise ExchangeError("Max retries exceeded")
    
    def _parse_order_info(self, order: Dict[str, Any]) -> OrderInfo:
        """解析订单信息"""
        return OrderInfo(
            order_id=str(order.get("orderId", "")),
            client_order_id=str(order.get("clientOrderId", "")),
            symbol=order.get("symbol", ""),
            side="BUY" if order.get("side") == "BUY" else "SELL",
            order_type=order.get("type", ""),
            status=self._map_order_status(order.get("status", "")),
            price=float(order.get("price", 0)),
            executed_qty=float(order.get("executedQty", 0)),
            cum_qty=float(order.get("cumQty", 0)),
            avg_price=float(order.get("avgPrice", 0)),
            time_in_force=order.get("timeInForce", ""),
            reduce_only=bool(order.get("reduceOnly", False)),
            post_only=bool(order.get("postOnly", False)),
            create_time=int(order.get("time", 0)),
            update_time=int(order.get("updateTime", 0)),
            raw=order,
        )
    
    def _map_order_status(self, status: str) -> str:
        """映射订单状态"""
        mapping = {
            "NEW": "NEW",
            "PARTIALLY_FILLED": "PARTIALLY_FILLED",
            "FILLED": "FILLED",
            "CANCELED": "CANCELED",
            "REJECTED": "REJECTED",
            "EXPIRED": "EXPIRED",
        }
        return mapping.get(status, "UNKNOWN")
    
    # ==================== 账户相关 ====================
    
    def get_account_info(self) -> AccountInfo:
        """获取账户信息"""
        response = self._request("GET", self.PATH_ACCOUNT, signed=True)
        
        balances = []
        for b in response.get("assets", []):
            balances.append(Balance(
                asset=b.get("asset", ""),
                free=float(b.get("availableBalance", 0)),
                locked=float(b.get("balance", 0)) - float(b.get("availableBalance", 0)),
            ))
        
        positions = []
        for p in response.get("positions", []):
            if float(p.get("positionAmt", 0)) != 0:
                positions.append(Position(
                    symbol=p.get("symbol", ""),
                    side="LONG" if float(p.get("positionAmt", 0)) > 0 else "SHORT",
                    size=abs(float(p.get("positionAmt", 0))),
                    entry_price=float(p.get("entryPrice", 0)),
                    mark_price=float(p.get("markPrice", 0)),
                    unrealized_pnl=float(p.get("unrealizedProfit", 0)),
                    percentage=float(p.get("percentage", 0)),
                ))
        
        return AccountInfo(
            account_type="FUTURES",
            total_wallet_balance=float(response.get("totalWalletBalance", 0)),
            total_unrealized_pnl=float(response.get("totalUnrealizedProfit", 0)),
            total_margin_balance=float(response.get("totalMarginBalance", 0)),
            total_position_initial_margin=float(response.get("totalPositionInitialMargin", 0)),
            total_position_margin=float(response.get("totalMaintMargin", 0)),
            total_wallet_balance=float(response.get("totalWalletBalance", 0)),
            balances=balances,
            positions=positions,
        )
    
    def get_balance(self, asset: Optional[str] = None) -> Balance:
        """获取余额"""
        response = self._request("GET", self.PATH_BALANCE, signed=True)
        
        for b in response:
            if asset is None or b.get("asset") == asset:
                return Balance(
                    asset=b.get("asset", ""),
                    free=float(b.get("availableBalance", 0)),
                    locked=float(b.get("balance", 0)) - float(b.get("availableBalance", 0)),
                )
        
        return Balance(asset=asset or "", free=0.0, locked=0.0)
    
    # ==================== 订单相关 ====================
    
    def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        post_only: bool = False,
        client_order_id: Optional[str] = None,
        **kwargs
    ) -> OrderInfo:
        """下单"""
        params = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "timeInForce": time_in_force,
            "reduceOnly": reduce_only,
        }
        
        if price is not None:
            params["price"] = price
        
        if post_only:
            params["postOnly"] = True
        
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        
        # 添加额外参数
        params.update(kwargs)
        
        response = self._request("POST", self.PATH_ORDER, params=params, signed=True)
        return self._parse_order_info(response)
    
    def cancel_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> OrderInfo:
        """撤单"""
        params = {"symbol": symbol}
        
        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("Must provide order_id or client_order_id")
        
        response = self._request("DELETE", self.PATH_CANCEL_ORDER, params=params, signed=True)
        return self._parse_order_info(response)
    
    def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """撤销所有订单"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        response = self._request("DELETE", self.PATH_CANCEL_ALL, params=params, signed=True)
        return len(response) if response else 0
    
    def get_order(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None
    ) -> OrderInfo:
        """查询订单"""
        params = {"symbol": symbol}
        
        if order_id:
            params["orderId"] = order_id
        elif client_order_id:
            params["origClientOrderId"] = client_order_id
        else:
            raise ValueError("Must provide order_id or client_order_id")
        
        response = self._request("GET", self.PATH_ORDER, params=params, signed=True)
        return self._parse_order_info(response)
    
    def get_open_orders(self, symbol: Optional[str] = None) -> List[OrderInfo]:
        """查询挂单"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        response = self._request("GET", self.PATH_OPEN_ORDERS, params=params, signed=True)
        return [self._parse_order_info(o) for o in response]
    
    # ==================== 市场数据 ====================
    
    def get_ticker(self, symbol: str) -> Ticker:
        """获取行情"""
        params = {"symbol": symbol}
        response = self._request("GET", self.PATH_TICKER, params=params, signed=False)
        
        return Ticker(
            symbol=symbol,
            last_price=float(response.get("price", 0)),
            bid_price=float(response.get("bidPrice", 0)),
            ask_price=float(response.get("askPrice", 0)),
            bid_qty=float(response.get("bidQty", 0)),
            ask_qty=float(response.get("askQty", 0)),
            volume=float(response.get("volume", 0)),
            quote_volume=float(response.get("quoteVolume", 0)),
            high=float(response.get("high", 0)),
            low=float(response.get("low", 0)),
            change=float(response.get("change", 0)),
            change_percent=float(response.get("changePercent", 0)),
            timestamp=int(response.get("time", 0)),
        )
    
    def get_orderbook(self, symbol: str, limit: int = 100) -> OrderBook:
        """获取订单薄"""
        params = {"symbol": symbol, "limit": limit}
        response = self._request("GET", self.PATH_DEPTH, params=params, signed=False, weight=5)
        
        bids = [
            (float(bid[0]), float(bid[1]))
            for bid in response.get("bids", [])[:limit]
        ]
        asks = [
            (float(ask[0]), float(ask[1]))
            for ask in response.get("asks", [])[:limit]
        ]
        
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=int(response.get("lastUpdateId", 0)),
        )
    
    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Kline]:
        """获取K线数据"""
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": min(limit, 1000),  # Binance最大1000
        }
        
        if start_time:
            params["startTime"] = start_time
        if end_time:
            params["endTime"] = end_time
        
        response = self._request("GET", self.PATH_KLINES, params=params, signed=False, weight=1)
        
        klines = []
        for k in response:
            klines.append(Kline(
                symbol=symbol,
                interval=interval,
                open_time=int(k[0]),
                close_time=int(k[6]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                quote_volume=float(k[7]),
                trades=int(k[8]),
            ))
        
        return klines
    
    # ==================== 交易对信息 ====================
    
    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """获取交易对信息"""
        params = {}
        if symbol:
            params["symbol"] = symbol
        
        response = self._request("GET", self.PATH_EXCHANGE_INFO, params=params, signed=False, weight=10)
        return response
    
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """获取单个交易对信息"""
        exchange_info = self.get_exchange_info(symbol)
        for s in exchange_info.get("symbols", []):
            if s.get("symbol") == symbol:
                return s
        raise ExchangeError(f"Symbol not found: {symbol}")
    
    # ==================== 关闭 ====================
    
    def close(self) -> None:
        """关闭连接"""
        self._session.close()


# 导出
__all__ = ["BinanceFuturesAdapter"]

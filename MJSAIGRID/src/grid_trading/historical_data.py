"""
历史数据加载器
用于回测引擎的历史数据获取

功能:
1. K线数据加载
2. 订单薄快照加载
3. 数据缓存
4. 数据清洗和验证
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.grid_trading.exchange_adapter import ExchangeAdapter, Kline
from src.grid_trading.binance_adapter import BinanceFuturesAdapter


logger = logging.getLogger(__name__)


class HistoricalDataLoader:
    """
    历史数据加载器
    
    从交易所加载历史数据并缓存
    """
    
    def __init__(
        self,
        exchange: ExchangeAdapter,
        cache_dir: str = "data/cache",
        cache_ttl_hours: int = 24,
    ):
        """
        初始化数据加载器
        
        Args:
            exchange: 交易所适配器
            cache_dir: 缓存目录
            cache_ttl_hours: 缓存有效期(小时)
        """
        self.exchange = exchange
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_hours = cache_ttl_hours
        
        # 创建缓存目录
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 缓存
        self._kline_cache: Dict[str, Tuple[datetime, List[Kline]]] = {}
    
    def _get_cache_key(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        end_time: int,
    ) -> str:
        """获取缓存键"""
        return f"{symbol}_{interval}_{start_time}_{end_time}"
    
    def _get_cache_path(self, cache_key: str) -> Path:
        """获取缓存文件路径"""
        return self.cache_dir / f"{cache_key}.pkl"
    
    def _is_cache_valid(self, cache_path: Path) -> bool:
        """检查缓存是否有效"""
        if not cache_path.exists():
            return False
        
        # 检查文件修改时间
        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)
        age = datetime.now() - mtime
        
        return age < timedelta(hours=self.cache_ttl_hours)
    
    def _load_from_cache(self, cache_key: str) -> Optional[List[Kline]]:
        """从缓存加载数据"""
        cache_path = self._get_cache_path(cache_key)
        
        if not self._is_cache_valid(cache_path):
            return None
        
        try:
            with open(cache_path, "rb") as f:
                klines = pickle.load(f)
            logger.debug(f"Loaded {len(klines)} klines from cache: {cache_key}")
            return klines
        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return None
    
    def _save_to_cache(
        self,
        cache_key: str,
        klines: List[Kline],
    ) -> None:
        """保存数据到缓存"""
        cache_path = self._get_cache_path(cache_key)
        
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(klines, f)
            logger.debug(f"Saved {len(klines)} klines to cache: {cache_key}")
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
    
    def load_klines(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> List[Kline]:
        """
        加载K线数据
        
        Args:
            symbol: 交易对
            interval: K线间隔 (1m, 5m, 15m, 1h, 4h, 1d)
            start_time: 开始时间
            end_time: 结束时间
            use_cache: 是否使用缓存
            
        Returns:
            K线数据列表
        """
        # 默认时间范围: 最近30天
        if start_time is None:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)
        if end_time is None:
            end_time = datetime.now(timezone.utc)
        
        # 转换为毫秒时间戳
        start_ms = int(start_time.timestamp() * 1000)
        end_ms = int(end_time.timestamp() * 1000)
        
        # 检查缓存
        cache_key = self._get_cache_key(symbol, interval, start_ms, end_ms)
        if use_cache:
            cached = self._load_from_cache(cache_key)
            if cached is not None:
                return cached
        
        # 从交易所加载
        logger.info(f"Loading klines from exchange: {symbol} {interval} {start_time} - {end_time}")
        
        klines = []
        current_start = start_ms
        
        while current_start < end_ms:
            try:
                # 单次最多获取1000条
                batch_klines = self.exchange.get_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=1000,
                    start_time=current_start,
                    end_time=end_ms,
                )
                
                if not batch_klines:
                    break
                
                klines.extend(batch_klines)
                
                # 更新起始时间(使用最后一条K线的close_time + 1ms)
                last_kline = batch_klines[-1]
                current_start = last_kline.close_time + 1
                
                # 避免无限循环
                if len(batch_klines) < 1000:
                    break
                
                logger.debug(f"Loaded {len(klines)} klines so far...")
                
            except Exception as e:
                logger.error(f"Error loading klines: {e}")
                break
        
        # 去重
        klines = self._deduplicate_klines(klines)
        
        # 保存缓存
        if use_cache and klines:
            self._save_to_cache(cache_key, klines)
        
        logger.info(f"Loaded {len(klines)} klines total")
        return klines
    
    def _deduplicate_klines(self, klines: List[Kline]) -> List[Kline]:
        """
        去重K线数据
        
        基于open_time去重,保留最新的
        """
        seen = {}
        for kline in klines:
            key = (kline.symbol, kline.interval, kline.open_time)
            seen[key] = kline
        
        return list(seen.values())
    
    def load_klines_to_dataframe(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        加载K线数据到DataFrame
        
        Args:
            symbol: 交易对
            interval: K线间隔
            start_time: 开始时间
            end_time: 结束时间
            use_cache: 是否使用缓存
            
        Returns:
            DataFrame
        """
        klines = self.load_klines(
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            use_cache=use_cache,
        )
        
        if not klines:
            return pd.DataFrame()
        
        # 转换为DataFrame
        data = {
            "open_time": [k.open_time for k in klines],
            "open": [k.open for k in klines],
            "high": [k.high for k in klines],
            "low": [k.low for k in klines],
            "close": [k.close for k in klines],
            "volume": [k.volume for k in klines],
            "close_time": [k.close_time for k in klines],
            "quote_volume": [k.quote_volume for k in klines],
            "trades": [k.trades for k in klines],
        }
        
        df = pd.DataFrame(data)
        df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        
        return df
    
    def validate_klines(self, klines: List[Kline]) -> Tuple[bool, List[str]]:
        """
        验证K线数据质量
        
        Args:
            klines: K线数据列表
            
        Returns:
            (是否有效, 错误列表)
        """
        errors = []
        
        if not klines:
            errors.append("No klines provided")
            return False, errors
        
        # 检查时间顺序
        for i in range(1, len(klines)):
            if klines[i].open_time < klines[i-1].open_time:
                errors.append(f"Kline {i} open_time < previous")
        
        # 检查价格有效性
        for i, k in enumerate(klines):
            if k.high < k.low:
                errors.append(f"Kline {i} high < low")
            if k.high < k.open or k.high < k.close:
                errors.append(f"Kline {i} high < open/close")
            if k.low > k.open or k.low > k.close:
                errors.append(f"Kline {i} low > open/close")
        
        # 检查体积有效性
        for i, k in enumerate(klines):
            if k.volume < 0:
                errors.append(f"Kline {i} negative volume")
        
        # 检查间隔一致性
        if len(klines) >= 2:
            interval_ms = klines[1].open_time - klines[0].open_time
            for i in range(2, len(klines)):
                expected_time = klines[i-1].open_time + interval_ms
                if abs(klines[i].open_time - expected_time) > 1000:  # 允许1秒误差
                    errors.append(f"Kline {i} inconsistent interval")
        
        return len(errors) == 0, errors
    
    def clear_cache(self, symbol: Optional[str] = None) -> int:
        """
        清空缓存
        
        Args:
            symbol: 交易对(可选,如果为None则清空所有)
            
        Returns:
            删除的文件数量
        """
        count = 0
        
        for cache_file in self.cache_dir.glob("*.pkl"):
            if symbol is None or symbol in cache_file.name:
                try:
                    cache_file.unlink()
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to delete cache file {cache_file}: {e}")
        
        logger.info(f"Cleared {count} cache files")
        return count
    
    def get_cache_size(self) -> int:
        """获取缓存大小(字节)"""
        total_size = 0
        for cache_file in self.cache_dir.glob("*.pkl"):
            try:
                total_size += cache_file.stat().st_size
            except Exception:
                pass
        return total_size


class MultiSymbolDataLoader:
    """
    多交易对数据加载器
    
    并行加载多个交易对的历史数据
    """
    
    def __init__(
        self,
        exchange: ExchangeAdapter,
        cache_dir: str = "data/cache",
        cache_ttl_hours: int = 24,
    ):
        """
        初始化多交易对加载器
        
        Args:
            exchange: 交易所适配器
            cache_dir: 缓存目录
            cache_ttl_hours: 缓存有效期
        """
        self.loader = HistoricalDataLoader(
            exchange=exchange,
            cache_dir=cache_dir,
            cache_ttl_hours=cache_ttl_hours,
        )
    
    async def load_multiple(
        self,
        symbols: List[str],
        interval: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        use_cache: bool = True,
    ) -> Dict[str, List[Kline]]:
        """
        并行加载多个交易对的数据
        
        Args:
            symbols: 交易对列表
            interval: K线间隔
            start_time: 开始时间
            end_time: 结束时间
            use_cache: 是否使用缓存
            
        Returns:
            交易对 -> K线数据的字典
        """
        async def load_symbol(symbol: str) -> Tuple[str, List[Kline]]:
            loop = asyncio.get_event_loop()
            klines = await loop.run_in_executor(
                None,
                self.loader.load_klines,
                symbol,
                interval,
                start_time,
                end_time,
                use_cache,
            )
            return symbol, klines
        
        # 并行加载
        tasks = [load_symbol(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks)
        
        return {symbol: klines for symbol, klines in results}


# 导出
__all__ = [
    "HistoricalDataLoader",
    "MultiSymbolDataLoader",
]

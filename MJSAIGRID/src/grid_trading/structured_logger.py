"""
Structured Logger for Grid Trading System
基于文档 13_日志监控与告警方案 实现

实现结构化日志记录,符合文档规范:
- 统一日志字段
- 支持不同日志级别
- 包含trace_id追踪
- 支持日志输出到文件和控制台
"""

import logging
import json
from datetime import datetime
from typing import Optional, Dict, Any
from pathlib import Path

from src.grid_trading.models import (
    StrategyStatus,
    RiskLevel,
    GridMode,
    OrderSide,
    OrderStatus,
    MarketState,
)


class StructuredLogger:
    """结构化日志记录器"""

    def __init__(
        self,
        name: str = "GridTrading",
        log_dir: str = "logs",
        log_level: str = "INFO",
        audit_enabled: bool = True
    ):
        """
        初始化日志记录器

        Args:
            name: 日志名称
            log_dir: 日志目录
            log_level: 日志级别
            audit_enabled: 是否启用审计日志
        """
        self.name = name
        self.log_dir = Path(log_dir)
        self.audit_enabled = audit_enabled

        # 创建日志目录
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 设置日志级别
        level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARN': logging.WARN,
            'ERROR': logging.ERROR,
            'FATAL': logging.CRITICAL
        }
        log_level = level_map.get(log_level.upper(), logging.INFO)

        # 创建主日志记录器
        self.logger = logging.getLogger(name)
        self.logger.setLevel(log_level)

        # 清除已有的处理器
        self.logger.handlers.clear()

        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # 文件处理器
        log_file = self.log_dir / f"{name}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(log_level)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # 审计日志文件
        if audit_enabled:
            audit_file = self.log_dir / "audit.log"
            self.audit_handler = logging.FileHandler(audit_file, encoding='utf-8')
            self.audit_handler.setLevel(logging.INFO)
            self.audit_handler.setFormatter(formatter)
            self.audit_logger = logging.getLogger(f"{name}_audit")
            self.audit_logger.setLevel(logging.INFO)
            self.audit_logger.addHandler(self.audit_handler)
        else:
            self.audit_logger = None

        # 初始化通用上下文
        self.base_context: Dict[str, Any] = {
            'service_name': name,
            'environment': 'test',
            'host': 'localhost',
        }

    def set_context(self, **kwargs):
        """
        设置日志上下文

        Args:
            **kwargs: 上下文字段
        """
        self.base_context.update(kwargs)

    def log_system_event(
        self,
        event_type: str,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录系统运行日志

        Args:
            event_type: 事件类型
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'system',
            'event_type': event_type,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            **self.base_context,
            **kwargs
        }

        message = f"[{event_type}] {json.dumps(log_data, ensure_ascii=False)}"
        self._log(level, message)

    def log_strategy_event(
        self,
        strategy_id: str,
        event_type: str,
        level: str = "INFO",
        symbol: str = "",
        grid_mode: str = "",
        **kwargs
    ):
        """
        记录策略运行日志

        Args:
            strategy_id: 策略ID
            event_type: 事件类型
            level: 日志级别
            symbol: 交易对
            grid_mode: 网格模式
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'strategy',
            'strategy_id': strategy_id,
            'event_type': event_type,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'symbol': symbol,
            'grid_mode': grid_mode,
            **self.base_context,
            **kwargs
        }

        message = f"[STRATEGY:{strategy_id}] {event_type} {json.dumps(log_data, ensure_ascii=False)}"
        self._log(level, message)

    def log_ai_decision(
        self,
        strategy_id: str,
        market_state: MarketState,
        confidence: float,
        risk_score: int,
        recommended_mode: GridMode,
        reason: str,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录AI决策日志

        Args:
            strategy_id: 策略ID
            market_state: 市场状态
            confidence: 置信度
            risk_score: 风险评分
            recommended_mode: 推荐模式
            reason: 决策原因
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'ai_decision',
            'strategy_id': strategy_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'market_state': market_state.value,
            'confidence': confidence,
            'risk_score': risk_score,
            'recommended_mode': recommended_mode.value,
            'reason': reason,
            **self.base_context,
            **kwargs
        }

        message = f"[AI:{strategy_id}] market={market_state.value} confidence={confidence:.2f} risk={risk_score} mode={recommended_mode.value} reason={reason}"
        self._log(level, message)

    def log_risk_event(
        self,
        strategy_id: str,
        risk_level: RiskLevel,
        risk_score: int,
        action: str,
        reason: str,
        level: str = "WARN",
        **kwargs
    ):
        """
        记录风控日志

        Args:
            strategy_id: 策略ID
            risk_level: 风险等级
            risk_score: 风险评分
            action: 风控动作
            reason: 风控原因
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'risk',
            'strategy_id': strategy_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'risk_level': risk_level.value,
            'risk_score': risk_score,
            'action': action,
            'reason': reason,
            **self.base_context,
            **kwargs
        }

        message = f"[RISK:{strategy_id}] level={risk_level.value} score={risk_score} action={action} reason={reason}"
        self._log(level, message)

    def log_order_event(
        self,
        strategy_id: str,
        order_id: str,
        event_type: str,
        side: OrderSide,
        price: float,
        quantity: float,
        status: OrderStatus,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录订单执行日志

        Args:
            strategy_id: 策略ID
            order_id: 订单ID
            event_type: 事件类型
            side: 订单方向
            price: 订单价格
            quantity: 订单数量
            status: 订单状态
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'order',
            'strategy_id': strategy_id,
            'order_id': order_id,
            'event_type': event_type,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'side': side.value,
            'price': price,
            'quantity': quantity,
            'status': status.value,
            **self.base_context,
            **kwargs
        }

        message = f"[ORDER:{order_id}] {event_type} {side.value} {price:.6f} x{quantity:.6f} status={status.value}"
        self._log(level, message)

    def log_trade_fill(
        self,
        strategy_id: str,
        trade_id: str,
        order_id: str,
        side: OrderSide,
        price: float,
        quantity: float,
        fee: float,
        pnl: float = 0.0,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录成交日志

        Args:
            strategy_id: 策略ID
            trade_id: 成交ID
            order_id: 订单ID
            side: 订单方向
            price: 成交价格
            quantity: 成交数量
            fee: 手续费
            pnl: 盈亏
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'trade',
            'strategy_id': strategy_id,
            'trade_id': trade_id,
            'order_id': order_id,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'side': side.value,
            'price': price,
            'quantity': quantity,
            'fee': fee,
            'pnl': pnl,
            **self.base_context,
            **kwargs
        }

        message = f"[TRADE:{trade_id}] {side.value} {price:.6f} x{quantity:.6f} fee={fee:.4f} pnl={pnl:.2f}"
        self._log(level, message)

    def log_audit_event(
        self,
        event_type: str,
        user_or_system: str,
        before: Optional[Dict] = None,
        after: Optional[Dict] = None,
        reason: str = "",
        **kwargs
    ):
        """
        记录审计日志

        Args:
            event_type: 事件类型
            user_or_system: 用户或系统
            before: 变更前状态
            after: 变更后状态
            reason: 变更原因
            **kwargs: 附加字段
        """
        if not self.audit_enabled:
            return

        log_data = {
            'log_type': 'audit',
            'event_type': event_type,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'actor': user_or_system,
            'before': before,
            'after': after,
            'reason': reason,
            **self.base_context,
            **kwargs
        }

        message = f"[AUDIT:{event_type}] actor={user_or_system} reason={reason}"
        self.audit_logger.info(message)

        # 保存详细的审计数据
        audit_file = self.log_dir / "audit_details.jsonl"
        with open(audit_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_data, ensure_ascii=False) + '\n')

    def log_exchange_interaction(
        self,
        api_name: str,
        method: str,
        latency_ms: int,
        status: str,
        error_code: Optional[str] = None,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录交易所交互日志

        Args:
            api_name: API名称
            method: 请求方法
            latency_ms: 延迟(毫秒)
            status: 响应状态
            error_code: 错误码
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'exchange',
            'api_name': api_name,
            'method': method,
            'latency_ms': latency_ms,
            'status': status,
            'error_code': error_code,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            **self.base_context,
            **kwargs
        }

        message = f"[EXCHANGE:{api_name}] {method} {latency_ms}ms status={status}"
        if error_code:
            message += f" error={error_code}"

        self._log(level, message)

    def log_state_sync(
        self,
        sync_type: str,
        object_type: str,
        success: bool,
        records_count: int = 0,
        level: str = "INFO",
        **kwargs
    ):
        """
        记录状态同步日志

        Args:
            sync_type: 同步类型
            object_type: 对象类型
            success: 是否成功
            records_count: 记录数量
            level: 日志级别
            **kwargs: 附加字段
        """
        log_data = {
            'log_type': 'state_sync',
            'sync_type': sync_type,
            'object_type': object_type,
            'success': success,
            'records_count': records_count,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            **self.base_context,
            **kwargs
        }

        status_str = "SUCCESS" if success else "FAILED"
        message = f"[SYNC:{sync_type}] {object_type} {status_str} records={records_count}"
        self._log(level, message)

    def generate_trace_id(self) -> str:
        """
        生成trace_id

        Returns:
            trace_id字符串
        """
        return f"trace_{datetime.now().strftime('%Y%m%d%H%M%S%f')}"

    def _log(self, level: str, message: str):
        """
        内部日志记录方法

        Args:
            level: 日志级别
            message: 日志消息
        """
        level_map = {
            'DEBUG': self.logger.debug,
            'INFO': self.logger.info,
            'WARN': self.logger.warning,
            'ERROR': self.logger.error,
            'FATAL': self.logger.critical
        }

        log_func = level_map.get(level.upper(), self.logger.info)
        log_func(message)


# 创建全局日志记录器实例
_global_logger: Optional[StructuredLogger] = None


def get_logger(
    name: str = "GridTrading",
    log_dir: str = "logs",
    log_level: str = "INFO",
    audit_enabled: bool = True
) -> StructuredLogger:
    """
    获取全局日志记录器

    Args:
        name: 日志名称
        log_dir: 日志目录
        log_level: 日志级别
        audit_enabled: 是否启用审计日志

    Returns:
        结构化日志记录器实例
    """
    global _global_logger

    if _global_logger is None:
        _global_logger = StructuredLogger(
            name=name,
            log_dir=log_dir,
            log_level=log_level,
            audit_enabled=audit_enabled
        )

    return _global_logger

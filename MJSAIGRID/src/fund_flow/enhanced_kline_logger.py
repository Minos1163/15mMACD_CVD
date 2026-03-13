"""
增强K线操作日志记录器
用于完整记录每次操作K线的关键信息,便于调试策略BUG和优化参数
"""

import json
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class DecisionAction(Enum):
    """决策动作类型"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    ADD_POSITION = "ADD_POSITION"
    REDUCE_POSITION = "REDUCE_POSITION"


class MarketRegime(Enum):
    """市场状态"""
    TREND = "TREND"
    RANGE = "RANGE"
    NO_TRADE = "NO_TRADE"


class Direction(Enum):
    """方向"""
    LONG = "LONG"
    SHORT = "SHORT"
    BOTH = "BOTH"
    NONE = "NONE"


@dataclass
class KlineInfo:
    """K线信息"""
    symbol: str
    open_time: int
    close_time: int
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float
    timeframe: str = "5m"
    
    def __str__(self):
        change_pct = ((self.close_price - self.open_price) / self.open_price) * 100
        return (
            f"{self.symbol} | "
            f"O:{self.open_price:.6f} H:{self.high_price:.6f} "
            f"L:{self.low_price:.6f} C:{self.close_price:.6f} | "
            f"Change:{change_pct:+.2f}% | TF:{self.timeframe}"
        )


@dataclass
class AIStrategyInfo:
    """AI策略信息"""
    regime: MarketRegime
    direction: Direction
    signal_long: float
    signal_short: float
    score_15m_long: float
    score_15m_short: float
    score_5m_long: float
    score_5m_short: float
    confidence: float = 0.0
    ai_weights: Optional[Dict[str, float]] = None
    fallback_used: bool = False
    model_version: str = "v1.0"
    
    def __str__(self):
        return (
            f"Regime:{self.regime.value} | "
            f"Dir:{self.direction.value} | "
            f"Signal(L/S):{self.signal_long:.3f}/{self.signal_short:.3f} | "
            f"Score15m(L/S):{self.score_15m_long:.3f}/{self.score_15m_short:.3f} | "
            f"Score5m(L/S):{self.score_5m_long:.3f}/{self.score_5m_short:.3f} | "
            f"Conf:{self.confidence:.2f}"
        )


@dataclass
class RiskControlInfo:
    """风控信息"""
    gate_score: float
    gate_action: str
    risk_level: str = "LOW"
    position_size: float = 0.0
    target_size: float = 0.0
    leverage: int = 1
    margin_used: float = 0.0
    margin_available: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_drawdown: float = 0.0
    consecutive_losses: int = 0
    is_protected: bool = False
    
    def __str__(self):
        return (
            f"Gate:{self.gate_action}({self.gate_score:+.3f}) | "
            f"Level:{self.risk_level} | "
            f"Pos:{self.position_size:.2f}/{self.target_size:.2f} | "
            f"Leverage:{self.leverage}x | "
            f"Margin:{self.margin_used:.2f}/{self.margin_available:.2f} | "
            f"Protected:{self.is_protected}"
        )


@dataclass
class DecisionInfo:
    """决策信息"""
    action: DecisionAction
    reason: str
    hold_attribution: str = ""
    signal_source: str = "signal"
    trigger_type: str = ""
    additional_info: Optional[Dict[str, Any]] = None
    
    def __str__(self):
        return (
            f"Action:{self.action.value} | "
            f"Reason:{self.reason} | "
            f"Source:{self.signal_source} | "
            f"Trigger:{self.trigger_type}"
        )


@dataclass
class KlineOperationLog:
    """K线操作日志"""
    timestamp: datetime
    cycle: int
    mode: str
    kline: KlineInfo
    ai_strategy: AIStrategyInfo
    risk_control: RiskControlInfo
    decision: DecisionInfo
    position_info: Optional[Dict[str, Any]] = None
    performance: Optional[Dict[str, float]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "cycle": self.cycle,
            "mode": self.mode,
            "kline": {
                "symbol": self.kline.symbol,
                "open_time": self.kline.open_time,
                "close_time": self.kline.close_time,
                "open_price": self.kline.open_price,
                "high_price": self.kline.high_price,
                "low_price": self.kline.low_price,
                "close_price": self.kline.close_price,
                "volume": self.kline.volume,
                "timeframe": self.kline.timeframe,
                "change_pct": ((self.kline.close_price - self.kline.open_price) / self.kline.open_price) * 100
            },
            "ai_strategy": {
                "regime": self.ai_strategy.regime.value,
                "direction": self.ai_strategy.direction.value,
                "signal_long": self.ai_strategy.signal_long,
                "signal_short": self.ai_strategy.signal_short,
                "score_15m_long": self.ai_strategy.score_15m_long,
                "score_15m_short": self.ai_strategy.score_15m_short,
                "score_5m_long": self.ai_strategy.score_5m_long,
                "score_5m_short": self.ai_strategy.score_5m_short,
                "confidence": self.ai_strategy.confidence,
                "ai_weights": self.ai_strategy.ai_weights,
                "fallback_used": self.ai_strategy.fallback_used,
                "model_version": self.ai_strategy.model_version
            },
            "risk_control": {
                "gate_score": self.risk_control.gate_score,
                "gate_action": self.risk_control.gate_action,
                "risk_level": self.risk_control.risk_level,
                "position_size": self.risk_control.position_size,
                "target_size": self.risk_control.target_size,
                "leverage": self.risk_control.leverage,
                "margin_used": self.risk_control.margin_used,
                "margin_available": self.risk_control.margin_available,
                "stop_loss": self.risk_control.stop_loss,
                "take_profit": self.risk_control.take_profit,
                "max_drawdown": self.risk_control.max_drawdown,
                "consecutive_losses": self.risk_control.consecutive_losses,
                "is_protected": self.risk_control.is_protected
            },
            "decision": {
                "action": self.decision.action.value,
                "reason": self.decision.reason,
                "hold_attribution": self.decision.hold_attribution,
                "signal_source": self.decision.signal_source,
                "trigger_type": self.decision.trigger_type,
                "additional_info": self.decision.additional_info
            },
            "position_info": self.position_info,
            "performance": self.performance
        }
    
    def to_json(self, indent: int = 2) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def to_console_format(self) -> str:
        """转换为控制台友好的格式"""
        lines = [
            "=" * 100,
            f"📊 K线操作日志 - Cycle {self.cycle} @ {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "=" * 100,
            "",
            "🎯 K线信息:",
            f"   {self.kline}",
            "",
            "🤖 AI策略:",
            f"   {self.ai_strategy}",
            "",
            "🛡️  风控:",
            f"   {self.risk_control}",
            "",
            "📝 决策:",
            f"   {self.decision}",
        ]
        
        if self.ai_strategy.ai_weights:
            lines.append("")
            lines.append("⚖️  AI权重:")
            weights_str = ", ".join([f"{k}:{v:.2f}" for k, v in self.ai_strategy.ai_weights.items()])
            lines.append(f"   {weights_str}")
        
        if self.position_info:
            lines.append("")
            lines.append("💼 持仓:")
            pos_str = ", ".join([f"{k}:{v}" for k, v in self.position_info.items()])
            lines.append(f"   {pos_str}")
        
        if self.performance:
            lines.append("")
            lines.append("📈 性能:")
            perf_str = ", ".join([f"{k}:{v:.2%}" for k, v in self.performance.items()])
            lines.append(f"   {perf_str}")
        
        lines.append("")
        lines.append("=" * 100)
        
        return "\n".join(lines)


class EnhancedKlineLogger:
    """增强K线日志记录器"""
    
    def __init__(self, log_file_path: str = None):
        """
        初始化日志记录器
        
        Args:
            log_file_path: 日志文件路径
        """
        self.log_file_path = log_file_path
        self.logs = []
    
    def log_operation(self, log_entry: KlineOperationLog):
        """
        记录K线操作
        
        Args:
            log_entry: K线操作日志条目
        """
        # 添加到内存
        self.logs.append(log_entry)
        
        # 输出到控制台
        print(log_entry.to_console_format())
        
        # 输出到文件
        if self.log_file_path:
            try:
                with open(self.log_file_path, 'a', encoding='utf-8') as f:
                    f.write(log_entry.to_console_format() + "\n\n")
            except Exception as e:
                print(f"⚠️  写入日志文件失败: {e}")
    
    def log_json(self, log_entry: KlineOperationLog, json_file_path: str = None):
        """
        记录JSON格式的日志
        
        Args:
            log_entry: K线操作日志条目
            json_file_path: JSON文件路径,如果为None则使用默认路径
        """
        if json_file_path is None and self.log_file_path:
            json_file_path = self.log_file_path.replace('.log', '.json')
        
        if json_file_path:
            try:
                with open(json_file_path, 'a', encoding='utf-8') as f:
                    f.write(log_entry.to_json() + "\n")
            except Exception as e:
                print(f"⚠️  写入JSON日志文件失败: {e}")
    
    def get_logs_by_symbol(self, symbol: str) -> list[KlineOperationLog]:
        """
        根据交易对获取日志
        
        Args:
            symbol: 交易对
            
        Returns:
            日志列表
        """
        return [log for log in self.logs if log.kline.symbol == symbol]
    
    def get_logs_by_action(self, action: DecisionAction) -> list[KlineOperationLog]:
        """
        根据动作获取日志
        
        Args:
            action: 决策动作
            
        Returns:
            日志列表
        """
        return [log for log in self.logs if log.decision.action == action]
    
    def get_logs_by_cycle(self, cycle: int) -> Optional[KlineOperationLog]:
        """
        根据周期获取日志
        
        Args:
            cycle: 周期号
            
        Returns:
            日志条目或None
        """
        for log in reversed(self.logs):
            if log.cycle == cycle:
                return log
        return None
    
    def export_to_csv(self, output_path: str):
        """
        导出日志到CSV文件
        
        Args:
            output_path: 输出文件路径
        """
        import csv
        
        if not self.logs:
            print("⚠️  没有日志可导出")
            return
        
        # 准备数据
        fieldnames = [
            'timestamp', 'cycle', 'mode', 'symbol', 'timeframe',
            'open_price', 'high_price', 'low_price', 'close_price', 'volume', 'change_pct',
            'regime', 'direction', 'signal_long', 'signal_short', 'confidence',
            'gate_score', 'gate_action', 'risk_level', 'position_size', 'target_size', 'leverage',
            'action', 'reason', 'signal_source', 'trigger_type'
        ]
        
        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for log in self.logs:
                row = {
                    'timestamp': log.timestamp.isoformat(),
                    'cycle': log.cycle,
                    'mode': log.mode,
                    'symbol': log.kline.symbol,
                    'timeframe': log.kline.timeframe,
                    'open_price': log.kline.open_price,
                    'high_price': log.kline.high_price,
                    'low_price': log.kline.low_price,
                    'close_price': log.kline.close_price,
                    'volume': log.kline.volume,
                    'change_pct': ((log.kline.close_price - log.kline.open_price) / log.kline.open_price) * 100,
                    'regime': log.ai_strategy.regime.value,
                    'direction': log.ai_strategy.direction.value,
                    'signal_long': log.ai_strategy.signal_long,
                    'signal_short': log.ai_strategy.signal_short,
                    'confidence': log.ai_strategy.confidence,
                    'gate_score': log.risk_control.gate_score,
                    'gate_action': log.risk_control.gate_action,
                    'risk_level': log.risk_control.risk_level,
                    'position_size': log.risk_control.position_size,
                    'target_size': log.risk_control.target_size,
                    'leverage': log.risk_control.leverage,
                    'action': log.decision.action.value,
                    'reason': log.decision.reason,
                    'signal_source': log.decision.signal_source,
                    'trigger_type': log.decision.trigger_type
                }
                writer.writerow(row)
        
        print(f"✅ 已导出 {len(self.logs)} 条日志到 {output_path}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取日志统计信息
        
        Returns:
            统计信息字典
        """
        if not self.logs:
            return {}
        
        total_logs = len(self.logs)
        action_counts = {}
        regime_counts = {}
        symbol_counts = {}
        
        for log in self.logs:
            # 统计动作
            action = log.decision.action.value
            action_counts[action] = action_counts.get(action, 0) + 1
            
            # 统计市场状态
            regime = log.ai_strategy.regime.value
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            
            # 统计交易对
            symbol = log.kline.symbol
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
        
        return {
            "total_logs": total_logs,
            "action_distribution": action_counts,
            "regime_distribution": regime_counts,
            "symbol_distribution": symbol_counts,
            "first_timestamp": self.logs[0].timestamp.isoformat(),
            "last_timestamp": self.logs[-1].timestamp.isoformat()
        }


# 便捷函数
def create_kline_log_from_dict(data: Dict[str, Any]) -> KlineOperationLog:
    """
    从字典创建K线操作日志
    
    Args:
        data: 包含日志数据的字典
        
    Returns:
        Kline操作日志对象
    """
    kline = KlineInfo(
        symbol=data['symbol'],
        open_time=data.get('open_time', 0),
        close_time=data.get('close_time', 0),
        open_price=data['open'],
        high_price=data.get('high', data['open']),
        low_price=data.get('low', data['open']),
        close_price=data['close'],
        volume=data.get('volume', 0),
        timeframe=data.get('timeframe', '5m')
    )
    
    ai_strategy = AIStrategyInfo(
        regime=MarketRegime(data.get('regime', 'TREND')),
        direction=Direction(data.get('direction', 'BOTH')),
        signal_long=data.get('signal_long', 0.0),
        signal_short=data.get('signal_short', 0.0),
        score_15m_long=data.get('score_15m_long', 0.0),
        score_15m_short=data.get('score_15m_short', 0.0),
        score_5m_long=data.get('score_5m_long', 0.0),
        score_5m_short=data.get('score_5m_short', 0.0),
        confidence=data.get('confidence', 0.0),
        ai_weights=data.get('ai_weights'),
        fallback_used=data.get('fallback_used', False),
        model_version=data.get('model_version', 'v1.0')
    )
    
    risk_control = RiskControlInfo(
        gate_score=data.get('gate_score', 0.0),
        gate_action=data.get('gate_action', 'HOLD'),
        risk_level=data.get('risk_level', 'LOW'),
        position_size=data.get('position_size', 0.0),
        target_size=data.get('target_size', 0.0),
        leverage=data.get('leverage', 1),
        margin_used=data.get('margin_used', 0.0),
        margin_available=data.get('margin_available', 0.0),
        stop_loss=data.get('stop_loss'),
        take_profit=data.get('take_profit'),
        max_drawdown=data.get('max_drawdown', 0.0),
        consecutive_losses=data.get('consecutive_losses', 0),
        is_protected=data.get('is_protected', False)
    )
    
    decision = DecisionInfo(
        action=DecisionAction(data.get('action', 'HOLD')),
        reason=data.get('reason', ''),
        hold_attribution=data.get('hold_attribution', ''),
        signal_source=data.get('signal_source', 'signal'),
        trigger_type=data.get('trigger_type', ''),
        additional_info=data.get('additional_info')
    )
    
    return KlineOperationLog(
        timestamp=datetime.now(),
        cycle=data.get('cycle', 0),
        mode=data.get('mode', 'MIXED_AI_REVIEW'),
        kline=kline,
        ai_strategy=ai_strategy,
        risk_control=risk_control,
        decision=decision,
        position_info=data.get('position_info'),
        performance=data.get('performance')
    )


if __name__ == "__main__":
    # 测试代码
    logger = EnhancedKlineLogger("test_kline_log.log")
    
    # 创建测试日志
    test_log = KlineOperationLog(
        timestamp=datetime.now(),
        cycle=1,
        mode="MIXED_AI_REVIEW",
        kline=KlineInfo(
            symbol="BTCUSDT",
            open_time=1678886400000,
            close_time=1678886699999,
            open_price=65000.0,
            high_price=65100.0,
            low_price=64900.0,
            close_price=65050.0,
            volume=1000.0,
            timeframe="5m"
        ),
        ai_strategy=AIStrategyInfo(
            regime=MarketRegime.TREND,
            direction=Direction.LONG,
            signal_long=0.85,
            signal_short=0.15,
            score_15m_long=0.82,
            score_15m_short=0.18,
            score_5m_long=0.88,
            score_5m_short=0.12,
            confidence=0.75,
            ai_weights={"cvd": 0.22, "oi_delta": 0.27, "funding": 0.09}
        ),
        risk_control=RiskControlInfo(
            gate_score=0.235,
            gate_action="BUY",
            risk_level="LOW",
            position_size=0.0,
            target_size=0.10,
            leverage=5,
            margin_used=100.0,
            margin_available=900.0,
            is_protected=True
        ),
        decision=DecisionInfo(
            action=DecisionAction.BUY,
            reason="TREND | mode=TREND_STD | long=0.434",
            signal_source="signal",
            trigger_type="MACD_TRIGGER_REQUIRED"
        )
    )
    
    logger.log_operation(test_log)
    logger.log_json(test_log, "test_kline_log.json")
    logger.export_to_csv("test_kline_log.csv")
    
    # 打印统计信息
    stats = logger.get_statistics()
    print("\n📊 日志统计:")
    print(json.dumps(stats, indent=2, ensure_ascii=False))

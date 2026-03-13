"""
告警系统
实现告警规则引擎和通知渠道集成

功能:
1. 告警规则引擎
2. 多通知渠道(邮件、Telegram、企业微信)
3. 告警去重和限流
4. 告警历史记录
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any, Callable, Dict, List, Optional, Set

import requests

from src.grid_trading.models import (
    StrategyState,
    RiskStatus,
    GridMode,
    MarketState,
    RiskLevel,
)


logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """告警"""
    alert_id: str
    rule_name: str
    severity: str  # INFO, WARNING, ERROR, CRITICAL
    title: str
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "alert_id": self.alert_id,
            "rule_name": self.rule_name,
            "severity": self.severity,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "data": self.data,
        }


@dataclass
class AlertRule:
    """告警规则"""
    name: str
    description: str
    severity: str
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        """
        检查规则是否触发
        
        Args:
            strategy_state: 策略状态
            risk_status: 风险状态
            current_price: 当前价格
            
        Returns:
            触发的告警,如果未触发则返回None
        """
        raise NotImplementedError


class HighRiskScoreRule(AlertRule):
    """高风险评分规则"""
    
    def __init__(self, threshold: int = 80):
        super().__init__(
            name="HIGH_RISK_SCORE",
            description=f"风险评分超过{threshold}",
            severity="WARNING",
        )
        self.threshold = threshold
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        if risk_status.risk_score >= self.threshold:
            return Alert(
                alert_id=f"{self.name}_{int(datetime.now().timestamp())}",
                rule_name=self.name,
                severity=self.severity,
                title="高风险评分告警",
                message=f"风险评分达到 {risk_status.risk_score}, 超过阈值 {self.threshold}",
                data={
                    "risk_score": risk_status.risk_score,
                    "threshold": self.threshold,
                    "risk_level": risk_status.risk_level.value,
                },
            )
        return None


class HighMarginUsageRule(AlertRule):
    """高保证金使用率规则"""
    
    def __init__(self, threshold: float = 0.8):
        super().__init__(
            name="HIGH_MARGIN_USAGE",
            description=f"保证金使用率超过{threshold*100}%",
            severity="ERROR",
        )
        self.threshold = threshold
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        if risk_status.margin_usage >= self.threshold:
            return Alert(
                alert_id=f"{self.name}_{int(datetime.now().timestamp())}",
                rule_name=self.name,
                severity=self.severity,
                title="高保证金使用率告警",
                message=f"保证金使用率达到 {risk_status.margin_usage*100:.1%}, 超过阈值 {self.threshold*100:.0%}",
                data={
                    "margin_usage": risk_status.margin_usage,
                    "threshold": self.threshold,
                    "total_balance": risk_status.total_balance,
                    "position_margin": risk_status.position_margin,
                },
            )
        return None


class LargeDrawdownRule(AlertRule):
    """大回撤规则"""
    
    def __init__(self, threshold: float = 0.05):
        super().__init__(
            name="LARGE_DRAWDOWN",
            description=f"回撤超过{threshold*100}%",
            severity="ERROR",
        )
        self.threshold = threshold
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        if strategy_state.max_drawdown >= self.threshold:
            return Alert(
                alert_id=f"{self.name}_{int(datetime.now().timestamp())}",
                rule_name=self.name,
                severity=self.severity,
                title="大回撤告警",
                message=f"最大回撤达到 {strategy_state.max_drawdown*100:.2%}, 超过阈值 {self.threshold*100:.0%}",
                data={
                    "max_drawdown": strategy_state.max_drawdown,
                    "threshold": self.threshold,
                    "current_pnl": strategy_state.total_pnl,
                    "initial_balance": strategy_state.initial_balance,
                },
            )
        return None


class ConsecutiveLossesRule(AlertRule):
    """连续亏损规则"""
    
    def __init__(self, threshold: int = 3):
        super().__init__(
            name="CONSECUTIVE_LOSSES",
            description=f"连续亏损超过{threshold}次",
            severity="WARNING",
        )
        self.threshold = threshold
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        if strategy_state.consecutive_losses >= self.threshold:
            return Alert(
                alert_id=f"{self.name}_{int(datetime.now().timestamp())}",
                rule_name=self.name,
                severity=self.severity,
                title="连续亏损告警",
                message=f"连续亏损 {strategy_state.consecutive_losses} 次, 超过阈值 {self.threshold}",
                data={
                    "consecutive_losses": strategy_state.consecutive_losses,
                    "threshold": self.threshold,
                    "last_pnl": strategy_state.last_pnl,
                },
            )
        return None


class AbnormalMarketStateRule(AlertRule):
    """异常市场状态规则"""
    
    def __init__(self):
        super().__init__(
            name="ABNORMAL_MARKET_STATE",
            description="市场处于异常状态",
            severity="WARNING",
        )
    
    def check(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> Optional[Alert]:
        if strategy_state.market_state == MarketState.ABNORMAL:
            return Alert(
                alert_id=f"{self.name}_{int(datetime.now().timestamp())}",
                rule_name=self.name,
                severity=self.severity,
                title="异常市场状态告警",
                message=f"检测到异常市场状态: {strategy_state.market_state.value}",
                data={
                    "market_state": strategy_state.market_state.value,
                    "current_price": current_price,
                },
            )
        return None


class NotificationChannel:
    """通知渠道基类"""
    
    def send(self, alert: Alert) -> bool:
        """
        发送告警
        
        Args:
            alert: 告警对象
            
        Returns:
            是否发送成功
        """
        raise NotImplementedError


class EmailNotificationChannel(NotificationChannel):
    """邮件通知渠道"""
    
    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        username: str,
        password: str,
        from_email: str,
        to_emails: List[str],
    ):
        """
        初始化邮件通知渠道
        
        Args:
            smtp_server: SMTP服务器
            smtp_port: SMTP端口
            username: 用户名
            password: 密码
            from_email: 发件人邮箱
            to_emails: 收件人邮箱列表
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.to_emails = to_emails
    
    def send(self, alert: Alert) -> bool:
        """发送邮件"""
        try:
            # 创建邮件
            msg = MIMEMultipart()
            msg["From"] = self.from_email
            msg["To"] = ", ".join(self.to_emails)
            msg["Subject"] = f"[{alert.severity}] {alert.title}"
            
            # 邮件正文
            body = f"""
            <html>
            <body>
                <h2>{alert.title}</h2>
                <p><strong>规则:</strong> {alert.rule_name}</p>
                <p><strong>严重程度:</strong> {alert.severity}</p>
                <p><strong>时间:</strong> {alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")}</p>
                <p><strong>消息:</strong></p>
                <p>{alert.message}</p>
                
                <h3>详细信息:</h3>
                <pre>{self._format_data(alert.data)}</pre>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(body, "html"))
            
            # 发送邮件
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)
            
            logger.info(f"Email sent: {alert.alert_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
    
    def _format_data(self, data: Dict[str, Any]) -> str:
        """格式化数据"""
        lines = []
        for key, value in data.items():
            lines.append(f"{key}: {value}")
        return "\n".join(lines)


class TelegramNotificationChannel(NotificationChannel):
    """Telegram通知渠道"""
    
    def __init__(self, bot_token: str, chat_id: str):
        """
        初始化Telegram通知渠道
        
        Args:
            bot_token: Bot token
            chat_id: Chat ID
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send(self, alert: Alert) -> bool:
        """发送Telegram消息"""
        try:
            # 格式化消息
            severity_emoji = {
                "INFO": "ℹ️",
                "WARNING": "⚠️",
                "ERROR": "❌",
                "CRITICAL": "🚨",
            }
            
            emoji = severity_emoji.get(alert.severity, "")
            
            message = f"""
            {emoji} <b>{alert.title}</b>
            
            <b>规则:</b> {alert.rule_name}
            <b>严重程度:</b> {alert.severity}
            <b>时间:</b> {alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")}
            
            <b>消息:</b>
            {alert.message}
            """
            
            # 发送消息
            url = f"{self.api_url}/sendMessage"
            data = {
                "chat_id": self.chat_id,
                "text": message.strip(),
                "parse_mode": "HTML",
            }
            
            response = requests.post(url, json=data, timeout=10)
            response.raise_for_status()
            
            logger.info(f"Telegram message sent: {alert.alert_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False


class WeComNotificationChannel(NotificationChannel):
    """企业微信通知渠道"""
    
    def __init__(self, webhook_url: str):
        """
        初始化企业微信通知渠道
        
        Args:
            webhook_url: Webhook URL
        """
        self.webhook_url = webhook_url
    
    def send(self, alert: Alert) -> bool:
        """发送企业微信消息"""
        try:
            # 格式化消息
            severity_color = {
                "INFO": "info",
                "WARNING": "warning",
                "ERROR": "error",
                "CRITICAL": "critical",
            }
            
            color = severity_color.get(alert.severity, "info")
            
            message = {
                "msgtype": "markdown",
                "markdown": {
                    "content": f"""
                    > {alert.title}
                    > 
                    > **规则:** {alert.rule_name}
                    > **严重程度:** {alert.severity}
                    > **时间:** {alert.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")}
                    > 
                    > **消息:**
                    > {alert.message}
                    """
                },
            }
            
            # 发送消息
            response = requests.post(
                self.webhook_url,
                json=message,
                timeout=10,
            )
            response.raise_for_status()
            
            logger.info(f"WeCom message sent: {alert.alert_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send WeCom message: {e}")
            return False


class AlertEngine:
    """
    告警引擎
    
    管理告警规则和通知渠道
    """
    
    def __init__(
        self,
        cooldown_minutes: int = 10,
        max_alerts_per_hour: int = 60,
    ):
        """
        初始化告警引擎
        
        Args:
            cooldown_minutes: 告警冷却时间(分钟)
            max_alerts_per_hour: 每小时最大告警数
        """
        self.cooldown_minutes = cooldown_minutes
        self.max_alerts_per_hour = max_alerts_per_hour
        
        # 告警规则
        self._rules: List[AlertRule] = []
        
        # 通知渠道
        self._channels: List[NotificationChannel] = []
        
        # 告警历史
        self._alert_history: List[Alert] = []
        self._alert_counter: Dict[str, int] = {}  # 规则计数
        self._alert_timestamps: List[datetime] = []
        
        # 回调函数
        self._on_alert: Optional[Callable[[Alert], None]] = None
    
    def add_rule(self, rule: AlertRule) -> None:
        """添加告警规则"""
        self._rules.append(rule)
        logger.info(f"Added alert rule: {rule.name}")
    
    def add_channel(self, channel: NotificationChannel) -> None:
        """添加通知渠道"""
        self._channels.append(channel)
        logger.info(f"Added notification channel: {type(channel).__name__}")
    
    def set_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """设置告警处理器"""
        self._on_alert = handler
    
    def check_rules(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> List[Alert]:
        """
        检查所有规则
        
        Args:
            strategy_state: 策略状态
            risk_status: 风险状态
            current_price: 当前价格
            
        Returns:
            触发的告警列表
        """
        triggered_alerts = []
        
        for rule in self._rules:
            try:
                alert = rule.check(strategy_state, risk_status, current_price)
                if alert:
                    # 检查冷却时间
                    if self._should_send_alert(alert):
                        triggered_alerts.append(alert)
                        self._process_alert(alert)
            except Exception as e:
                logger.error(f"Error checking rule {rule.name}: {e}")
        
        return triggered_alerts
    
    def _should_send_alert(self, alert: Alert) -> bool:
        """检查是否应该发送告警"""
        # 检查冷却时间
        cooldown_period = timedelta(minutes=self.cooldown_minutes)
        recent_alerts = [
            a for a in self._alert_history
            if a.rule_name == alert.rule_name
            and datetime.now(timezone.utc) - a.timestamp < cooldown_period
        ]
        
        if recent_alerts:
            logger.debug(f"Alert {alert.rule_name} in cooldown, skipping")
            return False
        
        # 检查每小时限制
        one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
        recent_count = len([t for t in self._alert_timestamps if t > one_hour_ago])
        
        if recent_count >= self.max_alerts_per_hour:
            logger.warning(f"Hourly alert limit reached, skipping")
            return False
        
        return True
    
    def _process_alert(self, alert: Alert) -> None:
        """处理告警"""
        # 添加到历史
        self._alert_history.append(alert)
        if len(self._alert_history) > 1000:
            self._alert_history = self._alert_history[-1000:]
        
        # 更新计数
        self._alert_counter[alert.rule_name] = self._alert_counter.get(alert.rule_name, 0) + 1
        
        # 更新时间戳
        self._alert_timestamps.append(alert.timestamp)
        if len(self._alert_timestamps) > 1000:
            self._alert_timestamps = self._alert_timestamps[-1000:]
        
        # 发送通知
        for channel in self._channels:
            try:
                channel.send(alert)
            except Exception as e:
                logger.error(f"Failed to send alert via {type(channel).__name__}: {e}")
        
        # 调用回调
        if self._on_alert:
            self._on_alert(alert)
        
        logger.warning(f"Alert triggered: {alert.rule_name} - {alert.message}")
    
    def get_alert_history(
        self,
        limit: int = 100,
        rule_name: Optional[str] = None,
    ) -> List[Alert]:
        """
        获取告警历史
        
        Args:
            limit: 数量限制
            rule_name: 规则名称过滤
            
        Returns:
            告警列表
        """
        alerts = self._alert_history
        
        if rule_name:
            alerts = [a for a in alerts if a.rule_name == rule_name]
        
        return alerts[-limit:]
    
    def get_alert_stats(self) -> Dict[str, Any]:
        """获取告警统计"""
        stats = {
            "total_alerts": len(self._alert_history),
            "alerts_by_rule": self._alert_counter.copy(),
            "alerts_by_severity": {},
            "recent_24h": 0,
        }
        
        # 按严重程度统计
        for alert in self._alert_history:
            severity = alert.severity
            stats["alerts_by_severity"][severity] = stats["alerts_by_severity"].get(severity, 0) + 1
        
        # 最近24小时
        one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
        stats["recent_24h"] = len([a for a in self._alert_history if a.timestamp > one_day_ago])
        
        return stats


# 导出
__all__ = [
    "Alert",
    "AlertRule",
    "HighRiskScoreRule",
    "HighMarginUsageRule",
    "LargeDrawdownRule",
    "ConsecutiveLossesRule",
    "AbnormalMarketStateRule",
    "NotificationChannel",
    "EmailNotificationChannel",
    "TelegramNotificationChannel",
    "WeComNotificationChannel",
    "AlertEngine",
]

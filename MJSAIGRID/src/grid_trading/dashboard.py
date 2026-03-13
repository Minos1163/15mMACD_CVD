"""
监控仪表盘
实时监控策略运行状态

功能:
1. 实时数据展示(价格、持仓、订单)
2. 策略状态监控(运行模式、风险评分)
3. 性能指标(收益、回撤、胜率)
4. 可视化图表(K线、权益曲线)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    import plotly
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("plotly not installed, dashboard visualization disabled")

from src.grid_trading.models import (
    StrategyState,
    RiskStatus,
    GridMode,
    MarketState,
    Trade,
    Position,
    OrderInfo,
)


logger = logging.getLogger(__name__)


@dataclass
class DashboardMetrics:
    """仪表盘指标"""
    # 策略状态
    strategy_status: str = "STOPPED"
    strategy_mode: str = "NEUTRAL"
    market_state: str = "UNKNOWN"
    
    # 账户信息
    balance: float = 0.0
    position_qty: float = 0.0
    position_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    
    # 风险指标
    risk_level: str = "LOW"
    risk_score: int = 0
    margin_usage: float = 0.0
    
    # 性能指标
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    
    # 市场数据
    current_price: float = 0.0
    price_change_24h: float = 0.0
    volume_24h: float = 0.0
    
    # 订单状态
    pending_orders: int = 0
    active_orders: int = 0
    
    # 时间戳
    last_update: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class Dashboard:
    """
    监控仪表盘
    
    实时展示策略运行状态
    """
    
    def __init__(
        self,
        port: int = 8080,
        update_interval: int = 5,
    ):
        """
        初始化仪表盘
        
        Args:
            port: Web服务端口
            update_interval: 更新间隔(秒)
        """
        self.port = port
        self.update_interval = update_interval
        
        # 数据存储
        self._metrics: DashboardMetrics = DashboardMetrics()
        self._trades: List[Trade] = []
        self._equity_curve: List[Tuple[datetime, float]] = []
        self._price_history: List[Tuple[datetime, float]] = []
        self._kline_data: List[Dict[str, Any]] = []
        
        # 回调函数
        self._on_metrics_update: Optional[Callable[[DashboardMetrics], None]] = None
        self._on_trade: Optional[Callable[[Trade], None]] = None
        
        # 状态
        self._running = False
        self._update_task: Optional[asyncio.Task] = None
    
    def set_metrics_update_handler(
        self,
        handler: Callable[[DashboardMetrics], None],
    ) -> None:
        """设置指标更新处理器"""
        self._on_metrics_update = handler
    
    def set_trade_handler(
        self,
        handler: Callable[[Trade], None],
    ) -> None:
        """设置交易处理器"""
        self._on_trade = handler
    
    def update_metrics(
        self,
        strategy_state: StrategyState,
        risk_status: RiskStatus,
        current_price: float,
    ) -> None:
        """
        更新指标
        
        Args:
            strategy_state: 策略状态
            risk_status: 风险状态
            current_price: 当前价格
        """
        # 更新指标
        self._metrics.strategy_status = "RUNNING" if strategy_state.is_running else "STOPPED"
        self._metrics.strategy_mode = strategy_state.current_mode.value
        self._metrics.market_state = strategy_state.market_state.value
        
        self._metrics.balance = strategy_state.total_balance
        self._metrics.position_qty = strategy_state.current_position
        self._metrics.position_value = strategy_state.position_value
        self._metrics.unrealized_pnl = strategy_state.unrealized_pnl
        self._metrics.realized_pnl = strategy_state.realized_pnl
        self._metrics.total_pnl = strategy_state.total_pnl
        
        self._metrics.risk_level = risk_status.risk_level.value
        self._metrics.risk_score = risk_status.risk_score
        self._metrics.margin_usage = risk_status.margin_usage
        
        self._metrics.current_price = current_price
        self._metrics.last_update = datetime.now(timezone.utc)
        
        # 更新价格历史
        self._price_history.append((datetime.now(timezone.utc), current_price))
        if len(self._price_history) > 1000:
            self._price_history = self._price_history[-1000:]
        
        # 调用回调
        if self._on_metrics_update:
            self._on_metrics_update(self._metrics)
    
    def add_trade(self, trade: Trade) -> None:
        """添加交易记录"""
        self._trades.append(trade)
        
        # 更新权益曲线
        if self._equity_curve:
            last_equity = self._equity_curve[-1][1]
            new_equity = last_equity + trade.quantity * trade.price - trade.commission
            self._equity_curve.append((trade.time, new_equity))
        else:
            self._equity_curve.append((trade.time, self._metrics.balance))
        
        # 调用回调
        if self._on_trade:
            self._on_trade(trade)
    
    def add_kline(self, kline: Dict[str, Any]) -> None:
        """添加K线数据"""
        self._kline_data.append(kline)
        if len(self._kline_data) > 500:
            self._kline_data = self._kline_data[-500:]
    
    def get_metrics(self) -> DashboardMetrics:
        """获取当前指标"""
        return self._metrics
    
    def get_trades(self, limit: int = 100) -> List[Trade]:
        """获取交易记录"""
        return self._trades[-limit:]
    
    def get_equity_curve(self) -> List[Tuple[datetime, float]]:
        """获取权益曲线"""
        return self._equity_curve
    
    def get_price_history(self, limit: int = 100) -> List[Tuple[datetime, float]]:
        """获取价格历史"""
        return self._price_history[-limit:]
    
    def create_kline_chart(self) -> Optional[str]:
        """
        创建K线图表
        
        Returns:
            HTML字符串 (如果plotly可用)
        """
        if not PLOTLY_AVAILABLE or not self._kline_data:
            return None
        
        # 准备数据
        df = pd.DataFrame(self._kline_data)
        
        # 创建子图
        fig = make_subplots(
            rows=2, cols=1,
            shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.7, 0.3],
            subplot_titles=("价格", "成交量"),
        )
        
        # K线图
        fig.add_trace(
            go.Candlestick(
                x=df["datetime"],
                open=df["open"],
                high=df["high"],
                low=df["low"],
                close=df["close"],
                name="K线",
            ),
            row=1, col=1,
        )
        
        # 成交量
        fig.add_trace(
            go.Bar(
                x=df["datetime"],
                y=df["volume"],
                name="成交量",
            ),
            row=2, col=1,
        )
        
        # 更新布局
        fig.update_layout(
            title="K线图表",
            xaxis_rangeslider_visible=False,
            height=600,
            showlegend=False,
        )
        
        # 转换为HTML
        return plotly.io.to_html(fig, include_plotlyjs=True)
    
    def create_equity_chart(self) -> Optional[str]:
        """
        创建权益曲线图表
        
        Returns:
            HTML字符串 (如果plotly可用)
        """
        if not PLOTLY_AVAILABLE or not self._equity_curve:
            return None
        
        # 准备数据
        df = pd.DataFrame(self._equity_curve, columns=["datetime", "equity"])
        
        # 创建图表
        fig = go.Figure()
        
        fig.add_trace(
            go.Scatter(
                x=df["datetime"],
                y=df["equity"],
                mode="lines",
                name="权益",
                line=dict(color="blue", width=2),
            )
        )
        
        # 更新布局
        fig.update_layout(
            title="权益曲线",
            xaxis_title="时间",
            yaxis_title="权益 (USDT)",
            height=400,
            showlegend=False,
        )
        
        # 转换为HTML
        return plotly.io.to_html(fig, include_plotlyjs=True)
    
    def create_performance_summary(self) -> str:
        """创建性能摘要"""
        m = self._metrics
        
        summary = f"""
        <div class="performance-summary">
            <h2>性能摘要</h2>
            <table>
                <tr><td>总盈亏:</td><td>{m.total_pnl:.2f} USDT ({m.total_pnl/m.balance*100:.2f}%)</td></tr>
                <tr><td>已实现盈亏:</td><td>{m.realized_pnl:.2f} USDT</td></tr>
                <tr><td>浮动盈亏:</td><td>{m.unrealized_pnl:.2f} USDT</td></tr>
                <tr><td>最大回撤:</td><td>{m.max_drawdown:.2f} USDT</td></tr>
                <tr><td>胜率:</td><td>{m.win_rate:.2%}</td></tr>
                <tr><td>交易次数:</td><td>{m.total_trades}</td></tr>
                <tr><td>夏普比率:</td><td>{m.sharpe_ratio:.2f}</td></tr>
            </table>
        </div>
        """
        return summary
    
    def create_html_dashboard(self) -> str:
        """
        创建HTML仪表盘
        
        Returns:
            HTML字符串
        """
        m = self._metrics
        
        # K线图表
        kline_chart = self.create_kline_chart() or "<p>暂无K线数据</p>"
        
        # 权益曲线
        equity_chart = self.create_equity_chart() or "<p>暂无权益数据</p>"
        
        # 性能摘要
        performance_summary = self.create_performance_summary()
        
        # HTML模板
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>网格交易监控仪表盘</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 20px;
                    background-color: #f5f5f5;
                }}
                .container {{
                    max-width: 1200px;
                    margin: 0 auto;
                    background-color: white;
                    padding: 20px;
                    border-radius: 10px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .header {{
                    text-align: center;
                    margin-bottom: 30px;
                }}
                .status-bar {{
                    display: flex;
                    justify-content: space-around;
                    margin-bottom: 30px;
                    padding: 15px;
                    background-color: #f0f0f0;
                    border-radius: 5px;
                }}
                .status-item {{
                    text-align: center;
                }}
                .status-label {{
                    font-weight: bold;
                    color: #666;
                }}
                .status-value {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #333;
                }}
                .charts {{
                    margin-top: 30px;
                }}
                .chart-container {{
                    margin-bottom: 30px;
                }}
                .performance-summary table {{
                    width: 100%;
                    border-collapse: collapse;
                }}
                .performance-summary td {{
                    padding: 10px;
                    border-bottom: 1px solid #ddd;
                }}
                .performance-summary tr:last-child td {{
                    border-bottom: none;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>网格交易监控仪表盘</h1>
                    <p>最后更新: {m.last_update.strftime("%Y-%m-%d %H:%M:%S UTC")}</p>
                </div>
                
                <div class="status-bar">
                    <div class="status-item">
                        <div class="status-label">策略状态</div>
                        <div class="status-value">{m.strategy_status}</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">运行模式</div>
                        <div class="status-value">{m.strategy_mode}</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">市场状态</div>
                        <div class="status-value">{m.market_state}</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">风险等级</div>
                        <div class="status-value">{m.risk_level}</div>
                    </div>
                </div>
                
                <div class="status-bar">
                    <div class="status-item">
                        <div class="status-label">账户余额</div>
                        <div class="status-value">{m.balance:.2f} USDT</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">持仓数量</div>
                        <div class="status-value">{m.position_qty:.4f}</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">当前价格</div>
                        <div class="status-value">{m.current_price:.2f} USDT</div>
                    </div>
                    <div class="status-item">
                        <div class="status-label">风险评分</div>
                        <div class="status-value">{m.risk_score}/100</div>
                    </div>
                </div>
                
                {performance_summary}
                
                <div class="charts">
                    <div class="chart-container">
                        {kline_chart}
                    </div>
                    <div class="chart-container">
                        {equity_chart}
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html
    
    def save_html_dashboard(self, filepath: str) -> None:
        """保存HTML仪表盘到文件"""
        html = self.create_html_dashboard()
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"Dashboard saved to {filepath}")
    
    async def start_server(self) -> None:
        """启动Web服务器"""
        if not PLOTLY_AVAILABLE:
            logger.warning("Plotly not available, web server disabled")
            return
        
        try:
            from aiohttp import web
        except ImportError:
            logger.warning("aiohttp not installed, web server disabled")
            return
        
        app = web.Application()
        
        # 添加路由
        app.router.add_get("/", self._handle_dashboard)
        app.router.add_get("/api/metrics", self._handle_metrics)
        app.router.add_get("/api/trades", self._handle_trades)
        
        # 启动服务器
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)
        await site.start()
        
        logger.info(f"Dashboard server started on http://localhost:{self.port}")
        
        self._running = True
    
    async def _handle_dashboard(self, request) -> web.Response:
        """处理仪表盘页面请求"""
        html = self.create_html_dashboard()
        return web.Response(text=html, content_type="text/html")
    
    async def _handle_metrics(self, request) -> web.Response:
        """处理指标API请求"""
        import json
        data = {
            "status": self._metrics.strategy_status,
            "mode": self._metrics.strategy_mode,
            "market_state": self._metrics.market_state,
            "balance": self._metrics.balance,
            "position_qty": self._metrics.position_qty,
            "position_value": self._metrics.position_value,
            "unrealized_pnl": self._metrics.unrealized_pnl,
            "realized_pnl": self._metrics.realized_pnl,
            "total_pnl": self._metrics.total_pnl,
            "risk_level": self._metrics.risk_level,
            "risk_score": self._metrics.risk_score,
            "margin_usage": self._metrics.margin_usage,
            "current_price": self._metrics.current_price,
            "last_update": self._metrics.last_update.isoformat(),
        }
        return web.Response(text=json.dumps(data), content_type="application/json")
    
    async def _handle_trades(self, request) -> web.Response:
        """处理交易API请求"""
        import json
        limit = int(request.query.get("limit", 100))
        trades = self.get_trades(limit)
        
        data = [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "side": t.side,
                "price": t.price,
                "quantity": t.quantity,
                "commission": t.commission,
                "time": t.time.isoformat(),
            }
            for t in trades
        ]
        
        return web.Response(text=json.dumps(data), content_type="application/json")
    
    async def start(self) -> None:
        """启动仪表盘"""
        if self._running:
            logger.warning("Dashboard already running")
            return
        
        await self.start_server()
        
        # 启动更新任务
        self._update_task = asyncio.create_task(self._update_loop())
    
    async def stop(self) -> None:
        """停止仪表盘"""
        if not self._running:
            return
        
        self._running = False
        
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Dashboard stopped")


# 导出
__all__ = [
    "DashboardMetrics",
    "Dashboard",
]

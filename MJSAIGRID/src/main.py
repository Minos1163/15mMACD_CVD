"""Live trading entrypoint for Grid Trading and Fund Flow strategies."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__ or "")))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Import both strategies
from src.app.fund_flow_bot import TradingBot as FundFlowBot

DEFAULT_CONFIG_REL = "config/trading_config_grid.json"
DEFAULT_FUND_FLOW_CONFIG = "config/trading_config_fund_flow.json"
FUND_FLOW_CONFIG_NAMES = ["trading_config_fund_flow.json", "fund_flow"]
MOVING_GRID_MODES = {
    "MOVING_GRID",
    "MOVING_FUTURES_GRID",
    "FUTURES_GRID",
    "DIRECTIONAL_MOVING_GRID",
}


def _resolve_config_path(config_arg: str | None) -> str | None:
    if config_arg is None:
        return None
    return config_arg if os.path.isabs(config_arg) else os.path.join(PROJECT_ROOT, config_arg)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _load_startup_config(config_path: str | None) -> dict:
    if not config_path:
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except Exception:
        return {}
    startup = loaded.get("startup", {})
    return startup if isinstance(startup, dict) else {}


def _confirm_live_launch(
    *,
    enabled: bool,
    confirm_live: bool,
    confirm_token: str,
    skip_tty_prompt: bool,
) -> None:
    if not enabled:
        return

    expected = "LIVE"
    first_confirm = bool(confirm_live) or _env_bool("LIVE_CONFIRM", False)
    if not first_confirm:
        print("BLOCKED: live confirmation enabled, pass --confirm-live (or set LIVE_CONFIRM=1).")
        raise SystemExit(2)

    token = str(confirm_token or os.getenv("LIVE_CONFIRM_TOKEN", "")).strip().upper()
    if not skip_tty_prompt and sys.stdin.isatty():
        typed = input(f"SECOND CONFIRMATION: type {expected} to continue live trading: ").strip().upper()
        if typed != expected:
            print("BLOCKED: second confirmation failed, live start cancelled.")
            raise SystemExit(2)
        return

    if token != expected:
        print(f"BLOCKED: non-interactive mode requires --confirm-token {expected} (or LIVE_CONFIRM_TOKEN={expected}).")
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Grid Trading bot")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help=f"配置文件路径（默认: {DEFAULT_CONFIG_REL} 或 {DEFAULT_FUND_FLOW_CONFIG}）",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        choices=["grid", "fund_flow", "moving_grid"],
        default="grid",
        help="交易策略类型: grid=旧网格运行时, fund_flow=兼容运行时, moving_grid=移动合约网格",
    )
    parser.add_argument("--once", action="store_true", help="仅执行一个周期")
    parser.add_argument(
        "--enable-live-confirmation",
        action="store_true",
        help="启用实盘二次确认（可用 startup.live_confirmation_enabled 或 LIVE_CONFIRMATION_ENABLED 控制）",
    )
    parser.add_argument("--confirm-live", action="store_true", help="实盘确认第一步")
    parser.add_argument("--confirm-token", type=str, default="", help="非交互二次确认令牌（需为 LIVE）")
    parser.add_argument("--skip-tty-prompt", action="store_true", help="跳过终端输入确认（需配合 confirm-token）")
    parser.add_argument("--backtest", action="store_true", help="运行回测模式")
    parser.add_argument("--days", type=int, default=30, help="回测天数（默认30天）")
    parser.add_argument("--interval", type=str, default="1h", help="K线周期（默认1小时）")
    args = parser.parse_args()

    # Determine config path and strategy
    config_path = _resolve_config_path(args.config)
    strategy = args.strategy
    
    # If config path provided, detect strategy from config content first.
    if config_path:
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                mode = str(config_data.get("strategy", {}).get("mode", "") or "").upper()
                if mode in MOVING_GRID_MODES:
                    strategy = "moving_grid"
                elif "grid" in config_data and strategy == "grid":
                    strategy = "grid"
                elif "trading" in config_data:
                    strategy = "fund_flow"
            except Exception:
                pass
        if strategy == "grid":
            config_lower = config_path.lower()
            if any(name in config_lower for name in FUND_FLOW_CONFIG_NAMES):
                strategy = "fund_flow"
    
    # Use default config based on strategy if no config specified
    if not config_path:
        if strategy in {"fund_flow", "moving_grid"}:
            config_path = os.path.join(PROJECT_ROOT, DEFAULT_FUND_FLOW_CONFIG)
        else:
            config_path = os.path.join(PROJECT_ROOT, DEFAULT_CONFIG_REL)

    if config_path and not os.path.exists(config_path):
        print(f"ERROR: config file not found: {config_path}")
        raise FileNotFoundError(config_path)

    startup_cfg = _load_startup_config(config_path)
    cfg_enabled = startup_cfg.get("live_confirmation_enabled")
    live_confirmation_enabled = bool(cfg_enabled) if isinstance(cfg_enabled, bool) else _env_bool("LIVE_CONFIRMATION_ENABLED", False)
    if args.enable_live_confirmation:
        live_confirmation_enabled = True
    _confirm_live_launch(
        enabled=live_confirmation_enabled,
        confirm_live=bool(args.confirm_live),
        confirm_token=str(args.confirm_token or ""),
        skip_tty_prompt=bool(args.skip_tty_prompt),
    )

    # Set environment
    os.environ["BINANCE_DRY_RUN"] = "0"

    if strategy == "grid":
        print(f"GRID TRADING MODE: CONFIG={config_path}")
        
        # Import and initialize Grid Trading Bot
        from src.grid_trading.grid_trading_bot import GridTradingBot
        
        # Check for backtest mode
        if args.backtest:
            print("Running backtest...")
            from src.grid_trading.models import GridConfig, GridMode
            from src.grid_trading.backtest_engine import BacktestDataLoader
            
            # Load config
            with open(config_path, "r", encoding="utf-8") as f:
                config_dict = json.load(f)
            
            grid_cfg = config_dict.get("grid", {})
            config = GridConfig(
                symbol=grid_cfg.get("symbol", "DOGEUSDT"),
                capital=grid_cfg.get("capital", 100),
                leverage=grid_cfg.get("leverage", 3),
                grid_mode=GridMode(grid_cfg.get("grid_mode", "neutral")),
                grid_type=grid_cfg.get("grid_type", "geometric"),
                grid_count=grid_cfg.get("grid_count", 12),
            )
            
            # Run backtest
            results = GridTradingBot.run_backtest(
                config=config,
                symbol=config.symbol,
                interval=args.interval or "1h",
                days=args.days or 30
            )
            
            print("=== Backtest Results ===")
            print(f"Total Return: {results.get('results', {}).get('total_return_pct', 0):.2f}%")
            print(f"Max Drawdown: {results.get('results', {}).get('max_drawdown_pct', 0):.2f}%")
            print(f"Total Trades: {results.get('results', {}).get('total_trades', 0)}")
            print(f"Win Rate: {results.get('results', {}).get('win_rate', 0):.2%}")
            return
        
        # Initialize live trading bot
        bot = GridTradingBot(config_path=config_path)
        
        # Connect to exchange
        if not bot.connect():
            print("Failed to connect to exchange")
            return
        
        # Initialize strategy
        if not bot.initialize():
            print("Failed to initialize strategy")
            return
        
        print("Grid Trading Bot ready")
        
        if args.once:
            result = bot.run_cycle()
            print(f"Cycle result: {result}")
            return
        
        # Start continuous trading
        bot.start()
        
        try:
            print("Press Ctrl+C to stop...")
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            bot.stop()
        
    elif strategy in {"fund_flow", "moving_grid"}:
        print(f"COMPAT RUNTIME MODE: CONFIG={config_path}")

        if args.once:
            bot = FundFlowBot(config_path=config_path)
            bot.run_cycle()
            return

        bot = FundFlowBot(config_path=config_path)
        bot.run()


__all__ = ["FundFlowBot", "main"]


if __name__ == "__main__":
    main()

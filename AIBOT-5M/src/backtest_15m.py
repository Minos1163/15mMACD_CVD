from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__ or '')))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.app.fund_flow_bot import TradingBot
from src.config.config_loader import ConfigLoader
from src.data.market_data import MarketDataManager
from src.fund_flow.decision_engine import FundFlowDecisionEngine
from src.fund_flow.market_ingestion import MarketIngestionService
from src.fund_flow.models import FundFlowDecision, Operation
from src.utils.indicators import calculate_ema, calculate_macd, calculate_rsi

INTERVAL_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
RESAMPLE_RULES = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h"}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_logs_dir() -> Path:
    path = project_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_timestamp(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, utc=True, errors="coerce")
    if parsed.notna().any():
        return parsed
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().any():
        unit = "ms" if float(numeric.dropna().abs().max()) > 10_000_000_000 else "s"
        return pd.to_datetime(numeric, utc=True, unit=unit, errors="coerce")
    return parsed


def normalize_frame(df: pd.DataFrame) -> pd.DataFrame:
    mapping: Dict[str, str] = {}
    for col in df.columns:
        key = str(col).strip().lower()
        if key in {"time", "datetime", "date", "open_time"}:
            mapping[col] = "timestamp"
        elif key in {"o", "open_price"}:
            mapping[col] = "open"
        elif key in {"h", "high_price"}:
            mapping[col] = "high"
        elif key in {"l", "low_price"}:
            mapping[col] = "low"
        elif key in {"c", "close_price", "price"}:
            mapping[col] = "close"
        elif key in {"v", "base_volume"}:
            mapping[col] = "volume"
        else:
            mapping[col] = key
    out = df.rename(columns=mapping).copy()
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [name for name in required if name not in out.columns]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    out["timestamp"] = parse_timestamp(out["timestamp"])
    out = out.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    for col in required[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=required[1:]).copy()
    for col in ("quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "taker_sell_base", "taker_sell_quote", "open_interest", "funding_rate", "spread_bps", "microprice", "mid_price", "micro_delta_norm", "phantom", "trap_score", "depth_ratio", "imbalance", "vpin", "flow_toxicity"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "quote_volume" not in out.columns:
        out["quote_volume"] = out["close"] * out["volume"]
    if "trades" not in out.columns:
        out["trades"] = 0.0
    if "taker_buy_base" not in out.columns:
        out["taker_buy_base"] = out["volume"] * 0.5
    if "taker_buy_quote" not in out.columns:
        out["taker_buy_quote"] = out["quote_volume"] * 0.5
    if "taker_sell_base" not in out.columns:
        out["taker_sell_base"] = out["volume"] - out["taker_buy_base"]
    if "taker_sell_quote" not in out.columns:
        out["taker_sell_quote"] = out["quote_volume"] - out["taker_buy_quote"]
    if "open_interest" not in out.columns:
        out["open_interest"] = out["quote_volume"].rolling(8, min_periods=1).mean()
    if "funding_rate" not in out.columns:
        out["funding_rate"] = 0.0
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    return out.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)


def load_csv(path: str) -> pd.DataFrame:
    return normalize_frame(pd.read_csv(path))


def fetch_futures_15m(symbol: str, start: Optional[str], end: Optional[str], limit: int) -> pd.DataFrame:
    params: Dict[str, Any] = {"symbol": normalize_symbol(symbol), "interval": "15m", "limit": min(max(limit, 1), 1500)}
    if start:
        params["startTime"] = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    if end:
        params["endTime"] = int(pd.Timestamp(end, tz="UTC").timestamp() * 1000)
    resp = requests.get("https://fapi.binance.com/fapi/v1/klines", params=params, timeout=30)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        raise ValueError("no klines returned")
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    return normalize_frame(frame)


class ReplayClient:
    def __init__(self, symbol: str, bars: pd.DataFrame):
        self.symbol = normalize_symbol(symbol)
        self.base = bars.copy().set_index("timestamp", drop=False)
        self.cursor: Optional[pd.Timestamp] = None
        self.cache: Dict[str, pd.DataFrame] = {"15m": self.base}
        if len(self.base) >= 2:
            deltas = self.base["timestamp"].diff().dropna().dt.total_seconds()
            self.base_seconds = int(float(deltas.mode().iloc[0])) if not deltas.empty else 900
        else:
            self.base_seconds = 900

    def set_cursor(self, ts: pd.Timestamp) -> None:
        ts = pd.Timestamp(ts)
        self.cursor = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")

    def frame_for(self, interval: str) -> pd.DataFrame:
        key = str(interval or "").strip().lower()
        if key in self.cache:
            return self.cache[key]
        sec = INTERVAL_SECONDS.get(key, self.base_seconds)
        if sec < self.base_seconds:
            self.cache[key] = self.base
            return self.base
        df = self.base.resample(RESAMPLE_RULES[key], label="right", closed="right").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum", "quote_volume": "sum", "trades": "sum", "taker_buy_base": "sum", "taker_buy_quote": "sum", "taker_sell_base": "sum", "taker_sell_quote": "sum", "open_interest": "last", "funding_rate": "last"})
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp", drop=False)
        self.cache[key] = df
        return df

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[List[Any]]:
        if normalize_symbol(symbol) != self.symbol:
            return []
        frame = self.frame_for(interval)
        if self.cursor is not None:
            frame = frame.loc[frame.index <= self.cursor]
        tail = frame.tail(max(1, int(limit or 1)))
        rows: List[List[Any]] = []
        for row in tail.itertuples(index=False):
            ts_ms = int(pd.Timestamp(row.timestamp).timestamp() * 1000)
            close_ms = ts_ms + INTERVAL_SECONDS.get(str(interval).lower(), self.base_seconds) * 1000 - 1
            rows.append([ts_ms, f"{to_float(row.open):.8f}", f"{to_float(row.high):.8f}", f"{to_float(row.low):.8f}", f"{to_float(row.close):.8f}", f"{to_float(row.volume):.8f}", close_ms, f"{to_float(getattr(row, 'quote_volume', 0.0)):.8f}", int(to_float(getattr(row, 'trades', 0.0))), f"{to_float(getattr(row, 'taker_buy_base', 0.0)):.8f}", f"{to_float(getattr(row, 'taker_buy_quote', 0.0)):.8f}", "0"])
        return rows


@dataclass
class Position:
    side: str
    qty: float
    entry_price: float
    leverage: int
    opened_at: pd.Timestamp
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None


class Backtest15mRunner:
    def __init__(self, *, symbol: str, bars_15m: pd.DataFrame, config_path: str, initial_capital: float = 10000.0, fee_rate: float = 0.0004) -> None:
        self.symbol = normalize_symbol(symbol)
        self.bars = bars_15m.reset_index(drop=True).copy()
        self.config_path = config_path
        self.config = ConfigLoader.load_trading_config(config_path)
        self.initial_capital = float(initial_capital)
        self.capital = float(initial_capital)
        self.fee_rate = max(0.0, float(fee_rate))
        self.position: Optional[Position] = None
        self.client = ReplayClient(self.symbol, self.bars)
        self.market_data = MarketDataManager(self.client)
        ff_cfg = self.config.get("fund_flow", {}) if isinstance(self.config.get("fund_flow"), dict) else {}
        self.ingestion = MarketIngestionService(window_seconds=max(15, int(to_float(ff_cfg.get("aggregation_window_seconds"), 15))), timeframes=ff_cfg.get("metric_timeframes"), range_quantile_config=ff_cfg.get("range_quantile"))
        self.engine = FundFlowDecisionEngine(self.config)
        self.bot = TradingBot.__new__(TradingBot)
        self.bot.config = self.config
        self.bot.client = self.client
        self.bot._prev_open_interest = {}
        self.bot._liquidity_ema_notional = {}
        self.confluence_cfg = TradingBot._ma10_macd_confluence_config(self.bot)
        self.regime_tf = str((ff_cfg.get("regime", {}) or {}).get("timeframe", ff_cfg.get("decision_timeframe", "15m"))).strip().lower()
        if self.regime_tf not in INTERVAL_SECONDS:
            self.regime_tf = "15m"
        self.last_ret = 0.0
        self.kline_rows: List[Dict[str, Any]] = []
        self.trade_rows: List[Dict[str, Any]] = []

    def equity(self, mark_price: float) -> float:
        if self.position is None:
            return self.capital
        return self.capital + self.position_pnl(self.position, mark_price, self.position.qty)

    @staticmethod
    def position_pnl(position: Position, exit_price: float, qty: float) -> float:
        return (exit_price - position.entry_price) * qty if position.side == "LONG" else (position.entry_price - exit_price) * qty

    def portfolio(self, mark_price: float) -> Dict[str, Any]:
        positions = {}
        if self.position is not None:
            positions[self.symbol] = {"side": self.position.side, "amount": self.position.qty, "entry_price": self.position.entry_price, "leverage": self.position.leverage}
        return {"cash": self.capital, "positions": positions, "total_assets": self.equity(mark_price)}

    def realtime_features(self, idx: int) -> Dict[str, Any]:
        row = self.bars.iloc[idx]
        close = to_float(row["close"])
        prev_close = to_float(self.bars.iloc[idx - 1]["close"], close) if idx > 0 else close
        ret_15m = ((close - prev_close) / prev_close) if prev_close > 0 else 0.0
        prev_24h_close = to_float(self.bars.iloc[max(0, idx - 96)]["close"], close)
        change_24h = ((close - prev_24h_close) / prev_24h_close) * 100.0 if prev_24h_close > 0 else 0.0
        quote_volume = to_float(row.get("quote_volume"), close * to_float(row.get("volume"), 0.0))
        prev_quote_volume = to_float(self.bars.iloc[idx - 1].get("quote_volume"), quote_volume) if idx > 0 else quote_volume
        candle_body = close - to_float(row["open"])
        range_size = max(to_float(row["high"]) - to_float(row["low"]), close * 1e-6, 1e-9)
        imbalance = to_float(row.get("imbalance"), max(-1.0, min(1.0, candle_body / range_size)))
        depth_ratio = to_float(row.get("depth_ratio"), max(0.2, min(5.0, quote_volume / max(prev_quote_volume, 1.0))))
        orderflow = MarketDataManager.extract_order_flow_metrics_from_klines(self.client.get_klines(self.symbol, self.confluence_cfg.get("tf_exec", "15m"), limit=12))
        cvd_ratio = to_float(orderflow.get("orderflow_cvd_ratio"), ret_15m)
        cvd_momentum = to_float(orderflow.get("orderflow_cvd_momentum"), ret_15m - self.last_ret)
        self.last_ret = ret_15m
        spread_bps = to_float(row.get("spread_bps"), 0.0)
        if spread_bps <= 0.0:
            spread_bps = min(10.0, abs(candle_body) / max(abs(close), 1e-9) * 1000.0)
        mid_price = to_float(row.get("mid_price"), close)
        microprice = to_float(row.get("microprice"), close)
        micro_delta_norm = to_float(row.get("micro_delta_norm"), (microprice - mid_price) / max(abs(mid_price), 1e-9))
        return {
            "price": close,
            "change_15m": ret_15m * 100.0,
            "change_24h": change_24h,
            "funding_rate": to_float(row.get("funding_rate"), 0.0),
            "open_interest": to_float(row.get("open_interest"), quote_volume),
            "quote_volume": quote_volume,
            "taker_buy_quote": to_float(row.get("taker_buy_quote"), orderflow.get("taker_buy_quote", quote_volume * 0.5)),
            "taker_sell_quote": to_float(row.get("taker_sell_quote"), orderflow.get("taker_sell_quote", quote_volume * 0.5)),
            "taker_delta_quote": to_float(orderflow.get("taker_delta_quote"), 0.0),
            "orderflow_cvd_quote": to_float(orderflow.get("orderflow_cvd_quote"), 0.0),
            "orderflow_cvd_ratio": cvd_ratio,
            "orderflow_cvd_momentum": cvd_momentum,
            "trade_imbalance": to_float(orderflow.get("trade_imbalance"), imbalance),
            "volume_imbalance": to_float(orderflow.get("volume_imbalance"), abs(imbalance)),
            "vpin": to_float(row.get("vpin"), orderflow.get("vpin", 0.0)),
            "flow_toxicity": to_float(row.get("flow_toxicity"), orderflow.get("flow_toxicity", 0.0)),
            "depth_ratio": depth_ratio,
            "imbalance": imbalance,
            "mid_price": mid_price,
            "microprice": microprice,
            "micro_delta_norm": micro_delta_norm,
            "spread_bps": spread_bps / 10000.0 if spread_bps > 1.0 else spread_bps,
            "phantom": to_float(row.get("phantom"), 0.0),
            "trap_score": to_float(row.get("trap_score"), 0.0),
            "ob_delta_notional": quote_volume - prev_quote_volume,
            "ob_total_notional": max(quote_volume, 1.0),
        }

    def build_flow_context(self, idx: int) -> Dict[str, Any]:
        ts = pd.Timestamp(self.bars.iloc[idx]["timestamp"])
        ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        self.client.set_cursor(ts)
        trend_filter = self.market_data.get_trend_filter_metrics(self.symbol, interval=self.regime_tf, limit=120) or {}
        market_data = {"realtime": self.realtime_features(idx), "trend_filter": trend_filter, "trend_filter_timeframe": self.regime_tf, "trend_filter_1m": {}, "order_flow_1m": {}, "execution_quality_timeframe": self.regime_tf}
        raw_flow = TradingBot._build_fund_flow_context(self.bot, self.symbol, market_data)
        snapshot = self.ingestion.aggregate_from_metrics(self.symbol, raw_flow, ts=ts.to_pydatetime())
        flow_context = TradingBot._apply_timeframe_context(self.bot, raw_flow, snapshot)
        flow_context["fund_flow_features"] = dict(snapshot.fund_flow_features or {})
        flow_context["microstructure_features"] = dict(snapshot.microstructure_features or {})
        tf_15m = flow_context.get("timeframes", {}).get("15m")
        if isinstance(tf_15m, dict):
            flow_context["timeframes"].setdefault("5m", dict(tf_15m))
        confluence = TradingBot._compute_ma10_macd_confluence(self.bot, self.symbol, self.confluence_cfg)
        TradingBot._inject_confluence_into_flow_context(self.bot, flow_context, confluence, self.confluence_cfg)
        return flow_context

    def apply_slippage(self, price: float, side: str, is_open: bool) -> float:
        slip = self.engine.entry_slippage
        if side == "LONG":
            return price * (1.0 + slip) if is_open else price * (1.0 - slip)
        return price * (1.0 - slip) if is_open else price * (1.0 + slip)

    def close_position(self, ts: pd.Timestamp, exit_price: float, reason: str, qty_ratio: float = 1.0) -> None:
        if self.position is None:
            return
        ratio = max(0.0, min(1.0, float(qty_ratio)))
        if ratio <= 0.0:
            return
        close_qty = self.position.qty * ratio
        pnl = self.position_pnl(self.position, exit_price, close_qty)
        fee = exit_price * close_qty * self.fee_rate
        self.capital += pnl - fee
        pnl_pct = pnl / max(abs(self.position.entry_price * close_qty), 1e-9) * 100.0
        self.trade_rows.append({"symbol": self.symbol, "direction": self.position.side, "entry_time": self.position.opened_at.isoformat(), "exit_time": ts.isoformat(), "entry_price": round(self.position.entry_price, 8), "exit_price": round(exit_price, 8), "qty": round(close_qty, 8), "pnl": round(pnl - fee, 8), "pnl_pct": round(pnl_pct, 4), "reason": reason})
        remain = self.position.qty - close_qty
        if remain <= 1e-12:
            self.position = None
        else:
            self.position.qty = remain

    def open_or_add(self, ts: pd.Timestamp, decision: FundFlowDecision, price: float) -> None:
        portion = max(0.0, float(decision.target_portion_of_balance or 0.0))
        if portion <= 0.0:
            return
        side = "LONG" if decision.operation == Operation.BUY else "SHORT"
        entry_price = self.apply_slippage(price, side, True)
        leverage = max(1, int(decision.leverage or 1))
        qty = max(0.0, self.equity(price) * portion * leverage / max(entry_price, 1e-9))
        if qty <= 0.0:
            return
        self.capital -= entry_price * qty * self.fee_rate
        if self.position is None:
            self.position = Position(side=side, qty=qty, entry_price=entry_price, leverage=leverage, opened_at=ts, take_profit_price=decision.take_profit_price, stop_loss_price=decision.stop_loss_price)
            return
        if self.position.side != side:
            return
        new_qty = self.position.qty + qty
        self.position.entry_price = (self.position.entry_price * self.position.qty + entry_price * qty) / max(new_qty, 1e-9)
        self.position.qty = new_qty
        self.position.leverage = max(self.position.leverage, leverage)
        if decision.take_profit_price:
            self.position.take_profit_price = decision.take_profit_price
        if decision.stop_loss_price:
            self.position.stop_loss_price = decision.stop_loss_price

    def hit_protection(self, ts: pd.Timestamp, high: float, low: float) -> bool:
        if self.position is None:
            return False
        stop_price = self.position.stop_loss_price
        tp_price = self.position.take_profit_price
        if self.position.side == "LONG":
            if stop_price and low <= stop_price:
                self.close_position(ts, stop_price, "STOP_LOSS")
                return True
            if tp_price and high >= tp_price:
                self.close_position(ts, tp_price, "TAKE_PROFIT")
                return True
        else:
            if stop_price and high >= stop_price:
                self.close_position(ts, stop_price, "STOP_LOSS")
                return True
            if tp_price and low <= tp_price:
                self.close_position(ts, tp_price, "TAKE_PROFIT")
                return True
        return False

    def append_bar(self, row: pd.Series, decision: Optional[FundFlowDecision], label: str = "") -> None:
        close_series = self.bars.loc[: row.name, "close"]
        rsi = calculate_rsi(close_series, period=14)
        _, _, macd_hist = calculate_macd(close_series, fast=12, slow=26, signal=9)
        ema_5 = calculate_ema(close_series, period=5)
        ema_20 = calculate_ema(close_series, period=20)
        md = decision.metadata if decision and isinstance(decision.metadata, dict) else {}
        final_score = md.get("final_score", {}) if isinstance(md.get("final_score"), dict) else {}
        self.kline_rows.append({"timestamp": pd.Timestamp(row["timestamp"]).isoformat(), "open": round(to_float(row["open"]), 8), "high": round(to_float(row["high"]), 8), "low": round(to_float(row["low"]), 8), "close": round(to_float(row["close"]), 8), "volume": round(to_float(row["volume"]), 8), "capital": round(self.equity(to_float(row["close"])), 8), "position_side": self.position.side if self.position else "", "position_qty": round(self.position.qty, 8) if self.position else 0.0, "decision": label or (decision.operation.value if decision else "hold"), "reason": decision.reason if decision else "", "rsi": round(to_float(rsi, 50.0), 6), "macd_hist": round(to_float(macd_hist, 0.0), 8), "ema_5": round(to_float(ema_5, to_float(row["close"])), 8), "ema_20": round(to_float(ema_20, to_float(row["close"])), 8), "change_pct": round((to_float(row["close"]) - to_float(row["open"])) / max(to_float(row["open"]), 1e-9) * 100.0, 6), "long_score": round(to_float(md.get("long_score"), to_float(final_score.get("long_score"), 0.0)), 6), "short_score": round(to_float(md.get("short_score"), to_float(final_score.get("short_score"), 0.0)), 6)})

    def run(self) -> Dict[str, Any]:
        warmup_bars = max(120, int(self.confluence_cfg.get("kline_limit_exec", 160)))
        for idx, row in self.bars.iterrows():
            ts = pd.Timestamp(row["timestamp"])
            ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            if idx < warmup_bars:
                self.append_bar(row, None, "warmup")
                continue
            if self.hit_protection(ts, to_float(row["high"]), to_float(row["low"])):
                self.append_bar(row, None, "protection_exit")
                continue
            flow_context = self.build_flow_context(idx)
            decision = self.engine.decide(symbol=self.symbol, portfolio=self.portfolio(to_float(row["close"])), price=to_float(row["close"]), market_flow_context=flow_context, trigger_context={"trigger_type": "backtest", "allow_entry_window": True}, use_weight_router=False, use_ai_weights=False)
            decision = TradingBot._apply_ma10_macd_entry_filter(self.bot, self.symbol, decision)
            if self.position is None:
                if decision.operation in (Operation.BUY, Operation.SELL):
                    self.open_or_add(ts, decision, to_float(row["close"]))
            else:
                if decision.operation == Operation.CLOSE:
                    ratio = float(decision.target_portion_of_balance or 1.0)
                    self.close_position(ts, self.apply_slippage(to_float(row["close"]), self.position.side, False), decision.reason or "CLOSE_SIGNAL", 1.0 if ratio <= 0 else min(1.0, ratio))
                elif decision.operation in (Operation.BUY, Operation.SELL):
                    desired_side = "LONG" if decision.operation == Operation.BUY else "SHORT"
                    if self.position.side == desired_side:
                        self.open_or_add(ts, decision, to_float(row["close"]))
            self.append_bar(row, decision)
        if self.position is not None:
            last = self.bars.iloc[-1]
            ts = pd.Timestamp(last["timestamp"])
            ts = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
            self.close_position(ts, self.apply_slippage(to_float(last["close"]), self.position.side, False), "END_OF_BACKTEST", 1.0)
        capital_curve = pd.Series([to_float(item.get("capital"), self.initial_capital) for item in self.kline_rows], dtype=float)
        drawdown = ((capital_curve - capital_curve.cummax()) / capital_curve.cummax().replace(0, pd.NA)).fillna(0.0)
        wins = [row for row in self.trade_rows if to_float(row.get("pnl"), 0.0) > 0]
        losses = [row for row in self.trade_rows if to_float(row.get("pnl"), 0.0) <= 0]
        gross_profit = sum(to_float(row.get("pnl"), 0.0) for row in wins)
        gross_loss = abs(sum(to_float(row.get("pnl"), 0.0) for row in losses))
        final_capital = float(capital_curve.iloc[-1]) if not capital_curve.empty else self.initial_capital
        return {"symbol": self.symbol, "bars": len(self.bars), "warmup_bars": warmup_bars, "initial_capital": round(self.initial_capital, 8), "final_capital": round(final_capital, 8), "total_return_pct": round(((final_capital / self.initial_capital) - 1.0) * 100.0, 4), "max_drawdown_pct": round(abs(float(drawdown.min())) * 100.0 if not drawdown.empty else 0.0, 4), "trade_count": len(self.trade_rows), "win_rate_pct": round((len(wins) / len(self.trade_rows) * 100.0) if self.trade_rows else 0.0, 4), "profit_factor": round((gross_profit / gross_loss) if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0), 4), "config_path": self.config_path}

    def write_outputs(self, summary: Dict[str, Any], output_dir: Optional[str] = None) -> Dict[str, str]:
        root = Path(output_dir) if output_dir else ensure_logs_dir()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = f"{self.symbol}_{stamp}"
        kline_path = root / f"backtest_klines_{base}.csv"
        trades_path = root / f"backtest_trades_{base}.csv"
        summary_path = root / f"backtest_summary_{base}.json"
        pd.DataFrame(self.kline_rows).to_csv(kline_path, index=False, encoding="utf-8")
        pd.DataFrame(self.trade_rows).to_csv(trades_path, index=False, encoding="utf-8")
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"klines": str(kline_path), "trades": str(trades_path), "summary": str(summary_path)}


def default_symbol(config_path: str) -> str:
    cfg = ConfigLoader.load_trading_config(config_path)
    symbols = ConfigLoader.get_trading_symbols(cfg)
    if not symbols:
        raise ValueError("no symbols configured")
    return normalize_symbol(symbols[0])


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="15m fund-flow replay backtest")
    parser.add_argument("--config", default="config/trading_config_fund_flow.json")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--csv", default="")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument("--fee-rate", type=float, default=0.0004)
    parser.add_argument("--output-dir", default="")
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    config_path = str(args.config)
    symbol = normalize_symbol(args.symbol or default_symbol(config_path))
    if bool(args.csv) == bool(args.fetch):
        raise SystemExit("choose exactly one of --csv or --fetch")
    bars = load_csv(args.csv) if args.csv else fetch_futures_15m(symbol, args.start or None, args.end or None, int(args.limit))
    if args.start:
        bars = bars.loc[bars["timestamp"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
    if args.end:
        bars = bars.loc[bars["timestamp"] <= pd.Timestamp(args.end, tz="UTC")].reset_index(drop=True)
    if bars.empty:
        raise SystemExit("no 15m bars available")
    runner = Backtest15mRunner(symbol=symbol, bars_15m=bars, config_path=config_path, initial_capital=float(args.initial_capital), fee_rate=float(args.fee_rate))
    summary = runner.run()
    outputs = runner.write_outputs(summary, args.output_dir or None)
    print(json.dumps({"summary": summary, "outputs": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.fund_flow_bot import TradingBot
from src.backtest_15m import ReplayClient
from src.config.config_loader import ConfigLoader
from src.fund_flow.market_storage import MarketStorage


@dataclass
class DecisionSnapshot:
    ts: datetime
    symbol: str
    price: float


def _parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(str(value))
    return ts.astimezone().astimezone(tz=None) if ts.tzinfo else ts


def _parse_utc_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(str(value))
    return ts if ts.tzinfo else ts.replace(tzinfo=pd.Timestamp.utcnow().tz)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _symbol_filter(raw: str) -> Optional[set[str]]:
    items = [item.strip().upper() for item in str(raw or '').split(',') if item.strip()]
    return set(items) if items else None


def _load_decision_snapshots(attribution_path: Path, start: str, end: str, symbols: Optional[set[str]]) -> Dict[str, List[DecisionSnapshot]]:
    out: Dict[str, List[DecisionSnapshot]] = {}
    with attribution_path.open('r', encoding='utf-8') as f:
        for line in f:
            obj = json.loads(line)
            if obj.get('event') != 'decision':
                continue
            ts_raw = str(obj.get('ts') or '')
            if ts_raw < start or ts_raw > end:
                continue
            decision = obj.get('decision') or {}
            symbol = str(decision.get('symbol') or '').upper()
            if not symbol:
                continue
            if symbols is not None and symbol not in symbols:
                continue
            context = obj.get('context') or {}
            price = _to_float(context.get('price'), 0.0)
            if price <= 0.0:
                continue
            ts = datetime.fromisoformat(ts_raw)
            out.setdefault(symbol, []).append(DecisionSnapshot(ts=ts, symbol=symbol, price=price))
    for symbol in list(out.keys()):
        uniq: Dict[str, DecisionSnapshot] = {}
        for row in sorted(out[symbol], key=lambda x: x.ts):
            uniq[row.ts.isoformat()] = row
        out[symbol] = list(uniq.values())
    return out


def _build_reconstructed_5m_frame(rows: List[DecisionSnapshot]) -> pd.DataFrame:
    payload: List[Dict[str, Any]] = []
    prev_close: Optional[float] = None
    for row in rows:
        open_price = prev_close if prev_close is not None else row.price
        close_price = row.price
        high_price = max(open_price, close_price)
        low_price = min(open_price, close_price)
        payload.append(
            {
                'timestamp': pd.Timestamp(row.ts).tz_convert('UTC') if pd.Timestamp(row.ts).tzinfo else pd.Timestamp(row.ts).tz_localize('UTC'),
                'open': float(open_price),
                'high': float(high_price),
                'low': float(low_price),
                'close': float(close_price),
                'volume': 0.0,
                'quote_volume': 0.0,
                'trades': 0.0,
                'taker_buy_base': 0.0,
                'taker_buy_quote': 0.0,
                'taker_sell_base': 0.0,
                'taker_sell_quote': 0.0,
                'open_interest': 0.0,
                'funding_rate': 0.0,
            }
        )
        prev_close = close_price
    frame = pd.DataFrame(payload)
    if frame.empty:
        return frame
    return frame.sort_values('timestamp').reset_index(drop=True)


def _build_bot(config_path: str, client: ReplayClient) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.config = ConfigLoader.load_trading_config(config_path)
    bot.client = client
    bot._prev_open_interest = {}
    bot._liquidity_ema_notional = {}
    return bot


def _synthetic_1m_row(ts: datetime, price: float) -> Dict[str, Any]:
    return {
        'timestamp': ts,
        'open': float(price),
        'high': float(price),
        'low': float(price),
        'close': float(price),
        'volume': 0.0,
    }


def _reconstructed_5m_row(ts: datetime, prev_price: Optional[float], price: float) -> Dict[str, Any]:
    open_price = float(prev_price if prev_price is not None else price)
    close_price = float(price)
    return {
        'timestamp': ts,
        'open': open_price,
        'high': max(open_price, close_price),
        'low': min(open_price, close_price),
        'close': close_price,
        'volume': 0.0,
    }


def backfill_symbol(storage: MarketStorage, config_path: str, symbol: str, rows: List[DecisionSnapshot], market: str, environment: str, exchange: str) -> Dict[str, Any]:
    frame_5m = _build_reconstructed_5m_frame(rows)
    if frame_5m.empty:
        return {'symbol': symbol, 'decision_points': 0, 'inserted_1m': 0, 'inserted_5m': 0, 'inserted_confluence': 0}

    client = ReplayClient(symbol, frame_5m)
    bot = _build_bot(config_path, client)
    confluence_cfg = TradingBot._ma10_macd_confluence_config(bot)

    rows_1m: List[Dict[str, Any]] = []
    rows_5m: List[Dict[str, Any]] = []
    confluence_count = 0
    prev_price: Optional[float] = None

    for row in rows:
        ts = pd.Timestamp(row.ts).to_pydatetime()
        client.set_cursor(pd.Timestamp(row.ts).tz_convert('UTC') if pd.Timestamp(row.ts).tzinfo else pd.Timestamp(row.ts).tz_localize('UTC'))
        confluence = TradingBot._compute_ma10_macd_confluence(bot, symbol, confluence_cfg)
        storage.upsert_ma10_macd_confluence_snapshot(
            exchange=exchange,
            symbol=symbol,
            timestamp=ts,
            exec_timeframe=str(confluence_cfg.get('tf_exec', '15m') or '15m'),
            anchor_timeframe=str(confluence_cfg.get('tf_anchor', '1h') or '1h'),
            snapshot=confluence,
        )
        confluence_count += 1
        rows_1m.append(_synthetic_1m_row(ts, row.price))
        rows_5m.append(_reconstructed_5m_row(ts, prev_price, row.price))
        prev_price = row.price

    inserted_1m = storage.upsert_klines(
        exchange=exchange,
        symbol=symbol,
        market=market,
        period='1m',
        environment=environment,
        rows=rows_1m,
    )
    inserted_5m = storage.upsert_klines(
        exchange=exchange,
        symbol=symbol,
        market=market,
        period='5m',
        environment=environment,
        rows=rows_5m,
    )
    return {
        'symbol': symbol,
        'decision_points': len(rows),
        'inserted_1m': int(inserted_1m),
        'inserted_5m': int(inserted_5m),
        'inserted_confluence': int(confluence_count),
        'source_mode': 'reconstructed_from_decision_prices',
        'notes': [
            '5m K线使用相邻 decision price 重建，非交易所原始 OHLCV。',
            '1m K线为 decision price 点位快照合成，用于补齐现有库结构。',
            'macd_15m/macd_1h 基于重建 5m 序列按当前 confluence 逻辑重新计算。',
        ],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Backfill 1m/5m klines and ma10/macd confluence from local attribution logs')
    parser.add_argument('--config', default='config/trading_config_fund_flow.json')
    parser.add_argument('--db', default='logs/2026-03/2026-03-16/fund_flow/fund_flow_strategy.db')
    parser.add_argument('--attribution', default='logs/2026-03/2026-03-16/fund_flow_attribution.jsonl')
    parser.add_argument('--start', default='2026-03-16T00:00:00+00:00')
    parser.add_argument('--end', default='2026-03-16T23:59:59+00:00')
    parser.add_argument('--symbols', default='')
    parser.add_argument('--market', default='futures')
    parser.add_argument('--environment', default='live')
    parser.add_argument('--exchange', default='binance')
    parser.add_argument('--output', default='.tmp/backfill_market_storage_20260316.json')
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    attribution_path = Path(args.attribution)
    if not attribution_path.exists():
        raise SystemExit(f'attribution file not found: {attribution_path}')
    symbol_filter = _symbol_filter(args.symbols)
    snapshots = _load_decision_snapshots(attribution_path, str(args.start), str(args.end), symbol_filter)
    if not snapshots:
        raise SystemExit('no decision snapshots found')
    storage = MarketStorage(str(args.db))
    results: List[Dict[str, Any]] = []
    for symbol in sorted(snapshots.keys()):
        results.append(
            backfill_symbol(
                storage=storage,
                config_path=str(args.config),
                symbol=symbol,
                rows=snapshots[symbol],
                market=str(args.market),
                environment=str(args.environment),
                exchange=str(args.exchange),
            )
        )
    payload = {
        'db': str(args.db),
        'attribution': str(args.attribution),
        'start': str(args.start),
        'end': str(args.end),
        'symbols': sorted(snapshots.keys()),
        'results': results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

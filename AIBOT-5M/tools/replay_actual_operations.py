from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ReplayTrade:
    symbol: str
    side: str
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_price_map_from_db(db_path: Path, symbol: str, period: str) -> Dict[str, Dict[str, float]]:
    import sqlite3

    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume FROM crypto_klines WHERE symbol = ? AND period = ? ORDER BY timestamp",
            (symbol, period),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {
        str(row['timestamp']): {
            'open': _to_float(row['open']),
            'high': _to_float(row['high']),
            'low': _to_float(row['low']),
            'close': _to_float(row['close']),
            'volume': _to_float(row['volume']),
        }
        for row in rows
    }


def load_decisions(attribution_path: Path, symbol: str, start: str, end: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with attribution_path.open('r', encoding='utf-8') as f:
        for line in f:
            obj = json.loads(line)
            if obj.get('event') != 'decision':
                continue
            ts = str(obj.get('ts') or '')
            if ts < start or ts > end:
                continue
            decision = obj.get('decision') or {}
            if str(decision.get('symbol') or '').upper() != symbol.upper():
                continue
            context = obj.get('context') or {}
            rows.append(
                {
                    'ts': ts,
                    'operation': str(decision.get('operation') or 'hold').lower(),
                    'price': _to_float(context.get('price'), 0.0),
                    'reason': str(decision.get('reason') or ''),
                }
            )
    return rows


def calc_pnl_pct(side: str, entry: float, exit: float, fee_pct_each_side: float) -> float:
    if side == 'LONG':
        gross = (exit / max(entry, 1e-9)) - 1.0
    else:
        gross = (entry - exit) / max(entry, 1e-9)
    return gross - 2.0 * fee_pct_each_side


def replay_actual_operations(rows: List[Dict[str, Any]], end: str, end_price: Optional[float], fee_pct_each_side: float, symbol: str) -> Dict[str, Any]:
    trades: List[ReplayTrade] = []
    current: Optional[Dict[str, Any]] = None
    ignored_events: List[Dict[str, Any]] = []

    for row in rows:
        op = row['operation']
        ts = row['ts']
        price = _to_float(row['price'], 0.0)
        if price <= 0.0:
            continue
        if op == 'buy':
            if current is None:
                current = {'side': 'LONG', 'entry_ts': ts, 'entry_price': price}
                continue
            if current['side'] == 'LONG':
                ignored_events.append({'ts': ts, 'operation': op, 'reason': 'same_side_scale_in_not_modeled'})
                continue
            trades.append(
                ReplayTrade(
                    symbol=symbol,
                    side=current['side'],
                    entry_ts=current['entry_ts'],
                    exit_ts=ts,
                    entry_price=current['entry_price'],
                    exit_price=price,
                    pnl_pct=calc_pnl_pct(current['side'], current['entry_price'], price, fee_pct_each_side),
                    exit_reason='reverse_to_long',
                )
            )
            current = {'side': 'LONG', 'entry_ts': ts, 'entry_price': price}
            continue
        if op == 'sell':
            if current is None:
                current = {'side': 'SHORT', 'entry_ts': ts, 'entry_price': price}
                continue
            if current['side'] == 'SHORT':
                ignored_events.append({'ts': ts, 'operation': op, 'reason': 'same_side_scale_in_not_modeled'})
                continue
            trades.append(
                ReplayTrade(
                    symbol=symbol,
                    side=current['side'],
                    entry_ts=current['entry_ts'],
                    exit_ts=ts,
                    entry_price=current['entry_price'],
                    exit_price=price,
                    pnl_pct=calc_pnl_pct(current['side'], current['entry_price'], price, fee_pct_each_side),
                    exit_reason='reverse_to_short',
                )
            )
            current = {'side': 'SHORT', 'entry_ts': ts, 'entry_price': price}
            continue
        if op == 'close':
            if current is None:
                ignored_events.append({'ts': ts, 'operation': op, 'reason': 'close_without_position'})
                continue
            trades.append(
                ReplayTrade(
                    symbol=symbol,
                    side=current['side'],
                    entry_ts=current['entry_ts'],
                    exit_ts=ts,
                    entry_price=current['entry_price'],
                    exit_price=price,
                    pnl_pct=calc_pnl_pct(current['side'], current['entry_price'], price, fee_pct_each_side),
                    exit_reason='close_signal',
                )
            )
            current = None

    if current is not None and end_price is not None and end_price > 0.0:
        trades.append(
            ReplayTrade(
                symbol=symbol,
                side=current['side'],
                entry_ts=current['entry_ts'],
                exit_ts=end,
                entry_price=current['entry_price'],
                exit_price=end_price,
                pnl_pct=calc_pnl_pct(current['side'], current['entry_price'], end_price, fee_pct_each_side),
                exit_reason='mark_to_market_end',
            )
        )

    total_pnl_pct = sum(item.pnl_pct for item in trades)
    wins = [item for item in trades if item.pnl_pct > 0]
    return {
        'symbol': symbol,
        'trade_count': len(trades),
        'win_rate_pct': (len(wins) / len(trades) * 100.0) if trades else 0.0,
        'total_pnl_pct': total_pnl_pct,
        'avg_pnl_pct': (total_pnl_pct / len(trades)) if trades else 0.0,
        'ignored_events': ignored_events,
        'trades': [item.__dict__ for item in trades],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Replay actual buy/sell/close operations from attribution logs')
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--db', default='logs/2026-03/2026-03-16/fund_flow/fund_flow_strategy.db')
    parser.add_argument('--attribution', default='logs/2026-03/2026-03-16/fund_flow_attribution.jsonl')
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--period', default='5m')
    parser.add_argument('--fee-pct-each-side', type=float, default=0.0004)
    parser.add_argument('--output', default='.tmp/replay_actual_operations.json')
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    symbol = str(args.symbol).upper()
    attribution_path = Path(args.attribution)
    if not attribution_path.exists():
        raise SystemExit(f'attribution file not found: {attribution_path}')
    decisions = load_decisions(attribution_path, symbol, str(args.start), str(args.end))
    price_map = load_price_map_from_db(Path(args.db), symbol, str(args.period))
    end_price = None
    last_ts = ''
    for ts, row in price_map.items():
        if ts <= str(args.end) and ts >= last_ts:
            last_ts = ts
            end_price = _to_float(row.get('close'), 0.0)
    if end_price is None:
        for row in decisions:
            if row['ts'] <= str(args.end):
                end_price = _to_float(row.get('price'), 0.0)
    payload = replay_actual_operations(decisions, str(args.end), end_price, float(args.fee_pct_each_side), symbol)
    payload['start'] = str(args.start)
    payload['end'] = str(args.end)
    payload['period'] = str(args.period)
    payload['db_price_rows'] = len(price_map)
    payload['end_mark_price'] = end_price
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

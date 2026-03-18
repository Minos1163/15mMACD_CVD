from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SignalPoint:
    ts: str
    price: float
    bar_open: float
    bar_close: float
    bar_high: float
    bar_low: float
    long_score: float
    short_score: float
    direction_lock: str
    operation: str
    cvd_ratio: float
    cvd_momentum: float
    oi_delta_ratio: float
    depth_ratio: float
    imbalance: float
    liquidity_delta_norm: float
    anchor_cross: str = "NONE"
    anchor_hist: float = 0.0
    exec_cross: str = "NONE"
    exec_hist: float = 0.0


@dataclass
class TradeResult:
    entry_ts: str
    exit_ts: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    bars_held: int
    exit_reason: str


DEFAULT_DB = Path("logs/2026-03/2026-03-16/fund_flow/fund_flow_strategy.db")
DEFAULT_ATTRIBUTION = Path("logs/2026-03/2026-03-16/fund_flow_attribution.jsonl")
DEFAULT_OUTPUT = Path(".tmp/sui_strategy_scan_result.json")


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_confluence_map(db_path: Path, symbol: str) -> Dict[str, Dict[str, Any]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, exec_timeframe, anchor_timeframe,
                   macd_15m_cross, macd_15m_hist,
                   macd_1h_cross, macd_1h_hist
            FROM market_ma10_macd_confluence
            WHERE symbol = ?
            ORDER BY timestamp
            """,
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        out[str(row["timestamp"])] = dict(row)
    return out


def load_5m_klines_map(db_path: Path, symbol: str) -> Dict[str, Dict[str, float]]:
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM crypto_klines
            WHERE symbol = ? AND period = '5m'
            ORDER BY timestamp
            """,
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    return {
        str(row["timestamp"]): {
            "open": to_float(row["open"]),
            "high": to_float(row["high"]),
            "low": to_float(row["low"]),
            "close": to_float(row["close"]),
            "volume": to_float(row["volume"]),
        }
        for row in rows
    }


def load_signal_points(
    attribution_path: Path,
    symbol: str,
    start_ts: str,
    end_ts: str,
    kline_map: Dict[str, Dict[str, float]],
    confluence_map: Dict[str, Dict[str, Any]],
) -> List[SignalPoint]:
    points: List[SignalPoint] = []
    with attribution_path.open("r", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("event") != "decision":
                continue
            ts = str(obj.get("ts") or "")
            if ts < start_ts or ts > end_ts:
                continue
            decision = obj.get("decision") or {}
            if decision.get("symbol") != symbol:
                continue
            metadata = decision.get("metadata") or {}
            ctx = obj.get("context") or {}
            flow = ctx.get("flow_context") or {}
            snap = flow.get("_ma10_macd_confluence") or {}
            tf15 = (flow.get("timeframes") or {}).get("15m") or {}
            bar_open = to_float(snap.get("last_open_5m"), to_float(snap.get("last_open_exec"), to_float(ctx.get("price"), 0.0)))
            bar_close = to_float(snap.get("last_close_5m"), to_float(snap.get("last_close_exec"), to_float(ctx.get("price"), 0.0)))
            bar = kline_map.get(ts) or {}
            if bar:
                bar_open = to_float(bar.get("open"), bar_open)
                bar_close = to_float(bar.get("close"), bar_close)
                bar_high = to_float(bar.get("high"), max(bar_open, bar_close))
                bar_low = to_float(bar.get("low"), min(bar_open, bar_close))
            else:
                bar_high = max(bar_open, bar_close)
                bar_low = min(bar_open, bar_close)
            db_conf = confluence_map.get(ts) or {}
            points.append(
                SignalPoint(
                    ts=ts,
                    price=to_float(ctx.get("price"), bar_close),
                    bar_open=bar_open,
                    bar_close=bar_close,
                    bar_high=bar_high,
                    bar_low=bar_low,
                    long_score=to_float(metadata.get("long_score"), 0.0),
                    short_score=to_float(metadata.get("short_score"), 0.0),
                    direction_lock=str(metadata.get("direction_lock") or "").upper(),
                    operation=str(decision.get("operation") or "hold").lower(),
                    cvd_ratio=to_float(tf15.get("cvd_ratio"), to_float(flow.get("cvd_ratio"), 0.0)),
                    cvd_momentum=to_float(tf15.get("cvd_momentum"), to_float(flow.get("cvd_momentum"), 0.0)),
                    oi_delta_ratio=to_float(tf15.get("oi_delta_ratio"), to_float(flow.get("oi_delta_ratio"), 0.0)),
                    depth_ratio=to_float(tf15.get("depth_ratio"), to_float(flow.get("depth_ratio"), 1.0)),
                    imbalance=to_float(tf15.get("imbalance"), to_float(flow.get("imbalance"), 0.0)),
                    liquidity_delta_norm=to_float(tf15.get("liquidity_delta_norm"), to_float(flow.get("liquidity_delta_norm"), 0.0)),
                    anchor_cross=str(db_conf.get("macd_1h_cross") or snap.get("macd_1h_cross") or "NONE").upper(),
                    anchor_hist=to_float(db_conf.get("macd_1h_hist"), to_float(snap.get("macd_1h_hist"), 0.0)),
                    exec_cross=str(db_conf.get("macd_15m_cross") or snap.get("macd_15m_cross") or snap.get("macd_5m_cross") or "NONE").upper(),
                    exec_hist=to_float(db_conf.get("macd_15m_hist"), to_float(snap.get("macd_15m_hist"), to_float(snap.get("macd_5m_hist"), 0.0))),
                )
            )
    return sorted(points, key=lambda x: x.ts)


def simulate_short_strategy(
    points: List[SignalPoint],
    *,
    entry_mode: str,
    short_score_threshold: float,
    require_bearish_bar: bool,
    max_cvd_ratio: float,
    hold_bars: int,
    take_profit_pct: float,
    stop_loss_pct: float,
    require_exec_confirm: bool,
    reject_anchor_golden: bool,
    fee_pct_each_side: float,
) -> Dict[str, Any]:
    trades: List[TradeResult] = []
    i = 0
    normalized_entry_mode = str(entry_mode or "synthetic_threshold").strip().lower()
    while i < len(points):
        p = points[i]
        can_enter = (
            p.short_score >= short_score_threshold
            and p.direction_lock in {"SHORT_ONLY", "BOTH"}
            and p.cvd_ratio <= max_cvd_ratio
        )
        if normalized_entry_mode == "actual_operations":
            can_enter = can_enter and p.operation == "sell"
        if require_bearish_bar:
            can_enter = can_enter and p.bar_close <= p.bar_open
        if require_exec_confirm:
            can_enter = can_enter and (p.exec_cross == "DEAD" or p.exec_hist < 0)
        if reject_anchor_golden:
            can_enter = can_enter and not (p.anchor_cross == "GOLDEN" or p.anchor_hist > 0)
        if not can_enter:
            i += 1
            continue

        entry_price = p.bar_close if p.bar_close > 0 else p.price
        exit_price = entry_price
        exit_reason = "end"
        exit_idx = i
        for j in range(i + 1, min(len(points), i + 1 + max(1, hold_bars))):
            nxt = points[j]
            tp_price = entry_price * (1.0 - take_profit_pct) if take_profit_pct > 0 else None
            sl_price = entry_price * (1.0 + stop_loss_pct) if stop_loss_pct > 0 else None
            if sl_price is not None and nxt.bar_high >= sl_price:
                exit_price = sl_price
                exit_idx = j
                exit_reason = "stop_loss"
                break
            if tp_price is not None and nxt.bar_low <= tp_price:
                exit_price = tp_price
                exit_idx = j
                exit_reason = "take_profit"
                break
            exit_price = nxt.bar_close if nxt.bar_close > 0 else nxt.price
            exit_idx = j
            exit_reason = "time_exit"
        gross_pct = (entry_price - exit_price) / max(entry_price, 1e-9)
        net_pct = gross_pct - 2.0 * fee_pct_each_side
        trades.append(
            TradeResult(
                entry_ts=p.ts,
                exit_ts=points[exit_idx].ts,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl_pct=net_pct,
                bars_held=max(1, exit_idx - i),
                exit_reason=exit_reason,
            )
        )
        i = exit_idx + 1

    total_pnl_pct = sum(t.pnl_pct for t in trades)
    wins = [t for t in trades if t.pnl_pct > 0]
    return {
        "entry_mode": normalized_entry_mode,
        "trade_count": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "total_pnl_pct": total_pnl_pct,
        "avg_pnl_pct": (total_pnl_pct / len(trades)) if trades else 0.0,
        "trades": [t.__dict__ for t in trades],
    }


def scan_short_strategies(points: List[SignalPoint], fee_pct_each_side: float, entry_mode: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    widened_stop = 0.00834375
    for short_score_threshold in [0.05, 0.07, 0.08, 0.1, 0.12]:
        for require_bearish_bar in [False, True]:
            for max_cvd_ratio in [0.05, 0.0, -0.05, -0.1, -0.2]:
                for hold_bars in [1, 2, 3, 4, 5, 6]:
                    for take_profit_pct in [0.0, 0.0005, 0.001, 0.0015, 0.002]:
                        for stop_loss_pct in [0.0045, 0.006, widened_stop]:
                            for require_exec_confirm in [False, True]:
                                for reject_anchor_golden in [False, True]:
                                    stats = simulate_short_strategy(
                                        points,
                                        entry_mode=entry_mode,
                                        short_score_threshold=short_score_threshold,
                                        require_bearish_bar=require_bearish_bar,
                                        max_cvd_ratio=max_cvd_ratio,
                                        hold_bars=hold_bars,
                                        take_profit_pct=take_profit_pct,
                                        stop_loss_pct=stop_loss_pct,
                                        require_exec_confirm=require_exec_confirm,
                                        reject_anchor_golden=reject_anchor_golden,
                                        fee_pct_each_side=fee_pct_each_side,
                                    )
                                    if stats["trade_count"] <= 0:
                                        continue
                                    results.append(
                                        {
                                            "params": {
                                                "entry_mode": entry_mode,
                                                "short_score_threshold": short_score_threshold,
                                                "require_bearish_bar": require_bearish_bar,
                                                "max_cvd_ratio": max_cvd_ratio,
                                                "hold_bars": hold_bars,
                                                "take_profit_pct": take_profit_pct,
                                                "stop_loss_pct": stop_loss_pct,
                                                "require_exec_confirm": require_exec_confirm,
                                                "reject_anchor_golden": reject_anchor_golden,
                                            },
                                            **stats,
                                        }
                                    )
    results.sort(key=lambda x: (x["total_pnl_pct"], x["win_rate_pct"], -x["trade_count"]), reverse=True)
    return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan locally profitable SUI short strategies from attribution logs")
    parser.add_argument("--symbol", default="SUIUSDT")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--attribution", default=str(DEFAULT_ATTRIBUTION))
    parser.add_argument("--start", default="2026-03-16T00:00:00+00:00")
    parser.add_argument("--end", default="2026-03-16T12:30:00+00:00")
    parser.add_argument("--fee-pct-each-side", type=float, default=0.0004)
    parser.add_argument("--entry-mode", choices=["synthetic_threshold", "actual_operations"], default="synthetic_threshold")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    db_path = Path(args.db)
    attribution_path = Path(args.attribution)
    if not attribution_path.exists():
        raise SystemExit(f"attribution file not found: {attribution_path}")
    confluence_map = load_confluence_map(db_path, args.symbol)
    kline_map = load_5m_klines_map(db_path, args.symbol)
    points = load_signal_points(attribution_path, args.symbol, args.start, args.end, kline_map, confluence_map)
    if not points:
        raise SystemExit("no signal points found")
    results = scan_short_strategies(points, fee_pct_each_side=float(args.fee_pct_each_side), entry_mode=str(args.entry_mode))
    top_results = results[: max(1, int(args.top))]
    payload = {
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "entry_mode": args.entry_mode,
        "point_count": len(points),
        "db_kline_rows": len(kline_map),
        "db_confluence_rows": len(confluence_map),
        "top_results": top_results,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

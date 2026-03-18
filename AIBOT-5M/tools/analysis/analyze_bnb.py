#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.fund_flow.decision_engine import FundFlowDecisionEngine


TZ_SHANGHAI = ZoneInfo("Asia/Shanghai")
TZ_UTC = ZoneInfo("UTC")


@dataclass
class ReplayComparison:
    ts_bj: str
    ts_utc: str
    old_op: str
    new_op: str
    old_dir: str
    new_dir: str
    old_long: float
    old_short: float
    new_long: float
    new_short: float
    old_reason: str
    new_reason: str
    old_source: str
    new_source: str
    changed: bool
    new_metadata: Dict[str, Any]


@dataclass
class ReplayEntryWindowState:
    alignment_active: bool
    flat_timeframe_seconds: int
    delay_seconds: float
    interval_seconds: int
    consumed_entry_bucket_id: Optional[int] = None
    cycle_cache: Dict[int, Dict[str, Any]] = field(default_factory=dict)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"config root must be object: {path}")
    return data


def _prepare_replay_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    replay_cfg = copy.deepcopy(cfg)
    ff = replay_cfg.get("fund_flow")
    if not isinstance(ff, dict):
        return replay_cfg

    ds_router = ff.get("deepseek_weight_router")
    if isinstance(ds_router, dict):
        ds_router["enabled"] = False
        ds_router["ai_enabled"] = False

    ds_ai = ff.get("deepseek_ai")
    if isinstance(ds_ai, dict):
        ds_ai["enabled"] = False

    return replay_cfg


def _parse_timeframe_seconds(value: Any) -> Optional[int]:
    tf = str(value or "").strip().lower()
    if not tf or tf == "raw":
        return None
    unit = tf[-1]
    try:
        n = int(tf[:-1])
    except Exception:
        return None
    if n <= 0:
        return None
    if unit == "s":
        return n
    if unit == "m":
        return n * 60
    if unit == "h":
        return n * 3600
    if unit == "d":
        return n * 86400
    return None


def _build_entry_window_state(cfg: Dict[str, Any]) -> ReplayEntryWindowState:
    ff_cfg = cfg.get("fund_flow", {}) if isinstance(cfg.get("fund_flow"), dict) else {}
    ai_cfg = ff_cfg.get("ai_review", {}) if isinstance(ff_cfg.get("ai_review"), dict) else {}
    schedule_cfg = cfg.get("schedule", {}) if isinstance(cfg.get("schedule"), dict) else {}

    decision_tf_seconds = _parse_timeframe_seconds(
        ff_cfg.get("decision_timeframe") or ff_cfg.get("signal_timeframe")
    )
    flat_tf_seconds = (
        _parse_timeframe_seconds(ai_cfg.get("flat_timeframe"))
        or decision_tf_seconds
        or 900
    )
    alignment_active = bool(schedule_cfg.get("align_to_kline_close", True)) and bool(
        decision_tf_seconds and decision_tf_seconds > 0
    )
    delay_seconds = max(0.0, _safe_float(schedule_cfg.get("kline_close_delay_seconds", 3)))
    interval_seconds = max(1, int(_safe_float(schedule_cfg.get("interval_seconds", 60)) or 60))
    return ReplayEntryWindowState(
        alignment_active=alignment_active,
        flat_timeframe_seconds=max(60, int(flat_tf_seconds or 900)),
        delay_seconds=delay_seconds,
        interval_seconds=interval_seconds,
    )


def _parse_local_dt(day: str, hhmm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00").replace(tzinfo=TZ_SHANGHAI)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _decision_source(metadata: Dict[str, Any]) -> str:
    source = metadata.get("decision_source")
    if source:
        return str(source)
    source = metadata.get("ds_source")
    if source:
        return str(source)
    return "unknown"


def _extract_scores(metadata: Dict[str, Any]) -> tuple[float, float]:
    long_score = metadata.get("final_long_score", metadata.get("long_score", 0.0))
    short_score = metadata.get("final_short_score", metadata.get("short_score", 0.0))
    return (_safe_float(long_score), _safe_float(short_score))


def _iter_decisions(path: Path, end_utc: datetime) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("event") != "decision":
                continue
            ts = datetime.fromisoformat(obj["ts"])
            if ts <= end_utc:
                items.append(obj)
    return items


def _infer_allow_entry_window(
    *,
    ts_utc: datetime,
    trigger_context: Dict[str, Any],
    decision_metadata: Dict[str, Any],
    window_state: ReplayEntryWindowState,
) -> Dict[str, Any]:
    ts_seconds = float(ts_utc.timestamp())
    cycle_id = int(ts_seconds // float(window_state.interval_seconds))

    if "allow_entry_window" in trigger_context:
        explicit = {
            "allow": bool(trigger_context.get("allow_entry_window")),
            "source": "historical_trigger_context",
            "cycle_id": cycle_id,
            "entry_bucket_id": None,
        }
        window_state.cycle_cache.setdefault(cycle_id, explicit)
        return explicit

    cached = window_state.cycle_cache.get(cycle_id)
    if cached is not None:
        return cached

    trigger_md = decision_metadata.get("trigger") if isinstance(decision_metadata.get("trigger"), dict) else {}
    ai_gate = str(trigger_md.get("ai_gate") or "").strip().lower()
    if ai_gate == "position_review":
        info = {
            "allow": False,
            "source": "ai_gate_position_review",
            "cycle_id": cycle_id,
            "entry_bucket_id": None,
        }
        window_state.cycle_cache[cycle_id] = info
        return info

    if not window_state.alignment_active or window_state.flat_timeframe_seconds <= 0:
        info = {
            "allow": True,
            "source": "alignment_disabled",
            "cycle_id": cycle_id,
            "entry_bucket_id": None,
        }
        window_state.cycle_cache[cycle_id] = info
        return info

    tf_seconds = float(window_state.flat_timeframe_seconds)
    close_ts = math.floor(ts_seconds / tf_seconds) * tf_seconds
    open_ts = close_ts + float(window_state.delay_seconds)
    bucket_id = int(close_ts // tf_seconds)
    window_seconds = min(tf_seconds, float(window_state.interval_seconds))

    if ts_seconds + 1e-6 < open_ts:
        info = {
            "allow": False,
            "source": "before_open_delay",
            "cycle_id": cycle_id,
            "entry_bucket_id": bucket_id,
        }
    elif (ts_seconds - open_ts) > window_seconds:
        info = {
            "allow": False,
            "source": "outside_open_window",
            "cycle_id": cycle_id,
            "entry_bucket_id": bucket_id,
        }
    elif window_state.consumed_entry_bucket_id == bucket_id:
        info = {
            "allow": False,
            "source": "entry_bucket_already_consumed",
            "cycle_id": cycle_id,
            "entry_bucket_id": bucket_id,
        }
    else:
        window_state.consumed_entry_bucket_id = bucket_id
        info = {
            "allow": True,
            "source": "aligned_open_window",
            "cycle_id": cycle_id,
            "entry_bucket_id": bucket_id,
        }

    window_state.cycle_cache[cycle_id] = info
    return info


def _replay(
    config_path: Path,
    attribution_path: Path,
    symbol: str,
    start_bj: datetime,
    end_bj: datetime,
) -> List[ReplayComparison]:
    cfg = _prepare_replay_config(_load_json(config_path))
    engine = FundFlowDecisionEngine(cfg)
    window_state = _build_entry_window_state(cfg)

    start_utc = start_bj.astimezone(TZ_UTC)
    end_utc = end_bj.astimezone(TZ_UTC)
    rows: List[ReplayComparison] = []

    for obj in _iter_decisions(attribution_path, end_utc):
        ts_utc = datetime.fromisoformat(obj["ts"])
        decision = obj["decision"]
        metadata = decision.get("metadata", {})
        context = obj.get("context", {})
        trigger_context = context.get("trigger_context") if isinstance(context.get("trigger_context"), dict) else {}
        allow_window = _infer_allow_entry_window(
            ts_utc=ts_utc,
            trigger_context=trigger_context,
            decision_metadata=metadata if isinstance(metadata, dict) else {},
            window_state=window_state,
        )

        if str(decision.get("symbol", "")).upper() != symbol.upper():
            continue

        replay_trigger_context = dict(trigger_context)
        replay_trigger_context["allow_entry_window"] = bool(allow_window["allow"])

        replay = engine.decide(
            symbol=str(context.get("symbol") or decision.get("symbol") or symbol),
            portfolio=context.get("portfolio") if isinstance(context.get("portfolio"), dict) else {"positions": {}},
            price=_safe_float(context.get("price")),
            market_flow_context=context.get("flow_context") if isinstance(context.get("flow_context"), dict) else {},
            trigger_context=replay_trigger_context,
        )

        if not (start_utc <= ts_utc <= end_utc):
            continue

        new_metadata = dict(replay.metadata) if isinstance(getattr(replay, "metadata", None), dict) else {}
        new_metadata["replay_allow_entry_window_source"] = allow_window["source"]
        new_metadata["replay_allow_entry_window_cycle_id"] = allow_window["cycle_id"]
        new_metadata["replay_allow_entry_window_bucket_id"] = allow_window["entry_bucket_id"]
        old_long, old_short = _extract_scores(metadata)
        new_long, new_short = _extract_scores(new_metadata)
        old_dir = str(metadata.get("guide_direction") or metadata.get("direction_lock") or "UNKNOWN")
        new_dir = str(new_metadata.get("guide_direction") or new_metadata.get("direction_lock") or "UNKNOWN")
        old_op = str(decision.get("operation", "")).upper()
        new_op = str(getattr(replay.operation, "value", replay.operation)).upper()

        rows.append(
            ReplayComparison(
                ts_bj=ts_utc.astimezone(TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
                ts_utc=ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
                old_op=old_op,
                new_op=new_op,
                old_dir=old_dir,
                new_dir=new_dir,
                old_long=old_long,
                old_short=old_short,
                new_long=new_long,
                new_short=new_short,
                old_reason=str(decision.get("reason", "")),
                new_reason=str(getattr(replay, "reason", "")),
                old_source=_decision_source(metadata),
                new_source=_decision_source(new_metadata),
                changed=(old_op != new_op) or (old_dir != new_dir),
                new_metadata=new_metadata,
            )
        )

    return rows


def _print_table(rows: List[ReplayComparison]) -> None:
    headers = [
        ("BJ Time", 19),
        ("OldOp", 8),
        ("NewOp", 8),
        ("OldDir", 12),
        ("NewDir", 12),
        ("OldL", 7),
        ("OldS", 7),
        ("NewL", 7),
        ("NewS", 7),
        ("Changed", 7),
    ]
    line = " ".join(name.ljust(width) for name, width in headers)
    print(line)
    print("-" * len(line))
    for row in rows:
        values = [
            row.ts_bj,
            row.old_op,
            row.new_op,
            row.old_dir,
            row.new_dir,
            f"{row.old_long:.3f}",
            f"{row.old_short:.3f}",
            f"{row.new_long:.3f}",
            f"{row.new_short:.3f}",
            "Y" if row.changed else "N",
        ]
        print(
            " ".join(
                str(value).ljust(width)
                for value, (_, width) in zip(values, headers)
            )
        )


def _print_inspect(rows: List[ReplayComparison], inspect_ts: str) -> int:
    target = inspect_ts.strip()
    match: Optional[ReplayComparison] = None
    for row in rows:
        if row.ts_bj == target or row.ts_bj.endswith(target):
            match = row
            break
    if match is None:
        print(f"\nInspect target not found: {inspect_ts}")
        return 1

    md = match.new_metadata if isinstance(match.new_metadata, dict) else {}
    print(f"\nInspect: {match.ts_bj}")
    print(f"  old -> {match.old_op}/{match.old_dir}")
    print(f"  new -> {match.new_op}/{match.new_dir}")
    print(f"  reason -> {match.new_reason}")
    print("\nDirection Layer")
    print(f"  guide_direction={md.get('guide_direction')} guide_score={md.get('guide_score')}")
    print(f"  lw_direction={md.get('lw_direction')} lw_score={md.get('lw_score')}")
    print(f"  ev_direction={md.get('ev_direction')} ev_score={md.get('ev_score')}")
    print(f"  direction_conflict={md.get('direction_conflict')} direction_lock_applied={md.get('direction_lock_applied')}")
    print(
        "  allow_entry_window="
        f"{md.get('allow_entry_window')} "
        f"source={md.get('replay_allow_entry_window_source')} "
        f"cycle_id={md.get('replay_allow_entry_window_cycle_id')} "
        f"bucket_id={md.get('replay_allow_entry_window_bucket_id')}"
    )

    print("\nScore Layer")
    print(f"  score_15m={json.dumps(md.get('score_15m'), ensure_ascii=False)}")
    print(f"  score_5m={json.dumps(md.get('score_5m'), ensure_ascii=False)}")
    print(f"  fused_score={json.dumps(md.get('final_score'), ensure_ascii=False)}")
    print(
        "  base_scores="
        f"long={md.get('base_long_score')} short={md.get('base_short_score')} "
        f"final_long={md.get('final_long_score')} final_short={md.get('final_short_score')}"
    )
    print(f"  entry_thresholds={md.get('open_thresholds') or md.get('params_override')}")

    print("\nTrend Capture Layer")
    print(
        "  trend_capture="
        f"side={md.get('trend_capture_side')} "
        f"long={md.get('trend_capture_score_long')} short={md.get('trend_capture_score_short')}"
    )
    print(
        "  trend_capture_injection="
        f"enabled={md.get('trend_capture_confluence_injected')} "
        f"side={md.get('trend_capture_confluence_injected_side')} "
        f"score={md.get('trend_capture_confluence_injected_score')} "
        f"pruned_side={md.get('trend_capture_pruned_side')} "
        f"confirm_pass={md.get('trend_capture_injection_confirm_pass')} "
        f"gate_pass={md.get('trend_capture_injection_gate_pass')}"
    )
    print(
        "  trend_capture_flags="
        f"breakout_long={md.get('trend_capture_breakout_long')} "
        f"breakout_short={md.get('trend_capture_breakout_short')} "
        f"cvd_long={md.get('trend_capture_cvd_align_long')} "
        f"cvd_short={md.get('trend_capture_cvd_align_short')} "
        f"depth_long={md.get('trend_capture_depth_align_long')} "
        f"depth_short={md.get('trend_capture_depth_align_short')}"
    )

    print("\nConfluence Layer")
    print(
        "  anchor="
        f"long={md.get('confluence_anchor_ma10_long')} short={md.get('confluence_anchor_ma10_short')}"
    )
    print(
        "  macd_trigger="
        f"long={md.get('confluence_macd_trigger_long')} short={md.get('confluence_macd_trigger_short')} "
        f"early_long={md.get('confluence_macd_early_long')} early_short={md.get('confluence_macd_early_short')}"
    )
    print(
        "  kdj_ok="
        f"long={md.get('confluence_kdj_ok_long')} short={md.get('confluence_kdj_ok_short')}"
    )
    print(
        "  hard_block="
        f"long={md.get('confluence_hard_block_long')} short={md.get('confluence_hard_block_short')} "
        f"soft_penalty_long={md.get('confluence_soft_penalty_long')} "
        f"soft_penalty_short={md.get('confluence_soft_penalty_short')}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay and compare BNB decisions in a Beijing-time window.")
    parser.add_argument("--config", default=str(ROOT / "config" / "trading_config_fund_flow.json"))
    parser.add_argument("--attribution", default=str(ROOT / "logs" / "2026-03" / "2026-03-10" / "fund_flow_attribution.jsonl"))
    parser.add_argument("--symbol", default="BNBUSDT")
    parser.add_argument("--day", default="2026-03-10", help="Beijing date, YYYY-MM-DD")
    parser.add_argument("--start", default="10:25", help="Beijing start time, HH:MM")
    parser.add_argument("--end", default="11:10", help="Beijing end time, HH:MM")
    parser.add_argument("--json-out", default="", help="Optional path to save full comparison JSON.")
    parser.add_argument("--inspect-ts", default="", help="Optional BJ timestamp to print gate breakdown, e.g. '2026-03-10 10:25:14'.")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    attribution_path = Path(args.attribution).expanduser().resolve()
    if not config_path.exists():
        print(f"FAIL: config not found: {config_path}")
        return 2
    if not attribution_path.exists():
        print(f"FAIL: attribution log not found: {attribution_path}")
        return 2

    start_bj = _parse_local_dt(args.day, args.start)
    end_bj = _parse_local_dt(args.day, args.end)
    rows = _replay(config_path, attribution_path, args.symbol, start_bj, end_bj)
    if not rows:
        print("No matching decision events found.")
        return 1

    _print_table(rows)
    print()
    for row in rows:
        print(f"[{row.ts_bj}] old={row.old_op}/{row.old_dir} -> new={row.new_op}/{row.new_dir}")
        print(f"  old_reason: {row.old_reason}")
        print(f"  new_reason: {row.new_reason}")

    inspect_rc = 0
    if args.inspect_ts:
        inspect_rc = _print_inspect(rows, args.inspect_ts)

    if args.json_out:
        out_path = Path(args.json_out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nSaved JSON to: {out_path}")

    return inspect_rc


if __name__ == "__main__":
    raise SystemExit(main())

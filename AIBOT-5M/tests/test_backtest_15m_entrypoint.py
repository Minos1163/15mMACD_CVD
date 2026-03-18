import json
from pathlib import Path

import pandas as pd

from src.backtest_15m import Backtest15mRunner, normalize_frame


def _sample_bars(count: int = 260) -> pd.DataFrame:
    rows = []
    ts = pd.Timestamp("2026-01-01T00:00:00Z")
    price = 100.0
    for i in range(count):
        drift = 0.6 if i < count // 2 else -0.7
        open_p = price
        close_p = max(1.0, open_p + drift + (0.1 if i % 7 == 0 else -0.05))
        high_p = max(open_p, close_p) + 0.3
        low_p = min(open_p, close_p) - 0.25
        volume = 1000 + i * 3
        quote_volume = close_p * volume
        rows.append(
            {
                "timestamp": ts + pd.Timedelta(minutes=15 * i),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "quote_volume": quote_volume,
                "taker_buy_base": volume * (0.58 if drift > 0 else 0.42),
                "taker_buy_quote": quote_volume * (0.58 if drift > 0 else 0.42),
                "open_interest": quote_volume * 0.9,
            }
        )
        price = close_p
    return pd.DataFrame(rows)


def test_normalize_frame_accepts_basic_columns():
    frame = normalize_frame(_sample_bars(5))
    assert list(frame.columns[:6]) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(frame) == 5
    assert frame["timestamp"].dt.tz is not None


def test_backtest_runner_creates_summary_and_outputs():
    bars = normalize_frame(_sample_bars())
    runner = Backtest15mRunner(
        symbol="BTCUSDT",
        bars_15m=bars,
        config_path="config/trading_config_fund_flow.json",
        initial_capital=10000.0,
        fee_rate=0.0004,
    )

    summary = runner.run()
    out_dir = Path(".tmp/backtest_test")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = runner.write_outputs(summary, str(out_dir))

    assert summary["bars"] == len(bars)
    assert summary["warmup_bars"] >= 120
    assert summary["trade_count"] >= 0
    assert Path(outputs["klines"]).exists()
    assert Path(outputs["trades"]).exists()
    assert Path(outputs["summary"]).exists()

    kline_df = pd.read_csv(outputs["klines"])
    assert len(kline_df) == len(bars)
    assert {"capital", "decision", "long_score", "short_score"}.issubset(kline_df.columns)

    summary_json = json.loads(Path(outputs["summary"]).read_text(encoding="utf-8"))
    assert summary_json["symbol"] == "BTCUSDT"

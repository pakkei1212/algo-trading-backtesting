"""AMP extraction and eligibility checks without a terminal or credentials."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from test_research import settings

from mnq_research.amp_mt5 import audit, expected_closes, publish, utc
from mnq_research.backtest import simulate_backtest
from mnq_research.data import load_dataset


def test_amp_audit_keeps_raw_rows_and_blocks_unsafe_execution(tmp_path: Path) -> None:
    start = utc("2026-01-06T14:30Z")
    end = utc("2026-01-06T16:30Z")
    timestamps = pd.date_range(start, end, freq="5min")
    columns = ["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
    rates = np.array(
        [
            (int(t.timestamp()), 100, 101, 99, 100, 0, 0, int(i != 12))
            for i, t in enumerate(timestamps)
        ],
        dtype=[(column, "f8") for column in columns],
    )
    info = SimpleNamespace(
        exchange="XCME",
        path="Expired",
        currency_profit="USD",
        trade_tick_size=0.25,
        trade_tick_value=0.5,
        chart_mode=1,
        expiration_time=utc("2026-03-20T21:00Z").timestamp(),
        start_time=0,
    )
    mt5 = SimpleNamespace(
        symbol_info=lambda _: info,
        symbol_select=lambda *_: True,
        TIMEFRAME_M5=5,
        copy_rates_range=lambda *_: rates,
    )
    result = audit(
        mt5,
        {"server": "AMPGlobalUSA-Demo"},
        "MNQH26",
        start.isoformat(),
        end.isoformat(),
        tmp_path / "raw",
    )
    folder = Path(result["audit"])
    coverage = json.loads((folder / "coverage.json").read_text())
    assert coverage["rejected_timestamps"] == 1
    assert coverage["zero_real_volume"] == 1
    assert len(coverage["eligible_ranges"]) == 2
    assert len(pd.read_parquet(next((tmp_path / "raw").glob("*.parquet")))) == len(timestamps)

    def fail_if_requested(*_):
        pytest.fail("Verified raw checkpoints should be reused")

    mt5.copy_rates_range = fail_if_requested
    assert (
        audit(
            mt5,
            {"server": "AMPGlobalUSA-Demo"},
            "MNQH26",
            start.isoformat(),
            end.isoformat(),
            tmp_path / "raw",
        )
        == result
    )
    published = publish(folder, tmp_path / "data")
    frame, metadata = load_dataset(published)
    assert frame.index[0] == start + pd.Timedelta(minutes=5)
    assert metadata["volume_field"] == "real_volume"
    assert "tick_volume" in pd.read_parquet(published / "bars.parquet")
    config = settings(start="2026-01-06T15:00Z", end="2026-01-06T15:25Z")
    summary = simulate_backtest(config, frame, metadata)
    assert isinstance(summary, dict)
    assert summary["bar_interval"] == "5m"
    assert summary["first_bar"] == "2026-01-06T15:00:00+00:00"
    with pytest.raises(ValueError, match="data gap"):
        simulate_backtest(
            settings(start="2026-01-06T15:00Z", end="2026-01-06T16:20Z"), frame, metadata
        )


def test_expected_closes_follow_dst_and_mark_special_sessions() -> None:
    for date, open_hour in [("2026-03-01", 23), ("2026-03-08", 22)]:
        grid = expected_closes(utc(f"{date}T00:00Z"), utc(f"{date}T23:59Z"))
        assert grid.index[0].hour == open_hour
        assert grid.index[0].minute == 5
    holiday = expected_closes(utc("2026-01-19T00:00Z"), utc("2026-01-19T20:00Z"))
    assert holiday.special.all()

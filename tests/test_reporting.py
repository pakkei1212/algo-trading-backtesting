"""Reporting boundaries: native accounting, timestamps, empty and legacy runs."""

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from test_research import dataset, settings

from mnq_research.backtest import run_backtest
from mnq_research.reporting import (
    TradeRecord,
    candle_index,
    chart_payload,
    display_time,
    load_run,
    trade_metrics,
)
from mnq_research.results_ui import filtered_trades


def trade(pnl: float, status: str = "Closed") -> TradeRecord:
    return TradeRecord(
        "position",
        1,
        "Long",
        status,
        "2026-01-01T00:00:00Z",
        "2026-01-01T01:00:00Z" if status == "Closed" else None,
        2,
        100,
        105 if status == "Closed" else None,
        pnl,
        0,
        2,
        1,
        "Protective stop",
        ["entry", "exit"],
    )


@pytest.mark.parametrize(
    "values,winners,losers,breakevens,factor",
    [
        ([], 0, 0, 0, None),
        ([20, 10], 2, 0, 0, None),
        ([-20, -10], 0, 2, 0, 0),
        ([20, -10, 0], 1, 1, 1, 2),
        ([0], 0, 0, 1, None),
    ],
)
def test_closed_trade_statistics(values, winners, losers, breakevens, factor) -> None:
    equity = pd.DataFrame({"equity_usd": [10000, 12000, 10500]})
    result = trade_metrics([trade(v) for v in values], equity, 10000)
    assert result["closed_net"] == sum(values)
    assert (result["winners"], result["losers"], result["breakevens"]) == (
        winners,
        losers,
        breakevens,
    )
    assert result["profit_factor"] == factor
    assert result["drawdown_usd"] == 1500
    assert result["drawdown_pct"] == 0.125
    assert result["win_rate"] == (winners / len(values) if values else None)


def test_execution_boundary_preserves_nanosecond_and_gap() -> None:
    closes = pd.DatetimeIndex(
        ["2026-01-02T21:00Z", "2026-01-02T22:00Z", "2026-01-05T01:00Z"]
    ).as_unit("us")
    assert candle_index(closes, "2026-01-02T21:00:00Z") == 0
    assert candle_index(closes, "2026-01-02T21:00:00.000000001Z") == 1
    assert candle_index(closes, "2026-01-05T00:00:00.000000001Z") == 2


def test_dst_changes_only_labels() -> None:
    before = "2026-11-01T05:30:00Z"
    after = "2026-11-01T06:30:00Z"
    assert display_time(before, "America/New_York").endswith("01:30:00 EDT")
    assert display_time(after, "America/New_York").endswith("01:30:00 EST")
    assert display_time(before, "UTC").endswith("05:30:00 UTC")


def test_filters_do_not_count_open_positions_as_winners() -> None:
    trades = [trade(20), replace(trade(-5), id="short", direction="Short"), trade(50, "Open")]
    assert len(filtered_trades(trades, "All", "All", "Winning")) == 1
    assert len(filtered_trades(trades, "Short", "Closed", "All")) == 1
    assert len(filtered_trades(trades, "All", "Open", "All")) == 1


def test_report_reconciles_open_position_and_legacy(tmp_path: Path) -> None:
    folder = run_backtest(
        settings(), dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112]), tmp_path / "runs"
    )
    report = load_run(folder)
    assert report.metrics["open_count"] == 1
    t = report.trades[0]
    assert t.exit_time is None and t.exit_price is None
    assert t.realized_pnl + t.unrealized_pnl == pytest.approx(report.summary["net_pnl_usd"])
    assert report.chart.stops and report.chart.indicators
    json.dumps(chart_payload(report), allow_nan=False)
    # Removing optional files from the temporary fixture emulates an untouched old run.
    (folder / "observations.csv").unlink()
    (folder / "protective_orders.csv").unlink()
    legacy = load_run(folder)
    assert len(legacy.unavailable) == 2
    assert legacy.summary == report.summary
    assert legacy.chart.stops == [] and legacy.chart.indicators == []


def test_report_handles_no_trades(tmp_path: Path) -> None:
    folder = run_backtest(
        settings(), dataset(tmp_path, [100, 101, 102, 103, 104, 105, 106, 107]), tmp_path / "runs"
    )
    report = load_run(folder)
    assert report.trades == []
    assert report.metrics["win_rate"] is None
    json.dumps(chart_payload(report), allow_nan=False)

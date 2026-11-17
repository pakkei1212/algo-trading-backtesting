"""Verify schedule exceptions and native account continuity across a contract roll."""

import pandas as pd
import pytest
from test_research import market_bars, settings

from mnq_research.amp_mt5 import BAR, utc
from mnq_research.backtest import simulate_backtest
from mnq_research.roll_backtest import RollSegment, roll_grid


def test_roll_calendar_excludes_outage_and_restores_late_afternoon_bars() -> None:
    grid = roll_grid(utc("2025-11-27T23:00Z"), utc("2025-11-28T19:00Z"))
    assert utc("2025-11-28T02:45Z") in grid.index
    assert not grid.index.intersection(
        pd.date_range("2025-11-28T02:50Z", "2025-11-28T13:30Z", freq=BAR)
    ).size
    assert utc("2025-11-28T13:35Z") in grid.index
    assert grid.index[-1] == utc("2025-11-28T18:15Z")
    normal = roll_grid(utc("2026-09-11T20:10Z"), utc("2026-09-11T21:00Z"))
    assert len(normal) == 10


@pytest.mark.parametrize("halt_threshold", [0.5, 0.00001])
def test_native_roll_flattens_and_carries_equity_and_halt(
    tmp_path, monkeypatch, halt_threshold: float
) -> None:
    outgoing = market_bars([100, 99, 98, 99, 100, 101, 102, 103, 104, 105], "5m")
    outgoing.index = pd.date_range("2026-01-05T12:00Z", periods=10, freq=BAR)
    incoming = market_bars([300, 299, 298, 299, 300, 301, 302, 303, 304, 305], "5m")
    incoming.index = pd.date_range("2026-01-06T12:00Z", periods=10, freq=BAR)
    config = settings(
        start=outgoing.index[4].isoformat(),
        end=incoming.index[-1].isoformat(),
        max_drawdown_fraction=halt_threshold,
    )
    segments = [
        RollSegment(
            outgoing,
            {"kind": "dated_contract", "symbol": "MNQH26"},
            outgoing.index[4],
            (outgoing.index[8] - BAR).value + 1,
        ),
        RollSegment(
            incoming,
            {"kind": "dated_contract", "symbol": "MNQM26"},
            incoming.index[4],
            None,
        ),
    ]
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: segments)
    frame = pd.concat([outgoing, incoming])
    result = simulate_backtest(
        config,
        frame,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
    )
    fills = pd.read_csv(result / "fills.csv")
    equity = pd.read_csv(result / "equity.csv")
    outgoing_equity = equity[equity.contract.eq("MNQH26")].equity_usd
    incoming_equity = equity[equity.contract.eq("MNQM26")].equity_usd
    assert incoming_equity.iloc[0] == outgoing_equity.iloc[-1]
    expected_drawdown = 1 - equity.equity_usd / equity.equity_usd.cummax().clip(lower=10000)
    pd.testing.assert_series_equal(equity.drawdown, expected_drawdown, check_names=False)
    if halt_threshold < 0.01:
        assert not fills.contract.eq("MNQM26").any()
        assert equity.drawdown.max() >= halt_threshold
        assert "drawdown_halt" in pd.read_csv(result / "decisions.csv").reason.values
    else:
        close = fills.loc[fills.exit_reason.eq("contract_roll")].iloc[0]
        assert close.contract == "MNQH26"
        assert close.price == 104.0
        assert close.commission_usd == 1.0
        assert pd.Timestamp(close.timestamp) == outgoing.index[8] - BAR + pd.Timedelta(1, unit="ns")
        assert fills.contract.eq("MNQM26").any()

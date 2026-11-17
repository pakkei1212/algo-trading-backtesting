from datetime import date, time

import pandas as pd
import pytest
from pydantic import ValidationError
from test_research import market_bars, settings

from mnq_research.backtest import simulate_backtest
from mnq_research.cash_research import cash_timestamp, qualify, session_calendar
from mnq_research.cash_strategy import OpeningRange, OrbConfig
from mnq_research.roll_backtest import RollSegment


@pytest.mark.parametrize(("direction", "breakout"), [(1, 103.0), (-1, 97.0)])
def test_opening_range_freezes_before_first_signal(direction: int, breakout: float) -> None:
    opening_range = OpeningRange(5)
    assert opening_range.update(5, 102, 98, 100) == 0
    assert opening_range.update(10, 104, 96, breakout) == direction
    assert (opening_range.low, opening_range.high) == (98, 102)
    assert opening_range.update(15, 104, 96, breakout) == 0
    with pytest.raises(ValidationError):
        OrbConfig.model_validate({"opening_range_minutes": 10})


def test_calendar_requires_every_cash_close() -> None:
    day = date(2026, 4, 2)
    index = pd.date_range(
        cash_timestamp(day, time(9, 35)), cash_timestamp(day, time(16)), freq="5min"
    )
    bars = pd.DataFrame({"volume": 1, "contract": "MNQM26"}, index=index)
    assert index[0] == pd.Timestamp("2026-04-02T13:35Z")
    assert cash_timestamp(date(2026, 3, 6), time(9, 35)).hour == 14
    assert session_calendar(bars, {"segments": []}, "2026-04-02", "2026-04-03") == [
        {"date": "2026-04-02", "status": "regular"},
        {"date": "2026-04-03", "status": "cash holiday"},
    ]
    with pytest.raises(ValueError, match="Incomplete"):
        session_calendar(bars.drop(index[-1]), {"segments": []}, "2026-04-02", "2026-04-02")


def test_orb_uses_native_next_open_and_liquidates(tmp_path, monkeypatch) -> None:
    session = date(2026, 4, 2)
    bars = market_bars([100] * 4 + [103] + [104] * 76, "5m")
    bars.index = pd.date_range("2026-04-02T13:20Z", periods=len(bars), freq="5min")
    native = {"kind": "dated_contract", "symbol": "MNQM26"}
    segment = RollSegment(bars, native, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    config = settings(start=bars.index[3].isoformat(), end=bars.index[-1].isoformat())
    folder = simulate_backtest(
        config,
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({session}),
        orb=OrbConfig(opening_range_minutes=5),
    )
    fills = pd.read_csv(folder / "fills.csv")
    assert fills.exit_reason.tolist() == ["entry", "cash_session_exit"]
    assert pd.Timestamp(fills.timestamp.iloc[0]) == bars.index[4] + pd.Timedelta(1, unit="ns")
    assert pd.Timestamp(fills.timestamp.iloc[-1]) == pd.Timestamp("2026-04-02T19:55:00.000000001Z")


def test_qualification_requires_profit_sample_and_drawdown() -> None:
    result = {
        "net_pnl_usd": 150,
        "max_drawdown_fraction": 0.05,
        "halted": False,
        "closed_trades": 30,
        "active_days": 20,
    }
    assert qualify(result, 30, 20) == []
    assert qualify(result, 10, 8, relative_pnl=150) == ["insufficient net P&L"]
    assert qualify({**result, "halted": True, "active_days": 19}, 30, 20) == [
        "drawdown limit or halt", "insufficient trades or active days"
    ]

from contextlib import closing
from datetime import date
from sqlite3 import connect

import pandas as pd
import pytest
from pydantic import ValidationError
from test_research import market_bars, settings

from mnq_research.backtest import simulate_backtest
from mnq_research.cash_indicators import (
    LateMomentumConfig,
    MomentumConfig,
    PriorCash,
    ReversionConfig,
    prior_cash_inputs,
)
from mnq_research.roll_backtest import RollSegment
from mnq_research.walk_forward import coarse_grid, record_once


def test_cash_inputs_exclude_current_and_future_sessions() -> None:
    dates = pd.bdate_range("2026-01-05", periods=40)
    frames = []
    for number, day in enumerate(dates):
        index = pd.date_range(day + pd.Timedelta(hours=14, minutes=35), periods=78,
                              freq="5min", tz="UTC")
        frames.append(pd.DataFrame(
            {"open": 100., "high": 105., "low": 98.,
             "close": 100. + number / 10, "volume": 50}, index=index,
        ))
    bars = pd.concat(frames)
    cutoff = frames[30].index[0]
    day = cutoff.date()
    before = prior_cash_inputs(bars, frozenset())
    changed = bars.copy()
    changed.loc[cutoff:, ["close", "high", "low"]] *= 100
    after = prior_cash_inputs(changed, frozenset())
    assert before.daily[day] == after.daily[day]
    assert before.daily[day][0] == pytest.approx(102.9)
    assert before.slots[30][day] == after.slots[30][day]
    assert before.slots[30][day][0] == pytest.approx(.0145)
    assert date.fromisoformat("2026-01-05") not in before.daily
    assert before.slots[30] != after.slots[30]


def test_family_grid_matches_predeclared_hypotheses() -> None:
    assert [len(grid) for grid in coarse_grid().values()] == [9, 6, 4, 3]
    for schema, invalid in [
        (MomentumConfig, {"history_sessions": 0}),
        (ReversionConfig, {"efficiency_max": 2}),
        (LateMomentumConfig, {"signal_daily_atr": -1}),
    ]:
        with pytest.raises(ValidationError):
            schema.model_validate(invalid)


def test_trial_resume_keeps_completed_result_and_records_retry() -> None:
    with closing(connect(":memory:")) as db:
        db.execute(
            "CREATE TABLE results (fold, phase, trial, payload, PRIMARY KEY(fold,phase,trial))"
        )
        db.execute("CREATE TABLE failures (fold, phase, trial, error)")

        def fail() -> dict:
            raise ValueError("test failure")

        with pytest.raises(ValueError, match="test failure"):
            record_once(db, "fold", "training", "trial", fail)
        assert db.execute("SELECT error FROM failures").fetchone() == ("test failure",)
        result = record_once(db, "fold", "training", "trial", lambda: {"pnl": 7})
        assert record_once(db, "fold", "training", "trial", fail) == result


@pytest.mark.parametrize(
    ("candidate", "entry_clock"),
    [(MomentumConfig(stop_atr=2.0), "14:00"), (LateMomentumConfig(), "19:30")],
)
def test_momentum_entries_use_completed_scheduled_slots(
    tmp_path, monkeypatch, candidate, entry_clock: str
) -> None:
    day = date(2026, 4, 2)
    timestamps = pd.date_range("2026-04-02T13:20Z", "2026-04-02T20:00Z", freq="5min")
    decision = pd.Timestamp(f"2026-04-02T{entry_clock}Z")
    closes = [110.0 if timestamp >= decision else 100.0 for timestamp in timestamps]
    bars = market_bars(closes, "5m")
    bars.index = timestamps
    native = {"kind": "dated_contract", "symbol": "MNQM26"}
    segment = RollSegment(bars, native, timestamps[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    config = settings(start=timestamps[3].isoformat(), end=timestamps[-1].isoformat())
    folder = simulate_backtest(
        config, bars,
        {
            "provider": "continuous_mnq", "kind": "continuous_research",
            "symbol": "MNQ continuous", "bar_interval": "5m",
        },
        tmp_path, cash_sessions=frozenset({day}), candidate=candidate,
        prior_cash={"MNQM26": PriorCash({20: {day: (0.01,) * 78}}, {day: (100.0, 4.0)})},
    )
    fills = pd.read_csv(folder / "fills.csv")
    assert fills.exit_reason.tolist() == ["entry", "cash_session_exit"]
    assert pd.Timestamp(fills.timestamp.iloc[0]) == decision + pd.Timedelta(1, unit="ns")

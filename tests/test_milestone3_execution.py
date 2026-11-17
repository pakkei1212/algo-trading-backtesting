import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pandas as pd
import pytest
from test_research import market_bars, settings

from mnq_research.backtest import simulate_backtest
from mnq_research.cash_indicators import MomentumConfig, PriorCash
from mnq_research.cash_strategy import CashSessionStrategy, OrbConfig
from mnq_research.milestone3 import save_snapshot
from mnq_research.roll_backtest import RollSegment
from mnq_research.strategy import RollRiskState


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("ticks", [None, 1, 2, 4])
@pytest.mark.parametrize("gap", [False, True])
def test_adverse_entries_and_stops(tmp_path, monkeypatch, side, ticks, gap) -> None:
    closes = [100.0] * 4 + [100.0 + side * 3] + [100.0 + side * 4] * 76
    bars = market_bars(closes, "5m")
    bars.index = pd.date_range("2026-04-02T13:20Z", periods=len(bars), freq="5min")
    if gap:
        bars.iloc[6:, bars.columns.get_indexer(["open", "high", "low", "close"])] -= side * 20
    else:
        bars.iloc[6, bars.columns.get_loc("low" if side > 0 else "high")] -= side * 20
    segment = RollSegment(bars, {"kind": "dated_contract", "symbol": "MNQM26"}, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    config = settings(
        start=bars.index[3].isoformat(),
        end=bars.index[-1].isoformat(),
        commission_per_side_usd=1.5,
        slippage_ticks=1,
    )
    path = simulate_backtest(
        config,
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({date(2026, 4, 2)}),
        orb=OrbConfig(opening_range_minutes=5),
        stress_ticks=ticks,
    )
    fills = pd.read_csv(path / "fills.csv")
    ticks = ticks or 1
    stop = pd.read_csv(path / "protective_orders.csv").query("event == 'submitted'").iloc[0]
    assert fills.price.iloc[0] == 100 + side * 4 + side * ticks * 0.25
    reference = float(bars.open.iloc[6]) if gap else float(stop.price)
    assert fills.price.iloc[1] == reference - side * ticks * 0.25
    assert fills.exit_reason.iloc[1] == "protective_stop"
    assert fills.commission_usd.eq(1.5).all()
    positions = pd.read_csv(path / "positions.csv")
    first = positions.iloc[0]
    expected = side * (fills.price.iloc[1] - fills.price.iloc[0]) * 2 - 3
    assert float(first.realized_pnl.split()[0]) == expected


def test_persistent_halt_blocks_next_window(tmp_path, monkeypatch) -> None:
    bars = market_bars([100.0] * 4 + [103.0] + [104.0] * 76, "5m")
    bars.index = pd.date_range("2026-04-02T13:20Z", periods=len(bars), freq="5min")
    segment = RollSegment(bars, {"kind": "dated_contract", "symbol": "MNQM26"}, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    risk = RollRiskState(11000, True)
    result = simulate_backtest(
        settings(
            start=bars.index[3].isoformat(), end=bars.index[-1].isoformat(), initial_equity_usd=9500
        ),
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({date(2026, 4, 2)}),
        orb=OrbConfig(opening_range_minutes=5),
        persistent_risk=risk,
    )
    summary = json.loads((result / "summary.json").read_text())
    assert summary["closed_trades"] == 0
    assert summary["final_equity_usd"] == 9500
    assert summary["max_drawdown_fraction"] == pytest.approx(1 - 9500 / 11000)
    assert risk.halted and risk.high_water == 11000


@pytest.mark.parametrize("side", [1, -1])
def test_extreme_trail_waits_until_next_bar_and_fills_gap(tmp_path, monkeypatch, side) -> None:
    bars = market_bars([100.0] * 4 + [103.0] + [110.0] + [100.0] * 75, "5m")
    bars.index = pd.date_range("2026-04-02T13:20Z", periods=len(bars), freq="5min")
    bars.iloc[5, bars.columns.get_indexer(["open", "high", "low", "close"])] = [104, 111, 103, 110]
    if side == -1:
        bars[["open", "high", "low", "close"]] = (
            200 - bars[["open", "low", "high", "close"]].to_numpy()
        )
    segment = RollSegment(bars, {"kind": "dated_contract", "symbol": "MNQM26"}, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    result = simulate_backtest(
        settings(start=bars.index[3].isoformat(), end=bars.index[-1].isoformat()),
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({date(2026, 4, 2)}),
        orb=OrbConfig(opening_range_minutes=5),
        experimental_trail=True,
    )
    fills = pd.read_csv(result / "fills.csv")
    stops = pd.read_csv(result / "protective_orders.csv")
    updated = stops.loc[stops.event.eq("updated")].iloc[0]
    assert pd.Timestamp(updated.timestamp) == bars.index[5]
    assert updated.price * 4 == round(updated.price * 4)
    assert fills.exit_reason.iloc[1] == "trailing_stop"
    assert pd.Timestamp(fills.timestamp.iloc[1]) == bars.index[5] + pd.Timedelta(1, unit="ns")
    assert fills.price.iloc[1] == 100
    # The activating bar crossed the proposed stop but the prior stop was untouched.
    assert side * (float(bars.low.iloc[5] if side > 0 else bars.high.iloc[5]) - updated.price) < 0


@pytest.mark.parametrize("ticks", [1, 2, 4])
def test_scheduled_exit_costs_and_signal_attribution(tmp_path, monkeypatch, ticks) -> None:
    bars = market_bars([100.0] * 4 + [103.0] + [104.0] * 76, "5m")
    bars.index = pd.date_range("2026-04-02T13:20Z", periods=len(bars), freq="5min")
    segment = RollSegment(bars, {"kind": "dated_contract", "symbol": "MNQM26"}, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    label = {
        "volatility_label": "low",
        "efficiency_label": "unavailable",
        "efficiency_unavailable_reason": "fewer_than_13_cash_closes",
    }
    config = settings(
        start=bars.index[3].isoformat(), end=bars.index[-1].isoformat(), commission_per_side_usd=2.0
    )
    # Wide protective stop avoids an immediate stop with the largest friction case.
    result = simulate_backtest(
        config,
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({date(2026, 4, 2)}),
        orb=OrbConfig(opening_range_minutes=5, stop_atr=2.0),
        stress_ticks=ticks,
        regime_rows={bars.index[4].isoformat(): label},
    )
    fills = pd.read_csv(result / "fills.csv")
    assert fills.exit_reason.tolist() == ["entry", "cash_session_exit"]
    assert fills.price.tolist() == [104 + ticks * 0.25, 104 - ticks * 0.25]
    assert fills.cash_signal_id.nunique() == 1
    assert fills.volatility_label.eq("low").all()
    summary = json.loads((result / "summary.json").read_text())
    assert summary["net_pnl_usd"] == -ticks - 4


@pytest.mark.parametrize("side", [1, -1])
def test_trailing_boundary_ratchet_and_exit_precedence(side) -> None:
    changes, events = [], []
    stop = SimpleNamespace(is_open=True, trigger_price=100 - side * 2.5)
    state = SimpleNamespace(
        active_stop=stop,
        entry_atr=1.03,
        favorable_extreme=100.0,
        pending=None,
        halted=False,
        flattened_for_roll=False,
        trail_activated=False,
        entry_fill_points=100.0,
        initial_r_points=2.5,
        record=lambda *args, **kwargs: events.append((args, kwargs)),
        instrument=SimpleNamespace(make_price=lambda value: value),
    )

    def modify(_order, *, trigger_price) -> None:
        changes.append(trigger_price)
        stop.trigger_price = trigger_price

    state.modify_order = modify

    def bar(close, extreme):
        return SimpleNamespace(
            close=100 + side * close,
            high=100 + (extreme if side > 0 else 0),
            low=100 - (extreme if side < 0 else 0),
            ts_event=1000,
        )

    CashSessionStrategy.update_extreme_trail(state, bar(2.49, 4.13), side)
    assert not changes and not state.trail_activated
    CashSessionStrategy.update_extreme_trail(state, bar(2.5, 4.13), side)
    assert changes == [100 + side * 2.0]  # Round away; distance remains >= 2.06.
    CashSessionStrategy.update_extreme_trail(state, bar(2.3, 3), side)
    assert len(changes) == 1
    state.pending = ("exit", 0, 0)  # Existing signal/scheduled exit wins.
    CashSessionStrategy.update_extreme_trail(state, bar(8, 9), side)
    assert len(changes) == 1
    state.pending = None
    CashSessionStrategy.update_extreme_trail(state, bar(2.5, 9), side)
    assert state.exit_reason == "extreme_trail_crossed_close"
    assert state.pending == ("exit", 0, 0) and state.expected_open_ns == 1001
    assert len(changes) == 1  # Retain the old protection until the next-open exit.


def test_snapshot_export_denial_keeps_atomic_database(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    def denied(*args):
        raise PermissionError("Windows replacement denied")

    monkeypatch.setattr(Path, "replace", denied)
    with sqlite3.connect(tmp_path / "study.sqlite") as db:
        save_snapshot(db, tmp_path, {"folds": [1]})
        save_snapshot(db, tmp_path, {"folds": [1, 2]})
        assert json.loads(db.execute("SELECT payload FROM snapshot").fetchone()[0]) == {
            "folds": [1, 2]
        }
    assert json.loads((tmp_path / "summary.json").read_text()) == {"folds": [1, 2]}


@pytest.mark.parametrize("side", [1, -1])
def test_momentum_native_trail_and_signal_exit_precedence(tmp_path, monkeypatch, side) -> None:
    day = date(2026, 4, 2)
    timestamps = pd.date_range("2026-04-02T13:20Z", "2026-04-02T20:00Z", freq="5min")
    bars = market_bars([100.0] * len(timestamps), "5m")
    bars.index = timestamps
    bars.loc["2026-04-02T14:00Z", ["open", "high", "low", "close"]] = [100, 103, 100, 103]
    bars.loc["2026-04-02T14:05Z", ["open", "high", "low", "close"]] = [104, 110, 103, 109]
    if side == -1:
        bars[["open", "high", "low", "close"]] = (
            200 - bars[["open", "low", "high", "close"]].to_numpy()
        )
    segment = RollSegment(bars, {"kind": "dated_contract", "symbol": "MNQM26"}, bars.index[3], None)
    monkeypatch.setattr("mnq_research.roll_backtest.prepare_segments", lambda *args: [segment])
    path = simulate_backtest(
        settings(start=bars.index[3].isoformat(), end=bars.index[-1].isoformat()),
        bars,
        {
            "provider": "continuous_mnq",
            "kind": "continuous_research",
            "symbol": "MNQ continuous",
            "bar_interval": "5m",
        },
        tmp_path,
        cash_sessions=frozenset({day}),
        candidate=MomentumConfig(stop_atr=2.0),
        prior_cash={"MNQM26": PriorCash({20: {day: (0.01,) * 78}}, {})},
        experimental_trail=True,
    )
    fills = pd.read_csv(path / "fills.csv")
    assert fills.exit_reason.tolist() == ["entry", "trailing_stop"]
    assert pd.Timestamp(fills.timestamp.iloc[0]) == pd.Timestamp(
        "2026-04-02T14:00Z"
    ) + pd.Timedelta(1, "ns")
    assert pd.Timestamp(fills.timestamp.iloc[1]) == pd.Timestamp(
        "2026-04-02T14:05Z"
    ) + pd.Timedelta(1, "ns")
    assert fills.price.iloc[1] == 100

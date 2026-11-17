"""Rule timing and safety scenarios; fixtures are not profitability evidence."""

import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError
from test_research import dataset, market_bars, read_run, settings

from mnq_research.backtest import run_backtest
from mnq_research.config import StrategyConfig, load_config, save_config
from mnq_research.criteria import (
    BuilderConfig,
    Condition,
    ConditionGroup,
    DirectionCriteria,
    Operand,
    trend_pullback,
)
from mnq_research.data import write_dataset
from mnq_research.reporting import chart_payload, load_run
from mnq_research.rule_engine import RuleEngine


def group(identifier: str, operator: str, value: float) -> ConditionGroup:
    return ConditionGroup.model_validate(
        dict(
            conditions=[
                dict(
                    id=identifier,
                    left=dict(kind="close"),
                    operator=operator,
                    right=dict(kind="number", value=value),
                )
            ]
        )
    )


def simple_builder(**changes) -> BuilderConfig:
    return BuilderConfig.model_validate(
        {
            "long": DirectionCriteria(
                market_filter=group("filter", "above", 0),
                setup=group("setup", "below", 100),
                trigger=group("trigger", "crosses_above", 100),
                signal_exit=group("exit", "below", 99),
            ),
            "setup_window_bars": 2,
            "max_holding_bars": 2,
            **changes,
        }
    )


def builder_settings(**changes) -> StrategyConfig:
    return settings(
        **{
            "schema_version": 2,
            "entry_rule": "criteria",
            "exit_rule": "criteria",
            "direction": "long",
            "builder": simple_builder(),
            **changes,
        }
    )


def update(engine: RuleEngine, close: float, net: float = 0, holding: int = 0) -> list[dict]:
    engine.update(dict(open=close, high=close + 0.25, low=close - 0.25, close=close))
    return engine.evaluate(net, holding)


def reasons(events: list[dict]) -> list[str]:
    return [event["reason"] for event in events]


def test_models_reject_ambiguous_or_unsafe_settings(tmp_path: Path) -> None:
    for operand in [
        dict(kind="ema"),
        dict(kind="close", period=20),
        dict(kind="number", value=float("nan")),
        dict(kind="number", value=1, offset=1),
        dict(kind="python", value=1),
    ]:
        with pytest.raises(ValidationError):
            Operand.model_validate(operand)
    with pytest.raises(ValidationError, match="schema version 2"):
        builder_settings(schema_version=1)
    with pytest.raises(ValidationError, match="separate criteria"):
        builder_settings(direction="both")
    duplicate = simple_builder().model_dump()
    duplicate["long"]["trigger"]["conditions"][0]["id"] = "setup"
    with pytest.raises(ValidationError, match="unique"):
        BuilderConfig.model_validate(duplicate)
    with pytest.raises(ValidationError):
        ConditionGroup(conditions=[])
    config = builder_settings()
    save_config(config, tmp_path / "strategy.yaml")
    assert load_config(tmp_path / "strategy.yaml") == config
    assert load_config(Path("configs/baseline.yaml")).builder is None


def test_indicator_seed_offsets_and_crossing_equality() -> None:
    builder = trend_pullback().model_dump()
    for section in builder["long"].values():
        for condition in section["conditions"]:
            for operand in (condition["left"], condition["right"]):
                if operand["kind"] == "ema":
                    operand["period"] = 3
                    operand["offset"] = min(operand["offset"], 1)
    engine = RuleEngine(BuilderConfig.model_validate(builder), 2)
    update(engine, 10)
    assert "ema_3" not in engine.history[-1] and engine.atr is None
    update(engine, 12)
    assert engine.atr == pytest.approx(1.375)
    update(engine, 14)
    assert engine.history[-1]["ema_3"] == 12
    update(engine, 16)
    assert engine.history[-1]["ema_3"] == 14
    assert engine.value(Operand(kind="ema", period=3, offset=1)) == 12
    condition = Condition(
        id="cross",
        left=Operand(kind="close"),
        operator="crosses_above",
        right=Operand(kind="number", value=14),
    )
    assert engine.condition(condition)["passed"]
    update(engine, 17)
    assert not engine.condition(condition)["passed"]


def test_all_any_and_unavailable_are_explicit() -> None:
    engine = RuleEngine(simple_builder(), 2)
    update(engine, 101)
    true = group("true", "above", 100).conditions[0]
    false = group("false", "below", 100).conditions[0]
    assert not engine.group(ConditionGroup(mode="all", conditions=[true, false])).passed
    assert engine.group(ConditionGroup(mode="any", conditions=[true, false])).passed
    missing = Condition(
        id="missing",
        left=Operand(kind="close", offset=5),
        operator="above",
        right=Operand(kind="number", value=0),
    )
    result = engine.group(ConditionGroup(mode="any", conditions=[true, missing]))
    assert not result.available and not result.passed
    update(engine, 100)
    assert not engine.condition(true)["passed"]  # Equality is not above.


def test_setup_window_is_inclusive_and_cannot_extend() -> None:
    engine = RuleEngine(simple_builder(), 2)
    update(engine, 101)
    assert "setup_activated" in reasons(update(engine, 99))
    activated = engine.setups[1]
    update(engine, 99)
    assert engine.setups[1] == activated
    assert "entry_signal" in reasons(update(engine, 101))  # Last permitted bar.
    assert not engine.setups
    assert "entry_signal" not in reasons(update(engine, 102))
    engine = RuleEngine(simple_builder(), 2)
    for close in [101, 99, 99, 99]:
        update(engine, close)
    expired = update(engine, 101)
    assert "setup_expired" in reasons(expired) and "entry_signal" not in reasons(expired)


def test_filter_failure_expires_setup_but_does_not_exit_position() -> None:
    initial = simple_builder().model_dump()
    initial["long"]["market_filter"] = group("filter", "above", 98).model_dump()
    engine = RuleEngine(BuilderConfig.model_validate(initial), 2)
    for close in [101, 99]:
        update(engine, close)
    assert "setup_expired" in reasons(update(engine, 97))
    # A failed entry filter does not itself produce an exit.
    initial["long"]["signal_exit"] = group("exit", "below", 90).model_dump()
    engine = RuleEngine(BuilderConfig.model_validate(initial), 2)
    update(engine, 100)
    assert "exit_signal" not in reasons(update(engine, 97, net=1, holding=1))


def test_holding_exit_precedes_signal_exit_and_records_both() -> None:
    engine = RuleEngine(simple_builder(), 2)
    update(engine, 100)
    events = update(engine, 98, net=1, holding=2)
    exit_event = next(event for event in events if event["reason"] == "exit_signal")
    assert exit_event["primary_reason"] == "holding_limit"
    assert exit_event["matched_reasons"] == ["holding_limit", "signal_exit"]


def test_conflicting_directions_are_consumed_and_skipped() -> None:
    initial = simple_builder().model_dump()
    initial["short"] = json.loads(json.dumps(initial["long"]).replace('"id": "', '"id": "short_'))
    engine = RuleEngine(BuilderConfig.model_validate(initial), 2)
    for close in [101, 99]:
        update(engine, close)
    events = update(engine, 101)
    assert "entry_skipped_conflicting_directions" in reasons(events)
    assert "entry_signal" not in reasons(events) and not engine.setups


def test_builder_next_open_holding_exit_and_reporting(tmp_path: Path) -> None:
    # Trigger at bar 3 close; entry at bar 4 open. Bar 4/5 closes count as 1/2.
    closes = [101, 101, 99, 101, 110, 111, 112, 113]
    run = run_backtest(builder_settings(), dataset(tmp_path, closes), tmp_path / "runs")
    fills = read_run(run, "fills")
    assert fills.price.tolist() == [110, 112]
    assert fills.exit_reason.tolist() == ["entry", "holding_limit"]
    assert fills.matched_exit_reasons.iloc[-1] == "holding_limit"
    report = load_run(run)
    assert "setup triggered after 1 observed bars" in report.trades[0].entry_explanation
    assert "holding limit" in report.trades[0].exit_explanation
    assert json.loads(
        next(
            e.detail["conditions_json"] for e in report.events if e.reason == "criteria_evaluation"
        )
    )
    json.dumps(chart_payload(report), allow_nan=False)
    assert report.summary["net_pnl_usd"] == 2  # $4 gross, $2 commissions.


def test_builder_stop_gap_and_halt_priority(tmp_path: Path) -> None:
    closes = [101, 101, 99, 101, 110, 98, 97, 99]
    run = run_backtest(
        builder_settings(atr_multiple=1), dataset(tmp_path, closes), tmp_path / "stops"
    )
    fills = read_run(run, "fills")
    assert fills.exit_reason.tolist() == ["entry", "protective_stop"]
    assert fills.price.iloc[-1] == 98  # Observed gap, never invented at the stop.
    run = run_backtest(
        builder_settings(atr_multiple=20, max_drawdown_fraction=0.001),
        dataset(tmp_path, closes, "halt"),
        tmp_path / "halt-runs",
    )
    fills = read_run(run, "fills")
    assert fills.exit_reason.iloc[-1] == "drawdown_halt"
    assert fills.matched_exit_reasons.iloc[-1] == "drawdown_halt;holding_limit;signal_exit"


def test_builder_future_changes_and_holdout_do_not_leak(tmp_path: Path) -> None:
    prefix = [101, 101, 99, 101, 110, 111]
    runs = [
        run_backtest(
            builder_settings(max_drawdown_fraction=0.5),
            dataset(tmp_path, prefix + suffix, name),
            tmp_path / "runs",
        )
        for name, suffix in [("a", [112, 113]), ("b", [95, 90])]
    ]
    for name in ("decisions", "fills", "equity", "observations"):
        frames = [read_run(run, name) for run in runs]
        frames = [
            frame[
                pd.to_datetime(frame.timestamp, format="ISO8601") <= market_bars(prefix).index[-1]
            ].drop(columns="order_id", errors="ignore")
            for frame in frames
        ]
        pd.testing.assert_frame_equal(*[frame.reset_index(drop=True) for frame in frames])
    config = builder_settings(start="2026-01-01T03:00:00Z")
    holdout = run_backtest(
        config, dataset(tmp_path, prefix + [112, 113], "holdout"), tmp_path / "holdout-runs"
    )
    assert load_run(holdout).trades == []  # Pre-evaluation setups are never carried in.


def test_builder_ema_prefix_is_same_with_evaluation_start(tmp_path: Path) -> None:
    config = builder_settings(builder=trend_pullback())
    closes = [100 + index % 30 for index in range(240)]
    folder = dataset(tmp_path, closes)
    full = run_backtest(config, folder, tmp_path / "full")
    config = StrategyConfig.model_validate(
        {**config.model_dump(), "start": market_bars(closes).index[220]}
    )
    cut = run_backtest(config, folder, tmp_path / "cut")
    pd.testing.assert_frame_equal(read_run(full, "observations"), read_run(cut, "observations"))
    assert any(
        definition["key"] == "ema_200"
        for definition in chart_payload(load_run(cut))["indicator_definitions"]
    )


def test_protective_stop_remains_active_until_signal_exit_fill(tmp_path: Path) -> None:
    closes = [101, 101, 99, 101, 110, 111, 98, 99]
    frame = market_bars(closes)
    # Limit reached at bar 5 close; next opening gap activates the existing stop.
    folder = write_dataset(frame, tmp_path / "market", {"kind": "test_fixture", "symbol": "MNQ"})
    run = run_backtest(builder_settings(atr_multiple=1), folder, tmp_path / "runs")
    fills = read_run(run, "fills")
    assert len(fills) == 2  # No extra close/reversal order after liquidation.
    assert fills.price.iloc[-1] == 98


def test_short_criteria_are_used_without_mirroring(tmp_path: Path) -> None:
    long = simple_builder().long
    assert long is not None
    data = long.model_dump()
    data["setup"] = group("setup", "above", 100).model_dump()
    data["trigger"] = group("trigger", "crosses_below", 100).model_dump()
    data["signal_exit"] = group("exit", "above", 110).model_dump()
    config = builder_settings(
        direction="short",
        builder=BuilderConfig(short=DirectionCriteria.model_validate(data), max_holding_bars=2),
    )
    run = run_backtest(
        config, dataset(tmp_path, [99, 99, 101, 99, 90, 89, 88, 87]), tmp_path / "runs"
    )
    assert read_run(run, "fills").side.tolist() == ["SELL", "BUY"]


def test_builder_gaps_count_observations_and_final_orders_stay_pending(tmp_path: Path) -> None:
    frame = market_bars([101, 101, 99, 101, 110, 111])
    frame.index = pd.DatetimeIndex(
        [
            "2026-01-02T20:00Z",
            "2026-01-02T21:00Z",
            "2026-01-02T22:00Z",
            "2026-01-05T01:00Z",
            "2026-01-05T02:00Z",
            "2026-01-05T03:00Z",
        ]
    )
    folder = write_dataset(frame, tmp_path / "market", {"kind": "test_fixture", "symbol": "MNQ"})
    run = run_backtest(builder_settings(), folder, tmp_path / "runs")
    report = load_run(run)
    assert len(report.trades) == 1 and report.trades[0].status == "Open"
    assert report.summary["pending_at_end"]
    assert "setup triggered after 1 observed bars" in report.trades[0].entry_explanation
    assert "holding_limit" in [e.detail.get("primary_reason") for e in report.events]
    assert len(report.chart.candles) == 6
    # ATR stop exists but no invented terminal exit or synthetic weekend candles.
    assert len(read_run(run, "fills")) == 1

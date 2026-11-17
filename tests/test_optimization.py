"""Native simulation parity and durable grid study behavior."""

import json
from pathlib import Path

import pandas as pd
import pytest
from test_research import dataset, settings

from mnq_research.backtest import run_backtest, simulate_backtest
from mnq_research.data import load_dataset
from mnq_research.optimization import (
    OptimizationSpec,
    cancel_study,
    create_study,
    read_holdout,
    read_study,
    reveal_holdout,
    run_study,
    validate_study_windows,
    window,
)
from mnq_research.tpe_ranges import RangeCandidates, TpeRanges


def test_grid_resume_and_native_summary_parity(tmp_path: Path) -> None:
    data = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112] * 6)
    bars, metadata = load_dataset(data)
    spec = OptimizationSpec(
        dataset=data,
        checksum=metadata["sha256"],
        base=settings(),
        fast=[3, 2, 2],
        slow=[3, 4],
        atr=[1],
        train=(bars.index[8].isoformat(), bars.index[23].isoformat()),
        validation=(bars.index[24].isoformat(), bars.index[39].isoformat()),
        holdout=(bars.index[40].isoformat(), bars.index[-1].isoformat()),
        minimum_days=1,
        minimum_bars=1,
        train_trades=1,
        validation_trades=1,
    )
    configs = spec.candidates()
    assert [(c.fast_period, c.slow_period) for c in configs] == [(2, 3), (2, 4), (3, 4)]
    with pytest.raises(ValueError, match="3 valid combinations"):
        spec.model_copy(update={"max_trials": 1}).candidates()
    config = window(configs[0], spec.train)
    report = run_backtest(config, data, tmp_path / "reports")
    expected = json.loads((report / "summary.json").read_text())
    assert simulate_backtest(config, bars, metadata) == expected
    assert pd.Timestamp(expected["first_bar"]) == pd.Timestamp(spec.train[0])
    assert pd.Timestamp(expected["last_bar"]) == pd.Timestamp(spec.train[1])

    folder = tmp_path / "studies" / "one"
    create_study(folder, spec)
    cancel_study(folder)
    run_study(folder)
    assert read_study(folder)[1:] == ("Canceled", [])
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(folder / "study.sqlite")) as db, db:
        db.execute("UPDATE state SET canceled=0")
    run_study(folder)
    _, status, rows = read_study(folder)
    assert status == "Completed"
    assert sum(row["phase"] == "Training" for row in rows) == 3
    run_study(folder)
    assert read_study(folder)[2] == rows

    assert read_holdout(folder) is None
    assert all(row["phase"] != "Holdout" for row in rows)
    shortlisted = [
        row["candidate"] for row in rows if row["phase"] == "Validation" and row["candidate"] >= 0
    ]
    assert shortlisted
    selected = shortlisted[0]
    held = reveal_holdout(folder, selected)
    assert held["phase"] == "Holdout"
    assert read_holdout(folder) == (selected, held)
    assert read_study(folder)[2] == rows


def test_continuous_preflight_uses_longest_warmup_and_labels_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stamps = pd.date_range("2026-01-05T00:00Z", periods=250, freq="5min")
    bars = pd.DataFrame(index=stamps)
    spec = OptimizationSpec(
        dataset=tmp_path,
        checksum="manifest",
        base=settings(),
        fast=[2],
        slow=[3, 20],
        atr=[1],
        train=(stamps[50].isoformat(), stamps[100].isoformat()),
        validation=(stamps[101].isoformat(), stamps[200].isoformat()),
        minimum_bars=1,
        minimum_days=1,
    )
    calls = []

    def prepare_for_test(config, frame, metadata):
        calls.append(config.warmup_bars())
        if config.start == stamps[101]:
            raise ValueError("Missing scheduled flatten open.")
        from mnq_research.roll_backtest import RollSegment

        return [RollSegment(frame.loc[config.start : config.end], {}, config.start, None)]

    monkeypatch.setattr("mnq_research.optimization.prepare_segments", prepare_for_test)
    metadata = {
        "provider": "continuous_mnq",
        "kind": "continuous_research",
        "sha256": "bars",
        "study_checksum": "manifest",
    }
    with pytest.raises(ValueError, match="Validation: Missing scheduled flatten open"):
        validate_study_windows(spec, bars, metadata, spec.candidates())
    assert calls == [21, 21]


@pytest.mark.parametrize("numeric", [False, True])
def test_tpe_resume_replays_unique_trials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, numeric: bool
) -> None:
    pytest.importorskip("optuna")
    import sqlite3
    from contextlib import closing

    data = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112] * 10)
    bars, metadata = load_dataset(data)
    parameters = (
        {
            "ranges": TpeRanges(
                fast_min=2,
                fast_max=6,
                slow_min=3,
                slow_max=7,
                atr_min="1.0",
                atr_max="1.2",
                atr_step="0.1",
            )
        }
        if numeric
        else {"fast": [2, 3], "slow": [4, 5, 6, 7], "atr": [1, 2, 3]}
    )
    spec = OptimizationSpec(
        dataset=data,
        checksum=metadata["sha256"],
        base=settings(),
        **parameters,
        method="Optuna TPE",
        seed=9,
        max_trials=16,
        train=(bars.index[12].isoformat(), bars.index[40].isoformat()),
        validation=(bars.index[41].isoformat(), bars.index[-1].isoformat()),
        minimum_days=1,
        minimum_bars=1,
    )
    interrupted = tmp_path / "interrupted"
    fresh = tmp_path / "fresh"
    create_study(interrupted, spec)
    create_study(fresh, spec)
    stop_after = 7
    observed = []

    def fake_evaluate(spec, config, frame, metadata, phase, candidate_id):
        observed.append((phase, candidate_id))
        if stop_after and len(observed) == stop_after:
            cancel_study(interrupted)
        return {
            "phase": phase,
            "candidate": candidate_id,
            "score": float(candidate_id) if candidate_id % 3 else None,
            "max_drawdown_fraction": 0.04,
        }

    monkeypatch.setattr("mnq_research.optimization.evaluate", fake_evaluate)
    run_study(interrupted)
    assert read_study(interrupted)[1] == "Canceled"
    assert len(observed) == 7 and all(phase == "Training" for phase, _ in observed)
    with closing(sqlite3.connect(interrupted / "study.sqlite")) as db, db:
        db.execute("UPDATE state SET canceled=0")
    stop_after = 0
    run_study(interrupted)
    assert read_study(interrupted)[1] == "Completed"
    resumed_order = observed[:]
    observed.clear()
    run_study(fresh)
    assert read_study(fresh)[1] == "Completed"
    assert observed == resumed_order
    training = [index for phase, index in observed if phase == "Training"]
    assert len(training) == len(set(training)) == 16
    assert observed[16][0] == "Validation"


def test_numeric_tpe_counts_valid_pairs_and_decimal_steps() -> None:
    default = RangeCandidates(settings(), TpeRanges())
    assert len(default) == 40656
    assert default.parameters(0) == (5, 30, 1.0)
    assert default.parameters(-1) == (25, 150, 2.5)
    overlap = RangeCandidates(
        settings(),
        TpeRanges(
            fast_min=3,
            fast_max=5,
            slow_min=3,
            slow_max=5,
            atr_min="1.0",
            atr_max="1.2",
            atr_step="0.1",
        ),
    )
    assert len(overlap) == 9
    assert overlap.index_for(5, 5, 1.1) is None
    assert overlap.index_for(4, 5, 1.2000000000000002) == 8
    assert overlap[8].atr_multiple == 1.2
    with pytest.raises(ValueError, match="align with the step"):
        TpeRanges(atr_min="1.0", atr_max="1.3", atr_step="0.2")
    with pytest.raises(ValueError, match="No valid combinations"):
        TpeRanges(fast_min=5, fast_max=6, slow_min=3, slow_max=5)

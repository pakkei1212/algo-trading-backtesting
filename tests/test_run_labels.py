"""Human-readable selector names for saved runs."""

import json
from pathlib import Path

from mnq_research.run_labels import format_run_label


def test_format_run_label_identifies_strategy_data_period_and_run(tmp_path: Path) -> None:
    run = tmp_path / "20261110T081129-bc4babc9"
    run.mkdir()
    (run / "summary.json").write_text(
        json.dumps(
            {
                "symbol": "MNQ=F",
                "bar_interval": "5m",
                "first_bar": "2026-09-11T08:15:00+00:00",
                "last_bar": "2026-11-10T08:05:00+00:00",
                "closed_trades": 8,
                "halted": True,
            }
        ),
        encoding="utf-8",
    )
    (run / "config.yaml").write_text(
        "entry_rule: sma_cross\nfast_period: 20\nslow_period: 50\n", encoding="utf-8"
    )

    assert format_run_label(run / "summary.json") == (
        "SMA crossover 20/50 · MNQ=F · 5 min · Sep 11–Nov 10, 2026 · "
        "8 trades · Halted · bc4babc9"
    )


def test_format_run_label_handles_legacy_missing_metadata(tmp_path: Path) -> None:
    run = tmp_path / "legacy-run"
    run.mkdir()
    (run / "summary.json").write_text("{}", encoding="utf-8")

    assert format_run_label(run / "summary.json") == (
        "Custom strategy · MNQ · timeframe unknown · dates unavailable · "
        "0 trades · Completed · legacy-run"
    )

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from test_research import dataset, read_run, settings

from mnq_research.backtest import run_backtest
from mnq_research.config import StrategyConfig
from mnq_research.variant_comparison import (
    PHASES,
    matched_table,
    phase_metrics,
    register_trials,
    registry,
)
from mnq_research.variant_comparison_ui import chart_points


@pytest.mark.parametrize("halt_enabled", [False, True])
def test_protective_stop_then_ten_percent_breach(tmp_path: Path, halt_enabled: bool) -> None:
    bars = dataset(tmp_path, [103, 102, 101, 102, 103, 104, 45, 46, 47, 48, 49, 50, 45, 42, 40])
    values = settings(
        initial_equity_usd=1000,
        margin_per_contract_usd=100,
        atr_multiple=1,
        max_drawdown_fraction=0.10,
    ).model_dump()
    # With no explicit flag this is an old saved configuration.
    values.pop("drawdown_halt_enabled")
    if not halt_enabled:
        values["drawdown_halt_enabled"] = False
    config = StrategyConfig.model_validate(values)
    assert config.drawdown_halt_enabled is halt_enabled
    run = run_backtest(config, bars, tmp_path / "runs")
    fills = read_run(run, "fills")
    assert fills.exit_reason.iloc[1] == "protective_stop"
    assert fills.price.iloc[1] == 45
    summary = json.loads((run / "summary.json").read_text())
    assert summary["max_drawdown_fraction"] >= 0.10
    assert summary["halted"] is halt_enabled
    decisions = read_run(run, "decisions")
    if halt_enabled:
        assert len(fills) == 2
        assert decisions.reason.eq("drawdown_halt").sum() == 1
    else:
        assert fills.exit_reason.eq("entry").sum() > 1
        assert decisions.reason.eq("drawdown_threshold_breach").any()
        assert not decisions.reason.eq("drawdown_halt").any()


def test_registers_all_phases_and_keeps_failed_metrics_blank() -> None:
    configs = registry(settings(drawdown_halt_enabled=False))
    windows = {
        phase: {
            "first": f"2026-0{month}-01",
            "last": f"2026-0{month}-02",
            "eligible_days": [f"2026-0{month}-02"],
        }
        for phase, month in zip(PHASES, (2, 3, 4), strict=True)
    }
    manifest = {
        "recipe": "fixture",
        "source_sha256": "src",
        "data_checksum": "data",
        "registry": configs,
        "exposure": "Development-exposed",
        "folds": [{"as_of": "2026-03-31", "month": "2026-04", "windows": windows}],
    }
    db = sqlite3.connect(":memory:")
    try:
        db.executescript("""
            CREATE TABLE executions(key TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE phases(fold TEXT,phase TEXT,config TEXT,execution TEXT,
                PRIMARY KEY(fold,phase,config));
        """)
        assert len(register_trials(db, manifest)) == 114
        assert len(register_trials(db, manifest)) == 114
        assert db.execute("SELECT phase,count(*) FROM phases GROUP BY phase").fetchall() == [
            ("oot", 38),
            ("training", 38),
            ("validation", 38),
        ]
    finally:
        db.close()
    config_id = configs[0]["id"]
    phase_key = ("2026-03-31", config_id)
    table = matched_table(
        manifest,
        {
            (*phase_key, "training"): {"status": "completed", "metrics": {"return_pct": -3}},
            (*phase_key, "validation"): {"status": "failed", "error": "fixture failure"},
            (*phase_key, "oot"): {"status": "completed", "metrics": {"return_pct": 2}},
        },
    )
    row = table.loc[table["Configuration ID"].eq(config_id)].iloc[0]
    assert (row["Training return %"], row["OOT return %"]) == (-3, 2)
    assert row["Validation status"] == "failed" and pd.isna(row["Validation return %"])
    assert len(table) == 38 and table["Configuration ID"].is_unique


def test_daily_metrics_preserve_flat_days_and_reconcile_native_pnl() -> None:
    summary = {
        "daily_pnl_usd": {"2026-04-01": 0, "2026-04-02": 30, "2026-04-03": -10},
        "net_pnl_usd": 20,
        "max_drawdown_fraction": 0.01,
        "closed_trades": 2,
        "commission_usd": 4,
        "slippage_cost_usd": 2,
        "average_closed_trade_usd": 10,
        "halted": False,
        "active_days": 2,
    }
    dates = list(summary["daily_pnl_usd"])
    result = phase_metrics(summary, 10000, dates)
    assert result["return_pct"] == 0.2 and result["drawdown_pct"] == 1
    assert result["pnl_per_session_usd"] == pytest.approx(20 / 3)
    assert result["costs_usd"] == 6 and result["expectancy_usd"] == 10
    with pytest.raises(ValueError, match="reconcile"):
        phase_metrics({**summary, "net_pnl_usd": 19}, 10000, dates)


def test_chart_display_preserves_extrema_and_endpoints() -> None:
    index = pd.date_range("2026-04-01", periods=2000, freq="5min", tz="UTC")
    equity = pd.Series(10000.0, index=index)
    equity.iloc[720] = 11000.0
    equity.iloc[721] = 7800.0
    displayed = chart_points(equity)
    assert len(displayed) < len(equity)
    assert displayed.index.is_monotonic_increasing
    assert index[0] == displayed.index[0] and index[-1] == displayed.index[-1]
    assert displayed.loc[index[720]] == 11000.0 and displayed.loc[index[721]] == 7800.0

import pytest

from mnq_research.research_diagnostics import paired_bootstrap, qualification_checks


def test_qualification_boundaries_and_simultaneous_failures() -> None:
    row = {
        "net_pnl_usd": 1, "max_drawdown_fraction": .1, "halted": False,
        "closed_trades": 30, "active_days": 20,
    }
    assert all(qualification_checks(row, "training", 0).values())
    row.update(halted=True, net_pnl_usd=0, active_days=0)
    checks = qualification_checks(row, "validation", 0)
    assert not checks["no_permanent_halt"]
    assert not checks["positive_profit"]
    assert not checks["enough_active_days"]
    assert not checks["beats_paired_sma"]


def test_bootstrap_keeps_flat_days_alignment_and_determinism() -> None:
    left = {f"2026-04-{day:02}": 0. for day in range(1, 22)}
    right = {day: -2. for day in left}
    result = paired_bootstrap(left, right)
    assert result["sessions"] == 21
    assert result["lower_95_usd"] == result["upper_95_usd"] == 2
    assert paired_bootstrap(left, right) == result
    assert paired_bootstrap(left, left)["status"] == "inconclusive"
    with pytest.raises(ValueError, match="identical"):
        paired_bootstrap(left, {})

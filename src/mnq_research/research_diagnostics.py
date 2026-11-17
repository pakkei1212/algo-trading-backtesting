"""Auditable qualification checks and paired historical uncertainty."""

import numpy as np
import pandas as pd


def qualification_checks(row: dict, phase: str, baseline_pnl: float) -> dict[str, bool]:
    training = phase == "training"
    checks = {
        "positive_profit": row["net_pnl_usd"] > 0,
        "drawdown_within_limit": row["max_drawdown_fraction"] <= .10,
        "no_permanent_halt": not row["halted"],
        "enough_closed_trades": row["closed_trades"] >= (30 if training else 10),
        "enough_active_days": row["active_days"] >= (20 if training else 8),
    }
    if not training:
        checks["beats_paired_sma"] = row["net_pnl_usd"] > baseline_pnl
    return checks


def qualification_rows(fold: dict) -> list[dict]:
    records = []
    for phase in ("training", "validation"):
        seen = set()
        for row in fold[phase]:
            if row["key"] in seen:
                continue
            seen.add(row["key"])
            checks = qualification_checks(
                row, phase, fold["reference"]["validation"]["net_pnl_usd"]
            )
            failures = [name for name, passed in checks.items() if not passed]
            if bool(failures) != bool(row["reasons"]):
                raise ValueError("Stored qualification differs from reconstructed checks")
            halt = next(iter(row["halt_times_utc"]), None)
            elapsed = None
            if halt:
                start = pd.Timestamp(row["first_bar"])
                elapsed = (pd.Timestamp(halt) - start).total_seconds() / 86400
            records.append({
                "phase": phase, "family": row["family"], "configuration": row["params"],
                "checks": checks, "first_rejection": next(iter(failures), None),
                "all_failed_checks": failures, "qualified": not failures,
                "trades": row["closed_trades"], "active_days": row["active_days"],
                "halt_utc": halt, "elapsed_calendar_days_until_halt": elapsed,
                "key": row["key"],
            })
    return records


def paired_bootstrap(
    left: dict[str, float], right: dict[str, float], block_length: int = 5,
    *, seed: int = 0, repetitions: int = 2000,
) -> dict:
    """Circular stationary bootstrap with geometric block lengths and paired dates."""
    if set(left) != set(right) or not left:
        raise ValueError("Paired daily series require identical nonempty date sets")
    if block_length < 1 or repetitions < 2:
        raise ValueError("Positive block length and at least two repetitions required")
    dates = sorted(left)
    differences = np.array([left[day] - right[day] for day in dates], dtype=float)
    if not np.isfinite(differences).all():
        raise ValueError("Daily P&L must be finite")
    rng = np.random.default_rng(seed)
    means = np.empty(repetitions)
    for sample in range(repetitions):
        indices = np.empty(len(dates), dtype=int)
        indices[0] = rng.integers(len(dates))
        for position in range(1, len(dates)):
            indices[position] = (
                rng.integers(len(dates)) if rng.random() < 1 / block_length
                else (indices[position - 1] + 1) % len(dates)
            )
        means[sample] = differences[indices].mean()
    lower, upper = np.quantile(means, [.025, .975], method="linear")
    return {
        "method": "circular_stationary_bootstrap_v1",
        "mean_block_sessions": block_length, "seed": seed, "replications": repetitions,
        "sessions": len(dates), "first_date": dates[0], "last_date": dates[-1],
        "paired_mean_daily_usd": float(differences.mean()),
        "lower_95_usd": float(lower), "upper_95_usd": float(upper),
        "status": (
            "inconclusive" if len(dates) < 20 or lower <= 0 <= upper else "historical interval"
        ),
    }

"""Local, resumable four-family historical walk-forward experiment."""

import argparse
import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from contextlib import closing
from datetime import date
from importlib.metadata import version
from itertools import product
from pathlib import Path

import pandas as pd

from mnq_research.backtest import CashMarketCache, load_backtest_dataset, simulate_backtest
from mnq_research.cash_indicators import (
    LateMomentumConfig,
    MomentumConfig,
    ReversionConfig,
    prior_cash_inputs,
)
from mnq_research.cash_research import SPECIAL_DATES, cash_timestamp, qualify, session_calendar
from mnq_research.cash_strategy import CASH_FIRST_CLOSE, CASH_LAST_CLOSE, CashConfig, OrbConfig
from mnq_research.config import StrategyConfig
from mnq_research.data import load_dataset
from mnq_research.optimization import source_version
from mnq_research.research_exposure import archive_exposures

RECIPE = "four_family_cash_v1"
BASELINE = "sma_cash_v1"
PERIODS = (
    ("2026-02-27", "2026-03-02", "2026-03-31", "2026-04-01", "2026-04-30"),
    ("2026-03-31", "2026-04-01", "2026-04-30", "2026-05-01", "2026-05-29"),
    ("2026-04-30", "2026-05-01", "2026-05-29", "2026-06-01", "2026-06-30"),
    ("2026-05-29", "2026-06-01", "2026-06-30", "2026-07-01", "2026-07-31"),
    ("2026-06-30", "2026-07-01", "2026-07-31", "2026-08-03", "2026-08-31"),
    ("2026-07-31", "2026-08-03", "2026-08-31", "2026-09-01", "2026-09-30"),
    ("2026-08-31", "2026-09-01", "2026-09-30", "2026-10-01", "2026-10-30"),
    ("2026-09-30", "2026-10-01", "2026-10-30", "2026-11-02", "2026-11-10"),
)
FAMILIES = {
    "orb_cash_v1": OrbConfig,
    "slot_momentum_cash_v1": MomentumConfig,
    "vwap_reversion_cash_v1": ReversionConfig,
    "late_momentum_cash_v1": LateMomentumConfig,
}


def coarse_grid() -> dict[str, list[CashConfig]]:
    return {
        "orb_cash_v1": [
            OrbConfig(opening_range_minutes=minutes, stop_atr=stop)
            for minutes, stop in product((5, 15, 30), (1., 1.5, 2.))
        ],
        "slot_momentum_cash_v1": [
            MomentumConfig(history_sessions=days, band_multiplier=band)
            for days, band in product((10, 20, 30), (1., 1.5))
        ],
        "vwap_reversion_cash_v1": [
            ReversionConfig(band_atr=band, efficiency_max=efficiency)
            for band, efficiency in product((1.5, 2.), (.2, .3))
        ],
        "late_momentum_cash_v1": [
            LateMomentumConfig(signal_daily_atr=threshold) for threshold in (0., .25, .50)
        ],
    }


def canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, allow_nan=False)


def record_once(
    db: sqlite3.Connection,
    fold: str,
    phase: str,
    key: str,
    calculate: Callable[[], dict],
) -> dict:
    stored = db.execute(
        "SELECT payload FROM results WHERE fold=? AND phase=? AND trial=?", (fold, phase, key)
    ).fetchone()
    if stored:
        return json.loads(stored[0])
    try:
        payload = calculate()
    except Exception as error:
        with db:
            db.execute("INSERT INTO failures VALUES (?, ?, ?, ?)", (fold, phase, key, str(error)))
        raise
    with db:
        db.execute(
            "INSERT INTO results VALUES (?, ?, ?, ?)", (fold, phase, key, canonical(payload))
        )
    return payload


def rank(rows: list[dict], training: list[dict]) -> list[dict]:
    def key(row: dict) -> tuple:
        neighbors = [
            other for other in training
            if other["family"] == row["family"]
            and sum(value != other["params"][key] for key, value in row["params"].items()) == 1
        ]
        agreement = sum(not other["reasons"] for other in neighbors) / max(1, len(neighbors))
        return (-row["net_pnl_usd"], row["max_drawdown_fraction"], -agreement, row["key"])

    return sorted((row for row in rows if not row["reasons"]), key=key)


def run_walk_forward(dataset: Path, output: Path, commit: str) -> None:
    bars, metadata = load_backtest_dataset(dataset)
    if metadata.get("provider") != "continuous_mnq":
        raise ValueError("Select the verified continuous MNQ dataset")
    excluded = frozenset(date.fromisoformat(day) for day in SPECIAL_DATES)
    histories = {}
    for segment in metadata["segments"]:
        native, info = load_dataset(Path(metadata["source_root"]) / segment["source_dataset"])
        if info["sha256"] != segment["source_sha256"]:
            raise ValueError(f"Native source changed for {segment['contract']}")
        histories[segment["contract"]] = prior_cash_inputs(native.loc[:bars.index[-1]], excluded)
    calendar = session_calendar(bars, metadata, "2025-09-15", "2026-11-10")
    day_contracts = bars.tz_convert("America/New_York").between_time("09:35", "16:00")
    contracts = dict(zip(day_contracts.index.date, day_contracts.contract, strict=True))
    for row in calendar:
        day = date.fromisoformat(row["date"])
        if row["status"] == "regular" and (
            day not in histories[contracts[day]].slots[30]
            or day not in histories[contracts[day]].daily
        ):
            row["status"] = "insufficient common native cash warm-up"
    output.mkdir(parents=True, exist_ok=True)
    base = StrategyConfig(
        name=BASELINE, sizing_rule="fixed_contracts", max_contracts=1,
        commission_per_side_usd=1, slippage_ticks=1, margin_per_contract_usd=3000,
    )
    manifest = {
        "recipe": RECIPE, "commit": commit, "source_sha256": source_version(),
        "versions": {name: version(name) for name in ("nautilus_trader", "pandas", "pydantic")},
        "data_checksum": metadata["study_checksum"], "calendar": calendar, "seed": 0,
        "base": base.model_dump(mode="json"), "periods": PERIODS,
        "stage": "Historical walk-forward", "archive_exposure": "Development-exposed",
        "prior_exposures": archive_exposures(
            Path(__file__).resolve().parents[2], dataset, metadata["study_checksum"], output
        ),
        "locked": {"status": "Reserved, no result", "start": "2026-12-01", "end": "2027-01-29"},
        "prospective": {"status": "No registered paper result"},
    }
    with closing(sqlite3.connect(output / "study.sqlite", timeout=1)) as db:
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        with db:
            db.execute("BEGIN EXCLUSIVE")
            db.execute("CREATE TABLE IF NOT EXISTS manifest (payload TEXT NOT NULL)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS results
                (fold TEXT, phase TEXT, trial TEXT, payload TEXT, PRIMARY KEY(fold,phase,trial))
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS failures (fold TEXT, phase TEXT, trial TEXT, error TEXT)
            """)
            stored = db.execute("SELECT payload FROM manifest").fetchone()
            if stored:
                # Keep the initial exposure snapshot even as new research is recorded.
                manifest["prior_exposures"] = json.loads(stored[0])["prior_exposures"]
                if stored[0] != canonical(manifest):
                    raise ValueError("Study inputs changed. Choose a new output directory.")
            if not stored:
                db.execute("INSERT INTO manifest VALUES (?)", (canonical(manifest),))
        folds = []
        for train_end, validation_start, as_of, outer_start, outer_end in PERIODS:
            bounds = {
                "training": ("2025-10-27", train_end),
                "validation": (validation_start, as_of),
                "historical": (outer_start, outer_end),
            }
            fold = run_period(db, as_of, bounds, calendar, base, bars, metadata, histories)
            folds.append(fold)
            report = output / "summary.json"
            temporary = report.with_suffix(".tmp")
            temporary.write_text(
                canonical({"manifest": manifest, "folds": folds}), encoding="utf-8"
            )
            temporary.replace(report)
            print(as_of, fold["selected"], flush=True)


def run_period(
    db: sqlite3.Connection,
    as_of: str,
    bounds: dict,
    calendar: list[dict],
    base: StrategyConfig,
    bars: pd.DataFrame,
    metadata: dict,
    histories: dict,
) -> dict:
    windows = {}
    configs = {}
    market = CashMarketCache()
    for phase, (first, last) in bounds.items():
        included = [
            row["date"] for row in calendar
            if first <= row["date"] <= last
            and row["status"] in {"regular", "roll entry blackout"}
        ]
        windows[phase] = {
            "requested": [first, last],
            "actual": [included[0], included[-1]],
            "sessions": len(included),
        }
        dates = frozenset(map(date.fromisoformat, included))
        # Include calendar span even when its opening date is a common warm-up blackout.
        config = base.model_copy(update={
            "start": cash_timestamp(date.fromisoformat(first), CASH_FIRST_CLOSE),
            "end": cash_timestamp(max(dates), CASH_LAST_CLOSE),
        })
        configs[phase] = (config, dates)

    def run(
        phase: str, family: str, candidate: CashConfig | None = None,
        *, relative_pnl: float = 0,
    ) -> dict:
        params = candidate.model_dump(mode="json") if candidate else {}
        key = f"{family}:{canonical(params)}"

        def calculate() -> dict:
            config, dates = configs[phase]
            result = simulate_backtest(
                config.model_copy(update={"name": family}), bars.loc[:config.end], metadata,
                cash_sessions=dates,
                orb=candidate if isinstance(candidate, OrbConfig) else None,
                candidate=candidate, prior_cash=histories,
                cash_market=market,
            )
            assert isinstance(result, dict)
            reasons = []
            if candidate and phase == "training":
                reasons = qualify(result, 30, 20)
            if candidate and phase == "validation":
                reasons = qualify(result, 10, 8, relative_pnl=relative_pnl)
            return {**result, "family": family, "params": params, "key": key, "reasons": reasons}

        return record_once(db, as_of, phase, key, calculate)

    reference = {phase: run(phase, BASELINE) for phase in ("training", "validation")}
    training, validation = [], []
    for family, grid in coarse_grid().items():
        rows = [run("training", family, params) for params in grid]
        if family != "orb_cash_v1":
            for trial in rank(rows, rows)[:2]:
                for stop in (1., 2.):
                    params = FAMILIES[family].model_validate({**trial["params"], "stop_atr": stop})
                    refined = run("training", family, params)
                    if refined not in rows:
                        rows.append(refined)
        training.extend(rows)
        validation.extend(
            run(
                "validation", family, FAMILIES[family].model_validate(trial["params"]),
                relative_pnl=reference["validation"]["net_pnl_usd"],
            )
            for trial in rank(rows, rows)[:2]
        )
    passing = rank(validation, training)
    selected = ({key: passing[0][key] for key in ("family", "params")} if passing else None)
    # Store the as-of selection before evaluating any outer bar.
    record_once(db, as_of, "selection", RECIPE, lambda: {"selected": selected})
    baseline = run("historical", BASELINE)
    outer = {}
    for family, schema in FAMILIES.items():
        family_passed = [row for row in passing if row["family"] == family]
        params = schema.model_validate(family_passed[0]["params"]) if family_passed else schema()
        outer[family] = {
            "basis": "Validated family candidate" if family_passed else "Fixed-default diagnostic",
            **run("historical", family, params),
        }
    policy = outer[selected["family"]] if selected else None
    paired = {
        day: (policy["daily_pnl_usd"][day] if policy else 0) - pnl
        for day, pnl in baseline["daily_pnl_usd"].items()
    }
    return {
        "as_of": as_of, "windows": windows,
        "training": training, "validation": validation, "selected": selected,
        "reference": reference, "historical": outer, "baseline": baseline,
        "policy_flat_sessions": policy["flat_sessions"] if policy else len(paired),
        "policy_flat_time_fraction": policy["flat_time_fraction"] if policy else 1.0,
        "paired_daily_pnl_usd": paired, "paired_pnl_usd": sum(paired.values()),
        "rejections": dict(Counter(reason for row in training + validation
                                   for reason in row["reasons"])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args()
    run_walk_forward(args.dataset, args.output, args.code_commit)


if __name__ == "__main__":
    main()

"""Reproduce the preregistered first ORB versus cash SMA historical fold."""

import argparse
import hashlib
import json
from datetime import date, datetime, time
from itertools import product
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from mnq_research.backtest import load_backtest_dataset, simulate_backtest
from mnq_research.cash_strategy import CASH_FIRST_CLOSE, CASH_LAST_CLOSE, CASH_ZONE, OrbConfig
from mnq_research.config import StrategyConfig
from mnq_research.optimization import source_version
from mnq_research.roll_backtest import prepare_segments

WINDOWS = {
    "warmup": ("2025-09-15", "2025-10-24"),
    "training": ("2025-10-27", "2026-02-27"),
    "validation": ("2026-03-02", "2026-03-31"),
    "april": ("2026-04-01", "2026-04-30"),
}
# Exchange holidays and shortened cash sessions within the historical recipe.
SPECIAL_DATES = {
    "2025-11-27": "cash holiday",
    "2025-11-28": "short cash session",
    "2025-12-24": "short cash session",
    "2025-12-25": "cash holiday",
    "2026-01-01": "cash holiday",
    "2026-01-19": "cash holiday",
    "2026-02-16": "cash holiday",
    "2026-04-03": "cash holiday",
    "2026-05-25": "cash holiday",
    "2026-06-19": "cash holiday",
    "2026-07-03": "cash holiday",
    "2026-09-07": "cash holiday",
}


def cash_timestamp(day: date, clock: time) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(day, clock, ZoneInfo(CASH_ZONE))).tz_convert("UTC")


def session_calendar(
    bars: pd.DataFrame, metadata: dict, start: str, end: str
) -> list[dict]:
    if not WINDOWS["warmup"][0] <= start <= end <= "2026-11-10":
        raise ValueError("This cash calendar only covers the historical research recipe")
    boundaries = [pd.Timestamp(segment["segment_end_utc"]) for segment in metadata["segments"][:-1]]
    roll_days = {
        bars.index[bars.index.searchsorted(boundary, side="right") - 1]
        .tz_convert(CASH_ZONE).date()
        for boundary in boundaries if bars.index[0] < boundary <= bars.index[-1]
    }
    records = []
    for day in pd.bdate_range(start, end).date:
        reason = SPECIAL_DATES.get(day.isoformat())
        if reason is None:
            expected = pd.date_range(
                cash_timestamp(day, CASH_FIRST_CLOSE),
                cash_timestamp(day, CASH_LAST_CLOSE),
                freq="5min",
            )
            session = bars.reindex(expected)
            if not session.volume.gt(0).all() or session.contract.nunique() != 1:
                raise ValueError(f"Incomplete or mixed-contract cash session: {day}")
            reason = "roll entry blackout" if day in roll_days else "regular"
        records.append({"date": day.isoformat(), "status": reason})
    return records


def qualify(
    result: dict, minimum_trades: int, minimum_days: int, *, relative_pnl: float = 0.0
) -> list[str]:
    reasons = []
    if result["halted"] or result["max_drawdown_fraction"] > 0.10:
        reasons.append("drawdown limit or halt")
    if result["closed_trades"] < minimum_trades or result["active_days"] < minimum_days:
        reasons.append("insufficient trades or active days")
    if result["net_pnl_usd"] <= max(0.0, relative_pnl):
        reasons.append("insufficient net P&L")
    return reasons


def run_fold(dataset: Path, output: Path, code_commit: str) -> dict:
    bars, metadata = load_backtest_dataset(dataset)
    if metadata.get("provider") != "continuous_mnq":
        raise ValueError("Select the verified five-minute continuous MNQ dataset")
    calendar = {
        phase: session_calendar(bars, metadata, *window) for phase, window in WINDOWS.items()
    }
    sessions = {
        phase: frozenset(
            date.fromisoformat(row["date"])
            for row in rows
            if row["status"] in {"regular", "roll entry blackout"}
        )
        for phase, rows in calendar.items()
    }
    base = StrategyConfig(
        name="sma_cash_v1",
        sizing_rule="fixed_contracts",
        max_contracts=1,
        commission_per_side_usd=1,
        slippage_ticks=1,
        margin_per_contract_usd=3000,
    )
    settings = {
        phase: StrategyConfig.model_validate(
            {
                **base.model_dump(),
                "start": cash_timestamp(min(days), CASH_FIRST_CLOSE),
                "end": cash_timestamp(max(days), CASH_LAST_CLOSE),
            }
        )
        for phase, days in sessions.items()
        if phase != "warmup"
    }
    # Validate every split's native warm-up and roll inputs before the first trial.
    for config in settings.values():
        prepare_segments(config, bars, metadata)

    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "recipe": "orb_cash_first_fold_v1",
        "stage": "historical_walk_forward",
        "code_commit": code_commit,
        "source_sha256": source_version(),
        "requirements_sha256": hashlib.sha256(
            Path("requirements.lock").read_bytes()
        ).hexdigest(),
        "dataset_checksum": metadata["study_checksum"],
        "bar_checksum": metadata["sha256"],
        "calendar_version": "nyse_full_cash_first_fold_v1",
        "calendar": calendar,
        "windows": WINDOWS,
        "settings": {key: val.model_dump(mode="json") for key, val in settings.items()},
        "seed": 0,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def simulate(phase: str, orb: OrbConfig | None, *, save: bool = False) -> dict:
        config = settings[phase]
        if orb:
            config = config.model_copy(update={"name": "orb_cash_v1"})
        result = simulate_backtest(
            config,
            bars,
            metadata,
            output / "reports" if save else None,
            cash_sessions=sessions[phase],
            orb=orb,
        )
        if isinstance(result, Path):
            return {
                **json.loads((result / "summary.json").read_text(encoding="utf-8")),
                "report": result.relative_to(output).as_posix(),
            }
        return result

    trials = []
    for minutes, atr in product((5, 15, 30), (1.0, 1.5, 2.0)):
        orb = OrbConfig.model_validate({"opening_range_minutes": minutes, "stop_atr": atr})
        trial = {"id": len(trials) + 1, "parameters": orb.model_dump()}
        try:
            result = simulate("training", orb)
            trial.update(result=result, reasons=qualify(result, 30, 20))
        except Exception as error:
            trial.update(reasons=[f"simulation failed: {error}"])
        trials.append(trial)
        with (output / "training.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(trial, allow_nan=False) + "\n")

    def neighbor_agreement(trial: dict) -> float:
        params = trial["parameters"]
        neighbors = [
            other for other in trials if other is not trial
            and (
                params["opening_range_minutes"] == other["parameters"]["opening_range_minutes"]
                and abs(params["stop_atr"] - other["parameters"]["stop_atr"]) == 0.5
                or params["stop_atr"] == other["parameters"]["stop_atr"]
                and abs(
                    (5, 15, 30).index(params["opening_range_minutes"])
                    - (5, 15, 30).index(other["parameters"]["opening_range_minutes"])
                ) == 1
            )
        ]
        return sum(not other["reasons"] for other in neighbors) / len(neighbors)

    shortlist = sorted(
        (trial for trial in trials if not trial["reasons"]),
        key=lambda trial: (
            -trial["result"]["net_pnl_usd"],
            trial["result"]["max_drawdown_fraction"],
            -neighbor_agreement(trial),
            trial["id"],
        ),
    )[:2]
    reference = simulate("validation", None)
    validation = []
    for trial in shortlist:
        result = simulate("validation", OrbConfig.model_validate(trial["parameters"]))
        validation.append({
            "id": trial["id"],
            "parameters": trial["parameters"],
            "result": result,
            "reasons": qualify(result, 10, 8, relative_pnl=reference["net_pnl_usd"]),
        })
    passing = sorted(
        (trial for trial in validation if not trial["reasons"]),
        key=lambda trial: (
            -trial["result"]["net_pnl_usd"],
            trial["result"]["max_drawdown_fraction"],
            -neighbor_agreement(trials[trial["id"] - 1]),
            trial["id"],
        ),
    )
    selected = passing[0] if passing else None
    selection = {"baseline_validation": reference, "validation": validation, "selected": selected}
    (output / "selection.json").write_text(json.dumps(selection, indent=2), encoding="utf-8")

    sma = simulate("april", None, save=True)
    challenger = (
        simulate("april", OrbConfig.model_validate(selected["parameters"]), save=True)
        if selected
        else None
    )
    paired = {
        day: (challenger["daily_pnl_usd"][day] if challenger else 0.0) - pnl
        for day, pnl in sma["daily_pnl_usd"].items()
    }
    summary = {
        "session_counts": {phase: len(days) for phase, days in sessions.items()},
        **selection,
        "april_sma": sma,
        "april_orb": challenger,
        "april_policy": "orb" if selected else "cash_abstention",
        "april_paired_daily_difference_usd": paired,
        "april_paired_difference_usd": sum(paired.values()),
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--code-commit", required=True)
    args = parser.parse_args()
    run_fold(args.dataset, args.output, args.code_commit)
    print(args.output / "summary.json")


if __name__ == "__main__":
    main()

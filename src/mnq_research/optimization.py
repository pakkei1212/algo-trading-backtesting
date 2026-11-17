"""Local, resumable parameter studies using the native backtest runner."""

import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from importlib.metadata import PackageNotFoundError, version
from itertools import product
from pathlib import Path
from time import monotonic
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from mnq_research.backtest import load_backtest_dataset, simulate_backtest
from mnq_research.config import StrategyConfig
from mnq_research.roll_backtest import prepare_segments
from mnq_research.tpe_ranges import RangeCandidates, TpeRanges


class OptimizationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    dataset: Path
    checksum: str
    base: StrategyConfig
    fast: list[int] = Field(default_factory=list)
    slow: list[int] = Field(default_factory=list)
    atr: list[float] = Field(default_factory=list)
    ranges: TpeRanges | None = None
    method: Literal["Grid Search", "Optuna TPE"] = "Grid Search"
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    max_trials: int = Field(default=100, ge=1, le=200)
    drawdown: float = Field(default=0.10, gt=0, lt=1)
    train_trades: int = Field(default=30, ge=1)
    validation_trades: int = Field(default=15, ge=1)
    minimum_bars: int = Field(default=200, ge=1)
    minimum_days: int = Field(default=5, ge=1)
    train: tuple[str, str]
    validation: tuple[str, str]
    holdout: tuple[str, str] | None = None
    objective: Literal["Net profit", "Return / drawdown"] = "Net profit"

    def candidates(self) -> list[StrategyConfig] | RangeCandidates:
        if self.base.entry_rule != "sma_cross":
            raise ValueError("Select an SMA crossover configuration.")
        if self.ranges is not None:
            if self.method != "Optuna TPE" or self.fast or self.slow or self.atr:
                raise ValueError("Numeric ranges are exclusive to TPE range studies.")
            return RangeCandidates(self.base, self.ranges)
        values = [sorted(set(group)) for group in (self.fast, self.slow, self.atr)]
        if any(not group or len(group) > 200 for group in values):
            raise ValueError("Supply between 1 and 200 distinct values per parameter.")
        pairs = [(fast, slow) for fast, slow in product(*values[:2]) if fast < slow]
        count = len(pairs) * len(values[2])
        limit = self.max_trials if self.method == "Grid Search" else 10_000
        if not count or count > limit:
            raise ValueError(
                f"{count} valid combinations exceed the limit of {limit}. "
                "Remove candidate values to narrow the grid."
                if count
                else "No valid combinations. Fast SMA must be shorter than Slow SMA."
            )
        return [
            StrategyConfig.model_validate(
                {
                    **self.base.model_dump(),
                    "fast_period": fast,
                    "slow_period": slow,
                    "atr_multiple": atr,
                }
            )
            for (fast, slow), atr in product(pairs, values[2])
        ]

    def training_budget(self, candidate_count: int) -> int:
        return min(self.max_trials, candidate_count)


def require_tpe() -> None:
    try:
        installed = version("optuna")
    except PackageNotFoundError:
        installed = None
    if installed != "4.6.0":
        raise ValueError('Optuna TPE requires optuna==4.6.0. Install the "optimization" extra.')


def window(config: StrategyConfig, bounds: tuple[str, str]) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            **config.model_dump(),
            "start": bounds[0],
            "end": bounds[1],
        }
    )


def dataset_checksum(metadata: dict) -> str:
    """Pin continuous studies to the manifest as well as its referenced bar hashes."""
    return metadata.get("study_checksum") or metadata["sha256"]


def validate_study_windows(
    spec: OptimizationSpec,
    frame: pd.DataFrame,
    metadata: dict,
    candidates: list[StrategyConfig] | RangeCandidates,
) -> None:
    if metadata.get("provider") == "amp_mt5":
        raise ValueError("Individual AMP MT5 datasets are available for backtests only.")
    if dataset_checksum(metadata) != spec.checksum:
        raise ValueError("Dataset changed. Restore the original snapshot or start a new study.")
    if metadata["kind"] == "rolling_proxy" and not spec.base.allow_rolling_proxy:
        raise ValueError("Enable exploratory rolling-proxy research in the strategy configuration.")
    # A window that passes with the largest lookback also supports every smaller candidate.
    # Numeric IDs are ordered by Fast, Slow, ATR, so the last has the largest Slow SMA.
    warmup_options = [candidates[-1]] if isinstance(candidates, RangeCandidates) else candidates
    longest = max([spec.base, *warmup_options], key=lambda cfg: cfg.warmup_bars())
    periods = [("Training", spec.train), ("Validation", spec.validation)]
    if spec.holdout:
        periods.append(("Holdout", spec.holdout))
    previous_end = None
    for label, bounds in periods:
        try:
            config = window(longest, bounds)
            start, end = config.start, config.end
            if previous_end is not None and start <= previous_end:
                raise ValueError("The window must begin after the preceding period ends.")
            previous_end = end
            if metadata.get("provider") == "continuous_mnq":
                segments = prepare_segments(config, frame, metadata)
                observed = pd.DatetimeIndex(
                    [
                        t
                        for segment in segments
                        for t in segment.bars.index
                        if t >= segment.first_evaluation_close
                    ]
                )
            else:
                if len(frame.loc[frame.index < start]) < longest.warmup_bars():
                    raise ValueError(f"Needs {longest.warmup_bars()} preceding observed bars.")
                observed = frame.loc[start:end].index
            if (
                len(observed) < spec.minimum_bars
                or observed.normalize().nunique() < spec.minimum_days
            ):
                raise ValueError("Insufficient evaluation bars or observed UTC days.")
        except ValueError as error:
            raise ValueError(f"{label}: {error}") from error


def prepare(
    spec: OptimizationSpec,
) -> tuple[pd.DataFrame, dict, list[StrategyConfig] | RangeCandidates]:
    if spec.method == "Optuna TPE":
        require_tpe()
    candidates = spec.candidates()
    frame, metadata = load_backtest_dataset(spec.dataset)
    validate_study_windows(spec, frame, metadata, candidates)
    return frame, metadata, candidates


def source_version() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def create_study(folder: Path, spec: OptimizationSpec) -> None:
    prepare(spec)
    folder.mkdir(parents=True)
    with closing(sqlite3.connect(folder / "study.sqlite")) as db, db:
        db.execute("CREATE TABLE state (status TEXT, canceled INTEGER, source TEXT)")
        db.execute("INSERT INTO state VALUES ('Ready', 0, ?)", (source_version(),))
        db.execute("CREATE TABLE holdout (candidate INTEGER PRIMARY KEY, result TEXT)")
        db.execute("CREATE TABLE spec (json TEXT)")
        db.execute("INSERT INTO spec VALUES (?)", (spec.model_dump_json(),))
        db.execute(
            "CREATE TABLE trials (phase TEXT, id INTEGER, result TEXT, PRIMARY KEY (phase,id))"
        )


def read_study(folder: Path) -> tuple[OptimizationSpec, str, list[dict]]:
    with closing(sqlite3.connect(folder / "study.sqlite")) as db:
        payload = db.execute("SELECT json FROM spec").fetchone()[0]
        spec = OptimizationSpec.model_validate_json(payload)
        status = db.execute("SELECT status FROM state").fetchone()[0]
        rows = db.execute("SELECT result FROM trials ORDER BY phase,id").fetchall()
    return spec, status, [json.loads(row[0]) for row in rows]


def cancel_study(folder: Path) -> None:
    with closing(sqlite3.connect(folder / "study.sqlite")) as db, db:
        db.execute("UPDATE state SET canceled=1")


def evaluate(
    spec: OptimizationSpec,
    config: StrategyConfig,
    frame: pd.DataFrame,
    metadata: dict,
    phase: str,
    candidate_id: int,
) -> dict:
    bounds = {"Training": spec.train, "Validation": spec.validation, "Holdout": spec.holdout}[phase]
    assert bounds is not None
    result = simulate_backtest(window(config, bounds), frame, metadata)
    assert isinstance(result, dict)
    reasons = []
    minimum = spec.train_trades if phase == "Training" else spec.validation_trades
    if result["closed_trades"] < minimum:
        reasons.append("Too few closed trades")
    if result["halted"]:
        reasons.append("Trading halted")
    drawdown = result["max_drawdown_fraction"]
    if drawdown > spec.drawdown:
        reasons.append("Drawdown above ceiling")
    total_return = result["net_pnl_usd"] / config.initial_equity_usd
    score = result["net_pnl_usd"]
    if spec.objective == "Return / drawdown":
        score = total_return / drawdown if drawdown else None
        if score is None:
            reasons.append("Undefined return / drawdown")
    return {
        **result,
        "phase": phase,
        "candidate": candidate_id,
        "fast": config.fast_period,
        "slow": config.slow_period,
        "atr": config.atr_multiple,
        "return_fraction": total_return,
        "score": score if not reasons else None,
        "reason": "; ".join(reasons),
    }


def ranked(rows: list[dict]) -> list[dict]:
    return sorted(
        (r for r in rows if r["phase"] == "Training" and r["score"] is not None),
        key=lambda r: (-r["score"], r["max_drawdown_fraction"], r["candidate"]),
    )


def run_study(folder: Path) -> None:
    # The separate lock database holds an OS-backed lock even across Streamlit reruns.
    with closing(sqlite3.connect(folder.parent / "worker.sqlite", timeout=0)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            return
        with closing(sqlite3.connect(folder / "study.sqlite")) as db:

            def status(value: str) -> None:
                with db:
                    db.execute("UPDATE state SET status=?", (value,))

            started = monotonic()
            try:
                spec, previous_status, rows = read_study(folder)
                if previous_status == "Completed":
                    return
                if db.execute("SELECT source FROM state").fetchone()[0] != source_version():
                    raise ValueError("Code changed. Start a new study to keep results comparable.")
                frame, metadata, candidates = prepare(spec)
                status("Running")
                finished = {(row["phase"], row["candidate"]) for row in rows}

                def stop_requested() -> bool:
                    canceled = db.execute("SELECT canceled FROM state").fetchone()[0]
                    if canceled or monotonic() - started >= 1800:
                        status("Canceled" if canceled else "Time limit reached")
                        return True
                    return False

                def run_trial(phase: str, index: int, search_order: int | None = None) -> dict:
                    config = spec.base if index == -1 else candidates[index]
                    trial_started = monotonic()
                    result = evaluate(spec, config, frame, metadata, phase, index)
                    result["runtime_seconds"] = monotonic() - trial_started
                    if search_order is not None:
                        result["search_order"] = search_order
                    with db:
                        db.execute(
                            "INSERT INTO trials VALUES (?, ?, ?)",
                            (phase, index, json.dumps(result)),
                        )
                    rows.append(result)
                    return result

                def run_batch(phase: str, indices: list[int]) -> bool:
                    for index in indices:
                        if (phase, index) in finished:
                            continue
                        if stop_requested():
                            return False
                        run_trial(phase, index)
                    return True

                def run_training() -> bool:
                    if spec.method == "Grid Search":
                        return run_batch("Training", list(range(len(candidates))))
                    from mnq_research.tpe_search import TpeSearch

                    search = TpeSearch(candidates, spec.seed)
                    training = [r for r in rows if r["phase"] == "Training"]
                    search.replay(training)
                    for order in range(len(training), spec.training_budget(len(candidates))):
                        if stop_requested():
                            return False
                        index, trial = search.ask()
                        result = run_trial("Training", index, order)
                        search.tell(index, trial, result["score"])
                    return True

                if run_training():
                    shortlist = [r["candidate"] for r in ranked(rows)[:5]]
                    if run_batch("Validation", [-1, *shortlist]):
                        status("Completed")
            except Exception as error:
                status(f"Failed: {error}")


def read_holdout(folder: Path) -> tuple[int, dict | None] | None:
    with closing(sqlite3.connect(folder / "study.sqlite")) as db:
        row = db.execute("SELECT candidate, result FROM holdout").fetchone()
    if row is None:
        return None
    return row[0], json.loads(row[1]) if row[1] else None


def reveal_holdout(folder: Path, index: int) -> dict:
    """Lock one validation candidate before allowing access to the reserved period."""
    with closing(sqlite3.connect(folder.parent / "worker.sqlite", timeout=0)) as lock:
        lock.execute("BEGIN EXCLUSIVE")
        spec, status, rows = read_study(folder)
        if status != "Completed" or spec.holdout is None:
            raise ValueError("Complete a study with a reserved holdout first.")
        if not any(r["phase"] == "Validation" and r["candidate"] == index for r in rows):
            raise ValueError("Select a candidate that was evaluated on validation.")
        with closing(sqlite3.connect(folder / "study.sqlite")) as db:
            if db.execute("SELECT source FROM state").fetchone()[0] != source_version():
                raise ValueError("Code changed since this study.")
            existing = db.execute("SELECT candidate, result FROM holdout").fetchone()
            if existing is not None and existing[0] != index:
                raise ValueError("The holdout is already locked to another candidate.")
            if existing is not None and existing[1] is not None:
                return json.loads(existing[1])
            with db:
                db.execute("INSERT OR IGNORE INTO holdout VALUES (?, NULL)", (index,))
            frame, metadata, candidates = prepare(spec)
            result = evaluate(spec, candidates[index], frame, metadata, "Holdout", index)
            with db:
                db.execute(
                    "UPDATE holdout SET result=? WHERE candidate=?", (json.dumps(result), index)
                )
            return result


if __name__ == "__main__":
    run_study(Path(sys.argv[1]))

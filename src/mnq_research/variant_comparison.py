"""Matched training, validation and OOT results using fresh native accounts."""

import argparse
import hashlib
import json
import sqlite3
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import date
from importlib.metadata import version
from math import sqrt
from pathlib import Path
from statistics import mean, stdev

import pandas as pd

from mnq_research.backtest import CashMarketCache, load_backtest_dataset, simulate_backtest
from mnq_research.cash_indicators import prior_cash_inputs
from mnq_research.cash_research import SPECIAL_DATES, cash_timestamp
from mnq_research.cash_strategy import CASH_FIRST_CLOSE, CASH_LAST_CLOSE, OrbConfig
from mnq_research.config import StrategyConfig
from mnq_research.data import load_dataset
from mnq_research.walk_forward import BASELINE, FAMILIES, canonical, coarse_grid

RECIPE = "all_cash_variants_no_drawdown_halt_v1"
PHASES = ("training", "validation", "oot")
EXPOSURE = "Development-exposed"


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def execution_source_version() -> str:
    """Presentation edits do not invalidate results or exact-input reuse."""
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.endswith("_ui"):
            continue
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def registry(base: StrategyConfig) -> list[dict]:
    settings = base.model_dump(mode="json", exclude={"name", "start", "end"})
    rows = []
    families = {**coarse_grid(), BASELINE: [None]}
    for family, candidates in families.items():
        for candidate in candidates:
            params = candidate.model_dump(mode="json") if candidate else {}
            exits = ["original_v1"]
            if family in {"orb_cash_v1", "slot_momentum_cash_v1"}:
                exits.append("extreme_1R_2entryATR_v1")
            for exit_policy in exits:
                identity = {
                    "family": family,
                    "strategy_version": family,
                    "parameters": params,
                    "exit_policy": exit_policy,
                    "account_and_engine_settings": settings,
                }
                rows.append({"id": fingerprint(identity), **identity})
    unique = {row["id"]: row for row in rows}
    if len(unique) != len(rows) or len(unique) != 38:
        raise ValueError(f"Expected 38 unique configurations, got {len(unique)} of {len(rows)}")
    return list(unique.values())


def make_manifest(reference: dict, metadata: dict, reference_sha: str) -> dict:
    original = reference["manifest"]
    if original["data_checksum"] != metadata["study_checksum"]:
        raise ValueError("Continuous data differ from the registered Milestone 2 study")
    base = StrategyConfig.model_validate({**original["base"], "drawdown_halt_enabled": False})
    if (
        base.initial_equity_usd,
        base.fixed_contracts,
        base.max_contracts,
        base.commission_per_side_usd,
        base.slippage_ticks,
        base.sizing_rule,
    ) != (10000, 1, 1, 1, 1, "fixed_contracts"):
        raise ValueError("The reference does not have the registered capital and cost settings")
    eligible = [
        r["date"] for r in original["calendar"] if r["status"] in {"regular", "roll entry blackout"}
    ]
    folds = []
    for fold in reference["folds"]:
        windows = {}
        for phase in PHASES:
            first, last = fold["windows"]["historical" if phase == "oot" else phase]["requested"]
            days = [day for day in eligible if first <= day <= last]
            windows[phase] = {"first": first, "last": last, "eligible_days": days}
        folds.append(
            {"as_of": fold["as_of"], "month": windows["oot"]["first"][:7], "windows": windows}
        )
    if len(folds) != 8:
        raise ValueError("Expected eight reference folds")
    configs = registry(base)
    return {
        "recipe": RECIPE,
        "source_sha256": execution_source_version(),
        "data_checksum": metadata["study_checksum"],
        "reference_sha256": reference_sha,
        "base": base.model_dump(mode="json"),
        "folds": folds,
        "registry": configs,
        "expected": len(configs) * len(folds) * len(PHASES),
        "exposure": EXPOSURE,
        "versions": {name: version(name) for name in ("nautilus_trader", "pandas", "pydantic")},
    }


def phase_metrics(summary: dict, capital: float, dates: list[str]) -> dict:
    if set(summary["daily_pnl_usd"]) != set(dates):
        raise ValueError("Native P&L dates differ from the registered eligible sessions")
    daily = [summary["daily_pnl_usd"][day] for day in dates]
    pnl = summary["net_pnl_usd"]
    if abs(sum(daily) - pnl) > 0.000001:
        raise ValueError("Native daily P&L does not reconcile to ending equity")
    returns = [value / capital for value in daily]
    deviation = stdev(returns) if len(returns) > 1 else 0
    drawdown = summary["max_drawdown_fraction"]
    return {
        "return_pct": 100 * pnl / capital,
        "drawdown_pct": 100 * drawdown,
        "net_pnl_usd": pnl,
        "daily_sharpe": sqrt(252) * mean(returns) / deviation if deviation else None,
        "pnl_per_session_usd": mean(daily),
        "trades": summary["closed_trades"],
        "costs_usd": summary["commission_usd"] + summary["slippage_cost_usd"],
        "expectancy_usd": summary["average_closed_trade_usd"],
        "return_drawdown": pnl / capital / drawdown if drawdown else None,
        "order_halted": summary["halted"],
        "active_days": summary["active_days"],
    }


def connect(output: Path) -> sqlite3.Connection:
    return sqlite3.connect(output / "comparison.sqlite", timeout=30)


def register_trials(db: sqlite3.Connection, manifest: dict) -> dict[str, tuple[dict, dict]]:
    """Pre-register every phase and map identical execution inputs to one trial."""
    groups = {}
    records = []
    for fold in manifest["folds"]:
        for phase, window in fold["windows"].items():
            for config in manifest["registry"]:
                key = fingerprint(
                    [
                        manifest["recipe"],
                        manifest["source_sha256"],
                        manifest["data_checksum"],
                        config["id"],
                        window["first"],
                        window["eligible_days"],
                    ]
                )
                groups[key] = (window, config)
                records.append((fold["as_of"], phase, config["id"], key))
    with db:
        db.executemany(
            "INSERT OR IGNORE INTO executions(key,status) VALUES (?,'queued')",
            [(key,) for key in groups],
        )
        db.executemany("INSERT OR IGNORE INTO phases VALUES (?,?,?,?)", records)
    return groups


def run_window(
    dataset: Path, output: Path, base_data: dict, window: dict, trials: list[tuple[str, dict]]
) -> int:
    bars, metadata = load_backtest_dataset(dataset)
    histories = {}
    excluded = frozenset(map(date.fromisoformat, SPECIAL_DATES))
    for item in metadata["segments"]:
        native, info = load_dataset(Path(metadata["source_root"]) / item["source_dataset"])
        if info["sha256"] != item["source_sha256"]:
            raise ValueError(f"Native source changed for {item['contract']}")
        histories[item["contract"]] = prior_cash_inputs(native.loc[: bars.index[-1]], excluded)
    days = frozenset(map(date.fromisoformat, window["eligible_days"]))
    base = StrategyConfig.model_validate(base_data).model_copy(
        update={
            "start": cash_timestamp(date.fromisoformat(window["first"]), CASH_FIRST_CLOSE),
            "end": cash_timestamp(max(days), CASH_LAST_CLOSE),
        }
    )
    market = CashMarketCache()
    with closing(connect(output)) as db:
        for key, row in trials:
            with db:
                db.execute("UPDATE executions SET status='running', error=NULL WHERE key=?", (key,))
                db.execute("INSERT INTO attempts(key,status) VALUES (?,'running')", (key,))
            try:
                family = row["family"]
                candidate = (
                    FAMILIES[family].model_validate(row["parameters"])
                    if family in FAMILIES
                    else None
                )
                config = base.model_copy(update={"name": family})
                report = simulate_backtest(
                    config,
                    bars.loc[: base.end],
                    metadata,
                    output / "reports",
                    cash_sessions=days,
                    orb=candidate if isinstance(candidate, OrbConfig) else None,
                    candidate=candidate,
                    prior_cash=histories,
                    cash_market=market,
                    experimental_trail=row["exit_policy"] != "original_v1",
                )
                summary = json.loads((report / "summary.json").read_text())
                metrics = phase_metrics(summary, base.initial_equity_usd, window["eligible_days"])
                try:
                    decisions = pd.read_csv(report / "decisions.csv")
                except pd.errors.EmptyDataError:
                    decisions = pd.DataFrame()
                for column, reason in (
                    ("drawdown_breaches", "drawdown_threshold_breach"),
                    ("risk_margin_skips", "entry_skipped_risk_or_margin"),
                    ("order_rejections", "order_rejected"),
                ):
                    metrics[column] = (
                        int(decisions.reason.eq(reason).sum()) if len(decisions) else 0
                    )
                payload = canonical(
                    {
                        "metrics": metrics,
                        "report": report.relative_to(output).as_posix(),
                        "fills_sha256": summary["fills_sha256"],
                    }
                )
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                with db:
                    db.execute(
                        "UPDATE executions SET status='failed', error=? WHERE key=?", (message, key)
                    )
                    db.execute(
                        "INSERT INTO attempts(key,status,error) VALUES (?,'failed',?)",
                        (key, message),
                    )
            else:
                with db:
                    db.execute(
                        "UPDATE executions SET status='completed', payload=? WHERE key=?",
                        (payload, key),
                    )
                    db.execute("INSERT INTO attempts(key,status) VALUES (?,'completed')", (key,))
    print(f"{window['first']} to {max(days)}: processed {len(trials)} executions", flush=True)
    return len(trials)


def read_comparison(output: Path) -> tuple[dict, dict]:
    uri = f"{(output / 'comparison.sqlite').resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        manifest = json.loads(db.execute("SELECT payload FROM manifest").fetchone()[0])
        phases = {
            (fold, config, phase): {
                "status": status,
                "error": error,
                **(json.loads(payload) if payload else {}),
            }
            for fold, phase, config, status, error, payload in db.execute("""
            SELECT fold, phase, config, status, error, payload
            FROM phases JOIN executions ON phases.execution=executions.key
        """)
        }
    return manifest, phases


def run(reference: Path, dataset: Path, output: Path, workers: int) -> None:
    original = reference.read_bytes()
    _, metadata = load_backtest_dataset(dataset)
    manifest = make_manifest(json.loads(original), metadata, hashlib.sha256(original).hexdigest())
    output.mkdir(parents=True, exist_ok=True)
    # A separate lock DB excludes simultaneous coordinators without blocking trial writes.
    with closing(sqlite3.connect(output / "coordinator.sqlite", timeout=1)) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with closing(connect(output)) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS manifest(payload TEXT);
                CREATE TABLE IF NOT EXISTS executions(
                    key TEXT PRIMARY KEY,status TEXT,payload TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS phases(fold TEXT,phase TEXT,config TEXT,execution TEXT,
                    PRIMARY KEY(fold,phase,config));
                CREATE TABLE IF NOT EXISTS attempts(key TEXT,status TEXT,error TEXT,
                    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
            saved = db.execute("SELECT payload FROM manifest").fetchone()
            if saved and saved[0] != canonical(manifest):
                raise ValueError("Changed comparison inputs require a new output directory")
            if not saved:
                with db:
                    db.execute("INSERT INTO manifest VALUES (?)", (canonical(manifest),))
            groups = register_trials(db, manifest)
            with db:
                db.execute("""
                    INSERT INTO attempts(key,status,error)
                    SELECT key,'interrupted','Previous coordinator stopped during execution'
                    FROM executions WHERE status='running'
                """)
                db.execute("UPDATE executions SET status='queued' WHERE status='running'")
            pending = db.execute("SELECT key FROM executions WHERE status!='completed'").fetchall()
        (output / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
        print(
            f"{len(manifest['registry'])} configurations · {manifest['expected']} phase results · "
            f"{len(groups)} unique executions · {len(pending)} pending",
            flush=True,
        )
        windows = {}
        batches = defaultdict(list)
        for (key,) in pending:
            window, row = groups[key]
            window_key = fingerprint([window["first"], window["eligible_days"]])
            windows[window_key] = window
            batches[window_key].append((key, row))
        if pending:
            with ProcessPoolExecutor(max_workers=workers) as pool:
                jobs = [
                    pool.submit(run_window, dataset, output, manifest["base"], windows[key], batch)
                    for key, batch in batches.items()
                ]
                for job in jobs:
                    job.result()
        saved_manifest, phases = read_comparison(output)
        matched_table(saved_manifest, phases).to_csv(output / "comparison.csv", index=False)
        completed = sum(row["status"] == "completed" for row in phases.values())
        print(f"Completed {completed}/{manifest['expected']} phase results", flush=True)


def matched_table(manifest: dict, phases: dict) -> pd.DataFrame:
    rows = []
    for fold in manifest["folds"]:
        for config in manifest["registry"]:
            row = {
                "OOT month": fold["month"],
                "Family": config["family"],
                "Exit variant": "Original"
                if config["exit_policy"] == "original_v1"
                else "Trailing",
                "Configuration ID": config["id"],
                "Parameters": "; ".join(
                    f"{k.replace('_', ' ')}={v}" for k, v in config["parameters"].items()
                )
                or "SMA 20/50; ATR 1×",
            }
            for phase in PHASES:
                label = "OOT" if phase == "oot" else phase.title()
                result = phases.get((fold["as_of"], config["id"], phase), {"status": "queued"})
                metrics = result.get("metrics", {})
                window = fold["windows"][phase]
                for key, column in (
                    ("return_pct", "return %"),
                    ("drawdown_pct", "maximum drawdown %"),
                    ("net_pnl_usd", "net P&L USD"),
                    ("daily_sharpe", "daily Sharpe"),
                    ("pnl_per_session_usd", "P&L/session USD"),
                    ("trades", "trades"),
                    ("costs_usd", "costs USD"),
                    ("expectancy_usd", "expectancy USD"),
                    ("return_drawdown", "return/drawdown"),
                    ("drawdown_breaches", "10% crossings"),
                    ("risk_margin_skips", "risk/margin skips"),
                    ("order_rejections", "order rejections"),
                    ("active_days", "active days"),
                    ("order_halted", "order halt"),
                ):
                    row[f"{label} {column}"] = metrics.get(key)
                row[f"{label} status"] = result["status"]
                row[f"{label} error"] = result.get("error")
                row[f"{label} first session"] = window["eligible_days"][0]
                row[f"{label} last session"] = window["eligible_days"][-1]
                row[f"{label} sessions"] = len(window["eligible_days"])
            row["Archive exposure"] = manifest["exposure"]
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=3, choices=range(1, 9))
    args = parser.parse_args()
    run(args.reference, args.dataset, args.output, args.workers)


if __name__ == "__main__":
    main()

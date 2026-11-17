"""Registered exit, execution, regime and persistent-account historical experiments."""

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from importlib.metadata import version
from pathlib import Path

import pandas as pd

from mnq_research.backtest import CashMarketCache, load_backtest_dataset, simulate_backtest
from mnq_research.cash_indicators import prior_cash_inputs
from mnq_research.cash_research import SPECIAL_DATES, cash_timestamp, qualify
from mnq_research.cash_strategy import CASH_FIRST_CLOSE, CASH_LAST_CLOSE, OrbConfig
from mnq_research.config import StrategyConfig
from mnq_research.data import load_dataset
from mnq_research.optimization import source_version
from mnq_research.regimes import classify, feature_frame, fit_thresholds
from mnq_research.research_diagnostics import paired_bootstrap, qualification_rows
from mnq_research.strategy import RollRiskState
from mnq_research.walk_forward import BASELINE, FAMILIES, canonical, rank, record_once

SCENARIOS = {"base": (1.0, None), "middle": (1.5, 2), "high": (2.0, 4)}


def save_snapshot(db: sqlite3.Connection, output: Path, summary: dict) -> None:
    """Commit a complete snapshot before exporting its human-readable JSON copy."""
    payload = canonical(summary)
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS snapshot(id INTEGER PRIMARY KEY, payload TEXT)")
        db.execute("INSERT OR REPLACE INTO snapshot VALUES (1, ?)", (payload,))
    temporary = output / "summary.tmp"
    temporary.write_text(payload, encoding="utf-8")
    try:
        temporary.replace(output / "summary.json")
    except PermissionError:
        # Some Windows folders deny replacing an existing file. SQLite remains the
        # atomic source for readers; this JSON is only a recoverable export.
        (output / "summary.json").write_text(payload, encoding="utf-8")


def trade_details(folder: Path) -> list[dict]:
    try:
        positions = pd.read_csv(folder / "positions.csv")
    except pd.errors.EmptyDataError:
        return []
    if positions.empty:
        return []
    fills = pd.read_csv(folder / "fills.csv")
    bars = pd.read_parquet(folder / "bars.parquet")
    rows = []
    for position in positions.to_dict("records"):
        linked = fills.loc[fills.order_id.eq(position["opening_order_id"])]
        entry = linked.iloc[0].to_dict()
        exit_fill = fills.loc[fills.order_id.eq(position["closing_order_id"])].iloc[0]
        opened, closed = pd.Timestamp(position["ts_opened"]), pd.Timestamp(position["ts_closed"])
        # A stop fill stamped at the close may have happened inside that bar;
        # its later extreme is unknowable, so exclude the entire exit bar.
        observed = bars.loc[(bars.index > opened) & (bars.index < closed)]
        side = 1 if position["entry"] == "BUY" else -1
        price = float(position["avg_px_open"])
        extreme = (
            (observed.high.max() if side > 0 else observed.low.min()) if len(observed) else price
        )
        mfe = max(0, side * (float(extreme) - price) * 2)
        realized = float(str(position["realized_pnl"]).split()[0].replace(",", ""))
        rows.append(
            {
                **{
                    key: entry.get(key) if pd.notna(entry.get(key)) else None
                    for key in (
                        "cash_signal_id",
                        "signal_timestamp",
                        "session",
                        "volatility",
                        "efficiency",
                        "volatility_label",
                        "efficiency_label",
                        "direction",
                        "time_bucket",
                        "volatility_unavailable_reason",
                        "efficiency_unavailable_reason",
                    )
                },
                "entry_utc": opened.isoformat(),
                "exit_utc": closed.isoformat(),
                "net_pnl_usd": realized,
                "exit_reason": exit_fill.exit_reason,
                "mfe_usd_bar_estimate": mfe,
                "giveback_usd_bar_estimate": mfe - realized,
                "excursion_note": "Full entry bar included; partial exit bar excluded",
            }
        )
    return rows


def regime_report(trades: list[dict], days: list[str]) -> list[dict]:
    rows = []
    frame = pd.DataFrame(trades)
    if frame.empty:
        return rows
    for dimension in ("volatility_label", "efficiency_label", "direction", "time_bucket"):
        for label, group in frame.groupby(dimension, dropna=False):
            totals = group.groupby("session").net_pnl_usd.sum().to_dict()
            daily = {day: float(totals.get(day, 0)) for day in days}
            interval = paired_bootstrap(daily, dict.fromkeys(days, 0.0))
            rows.append(
                {
                    "dimension": dimension,
                    "label": str(label),
                    "trades": len(group),
                    "distinct_days": group.session.nunique(),
                    "net_pnl_usd": float(group.net_pnl_usd.sum()),
                    "expectancy_usd": float(group.net_pnl_usd.mean()),
                    "win_rate": float(group.net_pnl_usd.gt(0).mean()),
                    "daily_uncertainty": interval,
                    "status": "inconclusive"
                    if len(group) < 30 or group.session.nunique() < 20
                    else interval["status"],
                }
            )
    return rows


def run(source: Path, dataset: Path, output: Path) -> None:
    original_bytes = (source / "summary.json").read_bytes()
    original = json.loads(original_bytes)
    bars, metadata = load_backtest_dataset(dataset)
    histories, pieces = {}, []
    excluded = frozenset(map(date.fromisoformat, SPECIAL_DATES))
    for segment in metadata["segments"]:
        native, info = load_dataset(Path(metadata["source_root"]) / segment["source_dataset"])
        if info["sha256"] != segment["source_sha256"]:
            raise ValueError("Native source checksum changed")
        history = prior_cash_inputs(native, excluded)
        histories[segment["contract"]] = history
        features = feature_frame(native, history)
        selected = bars.index[bars.contract.eq(segment["contract"])].intersection(features.index)
        pieces.append(features.loc[selected])
    features = pd.concat(pieces).sort_index()
    eligible = frozenset(
        date.fromisoformat(row["date"])
        for row in original["manifest"]["calendar"]
        if row["status"] in {"regular", "roll entry blackout"}
    )
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "recipe": "m3_fixed_trail_stress_persistent_v1",
        "source_sha256": source_version(),
        "reference_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "reference_commit": original["manifest"]["commit"],
        "data_sha256": metadata["sha256"],
        "calendar": original["manifest"]["calendar"],
        "stage": "Historical research · development-exposed",
        "exit_policy": "1R activation / 2 signal ATR / post-entry extremes / next bar",
        "budget": "Top two training-ranked ORB and momentum configurations per fold, off/on",
        "costs": SCENARIOS,
        "cost_selection": "Frozen base selections; no cost reselection",
        "account": "Monthly native flat-account transfer, high-water and permanent halt carried",
        "uncertainty": "Stationary paired blocks 3/5/10; fold boundaries may be crossed",
        "versions": {
            "python": sys.version, "nautilus_trader": version("nautilus_trader"),
            "feature": "cash_regimes_v1", "execution": "adverse_tick_liquidity_v1",
            "calendar": original["manifest"].get("calendar_version", "M2 frozen calendar"),
            "selection": "M2 gates; top 2 training P&L ORB/momentum including rejected; off/on",
            "persistence": "SQLite atomic snapshot v1; JSON export",
        },
    }
    with closing(sqlite3.connect(output / "study.sqlite", timeout=1)) as db:
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS manifest(payload TEXT);
            CREATE TABLE IF NOT EXISTS results(
                fold TEXT,phase TEXT,trial TEXT,payload TEXT,PRIMARY KEY(fold,phase,trial));
            CREATE TABLE IF NOT EXISTS failures(fold TEXT,phase TEXT,trial TEXT,error TEXT);
        """)
        stored = db.execute("SELECT payload FROM manifest").fetchone()
        if stored and stored[0] != canonical(manifest):
            raise ValueError("Changed experiment inputs require a new output directory")
        if not stored:
            with db:
                db.execute("INSERT INTO manifest VALUES (?)", (canonical(manifest),))
        states = {
            name: {"equity": 10000.0, "high_water": 10000.0, "halted": False}
            for name in ("policy", "sma")
        }
        completed = []
        for fold in original["folds"]:
            as_of = fold["as_of"]
            thresholds = fit_thresholds(
                features, eligible, *fold["windows"]["training"]["requested"]
            )
            labelled = classify(features, thresholds).astype(object)
            labelled = labelled.where(pd.notna(labelled), None)
            contexts = {
                timestamp.isoformat(): row
                for timestamp, row in zip(labelled.index, labelled.to_dict("records"), strict=True)
            }
            market = CashMarketCache()

            def simulate(
                phase: str,
                family: str,
                params: dict,
                trail: bool = False,
                cost: str = "base",
                state: dict | None = None,
                key_suffix: str = "",
                fold: dict = fold,
                market: CashMarketCache = market,
                contexts: dict = contexts,
                as_of: str = as_of,
            ) -> dict:
                key = canonical([family, params, trail, cost, state, key_suffix])

                def calculate() -> dict:
                    bounds = fold["windows"][phase]["requested"]
                    days = frozenset(
                        day for day in eligible if bounds[0] <= day.isoformat() <= bounds[1]
                    )
                    config = StrategyConfig.model_validate(
                        {
                            **original["manifest"]["base"],
                            "name": family,
                            "initial_equity_usd": state["equity"] if state else 10000.0,
                            "commission_per_side_usd": SCENARIOS[cost][0],
                            "start": cash_timestamp(min(days), CASH_FIRST_CLOSE),
                            "end": cash_timestamp(max(days), CASH_LAST_CLOSE),
                        }
                    )
                    candidate = (
                        FAMILIES[family].model_validate(params) if family in FAMILIES else None
                    )
                    risk = RollRiskState(state["high_water"], state["halted"]) if state else None
                    report = simulate_backtest(
                        config,
                        bars,
                        metadata,
                        output / "reports",
                        cash_sessions=days,
                        orb=candidate if isinstance(candidate, OrbConfig) else None,
                        candidate=candidate,
                        prior_cash=histories,
                        cash_market=market,
                        experimental_trail=trail,
                        stress_ticks=SCENARIOS[cost][1],
                        persistent_risk=risk,
                        regime_rows=contexts,
                    )
                    result = json.loads((report / "summary.json").read_text())
                    equity = pd.read_csv(report / "equity.csv")
                    trades = trade_details(report)
                    return {
                        **result,
                        "family": family,
                        "params": params,
                        "trail": trail,
                        "cost": cost,
                        "key": key,
                        "report": report.relative_to(output).as_posix(),
                        "trades": trades,
                        "regimes": (
                            regime_report(trades, sorted(result["daily_pnl_usd"]))
                            if phase == "historical"
                            else []
                        ),
                        "high_water": risk.high_water
                        if risk
                        else max(10000.0, equity.equity_usd.max()),
                        "average_winner_usd": float(
                            pd.Series(
                                [t["net_pnl_usd"] for t in trades if t["net_pnl_usd"] > 0],
                                dtype=float,
                            ).mean()
                        )
                        if any(t["net_pnl_usd"] > 0 for t in trades)
                        else None,
                        "turnover_contracts": 2 * len(trades),
                        "reasons": [],
                    }

                return record_once(db, as_of, phase, key, calculate)

            training, validation = [], []
            for family in ("orb_cash_v1", "slot_momentum_cash_v1"):
                candidates = sorted(
                    [row for row in fold["training"] if row["family"] == family],
                    key=lambda row: (-row["net_pnl_usd"], row["max_drawdown_fraction"], row["key"]),
                )[:2]
                # Include rejected training leaders to diagnose whether the registered exit helps.
                for row in candidates:
                    for trail in (False, True):
                        trial = simulate("training", family, row["params"], trail)
                        trial["reasons"] = qualify(trial, 30, 20)
                        training.append(trial)
                qualified = rank([r for r in training if r["family"] == family], training)[:2]
                for row in qualified:
                    trial = simulate("validation", family, row["params"], row["trail"])
                    trial["reasons"] = qualify(
                        trial, 10, 8, relative_pnl=fold["reference"]["validation"]["net_pnl_usd"]
                    )
                    validation.append(trial)
            other = [
                row
                for row in fold["validation"]
                if row["family"] not in {"orb_cash_v1", "slot_momentum_cash_v1"}
            ]
            passing = rank(validation + other, training + fold["training"])
            selected = (
                {k: passing[0].get(k, False) for k in ("family", "params", "trail")}
                if passing
                else None
            )
            record_once(
                db, as_of, "selection", "m3", lambda selected=selected: {"selected": selected}
            )
            trailing_outer = []
            for family in ("orb_cash_v1", "slot_momentum_cash_v1"):
                candidates = [
                    row for row in training if row["family"] == family and not row["trail"]
                ]
                for row in candidates:
                    trailing_outer.extend(
                        simulate("historical", family, row["params"], trail)
                        for trail in (False, True)
                    )
            experimental = (
                simulate("historical", selected["family"], selected["params"], selected["trail"])
                if selected
                else None
            )
            stress = {}
            for cost in SCENARIOS:
                sma = simulate("historical", BASELINE, {}, cost=cost)
                chosen = fold["selected"]
                policy = (
                    simulate("historical", chosen["family"], chosen["params"], cost=cost)
                    if chosen
                    else None
                )
                stress[cost] = {"sma": sma, "policy": policy}
            accounts = {}
            for name in states:
                chosen = (
                    fold["selected"] if name == "policy" else {"family": BASELINE, "params": {}}
                )
                state = states[name]
                if chosen and not state["halted"]:
                    result = simulate(
                        "historical",
                        chosen["family"],
                        chosen["params"],
                        state=state,
                        key_suffix=f"continuous_{name}",
                    )
                    state = {
                        "equity": result["final_equity_usd"],
                        "high_water": result["high_water"],
                        "halted": result["halted"],
                    }
                    states[name] = state
                else:
                    result = {
                        "net_pnl_usd": 0.0,
                        "daily_pnl_usd": dict.fromkeys(fold["baseline"]["daily_pnl_usd"], 0.0),
                        "closed_trades": 0,
                        "commission_usd": 0.0,
                        "slippage_cost_usd": 0.0,
                        "max_drawdown_fraction": 1 - state["equity"] / state["high_water"],
                        "halted": state["halted"],
                        "halt_times_utc": [],
                    }
                accounts[name] = {"state": state.copy(), "result": result}
            completed.append(
                {
                    "as_of": as_of,
                    "windows": fold["windows"],
                    "thresholds": thresholds,
                    "qualification": qualification_rows(fold),
                    "variant_qualification": qualification_rows({
                        "training": training, "validation": validation,
                        "reference": fold["reference"],
                    }),
                    "training": training,
                    "validation": validation,
                    "selected": selected,
                    "trailing_outer": trailing_outer,
                    "experimental_policy": experimental,
                    "stress": stress,
                    "accounts": accounts,
                }
            )
            summary = {
                "manifest": manifest,
                "folds": completed,
                "uncertainty": uncertainty(completed),
            }
            save_snapshot(db, output, summary)
            print(as_of, selected, states, flush=True)


def uncertainty(folds: list[dict]) -> dict:
    reports = {}
    for convention in ("reset", "continuous"):
        left, right = {}, {}
        for fold in folds:
            base = fold["stress"]["base"]["sma"]["daily_pnl_usd"]
            if convention == "continuous":
                policy = fold["accounts"]["policy"]["result"]["daily_pnl_usd"]
                sma = fold["accounts"]["sma"]["result"]["daily_pnl_usd"]
            else:
                chosen = fold["stress"]["base"]["policy"]
                policy = chosen["daily_pnl_usd"] if chosen else dict.fromkeys(base, 0.0)
                sma = base
            if set(left).intersection(policy) or set(policy) != set(sma):
                raise ValueError("Overlapping or unmatched evaluation dates")
            left.update(policy)
            right.update(sma)
            reports[f"{convention}/{fold['as_of']}"] = [
                paired_bootstrap(policy, sma, block) for block in (3, 5, 10)
            ]
        for label, end in (("through_october", "2026-10-31"), ("including_november", "2026-11-10")):
            a = {day: value for day, value in left.items() if day <= end}
            b = {day: value for day, value in right.items() if day <= end}
            reports[f"{convention}/{label}"] = [
                paired_bootstrap(a, b, block) for block in (3, 5, 10)
            ]
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    run(args.source, args.dataset, args.output)


if __name__ == "__main__":
    main()

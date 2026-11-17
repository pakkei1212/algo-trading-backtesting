"""Streamlit controls for the local parameter optimization worker."""

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st
import yaml

from mnq_research.amp_mt5 import BAR, utc
from mnq_research.backtest import load_backtest_dataset, run_backtest
from mnq_research.config import load_config
from mnq_research.optimization import (
    OptimizationSpec,
    cancel_study,
    create_study,
    dataset_checksum,
    ranked,
    read_holdout,
    read_study,
    require_tpe,
    reveal_holdout,
    validate_study_windows,
    window,
)
from mnq_research.roll_backtest import roll_grid
from mnq_research.tpe_ranges import TpeRanges


def worker_busy(root: Path) -> bool:
    with closing(sqlite3.connect(root / "worker.sqlite", timeout=0)) as db:
        try:
            db.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            return True
        db.rollback()
    return False


def launch(folder: Path, root: Path) -> None:
    if worker_busy(folder.parent):
        st.warning("An optimization worker is already active.")
        return
    with closing(sqlite3.connect(folder / "study.sqlite")) as db, db:
        db.execute("UPDATE state SET canceled=0, status='Starting'")
    subprocess.Popen(
        [sys.executable, "-m", "mnq_research.optimization", str(folder)],
        cwd=root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def candidates_text(value: str, *, integer: bool) -> list[int] | list[float]:
    """Parse comma-separated values or one inclusive start:stop:step range."""
    from decimal import Decimal

    if ":" in value:
        start, end, step = map(Decimal, value.split(":"))
        if not all(x.is_finite() for x in (start, end, step)) or step <= 0 or end < start:
            raise ValueError("Enter a finite ascending range with a positive step.")
        count = int((end - start) // step) + 1
        if count > 200:
            raise ValueError("Use at most 200 values per parameter.")
        values = [start + i * step for i in range(count)]
    else:
        values = [Decimal(item.strip()) for item in value.split(",")]
    if any(not x.is_finite() for x in values):
        raise ValueError("Candidate values must be finite.")
    if integer:
        if any(x != int(x) for x in values):
            raise ValueError("SMA periods must be whole numbers.")
        return [int(x) for x in values]
    return [float(x) for x in values]


@st.fragment(run_every="2s")
def show_study(folder: Path, root: Path) -> None:
    spec, status, rows = read_study(folder)
    busy = worker_busy(folder.parent)
    candidates = spec.candidates()
    total = spec.training_budget(len(candidates))
    st.caption(
        f"Method: {spec.method}"
        + (
            f" · seed {spec.seed} · {len(candidates):,} valid combinations"
            if spec.method == "Optuna TPE"
            else ""
        )
    )
    training = [r for r in rows if r["phase"] == "Training"]
    validation = [r for r in rows if r["phase"] == "Validation"]
    qualified = ranked(rows)
    st.write(f"**{status}** · Training {len(training)}/{total} · Validation {len(validation)}")
    st.progress(len(training) / total)
    durations = [r.get("runtime_seconds", 0) for r in rows]
    elapsed = sum(durations)
    if durations:
        remaining = max(0, total - len(training))
        if status != "Completed":
            remaining += max(0, 1 + min(len(qualified), 5) - len(validation))
        estimate = elapsed / len(durations) * remaining
        st.caption(
            f"{remaining} estimated trials remaining · {elapsed:.1f}s simulation time · "
            f"{estimate:.1f}s estimated remaining"
        )
    if busy and status in {"Starting", "Running"}:
        if st.button("Cancel after current trial"):
            cancel_study(folder)
    elif status != "Completed" and not busy:
        if st.button("Resume optimization"):
            launch(folder, root)
    if not rows:
        return

    if qualified:
        best = qualified[0]
        st.metric("Best qualifying training net P&L", f"${best['net_pnl_usd']:,.2f}")
    else:
        st.info("No training candidates have qualified.")
    df = pd.DataFrame(rows)
    qualifying_only = st.checkbox("Show qualifying results only")
    order = {r["candidate"]: i + 1 for i, r in enumerate(qualified)}
    df["training_rank"] = df["candidate"].map(order)
    displayed = df[df.score.notna()] if qualifying_only else df
    columns = [
        "training_rank",
        "phase",
        "candidate",
        "fast",
        "slow",
        "atr",
        "net_pnl_usd",
        "return_fraction",
        "max_drawdown_fraction",
        "profit_factor",
        "win_rate",
        "closed_trades",
        "average_closed_trade_usd",
        "open_positions",
        "reason",
    ]
    table = displayed[columns].sort_values("training_rank")
    table["candidate"] = table["candidate"].map(
        lambda index: "Baseline" if index == -1 else f"#{index + 1}"
    )
    st.dataframe(table, hide_index=True)
    st.download_button("Export study CSV", df.to_csv(index=False), "optimization.csv")
    st.download_button("Export study settings", spec.model_dump_json(indent=2), "study.json")
    if training:
        plotted = pd.DataFrame(training)
        st.scatter_chart(plotted, x="max_drawdown_fraction", y="net_pnl_usd")
        atr = st.selectbox("Heatmap ATR multiplier", sorted(plotted.atr.unique()))
        matrix = plotted[plotted.atr.eq(atr)].pivot(
            index="fast", columns="slow", values="net_pnl_usd"
        )
        st.caption("Training P&L by Fast SMA (rows) and Slow SMA (columns).")
        import plotly.express as px

        st.plotly_chart(px.imshow(matrix, color_continuous_scale="RdBu"), width="stretch")
    st.subheader("Training versus validation")
    baseline = next((row for row in validation if row["candidate"] == -1), None)
    if baseline is not None:
        st.metric("Baseline validation net P&L", f"USD {baseline['net_pnl_usd']:,.2f}")
        st.caption(
            f"SMA {baseline['fast']}/{baseline['slow']} · ATR {baseline['atr']:g}× · "
            f"{baseline['closed_trades']} closed trades · "
            f"{baseline['max_drawdown_fraction']:.2%} maximum drawdown"
        )
    validated_ids = {row["candidate"] for row in validation if row["candidate"] >= 0}
    if validated_ids:
        paired = df[df.candidate.isin(validated_ids)].pivot(
            index="candidate", columns="phase", values="net_pnl_usd"
        )
        paired.index = paired.index.map(lambda index: f"Candidate #{index + 1}")
        st.bar_chart(paired, y=["Training", "Validation"], stack=False, y_label="Net P&L (USD)")
    elif status == "Completed":
        st.info("No candidates qualified for validation. The baseline was evaluated separately.")
    else:
        st.caption("Paired results appear after shortlisted candidates finish validation.")
    if not qualified:
        return

    index = st.selectbox(
        "Candidate", list(order), format_func=lambda i: f"Rank {order[i]} · #{i + 1}"
    )
    candidate = candidates[index]
    st.json(candidate.model_dump(mode="json"), expanded=False)
    axes = (
        spec.ranges.axis_values()
        if spec.ranges
        else (sorted(set(spec.fast)), sorted(set(spec.slow)), sorted(set(spec.atr)))
    )
    neighbors = []
    for row in training:
        steps = []
        for field, grid in zip(("fast", "slow", "atr"), axes, strict=True):
            selected = getattr(
                candidate,
                {"fast": "fast_period", "slow": "slow_period", "atr": "atr_multiple"}[field],
            )
            steps.append(abs(grid.index(row[field]) - grid.index(selected)))
        if sum(steps) == 1:
            neighbors.append(row)
    if len(neighbors) >= 2:
        qualifying = sum(row["score"] is not None for row in neighbors)
        low = min(row["net_pnl_usd"] for row in neighbors)
        high = max(row["net_pnl_usd"] for row in neighbors)
        st.caption(
            f"Adjacent training settings: {qualifying}/{len(neighbors)} qualify. "
            f"Net P&L ranges from USD {low:,.2f} to USD {high:,.2f}."
        )
    else:
        st.caption("Too few evaluated neighboring settings to assess local sensitivity.")
    st.download_button(
        "Download candidate YAML",
        yaml.safe_dump(candidate.model_dump(mode="json"), sort_keys=False),
        "candidate.yaml",
    )
    if st.button("Load candidate into Configure & run"):
        st.session_state["draft_config"] = candidate.model_dump(mode="json")
        st.rerun()
    if not busy and st.button("Generate validation reports and equity comparison"):
        try:
            _, metadata = load_backtest_dataset(spec.dataset)
            if dataset_checksum(metadata) != spec.checksum:
                raise ValueError("The study dataset changed.")
            curves = {}
            for label, config in (("Baseline", spec.base), ("Candidate", candidate)):
                run = run_backtest(window(config, spec.validation), spec.dataset, root / "runs")
                equity = pd.read_csv(run / "equity.csv", parse_dates=["timestamp"])
                curves[label] = equity.set_index("timestamp")["equity_usd"]
            st.session_state[f"optimization-equity:{folder.name}:{index}"] = pd.DataFrame(curves)
        except (ValueError, OSError) as error:
            st.error(str(error))
    if spec.holdout and status == "Completed" and not busy:
        locked = read_holdout(folder)
        st.caption("Revealing the reserved holdout permanently locks this study to one candidate.")
        can_reveal = locked is None or (locked[0] == index and locked[1] is None)
        if locked is not None:
            st.caption(f"Holdout candidate: #{locked[0] + 1}")
        if st.button("Lock candidate and reveal holdout", disabled=not can_reveal):
            try:
                reveal_holdout(folder, index)
                locked = read_holdout(folder)
            except (ValueError, OSError, sqlite3.Error) as error:
                st.error(str(error))
        if locked and locked[1]:
            st.dataframe(pd.DataFrame([locked[1]]), hide_index=True)
    curves = st.session_state.get(f"optimization-equity:{folder.name}:{index}")
    if curves is not None:
        st.line_chart(curves)


def render_optimizer(root: Path, *, cloud: bool) -> None:
    st.header("Optimize Parameters")
    if cloud:
        st.info("Parameter optimization runs in the local app.")
        return
    datasets = sorted(
        path
        for path in (root / "data").glob("*/metadata.json")
        if json.loads(path.read_text(encoding="utf-8")).get("provider") != "amp_mt5"
    )
    datasets += sorted((root / "data" / "continuous-research").glob("*/manifest.json"))
    configs = sorted((root / "configs").glob("*.yaml"))
    if not datasets or not configs:
        st.info("Add a dataset and an SMA crossover configuration to start.")
        return
    studies = root / "runs" / "optimization"
    studies.mkdir(parents=True, exist_ok=True)
    existing = sorted((p.parent for p in studies.glob("*/study.sqlite")), reverse=True)
    st.caption("Exploratory research. Training ranks candidates. Validation uses later bars.")
    dataset = st.selectbox(
        "Optimization dataset",
        datasets,
        format_func=lambda p: (
            f"MNQ continuous · 5m · {p.parent.name.rsplit('-', 1)[-1]}"
            if p.name == "manifest.json"
            else p.parent.name
        ),
    )
    config = st.selectbox("Optimization strategy", configs, format_func=lambda p: p.stem)
    assert dataset is not None and config is not None
    try:
        bars, metadata = load_backtest_dataset(dataset.parent)
        base = load_config(config)
    except (ValueError, OSError) as error:
        st.error(str(error))
        return
    st.caption(
        f"{metadata['symbol']} · {metadata.get('bar_interval', '1h')} · "
        f"{len(bars):,} observed bars · {metadata.get('kind')} · "
        f"{metadata.get('gaps_over_interval', 0)} intervals with gaps"
    )
    method = st.selectbox("Optimization method", ["Grid Search", "Optuna TPE"])
    budget, seed = 200, 0
    slow_max = 150
    range_inputs = {}
    st.caption("Costs, sizing, ATR lookback, and exit settings come from the preset.")
    if method == "Optuna TPE":
        left, right = st.columns(2)
        budget = left.number_input("TPE training trial budget", 1, 200, 30)
        seed = right.number_input("TPE random seed", 0, 2**32 - 1, 0)
        fast_col, slow_col, atr_col = st.columns(3)
        slow_max = slow_col.number_input("Slow SMA maximum", 3, 2000, 150)
        range_inputs = {
            "fast_min": fast_col.number_input("Fast SMA minimum", 2, 1000, 5),
            "fast_max": fast_col.number_input("Fast SMA maximum", 2, 1000, 25),
            "slow_min": slow_col.number_input("Slow SMA minimum", 3, 2000, 30),
            "slow_max": slow_max,
            "atr_min": atr_col.text_input("ATR multiplier minimum", "1.0"),
            "atr_max": atr_col.text_input("ATR multiplier maximum", "2.5"),
            "atr_step": atr_col.text_input("ATR multiplier step", "0.1"),
        }
    else:
        st.caption("Enter comma-separated values or an inclusive start:stop:step range.")
        cols = st.columns(3)
        fast = cols[0].text_input("Fast SMA candidates", "5,10,15,20,25")
        slow = cols[1].text_input("Slow SMA candidates", "30,50,75,100,150")
        atr = cols[2].text_input("ATR multiplier candidates", "1,1.5,2,2.5")
    continuous = metadata.get("provider") == "continuous_mnq"
    date_index = bars.index
    if continuous:
        if not metadata["verified_ranges"]:
            st.warning("The continuous dataset has no verified execution interval.")
            return
        left, right = max(metadata["verified_ranges"], key=lambda p: utc(p[1]) - utc(p[0]))
        date_index = roll_grid(utc(left) - BAR, utc(right)).index
        st.caption("Uses dated fills and flattens before each roll, as in Configure & run.")
    start_at = min(max(slow_max + 1, base.warmup_bars()), len(date_index) - 1)
    if len(date_index) < 3:
        st.warning("This dataset is too short for optimization.")
        return
    days = date_index[start_at:].normalize().unique()
    split_day = days[min(int(len(days) * 0.7), len(days) - 1)]
    split = min(max(start_at + 1, date_index.searchsorted(split_day)), len(date_index) - 1)
    defaults = [date_index[i].isoformat() for i in (start_at, split - 1, split, -1)]
    with st.expander("Evaluation dates and qualification"):
        train = (
            st.text_input("Training start UTC", defaults[0], key=f"{dataset}:train-start"),
            st.text_input("Training end UTC", defaults[1], key=f"{dataset}:train-end"),
        )
        validation = (
            st.text_input("Validation start UTC", defaults[2], key=f"{dataset}:val-start"),
            st.text_input("Validation end UTC", defaults[3], key=f"{dataset}:val-end"),
        )
        holdout_start = st.text_input("Reserved holdout start UTC (optional)")
        holdout_end = st.text_input("Reserved holdout end UTC (optional)")
        objective = st.selectbox("Objective", ["Net profit", "Return / drawdown"])
        drawdown = st.number_input("Maximum qualifying drawdown (%)", 0.1, 99.9, 10.0)
        train_trades = st.number_input("Minimum training trades", 1, value=30)
        validation_trades = st.number_input("Minimum validation trades", 1, value=15)
        min_bars = st.number_input("Minimum observed bars per period", 1, value=200)
        min_days = st.number_input("Minimum observed UTC days per period", 1, value=5)
    spec = None
    try:
        parameters = (
            {"ranges": TpeRanges.model_validate(range_inputs)}
            if method == "Optuna TPE"
            else {
                "fast": candidates_text(fast, integer=True),
                "slow": candidates_text(slow, integer=True),
                "atr": candidates_text(atr, integer=False),
            }
        )
        spec = OptimizationSpec(
            dataset=dataset.parent,
            checksum=dataset_checksum(metadata),
            base=base,
            **parameters,
            method=method,
            seed=seed,
            max_trials=budget,
            drawdown=drawdown / 100,
            train_trades=train_trades,
            validation_trades=validation_trades,
            minimum_bars=min_bars,
            minimum_days=min_days,
            train=train,
            validation=validation,
            holdout=(holdout_start, holdout_end) if holdout_start or holdout_end else None,
            objective=objective,
        )
        candidates = spec.candidates()
        count = len(candidates)
        trials = spec.training_budget(count)
        if method == "Optuna TPE":
            require_tpe()
            st.caption(f"{count:,} valid combinations · {trials} unique training trials.")
        validate_study_windows(spec, bars, metadata, candidates)
        if continuous:
            st.caption("Warm-up, evaluation coverage, and scheduled roll bars verified.")
        st.caption(f"{trials} training trials (limit 200) · up to {trials + 6} simulations")
        if trials >= 100:
            st.warning("This study may take several minutes.")
    except (ValueError, ArithmeticError, TypeError, OSError) as error:
        spec = None
        st.warning(str(error))

    busy = worker_busy(studies)
    if st.button("Start Optimization", disabled=spec is None or busy) and spec is not None:
        try:
            folder = studies / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8])
            create_study(folder, spec)
            launch(folder, root)
            st.rerun()
        except (ValueError, ArithmeticError, TypeError, OSError) as error:
            st.warning(str(error))
    if existing:
        chosen = st.selectbox("Saved optimization study", existing, format_func=lambda p: p.name)
        if chosen is not None:
            show_study(chosen, root)

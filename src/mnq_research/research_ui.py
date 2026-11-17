"""Read-only local results for the cash-session walk-forward experiment."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from mnq_research.experiment_ui import render_experiment
from mnq_research.variant_comparison_ui import render_comparison


def render_research(root: Path, *, cloud: bool) -> None:
    if cloud:
        st.info("Historical research studies are available in the local app.")
        return
    reports = sorted((root / "runs" / "cash-research").glob("*/summary.json"), reverse=True)
    studies = {}
    for path in reports:
        database = path.parent / "study.sqlite"
        snapshot = None
        if database.exists():
            try:
                uri = f"{database.resolve().as_uri()}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=1) as db:
                    table = db.execute("SELECT name FROM sqlite_master WHERE name='snapshot'")
                    if table.fetchone():
                        snapshot = db.execute("SELECT payload FROM snapshot WHERE id=1").fetchone()
            except sqlite3.OperationalError:
                # A running study may hold its exclusive lock. Keep showing its
                # last complete export; incomplete exports are skipped below.
                pass
        try:
            contents = json.loads(snapshot[0] if snapshot else path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "folds" in contents:
            studies[path.parent.name] = contents
    comparison_folders = sorted(
        path.parent for path in (root / "runs" / "cash-research").glob("*/comparison.sqlite")
        if (path.parent / "manifest.json").exists()
    )
    if comparison_folders:
        view = st.radio("Research view", ("All variants comparison", "Selection-policy studies"),
                        horizontal=True)
        if view == "All variants comparison":
            folder = st.selectbox(
                "Comparison study", comparison_folders, format_func=lambda path: path.name
            )
            render_comparison(folder)
            return
    if not studies:
        st.info("Run a local walk-forward study to see fold results.")
        return
    chosen = st.selectbox("Research study", list(studies))
    study = studies[chosen]
    if study["manifest"].get("recipe") == "m3_fixed_trail_stress_persistent_v1":
        render_experiment(root / "runs" / "cash-research" / chosen, study)
        return
    manifest = study["manifest"]
    stage = st.radio(
        "Evaluation stage",
        ("Historical walk-forward", "Internal validation", "Locked evaluation", "Prospective"),
        horizontal=True,
    )
    if stage == "Locked evaluation":
        locked = manifest["locked"]
        st.info(f"{locked['status']} · proposed {locked['start']} to {locked['end']}")
        return
    if stage == "Prospective":
        st.info(manifest["prospective"]["status"])
        return
    st.warning("Development-exposed archive. Historical results use previously inspected data.")
    fold = st.selectbox(
        "As-of selection date", study["folds"], format_func=lambda row: row["as_of"]
    )
    st.table(pd.DataFrame([
        {
            "Phase": phase, "First": window["actual"][0], "Last": window["actual"][1],
            "Sessions": window["sessions"],
        }
        for phase, window in fold["windows"].items()
    ]))
    selected = fold["selected"]
    if selected:
        st.write(f"Selection · {selected['family']}")
        st.json(selected["params"])
    else:
        st.info("No candidate passed selection. The historical policy stayed in cash.")

    def trial_table(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame([
            {
                "Family": row["family"], "Parameters": json.dumps(row["params"]),
                "Net P&L (USD)": row["net_pnl_usd"],
                "Max drawdown (%)": round(row["max_drawdown_fraction"] * 100, 2),
                "Trades": row["closed_trades"],
                "Costs (USD)": row["commission_usd"] + row["slippage_cost_usd"],
                "Flat sessions": row["flat_sessions"],
                "Time flat (%)": round(row["flat_time_fraction"] * 100, 2),
                "Halt (UTC)": ", ".join(row["halt_times_utc"]),
                "Outcome": row.get("basis") or "; ".join(row["reasons"]) or "Qualified",
            }
            for row in rows
        ])

    if stage == "Internal validation":
        baseline = {**fold["reference"]["validation"], "basis": "SMA reference"}
        st.dataframe(
            trial_table([baseline, *fold["validation"]]), hide_index=True,
            alt="Internal validation and SMA reference results",
        )
        with st.expander("Training results and rejection reasons"):
            st.dataframe(
                trial_table(fold["training"]), hide_index=True,
                alt="Training trials, results and rejection reasons",
            )
    else:
        st.metric("Policy minus SMA", f"${fold['paired_pnl_usd']:,.2f}")
        st.caption(
            f"Policy was flat for {fold['policy_flat_sessions']} full sessions and "
            f"{fold['policy_flat_time_fraction']:.1%} of total cash-session time."
        )
        baseline = {**fold["baseline"], "basis": "SMA reference"}
        st.dataframe(
            trial_table([baseline, *fold["historical"].values()]), hide_index=True,
            alt="Historical SMA and four strategy family results",
        )
        paired = pd.Series(fold["paired_daily_pnl_usd"], name="Policy minus SMA (USD)")
        st.line_chart(paired.cumsum(), alt="Cumulative paired net P&L difference")
    with st.expander("Recorded prior use of this archive"):
        st.dataframe(
            pd.DataFrame(manifest["prior_exposures"]), hide_index=True,
            alt="Previously recorded uses of this archive",
        )

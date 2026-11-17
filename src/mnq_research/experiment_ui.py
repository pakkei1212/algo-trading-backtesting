"""Milestone 3 historical experiment comparisons and native trade drill-down."""

from pathlib import Path

import pandas as pd
import streamlit as st

from mnq_research.results_ui import render_results


def result_row(label: str, result: dict | None) -> dict:
    result = result or {}
    return {
        "Run": label,
        "Net P&L (USD)": result.get("net_pnl_usd", 0),
        "Drawdown (%)": 100 * result.get("max_drawdown_fraction", 0),
        "Trades": result.get("closed_trades", 0),
        "Costs (USD)": result.get("commission_usd", 0) + result.get("slippage_cost_usd", 0),
        "Halted": result.get("halted", False),
    }


def render_experiment(folder: Path, study: dict) -> None:
    st.warning("Historical research on a development-exposed archive.")
    st.caption(study["manifest"]["recipe"])
    fold = st.selectbox(
        "Experiment as-of date", study["folds"], format_func=lambda row: row["as_of"]
    )
    st.caption("Outer dates " + " to ".join(fold["windows"]["historical"]["actual"]))
    st.caption("Base: $1 per side / 1 adverse tick · 10% permanent drawdown halt")
    view = st.radio(
        "Experiment view",
        [
            "Qualification",
            "Regimes",
            "Trailing exits",
            "Execution costs",
            "Account replay",
            "Uncertainty",
            "Trades and chart",
            "Provenance",
        ],
        horizontal=True,
    )
    runs = fold["trailing_outer"] + [
        result for scenario in fold["stress"].values() for result in scenario.values() if result
    ]
    if view == "Qualification":
        scope = st.radio("Selection recipe", ["Original Milestone 2", "Trailing variants"])
        data = pd.DataFrame(fold["qualification"] if scope == "Original Milestone 2"
                            else fold.get("variant_qualification", []))
        if data.empty:
            st.info("Variant checks are unavailable in this older snapshot.")
            return
        st.table(pd.DataFrame([
            {"Stage": phase, "First session": window["actual"][0],
             "Last session": window["actual"][1], "Eligible sessions": window["sessions"]}
            for phase, window in fold["windows"].items()
        ]))
        st.dataframe(
            data.groupby(["phase", "family"])
            .agg(Completed=("key", "size"), Qualified=("qualified", "sum"))
            .reset_index(),
            hide_index=True,
        )
        key = st.selectbox(
            "Configuration checks",
            data.index,
            format_func=lambda index: (
                f"{data.loc[index, 'phase']} · "
                f"{data.loc[index, 'family']} · {data.loc[index, 'configuration']}"
            ),
        )
        st.json(data.loc[key].to_dict())
        st.caption("Counts represent unique completed configurations; failed gates may overlap.")
        st.write(
            "Trailing-experiment selection", fold["selected"] or "Cash: no validation qualifier"
        )
    elif view == "Regimes":
        chosen = st.selectbox("Attributed run", runs, format_func=run_label)
        st.dataframe(pd.DataFrame(chosen["regimes"]), hide_index=True)
        with st.expander("Feature definitions and training thresholds"):
            st.json(fold["thresholds"])
            st.caption(
                "Volatility uses prior daily ATR / prior close. Efficiency needs 13 cash closes, "
                "first available at 10:35 New York. Sparse groups are inconclusive."
            )
    elif view == "Trailing exits":
        st.caption("Base costs · fixed +1R activation and 2 entry-ATR trailing distance")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        **result_row(run_label(row), row),
                        "Parameters": str(row["params"]),
                        "Mean winner (USD)": row["average_winner_usd"],
                        "Expectancy (USD)": row["net_pnl_usd"] / row["closed_trades"]
                        if row["closed_trades"] else None,
                        "Turnover (contracts)": row["turnover_contracts"],
                        "Exit reasons": str(pd.Series(
                            [trade["exit_reason"] for trade in row["trades"]], dtype=str
                        ).value_counts().to_dict()),
                        "Mean giveback (USD)": pd.Series(
                            [trade["giveback_usd_bar_estimate"] for trade in row["trades"]],
                            dtype=float,
                        ).mean(),
                    }
                    for row in fold["trailing_outer"]
                ]
            ),
            hide_index=True,
        )
        st.write("Experiment selection", fold["selected"] or "Cash")
        st.caption("Excursions include the full entry bar and exclude the partial exit bar.")
    elif view == "Execution costs":
        st.caption("Frozen original base selections · actual native execution reruns")
        st.json(study["manifest"]["costs"])
        st.dataframe(
            pd.DataFrame(
                [
                    result_row(f"{scenario} · {name}", row)
                    for scenario, results in fold["stress"].items()
                    for name, row in results.items()
                ]
            ),
            hide_index=True,
        )
    elif view == "Account replay":
        st.caption(study["manifest"]["account"])
        rows = []
        for window in study["folds"]:
            for name, account in window["accounts"].items():
                rows.append(
                    {
                        "As of": window["as_of"],
                        "Account": name,
                        **result_row(name, account["result"]),
                        **account["state"],
                        "Reset-fold P&L": (
                            (window["stress"]["base"][name] or {}).get("net_pnl_usd", 0)
                        ),
                    }
                )
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    elif view == "Uncertainty":
        st.caption(
            "Paired calendar days include flat days. November inclusion is shown separately."
        )
        st.caption("Stationary circular blocks can cross fold boundaries; 2,000 samples, seed 0.")
        st.dataframe(pd.DataFrame([
            {"Comparison": label, **interval}
            for label, intervals in study.get("uncertainty", {}).items()
            for interval in intervals
        ]), hide_index=True)
    elif view == "Trades and chart":
        chosen = st.selectbox("Native run", runs, format_func=run_label)
        st.dataframe(pd.DataFrame(chosen["trades"]), hide_index=True)
        if st.checkbox("Open Strategy Tester chart"):
            render_results(folder / chosen["report"])
    else:
        st.json(study["manifest"])
        st.info("Locked and prospective evaluation remain pending.")


def run_label(row: dict) -> str:
    return (
        f"{row['family']} · {row['params']} · {row['cost']} · "
        f"trail {'on' if row['trail'] else 'off'}"
    )

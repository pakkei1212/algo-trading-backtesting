"""Read-only matched comparison and native run drill-down."""

from pathlib import Path

import pandas as pd
import streamlit as st

from mnq_research.reporting import read_report
from mnq_research.research_diagnostics import qualification_checks
from mnq_research.results_ui import render_results
from mnq_research.variant_comparison import PHASES, matched_table, read_comparison
from mnq_research.walk_forward import BASELINE

LABELS = {"training": "Training", "validation": "Validation", "oot": "OOT"}
IDENTITY = ["OOT month", "Family", "Exit variant", "Configuration ID", "Parameters"]


def chart_points(series: pd.Series) -> pd.Series:
    """Keep extrema within each display bucket and retain the two endpoints."""
    if len(series) <= 1200:
        return series
    width = (len(series) + 499) // 500
    buckets = series.groupby(pd.RangeIndex(len(series)) // width)
    keep = buckets.idxmin().tolist() + buckets.idxmax().tolist()
    return series.loc[sorted({series.index[0], series.index[-1], *keep})]


def render_comparison(folder: Path) -> None:
    manifest, results = read_comparison(folder)
    st.subheader("All variants comparison")
    st.warning("Development-exposed archive. Retrospective results are research diagnostics.")
    st.caption(
        "Account drawdown halt disabled · 10,000 USD per phase · 1 MNQ · "
        "1 USD per side + 1 adverse tick per fill"
    )
    st.caption(
        "OOT means out-of-time evaluation, the period after validation. "
        "April is OOT for the first fold, May for the second, and so on."
    )
    table = matched_table(manifest, results)
    status_columns = [f"{label} status" for label in LABELS.values()]
    counts = table[status_columns].stack().value_counts()
    st.write(
        f"{int(counts.get('completed', 0)):,} / {manifest['expected']:,} phase results completed",
        " · ".join(f"{count} {status}" for status, count in counts.items()),
    )
    controls = st.columns(4)
    displayed = table
    for control, column in zip(controls, IDENTITY[:3], strict=False):
        selected = control.multiselect(
            column, sorted(table[column].unique()), key=f"variant-filter:{column}"
        )
        if selected:
            displayed = displayed[displayed[column].isin(selected)]
    statuses = controls[3].multiselect("Run status", list(counts.index))
    if statuses:
        displayed = displayed[displayed[status_columns].isin(statuses).any(axis=1)]
    detailed = st.checkbox("Show detailed metrics")
    basic = IDENTITY + [
        f"{label} {metric}"
        for label in LABELS.values()
        for metric in ("return %", "maximum drawdown %")
    ]
    incomplete = any(status != "completed" for status in counts.index)
    columns = list(table.columns) if detailed else basic + (status_columns if incomplete else [])
    st.download_button(
        "Download full matched CSV",
        table.to_csv(index=False),
        file_name="mnq-all-variants-development-exposed.csv",
        mime="text/csv",
    )
    event = st.dataframe(
        displayed[columns],
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"matched-variants:{folder.name}:{','.join(map(str, displayed.index))}",
        column_config={
            "Configuration ID": st.column_config.TextColumn(width=90),
            "Parameters": st.column_config.TextColumn(width=185),
            **{
                name: st.column_config.NumberColumn(format="%.2f")
                for name in columns
                if name.endswith(" %")
            },
            **{
                f"{label} {metric}": st.column_config.NumberColumn(
                    label=f"{short} {heading}",
                    help=f"{label} {metric}",
                    format="%.2f",
                    width=100,
                )
                for label, short in (("Training", "Train"), ("Validation", "Val"), ("OOT", "OOT"))
                for metric, heading in (
                    ("return %", "return %"),
                    ("maximum drawdown %", "max DD %"),
                )
            },
        },
        alt="Matched training, validation and out-of-time return and drawdown by configuration.",
    )
    if event.selection.rows:
        row = displayed.iloc[event.selection.rows[0]]
        render_row(folder, manifest, results, row["OOT month"], row["Configuration ID"])
    else:
        st.caption("Select a row to inspect its dates, settings, equity and individual trades.")
    with st.expander("OOT summary across independently reset folds"):
        st.caption("Net P&L is summed. Drawdown is the worst individual fold. Complete rows only.")
        complete = table[table["OOT status"].eq("completed")]
        if complete.empty:
            st.info("No OOT results completed yet.")
        else:
            overview = complete.groupby(IDENTITY[1:], as_index=False).agg(
                **{
                    "Completed folds": ("OOT status", "size"),
                    "Net P&L USD": ("OOT net P&L USD", "sum"),
                    "Profitable folds": ("OOT net P&L USD", lambda pnl: pnl.gt(0).sum()),
                    "Trades": ("OOT trades", "sum"),
                    "Worst individual drawdown %": ("OOT maximum drawdown %", "max"),
                }
            )
            st.dataframe(overview, hide_index=True, alt="Fixed configuration OOT results.")


def render_row(folder: Path, manifest: dict, results: dict, month: str, config_id: str) -> None:
    fold = next(fold for fold in manifest["folds"] if fold["month"] == month)
    config = next(config for config in manifest["registry"] if config["id"] == config_id)
    baseline = next(row["id"] for row in manifest["registry"] if row["family"] == BASELINE)
    st.markdown(f"**{month} · {config['family']} · {config['exit_policy']}**")
    with st.expander("Complete configuration"):
        st.json(config)
    show_diagnostics = st.checkbox("Show original qualification checks")
    reports = {}
    panels = st.tabs(list(LABELS.values()), key="variant-phase-plots")
    for phase, panel in zip(PHASES, panels, strict=True):
        with panel:
            window = fold["windows"][phase]
            days = window["eligible_days"]
            st.caption(
                f"{days[0]} to {days[-1]} · {len(days)} eligible sessions · "
                f"Requested span {window['first']} to {window['last']}"
            )
            result = results.get((fold["as_of"], config_id, phase), {"status": "queued"})
            if result["status"] != "completed":
                st.info(f"{result['status'].title()} · {result.get('error') or 'No metrics yet'}")
                continue
            metrics = result["metrics"]
            st.write(
                f"{metrics['trades']} closed trades · ${metrics['costs_usd']:,.2f} modeled costs · "
                f"{metrics['risk_margin_skips']} risk/margin skips · "
                f"{metrics['drawdown_breaches']} crossings of 10% drawdown · "
                f"{metrics['order_rejections']} order rejections"
            )
            if show_diagnostics and phase != "oot":
                paired = results.get((fold["as_of"], baseline, phase), {}).get("metrics")
                if phase == "training" or paired:
                    checks = qualification_checks(
                        {
                            **metrics,
                            "closed_trades": metrics["trades"],
                            "halted": metrics["order_halted"],
                            "max_drawdown_fraction": metrics["drawdown_pct"] / 100,
                        },
                        phase,
                        paired["net_pnl_usd"] if paired else 0,
                    )
                    st.write(
                        "Failed checks", [name for name, passed in checks.items() if not passed]
                    )
            report = folder / result["report"]
            reports[LABELS[phase]] = report
            equity = read_report(report, "equity")
            equity["timestamp"] = pd.to_datetime(equity.timestamp, format="ISO8601")
            series = equity.set_index("timestamp").rename(columns={"equity_usd": "Equity (USD)"})
            series["Drawdown (%)"] = 100 * series.drawdown
            if len(series) > 1200:
                st.caption(
                    "Chart retains peaks and troughs per display interval. Metrics use all bars."
                )
            st.line_chart(
                chart_points(series["Equity (USD)"]), alt="Marked equity by five-minute close."
            )
            st.line_chart(
                chart_points(series["Drawdown (%)"]), alt="Drawdown from the running peak."
            )
    if reports:
        selected = st.selectbox(
            "Inspect trades and candlesticks",
            ["Select phase", *reports],
            key=f"variant-native:{month}:{config_id}",
        )
        if selected in reports:
            render_results(reports[selected])

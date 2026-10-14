"""Integrated Strategy Tester; accounting is owned by reporting, execution by Nautilus."""

import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from mnq_research.chart_component import ChartRenderer, register_chart, render_chart
from mnq_research.criteria import strategy_summary
from mnq_research.reporting import (
    RunReport,
    TradeRecord,
    chart_payload,
    display_time,
    load_run,
    read_report,
    trade_table,
    utc,
)


def command(run_id: str, kind: str, trade_id: str | None = None) -> None:
    st.session_state[f"command:{run_id}"] = dict(id=uuid4().hex, kind=kind, trade_id=trade_id)
    if trade_id:
        st.session_state[f"selected:{run_id}"] = trade_id


def number(value: float | int | None, style: str = "number") -> str:
    if value is None:
        return "—"
    if style == "money":
        return f"${value:,.2f}"
    if style == "percent":
        return f"{value:.2%}"
    return f"{value:,.2f}"


def overview(
    report: RunReport, payload: dict, timezone: str, selected: str | None, component: ChartRenderer
) -> None:
    s, m = report.summary, report.metrics
    # Wrap cards by available width rather than truncating dollar amounts on narrow windows.
    st.html("""
        <style>
        .st-key-tester-summary [data-testid="stHorizontalBlock"] {flex-wrap:wrap}
        .st-key-tester-summary [data-testid="stColumn"] {flex:1 1 160px;min-width:160px}
        .st-key-tester-summary [data-testid="stMetricValue"] {font-size:1.65rem}
        .st-key-tester-summary [data-testid="stMetricLabel"] p {white-space:normal}
        </style>
    """)
    with st.container(border=True, key="tester-summary"):
        cols = st.columns(4)
        cols[0].metric(
            "Net profit / loss",
            number(s["net_pnl_usd"], "money"),
            f"{s['net_pnl_usd'] / report.config.initial_equity_usd:+.2%}",
            help="Final marked equity minus starting capital; includes unrealized P&L.",
        )
        cols[1].metric(
            "Maximum equity drawdown",
            number(m["drawdown_pct"], "percent"),
            help="Largest bar-close percentage decline from the running equity peak.",
        )
        cols[2].metric(
            "Winning trades",
            number(m["win_rate"], "percent"),
            f"{m['winners']} / {m['closed_count']} closed",
            delta_color="off",
            help="Breakevens remain in the denominator. — means no closed trades.",
        )
        cols[3].metric(
            "Profit factor",
            number(m["profit_factor"]),
            help="Positive closed-trade net P&L / absolute negative total. — means no losses.",
        )
        cols = st.columns(5)
        for col, label, value in zip(
            cols,
            ["Starting equity", "Final equity", "Closed trades", "Open positions", "Commissions"],
            [
                number(report.config.initial_equity_usd, "money"),
                number(s["final_equity_usd"], "money"),
                m["closed_count"],
                m["open_count"],
                number(s["commission_usd"], "money"),
            ],
            strict=True,
        ):
            col.metric(label, value)
    st.caption(
        "Performance cards always cover the whole run. Net P&L includes open-position P&L "
        "and paid commissions. Win rate and profit factor use closed trades only."
    )
    render_chart(payload, "performance", timezone, selected, component)


def performance(report: RunReport) -> None:
    s, m = report.summary, report.metrics
    groups = {
        "Returns": [
            (
                "Total equity change",
                s["net_pnl_usd"],
                "money",
                "Final equity less starting capital.",
            ),
            (
                "Closed-trade net P&L",
                m["closed_net"],
                "money",
                "Realized P&L including native commissions.",
            ),
            (
                "Unrealized P&L",
                m["unrealized"],
                "money",
                "Open positions marked at the final close; exit fees not charged.",
            ),
            (
                "Commissions paid",
                s["commission_usd"],
                "money",
                "Already included in net results; never subtracted twice.",
            ),
        ],
        "Risk": [
            (
                "Maximum drawdown (USD)",
                m["drawdown_usd"],
                "money",
                "Largest dollar decline from the running peak at bar closes.",
            ),
            (
                "Maximum drawdown (%)",
                m["drawdown_pct"],
                "percent",
                "Largest percentage decline from the running peak at bar closes.",
            ),
        ],
        "Trades": [
            (label, m[key], "number", definition)
            for label, key, definition in [
                ("Closed trades", "closed_count", "Positions with an exit fill."),
                ("Open positions", "open_count", "Positions still open at the evaluation end."),
                ("Winners", "winners", "Closed positions with net P&L greater than zero."),
                ("Losers", "losers", "Closed positions with net P&L below zero."),
                ("Breakevens", "breakevens", "Closed positions with exactly zero net P&L."),
            ]
        ]
        + [
            (
                "Win rate",
                m["win_rate"],
                "percent",
                "Winners / all closed trades, including breakevens; — if none.",
            ),
            (
                "Profit factor",
                m["profit_factor"],
                "number",
                "Positive net P&L / absolute losses; — if no losses.",
            ),
        ],
        "Trade characteristics": [
            (label, m[key], style, definition)
            for label, key, style, definition in [
                (
                    "Average net trade",
                    "average_trade",
                    "money",
                    "Mean closed-trade net P&L; — if no trades.",
                ),
                (
                    "Average winner",
                    "average_winner",
                    "money",
                    "Mean net P&L of winners; — if none.",
                ),
                ("Average loser", "average_loser", "money", "Mean net P&L of losers; — if none."),
                (
                    "Largest winner",
                    "largest_winner",
                    "money",
                    "Largest positive closed-trade net P&L; — if none.",
                ),
                (
                    "Largest loser",
                    "largest_loser",
                    "money",
                    "Most negative closed-trade net P&L; — if none.",
                ),
                (
                    "Average holding hours",
                    "holding_hours",
                    "number",
                    "Elapsed hours from entry to exit for closed positions; — if no closed trades.",
                ),
            ]
        ],
        "Exposure": [
            (
                "Bar-close exposure",
                s["bar_close_exposure"],
                "percent",
                "Share of observed evaluation bar closes with an open position.",
            )
        ],
    }
    # Keep each label/value pair together when Streamlit normally stacks narrow columns.
    st.html("""
        <style>
        .st-key-performance-report [data-testid="stHorizontalBlock"] {flex-wrap:nowrap}
        .st-key-performance-report [data-testid="stColumn"] {min-width:0 !important}
        </style>
    """)
    with st.container(key="performance-report"):
        for group, rows in groups.items():
            st.subheader(group)
            with st.container(border=True):
                for label, value, style, definition in rows:
                    title, result = st.columns([3, 2], vertical_alignment="center")
                    title.markdown(label, help=definition)
                    result.markdown(number(value, style), text_alignment="right")


def filtered_trades(
    trades: list[TradeRecord], direction: str, status: str, outcome: str
) -> list[TradeRecord]:
    return [
        t
        for t in trades
        if (direction == "All" or t.direction == direction)
        and (status == "All" or t.status == status)
        and (
            outcome == "All"
            or t.status == "Closed"
            and (
                outcome == "Winning"
                and t.realized_pnl > 0
                or outcome == "Losing"
                and t.realized_pnl < 0
                or outcome == "Breakeven"
                and t.realized_pnl == 0
            )
        )
    ]


def trades_panel(report: RunReport, folder: Path, timezone: str) -> None:
    run_id = report.run_id
    cols = st.columns(3)
    direction = cols[0].selectbox("Direction", ["All", "Long", "Short"], key=f"direction:{run_id}")
    status = cols[1].selectbox("Position status", ["All", "Closed", "Open"], key=f"status:{run_id}")
    outcome = cols[2].selectbox(
        "Outcome", ["All", "Winning", "Losing", "Breakeven"], key=f"outcome:{run_id}"
    )
    trades = filtered_trades(report.trades, direction, status, outcome)
    st.caption("Filters affect this list only. Select a row to frame the trade on the price chart.")
    if not trades:
        st.info("No trades match these filters.")
        return
    selected = st.session_state.get(f"selected:{run_id}")
    ids = [t.id for t in trades]
    index = ids.index(selected) if selected in ids else -1
    cols = st.columns([1, 1, 3])
    cols[0].button(
        "Previous trade",
        disabled=index <= 0,
        key=f"prev:{run_id}",
        on_click=command,
        args=(run_id, "trade", ids[max(0, index - 1)]),
    )
    cols[1].button(
        "Next trade",
        disabled=index == len(ids) - 1,
        key=f"next:{run_id}",
        on_click=command,
        args=(run_id, "trade", ids[min(index + 1, len(ids) - 1)]),
    )
    if selected and index < 0:
        cols[2].caption("The selected chart trade is outside these filters.")
    elif index >= 0:
        cols[2].caption(f"Selected: trade {trades[index].number} · {trades[index].direction}")
    table = trade_table(trades, timezone)
    table.insert(0, "Selected", [t.id == selected for t in trades])
    # Reset native row selection after chart/arrow navigation so stale highlights cannot
    # disagree with the Selected column, which is the shared source of truth.
    event = st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        selection_mode="single-row",
        on_select="rerun",
        key=f"trades:{run_id}:{direction}:{status}:{outcome}:{selected}",
        column_config={
            name: st.column_config.NumberColumn(format="%.2f")
            for name in [
                "Entry price",
                "Exit price",
                "Realized P&L (USD)",
                "Unrealized P&L (USD)",
                "Commission (USD)",
            ]
        },
    )
    rows = event.selection.rows
    token = (direction, status, outcome, selected, tuple(rows))
    if token != st.session_state.get(f"table_token:{run_id}"):
        st.session_state[f"table_token:{run_id}"] = token
        if rows:
            command(run_id, "trade", ids[rows[0]])
            st.rerun()
    st.download_button(
        "Export displayed trades (CSV)",
        table.to_csv(index=False),
        file_name=f"{run_id}-trades.csv",
        mime="text/csv",
    )
    trade = next((t for t in report.trades if t.id == selected), None)
    if trade:
        with st.container(border=True):
            st.subheader(f"Trade {trade.number} · {trade.direction} · {trade.status}")
            st.write(f"Exit reason: {trade.exit_reason}")
            if report.config.builder:
                st.write(f"Entry: {trade.entry_explanation}")
                st.write(f"Exit: {trade.exit_explanation}")
            st.caption(
                f"Exact entry: {trade.entry_time} · Exact exit: {trade.exit_time or 'Still open'}"
            )
            fills = read_report(folder, "fills")
            st.dataframe(
                fills[fills.order_id.isin(trade.order_ids)], hide_index=True, width="stretch"
            )
            stops = [s for s in report.chart.stops if s["entry_order_id"] == trade.order_ids[0]]
            st.write("Protective stops")
            if stops:
                st.dataframe(pd.DataFrame(stops), hide_index=True, width="stretch")
            else:
                st.caption("No recorded protective-stop interval available.")
            start, end = utc(trade.entry_time), utc(trade.exit_time or report.summary["last_bar"])
            if trade.signal_time:
                start = utc(trade.signal_time)
            events = [
                e.detail
                for e in report.events
                if start - pd.Timedelta(hours=1) <= utc(e.timestamp) <= end
            ]
            with st.expander("Decisions during the trade (including preceding signal hour)"):
                st.dataframe(pd.DataFrame(events), hide_index=True, width="stretch")


def render_results(folder: Path) -> None:
    component = register_chart()
    report = load_run(folder)
    s, run_id = report.summary, report.run_id
    st.subheader(report.config.name)
    cols = st.columns([3, 2])
    cols[0].caption(
        f"{s['symbol']} · 1h · {'HALTED' if s.get('halted') else 'COMPLETED'} · "
        f"{s['dataset_kind'].replace('_', ' ')} · Run {run_id}"
    )
    timezone = cols[1].selectbox(
        "Display timezone", ["America/New_York", "Asia/Singapore", "UTC"], key=f"timezone:{run_id}"
    )
    st.caption(
        f"Evaluation: {display_time(s['first_bar'], timezone)} → "
        f"{display_time(s['last_bar'], timezone)}"
    )
    if report.chart.markers:
        timestamps = sorted(m["timestamp"] for m in report.chart.markers)
        st.caption(
            f"First execution: {display_time(timestamps[0], timezone)} · "
            f"Last execution: {display_time(timestamps[-1], timezone)}"
        )
    halt = next((e for e in report.events if e.reason in {"drawdown_halt", "order_rejected"}), None)
    if halt:
        if halt.reason == "drawdown_halt":
            detail = (
                f"Drawdown {float(halt.detail['drawdown']):.2%} reached "
                f"the {report.config.max_drawdown_fraction:.2%} limit."
            )
        else:
            detail = "An order was rejected or denied."
        st.warning(
            f"Trading halted · {display_time(halt.timestamp, timezone)}. {detail} "
            "New entries are blocked for the rest of this run; liquidation is scheduled "
            "for the next observed open. Later market data remains visible."
        )
        st.button("Go to halt", on_click=command, args=(run_id, "halt"), key=f"halt:{run_id}")
    if not report.trades:
        reasons = pd.Series([e.reason for e in report.events], dtype=str).value_counts()
        skipped = {r: int(n) for r, n in reasons.items() if r.startswith("entry_skipped")}
        explanation = "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in skipped.items())
        st.info("No trades executed. " + (explanation or "No qualifying entries were submitted."))
    for message in report.unavailable:
        st.caption(message)
    st.checkbox(
        "Link time ranges",
        key=f"link:{run_id}",
        help="Synchronize price and performance navigation. Run statistics remain unchanged.",
    )
    payload = chart_payload(report)
    selected = st.session_state.get(f"selected:{run_id}")
    render_chart(payload, "price", timezone, selected, component)
    with st.expander("Strategy Tester", expanded=True):
        overview_tab, performance_tab, trades_tab, settings_tab, diagnostics_tab = st.tabs(
            ["Overview", "Performance", "Trades", "Run settings", "Diagnostics"]
        )
        with overview_tab:
            overview(report, payload, timezone, selected, component)
        with performance_tab:
            performance(report)
        with trades_tab:
            trades_panel(report, folder, timezone)
        with settings_tab:
            st.caption("These are the immutable assumptions saved with this run.")
            if report.config.builder:
                for line in strategy_summary(report.config.builder):
                    st.write(line)
            settings = report.config.model_dump(mode="json")
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Setting": k.replace("_", " ").title(), "Value": str(v)}
                        for k, v in settings.items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
            for label in ["execution", "margin", "end_policy"]:
                st.write(
                    f"**{label.replace('_', ' ').title()}:** "
                    f"{report.manifest.get(label, 'Unavailable')}"
                )
            with st.expander("Dataset coverage and provenance"):
                st.json(report.manifest["dataset"])
            if st.button("Use settings for a new run", key=f"reuse:{run_id}"):
                st.session_state["draft_config"] = settings
                st.session_state["draft_run"] = run_id
                st.rerun()
            st.caption(
                "Loads Configure & run without submitting a backtest or changing this result."
            )
            st.download_button(
                "Download configuration",
                (folder / "config.yaml").read_bytes(),
                file_name=f"{run_id}.yaml",
                mime="text/yaml",
            )
        with diagnostics_tab:
            reasons = pd.Series([e.reason for e in report.events], dtype=str).value_counts()
            st.dataframe(reasons.rename_axis("Decision").reset_index(name="Count"), hide_index=True)
            for name in [
                "positions",
                "fills",
                "orders",
                "decisions",
                "observations",
                "protective_orders",
            ]:
                with st.expander(f"Raw {name.replace('_', ' ')}"):
                    st.dataframe(read_report(folder, name), hide_index=True, width="stretch")
            if report.config.builder:
                traces = [e for e in report.events if e.reason == "criteria_evaluation"]
                if traces:
                    with st.expander("Condition evaluations (values and pass/fail)"):
                        picked = st.selectbox(
                            "Completed-bar decision",
                            traces,
                            format_func=lambda e: display_time(e.timestamp, timezone),
                            key=f"trace:{run_id}",
                        )
                        st.json(json.loads(picked.detail["conditions_json"]))
            with st.expander("Summary and reproduction metadata"):
                st.json(s)
                st.json(report.manifest)
    st.caption(
        "Research simulation. Hourly OHLC cannot establish intrabar chronology. "
        "Small samples do not establish profitability. "
        "Rolling proxies have unverified roll economics."
    )

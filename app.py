"""Local dashboard entry point: python -m streamlit run app.py."""

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from mnq_research.backtest import run_backtest
from mnq_research.config import StrategyConfig, load_config, save_config
from mnq_research.data import load_dataset
from mnq_research.results_ui import render_results
from mnq_research.risk import maximum_stop_by_risk

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="MNQ Research", page_icon="📈", layout="wide")
st.title("MNQ Strategy Research")
st.caption("Local historical research · NautilusTrader · No live trading")
build_tab, results_tab, compare_tab = st.tabs(
    ["Configure & run", "Inspect results", "Compare runs"]
)


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


with build_tab:
    datasets = sorted((ROOT / "data").glob("*/metadata.json"))
    if not datasets:
        st.info("Import actual market history first. See README for Yahoo and CSV commands.")
    selected = st.selectbox(
        "Historical dataset", datasets, format_func=lambda path: path.parent.name
    )
    if selected:
        try:
            bars, metadata = load_dataset(selected.parent)
            if metadata["kind"] == "rolling_proxy":
                st.warning(
                    "Rolling MNQ proxy: exploratory results; contract identity is unverified."
                )
            st.write(f"{len(bars):,} hourly bars · {bars.index[0]} → {bars.index[-1]}")
            with st.expander("Data provenance and gaps"):
                st.json(metadata)
        except (ValueError, OSError, KeyError) as error:
            st.error(str(error))
            selected = None
    configs = sorted((ROOT / "configs").glob("*.yaml"))
    config_path = st.selectbox(
        "Saved configuration",
        configs,
        format_func=lambda path: path.name,
        on_change=lambda: st.session_state.pop("draft_config", None),
    )
    base = load_config(config_path) if config_path else None
    if st.session_state.get("draft_config"):
        base = StrategyConfig.model_validate(st.session_state["draft_config"])
        st.info(f"Loaded settings from run {st.session_state['draft_run']}. Review and run below.")
    if base:
        if base.sizing_rule == "percent_risk":
            budget = base.initial_equity_usd * base.risk_fraction
            ceiling = maximum_stop_by_risk(base.initial_equity_usd, base)
            st.info(
                f"Saved configuration: ${budget:,.2f} risk budget per trade. "
                f"One contract permits at most a {ceiling:g}-point stop after estimated costs. "
                "Wider stops produce skipped entries. Margin is checked separately. "
                "This is an affordability limit, not a recommended stop."
            )
        else:
            st.info("Fixed-contract sizing does not enforce the percentage-risk budget.")
        with st.form("strategy:" + base.model_dump_json()):
            name = st.text_input("Strategy name", base.name)
            first, second, third = st.columns(3)
            with first:
                st.subheader("Entry & exit")
                fast = st.number_input("Fast SMA bars", 2, 1000, base.fast_period)
                slow = st.number_input("Slow SMA bars", 3, 2000, base.slow_period)
                atr = st.number_input("ATR bars", 2, 1000, base.atr_period)
                multiple = st.number_input("ATR stop multiple", 0.1, 20.0, base.atr_multiple)
                directions = ["both", "long", "short"]
                direction = st.selectbox(
                    "Direction", directions, index=directions.index(base.direction)
                )
            with second:
                st.subheader("Sizing & risk")
                capital = st.number_input(
                    "Initial equity (USD)", min_value=1.0, value=base.initial_equity_usd
                )
                sizing_options = ["percent_risk", "fixed_contracts"]
                sizing = st.selectbox(
                    "Sizing rule", sizing_options, index=sizing_options.index(base.sizing_rule)
                )
                risk = st.number_input("Risk per trade (%)", 0.01, 100.0, base.risk_fraction * 100)
                maximum = st.number_input("Maximum contracts", 1, 100, base.max_contracts)
                fixed = st.number_input("Fixed contracts", 1, 100, base.fixed_contracts)
                halt = st.number_input(
                    "Drawdown halt (%)", 0.1, 99.0, base.max_drawdown_fraction * 100
                )
            with third:
                st.subheader("Execution assumptions")
                fee = st.number_input(
                    "Commission per contract per side (USD)",
                    min_value=0.0,
                    value=base.commission_per_side_usd,
                )
                slip = st.number_input(
                    "Adverse slippage (0 or 1 tick per fill)", 0, 1, base.slippage_ticks
                )
                margin = st.number_input(
                    "Margin reserved per contract (USD)",
                    min_value=1.0,
                    value=base.margin_per_contract_usd,
                )
                proxy = st.checkbox(
                    "Allow exploratory rolling-contract data", base.allow_rolling_proxy
                )
                start = st.text_input(
                    "Evaluation start (ISO date with timezone; blank = all)",
                    base.start.isoformat() if base.start else "",
                )
                end = st.text_input(
                    "Evaluation end (ISO date with timezone; blank = all)",
                    base.end.isoformat() if base.end else "",
                )
            st.caption(
                "Exits: ATR stop or opposite crossover. Signals at close; orders at next open. "
                "Overnight/weekend holds allowed. Costs shown here are research assumptions."
            )
            save_name = st.text_input("Save configuration filename", "my_strategy")
            save_clicked = st.form_submit_button("Save configuration")
            run_clicked = st.form_submit_button("Run backtest", type="primary")
        if save_clicked or run_clicked:
            try:
                config = StrategyConfig.model_validate(
                    {
                        **base.model_dump(),
                        "name": name,
                        "fast_period": fast,
                        "slow_period": slow,
                        "atr_period": atr,
                        "atr_multiple": multiple,
                        "direction": direction,
                        "initial_equity_usd": capital,
                        "sizing_rule": sizing,
                        "risk_fraction": risk / 100,
                        "max_contracts": maximum,
                        "fixed_contracts": fixed,
                        "max_drawdown_fraction": halt / 100,
                        "commission_per_side_usd": fee,
                        "slippage_ticks": slip,
                        "margin_per_contract_usd": margin,
                        "allow_rolling_proxy": proxy,
                        "start": start or None,
                        "end": end or None,
                    }
                )
                if save_clicked:
                    if not save_name or any(
                        char
                        not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                        for char in save_name
                    ):
                        raise ValueError(
                            "Filename may contain letters, numbers, underscores and hyphens"
                        )
                    target = ROOT / "configs" / f"{save_name}.yaml"
                    if target.exists():
                        raise ValueError(
                            "Choose a new filename to preserve the existing configuration"
                        )
                    save_config(config, target)
                    st.success(f"Saved {target.name}. Reload the page to select it.")
                if run_clicked:
                    if not selected:
                        raise ValueError("Select a valid dataset first")
                    with st.spinner("Running NautilusTrader…"):
                        run_folder = run_backtest(config, selected.parent, ROOT / "runs")
                    st.success(f"Saved run {run_folder.name}. Open Inspect results.")
            except (ValueError, OSError, KeyError) as error:
                st.error(str(error))

runs = sorted((ROOT / "runs").glob("*/summary.json"), reverse=True)
with results_tab:
    chosen = st.selectbox("Saved run", runs, format_func=lambda path: path.parent.name)
    if chosen:
        render_results(chosen.parent)
with compare_tab:
    selections = st.multiselect("Runs to compare", runs, format_func=lambda path: path.parent.name)
    if selections:
        comparison = [
            {"run": path.parent.name, **json.loads(path.read_text(encoding="utf-8"))}
            for path in selections
        ]
        st.dataframe(pd.DataFrame(comparison).set_index("run").T.astype(str), width="stretch")
        st.caption(
            "Check periods, datasets and assumptions before interpreting differences. "
            "Reserve unseen history before tuning; no automatic strategy ranking."
        )

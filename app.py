"""Local dashboard entry point: python -m streamlit run app.py."""

import json
from hashlib import sha256
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from mnq_research.amp_mt5 import BAR, expected_closes, regular_coverage_counts, utc
from mnq_research.backtest import run_backtest
from mnq_research.builder_ui import render_builder
from mnq_research.cloud import run_cloud_backtest
from mnq_research.cloud_ui import (
    deployment_mode,
    download_run,
    session_workspace,
    upload_panel,
)
from mnq_research.config import StrategyConfig, load_config, save_config
from mnq_research.data import interval_duration, load_dataset, refresh_yahoo_archive
from mnq_research.optimization_ui import render_optimizer
from mnq_research.research_ui import render_research
from mnq_research.results_ui import render_results
from mnq_research.risk import maximum_stop_by_risk
from mnq_research.roll_backtest import load_continuous, roll_grid
from mnq_research.run_labels import format_run_label

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="MNQ Research", page_icon="📈", layout="wide")
try:
    CLOUD = deployment_mode() == "cloud"
except ValueError as error:
    st.error(str(error))
    st.stop()
workspace = session_workspace() if CLOUD else None
WORK = workspace.root if workspace else ROOT
st.title("MNQ Strategy Research")
st.caption(
    f"{'Shared' if CLOUD else 'Local'} historical research · NautilusTrader · No live trading"
)
if CLOUD:
    st.info(
        "Your uploads, configurations and runs are private to this browser session and temporary. "
        "Download anything you want to keep. Cloud limit: five retained runs per session."
    )
    if st.button("Clear my session"):
        workspace.close()
        st.session_state.clear()
        st.rerun()
build_tab, results_tab, compare_tab, optimize_tab, research_tab = st.tabs(
    [
        "Configure & run", "Inspect results", "Compare runs", "Optimize Parameters",
        "Research studies",
    ]
)


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


with build_tab:
    if workspace:
        upload_panel(workspace)
    else:
        with st.expander("Yahoo rolling MNQ 5-minute archive"):
            st.caption(
                "Fetch Yahoo's latest 60-day window and append it to a local archive. "
                "Previously downloaded bars and snapshots are retained. Rolling data is "
                "exploratory and is not redistributed by the public app."
            )
            if st.button("Refresh Yahoo MNQ 5-minute history"):
                try:
                    with st.spinner("Fetching and merging Yahoo's 5-minute history…"):
                        archive, refresh = refresh_yahoo_archive(ROOT / "data" / "yahoo-mnq-5m")
                    st.success(
                        f"Added {refresh['added_bars']:,} bars; "
                        f"{refresh['revised_overlap_bars']:,} overlapping bars changed; "
                        f"{refresh['total_bars']:,} total bars across "
                        f"{refresh['snapshot_count']} preserved downloads."
                    )
                    st.caption(
                        f"Archive: {archive} · {refresh['first_close_utc']} → "
                        f"{refresh['last_close_utc']}"
                    )
                except (ValueError, OSError, KeyError) as error:
                    st.error(f"Yahoo refresh failed; existing archive was retained: {error}")
    datasets = sorted((WORK / "data").glob("*/metadata.json"))
    if not CLOUD:
        datasets += sorted((WORK / "data" / "continuous-research").glob("*/manifest.json"))
    if not datasets:
        st.info(
            "Upload real market data above."
            if CLOUD
            else "Import actual market history first. See README for Yahoo and CSV commands."
        )
    # Snapshot labels: a new upload may remove a previously selected dataset before rerun.
    dataset_labels = {}
    amp_range = None
    for path in datasets:
        if CLOUD:
            info = json.loads(path.read_text(encoding="utf-8"))
            dataset_labels[path] = f"{info['symbol']} · {info['source']}"
        else:
            dataset_labels[path] = path.parent.name
            if path.name == "manifest.json":
                version = path.parent.name.rsplit("-", 1)[-1]
                dataset_labels[path] = f"MNQ continuous · 5m · {version}"
            elif path.parent.name.startswith("amp-"):
                info = json.loads(path.read_text(encoding="utf-8"))
                dataset_labels[path] = f"{info['symbol']} · AMP MT5 · 5m · {info['quality']}"
    selected = st.selectbox(
        "Historical dataset",
        datasets,
        format_func=lambda path: dataset_labels.get(path, path.parent.name),
    )
    if selected:
        try:
            if selected.name == "manifest.json":
                bars, metadata = load_continuous(selected.parent)
            else:
                bars, metadata = load_dataset(selected.parent)
            if metadata["kind"] == "rolling_proxy":
                st.warning(
                    "Rolling MNQ proxy: exploratory results; contract identity is unverified."
                )
            bar_interval = metadata.get("bar_interval", "1h")
            st.write(
                f"{len(bars):,} {bar_interval} bars · {bars.index[0]} → {bars.index[-1]}"
            )
            interval_minutes = int(interval_duration(bar_interval).total_seconds() // 60)
            st.caption(
                f"Indicator and setup periods use bars: each bar is {interval_minutes} minutes."
            )
            if metadata.get("provider") == "amp_mt5":
                coverage = json.loads((selected.parent / "coverage.json").read_text("utf-8"))
                if "coverage_start_utc" not in coverage:
                    cutoff = utc(coverage["effective_end_utc"])
                    if cutoff == utc(metadata["expiry_utc"]):
                        cutoff -= BAR
                    coverage.update(
                        regular_coverage_counts(
                            bars, utc(coverage["requested_start_utc"]), cutoff
                        )
                    )
                ranges = sorted(
                    (pair for pair in coverage["eligible_ranges"] if pair[0] != pair[1]),
                    key=lambda pair: utc(pair[1]) - utc(pair[0]),
                    reverse=True,
                )
                st.caption(
                    f"AMP coverage: {coverage['status']} · "
                    f"{coverage['missing_regular_bars']:,} missing regular-session bars "
                    "from the first observed bar to the audit cutoff. Select a verified interval."
                )
                amp_range = st.selectbox(
                    "Verified AMP interval (UTC)",
                    ranges,
                    format_func=lambda pair: f"{pair[0]} to {pair[1]}",
                    key=f"amp-window:{selected.parent.name}",
                )
            if metadata.get("provider") == "continuous_mnq":
                windows = sorted(
                    (pair for pair in metadata["verified_ranges"] if pair[0] != pair[1]),
                    key=lambda pair: utc(pair[1]) - utc(pair[0]),
                    reverse=True,
                )
                st.caption("Positions close at the open of the last outgoing trading session.")
                amp_range = st.selectbox(
                    "Verified continuous interval (UTC)",
                    windows,
                    format_func=lambda pair: f"{pair[0]} to {pair[1]}",
                    key=f"continuous-window:{selected.parent.name}",
                )
            with st.expander("Data provenance and gaps"):
                if metadata.get("provider") == "amp_mt5":
                    st.write(
                        f"**Audit request (UTC)** · {coverage['requested_start_utc']} → "
                        f"{coverage['requested_end_utc']}"
                    )
                    st.write(
                        f"**Observed data (UTC)** · {coverage['first_observed']} → "
                        f"{coverage['last_observed']}"
                    )
                    st.caption(
                        "Months are grouped by New York time. Expected bars start at the "
                        "first observed close and extend through the effective audit cutoff."
                    )
                    st.write("Holiday and other special sessions are outside verified intervals.")
                    st.dataframe(pd.DataFrame.from_dict(coverage["monthly"], orient="index"))
                st.json({k: v for k, v in metadata.items() if k != "eligible_ranges"})
        except (ValueError, OSError, KeyError) as error:
            st.error(str(error))
            selected = None
    configs = sorted((ROOT / "configs").glob("*.yaml"))
    if CLOUD:
        configs += sorted((WORK / "configs").glob("*.yaml"))
    config_path = st.selectbox(
        "Saved configuration",
        configs,
        key="saved_configuration",
        format_func=lambda path: (
            (
                "Session: "
                if CLOUD and path.parent == WORK / "configs"
                else "Preset: "
                if CLOUD
                else ""
            )
            + path.name
        ),
        on_change=lambda: st.session_state.pop("draft_config", None),
    )
    base = load_config(config_path) if config_path else None
    if st.session_state.get("draft_config"):
        base = StrategyConfig.model_validate(st.session_state["draft_config"])
        st.info(f"Loaded settings from run {st.session_state['draft_run']}. Review and run below.")
    if base:
        editor_key = sha256(base.model_dump_json().encode()).hexdigest()[:12]
        strategy_type = st.selectbox(
            "Strategy construction",
            ["SMA crossover baseline", "Condition builder"],
            index=1 if base.builder else 0,
            key=f"construction:{editor_key}",
            help="The condition builder supports filters, sequential setups, entry triggers "
            "and independent exits. Changing the display never runs a backtest.",
        )
        builder, builder_direction = (
            render_builder(base, f"builder:{editor_key}")
            if strategy_type == "Condition builder"
            else (None, base.direction)
        )
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
                st.subheader(
                    "Protective stop" if strategy_type == "Condition builder" else "Entry & exit"
                )
                fast, slow = base.fast_period, base.slow_period
                if strategy_type == "SMA crossover baseline":
                    fast = st.number_input("Fast SMA bars", 2, 1000, base.fast_period)
                    slow = st.number_input("Slow SMA bars", 3, 2000, base.slow_period)
                atr = st.number_input("ATR bars", 2, 1000, base.atr_period)
                multiple = st.number_input("ATR stop multiple", 0.1, 20.0, base.atr_multiple)
                trailing = False
                activation_r = base.trailing_activation_r
                distance_r = base.trailing_distance_r
                if strategy_type == "SMA crossover baseline":
                    trailing = st.checkbox(
                        "Enable R-based trailing stop", base.trailing_stop_enabled
                    )
                    activation_r = st.number_input(
                        "Activate after profit reaches (R)", value=activation_r, step=0.25
                    )
                    distance_r = st.number_input(
                        "Trail distance (R)", value=distance_r, step=0.25
                    )
                direction = builder_direction
                if strategy_type == "SMA crossover baseline":
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
                halt_enabled = st.checkbox(
                    "Enable account drawdown halt", base.drawdown_halt_enabled
                )
                halt = st.number_input(
                    "Drawdown threshold (%)", 0.1, 99.0, base.max_drawdown_fraction * 100,
                    help="Used for diagnostics when the account drawdown halt is disabled.",
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
                if amp_range and selected:
                    calendar = roll_grid if selected.name == "manifest.json" else expected_closes
                    expected = calendar(utc(amp_range[0]) - BAR, utc(amp_range[1])).index
                    warmup_index = min(base.warmup_bars(), len(expected) - 1)
                    start_default = expected[warmup_index].isoformat()
                    end_default = expected[-1].isoformat()
                    date_key = f"{selected.parent.name}:{amp_range[0]}"
                else:
                    start_default = base.start.isoformat() if base.start else ""
                    end_default = base.end.isoformat() if base.end else ""
                    date_key = "standard"
                start = st.text_input(
                    "Evaluation start (ISO date with timezone)",
                    start_default,
                    key=f"evaluation-start:{editor_key}:{date_key}",
                    help="AMP and continuous datasets require an explicit evaluation window.",
                )
                end = st.text_input(
                    "Evaluation end (ISO date with timezone)",
                    end_default,
                    key=f"evaluation-end:{editor_key}:{date_key}",
                    help="AMP and continuous datasets require an explicit evaluation window.",
                )
            st.caption(
                (
                    "Exits: fixed ATR protection plus configured signal/holding rules. "
                    if strategy_type == "Condition builder"
                    else "Exits: ATR stop or opposite crossover. "
                )
                + "Signals at close; orders at next open. "
                "Overnight/weekend holds allowed. Costs shown here are research assumptions."
            )
            save_name = st.text_input("Save configuration filename", "my_strategy")
            save_clicked = st.form_submit_button("Save configuration")
            run_clicked = st.form_submit_button("Run backtest", type="primary")
        if save_clicked or run_clicked:
            try:
                if strategy_type == "Condition builder" and builder is None:
                    raise ValueError("Correct the criteria before saving or running")
                config = StrategyConfig.model_validate(
                    {
                        **base.model_dump(),
                        "schema_version": 2 if builder else 1,
                        "entry_rule": "criteria" if builder else "sma_cross",
                        "exit_rule": "criteria" if builder else "atr_stop_and_opposite",
                        "builder": builder,
                        "name": name,
                        "fast_period": fast,
                        "slow_period": slow,
                        "atr_period": atr,
                        "atr_multiple": multiple,
                        "trailing_stop_enabled": trailing,
                        "trailing_activation_r": activation_r,
                        "trailing_distance_r": distance_r,
                        "direction": direction,
                        "initial_equity_usd": capital,
                        "sizing_rule": sizing,
                        "risk_fraction": risk / 100,
                        "max_contracts": maximum,
                        "fixed_contracts": fixed,
                        "max_drawdown_fraction": halt / 100,
                        "drawdown_halt_enabled": halt_enabled,
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
                    target = WORK / "configs" / f"{save_name}.yaml"
                    if target.exists():
                        raise ValueError(
                            "Choose a new filename to preserve the existing configuration"
                        )
                    save_config(config, target)
                    st.success(
                        f"Saved {target.name}. "
                        "It appears in the configuration list on the next interaction."
                    )
                st.session_state["reviewed_config"] = config.model_dump(mode="json")
                if run_clicked:
                    if not selected:
                        raise ValueError("Select a valid dataset first")
                    with st.spinner("Running NautilusTrader…"):
                        run_folder = (
                            run_cloud_backtest(config, selected.parent, workspace)
                            if workspace
                            else run_backtest(config, selected.parent, ROOT / "runs")
                        )
                    st.success(f"Saved run {run_folder.name}. Open Inspect results.")
            except (ValueError, OSError, KeyError) as error:
                st.error(str(error))
            except Exception:
                if not CLOUD:
                    raise
                st.error(
                    "The backtest failed. Earlier runs are retained; review the data and retry."
                )
        if CLOUD:
            reviewed = st.session_state.get("reviewed_config", base.model_dump(mode="json"))
            st.download_button(
                "Download strategy YAML",
                yaml.safe_dump(reviewed, sort_keys=False),
                file_name="strategy.yaml",
                mime="application/yaml",
                help="Saved or submitted settings; unsent form edits are not included.",
            )

runs = sorted((WORK / "runs").glob("*/summary.json"), reverse=True)
with results_tab:
    chosen = st.selectbox("Saved run", runs, format_func=format_run_label)
    if chosen:
        if CLOUD:
            download_run(chosen.parent)
        render_results(chosen.parent)
with compare_tab:
    selections = st.multiselect("Runs to compare", runs, format_func=format_run_label)
    if selections:
        comparison = [
            {"run": format_run_label(path), **json.loads(path.read_text(encoding="utf-8"))}
            for path in selections
        ]
        st.dataframe(pd.DataFrame(comparison).set_index("run").T.astype(str), width="stretch")
        st.caption(
            "Check periods, datasets and assumptions before interpreting differences. "
            "Reserve unseen history before tuning; no automatic strategy ranking."
        )

with optimize_tab:
    render_optimizer(ROOT, cloud=CLOUD)

with research_tab:
    render_research(ROOT, cloud=CLOUD)

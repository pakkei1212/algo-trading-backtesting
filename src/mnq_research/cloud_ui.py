"""Cloud-specific controls; research calculations stay in the existing engine."""

import os
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from mnq_research.cloud import MAX_BARS, MAX_DATASETS, CloudWorkspace, import_upload, run_archive


def deployment_mode() -> str:
    mode = os.environ.get("MNQ_DEPLOYMENT_MODE")
    if mode is None:
        try:
            mode = st.secrets.get("deployment_mode", "local")
        except (StreamlitSecretNotFoundError, FileNotFoundError):
            mode = "local"
    if mode not in {"local", "cloud"}:
        raise ValueError("deployment_mode must be local or cloud")
    return mode


def session_workspace() -> CloudWorkspace:
    if "cloud_workspace" not in st.session_state:
        st.session_state["cloud_workspace"] = CloudWorkspace()
    return st.session_state["cloud_workspace"]


def upload_panel(workspace: CloudWorkspace) -> None:
    with st.expander(
        "Upload market data", expanded=not any((workspace.root / "data").iterdir())
    ):
        st.write("CSV columns: timestamp, open, high, low, close, volume.")
        st.caption(
            f"Use completed bars labeled at their close, with timezone offsets. "
            f"Supported intervals: 5m, 15m, 30m and 1h; {MAX_BARS:,} bars maximum. "
            f"Up to {MAX_DATASETS} datasets are retained per session. "
            "Exchange closures remain gaps."
        )
        if len(list((workspace.root / "data").glob("*/metadata.json"))) >= MAX_DATASETS:
            st.info("This session has reached its dataset limit. Clear the session to start fresh.")
        kind_label = st.selectbox("Contract type", ["Dated MNQ contract", "Rolling MNQ proxy"])
        kind = "dated_contract" if kind_label == "Dated MNQ contract" else "rolling_proxy"
        with st.form("upload-market-data"):
            uploaded = st.file_uploader("OHLCV CSV", type=["csv"], max_upload_size=10)
            bar_interval = st.selectbox(
                "Bar interval", ["5m", "15m", "30m", "1h"], index=3
            )
            symbol = st.text_input("Symbol", "MNQZ6" if kind == "dated_contract" else "MNQ=F")
            source = st.text_input("Data source", placeholder="Provider or export source")
            expiry = st.text_input(
                "Contract expiration (ISO timestamp with offset)",
                placeholder="2026-12-18T14:30:00Z",
                disabled=kind != "dated_contract",
            )
            proxy = st.checkbox(
                "I acknowledge rolling-contract data is exploratory",
                disabled=kind != "rolling_proxy",
            )
            submitted = st.form_submit_button("Import dataset")
        if submitted:
            try:
                if uploaded is None:
                    raise ValueError("Select a CSV first")
                import_upload(
                    uploaded.getvalue(),
                    workspace,
                    symbol,
                    source,
                    kind,
                    expiry,
                    proxy,
                    bar_interval,
                )
                st.success("Dataset imported into your session.")
            except (ValueError, OSError, KeyError) as error:
                st.error(str(error))


def download_run(folder: Path) -> None:
    st.download_button(
        "Download complete run (ZIP)",
        run_archive(folder),
        file_name=f"{folder.name}.zip",
        mime="application/zip",
    )

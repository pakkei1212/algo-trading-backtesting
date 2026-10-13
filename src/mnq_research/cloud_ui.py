"""Cloud-specific controls; research calculations stay in the existing engine."""

import os
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from mnq_research.cloud import CloudWorkspace, import_upload, run_archive


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
        "Upload hourly market data", expanded=not any((workspace.root / "data").iterdir())
    ):
        st.write("CSV columns: timestamp, open, high, low, close, volume.")
        st.caption(
            "Use completed hourly bars labeled at their close, with timezone offsets. "
            "10 MB / 10,000 bars maximum. Exchange closures remain gaps. "
            "Uploading replaces this session's previous dataset; saved runs retain their bars."
        )
        kind_label = st.selectbox("Contract type", ["Dated MNQ contract", "Rolling MNQ proxy"])
        kind = "dated_contract" if kind_label == "Dated MNQ contract" else "rolling_proxy"
        with st.form("upload-market-data"):
            uploaded = st.file_uploader("Hourly OHLCV CSV", type=["csv"], max_upload_size=10)
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
                import_upload(uploaded.getvalue(), workspace, symbol, source, kind, expiry, proxy)
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

"""Local bundled chart component and its versioned Python boundary."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import streamlit as st

ASSETS = Path(__file__).parent / "chart_assets"
ChartRenderer = Callable[..., Any]


def register_chart() -> ChartRenderer:
    """Register once per script run, also supporting isolated AppTest runtimes."""
    return st.components.v2.component(
        "mnq_chart",
        js=(ASSETS / "chart.js").read_text(encoding="utf-8"),
        css=(ASSETS / "chart.css").read_text(encoding="utf-8"),
    )


def render_chart(
    payload: dict, mode: str, timezone: str, selected: str | None, component: ChartRenderer
) -> None:
    run_id = payload["run_id"]
    key = f"chart:{run_id}:{mode}"
    shared = f"range:{run_id}"

    def on_viewport() -> None:
        if st.session_state.get(f"link:{run_id}"):
            st.session_state[shared] = st.session_state[key].get("viewport")

    def on_selection() -> None:
        selected_id = st.session_state[key].get("selection")
        if selected_id in {t["id"] for t in payload["trades"]}:
            st.session_state[f"selected:{run_id}"] = selected_id

    component(
        key=key,
        data={
            **payload,
            "mode": mode,
            "timezone": timezone,
            "selected": selected,
            "command": st.session_state.get(f"command:{run_id}") if mode == "price" else None,
            "linked_range": st.session_state.get(shared)
            if st.session_state.get(f"link:{run_id}")
            else None,
        },
        default={"viewport": None, "selection": None},
        on_viewport_change=on_viewport,
        on_selection_change=on_selection,
    )

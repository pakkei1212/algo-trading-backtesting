"""Small native Streamlit editor for completed-bar condition groups."""

import streamlit as st
from pydantic import ValidationError

from mnq_research.config import StrategyConfig
from mnq_research.criteria import (
    BuilderConfig,
    Condition,
    ConditionGroup,
    DirectionCriteria,
    Operand,
    strategy_summary,
    trend_pullback,
)

KINDS = ["close", "open", "high", "low", "sma", "ema", "atr", "number"]
OPERATORS = ["above", "below", "crosses_above", "crosses_below"]
GROUP_HELP = {
    "market_filter": "Permits new entries in suitable conditions. Failure expires a setup; "
    "it does not close an existing position.",
    "setup": "Starts an opportunity window. A trigger must occur on a later completed bar.",
    "trigger": "Selects an entry while a setup is active and the market filter passes.",
    "signal_exit": "Closes an existing position independently of entry filters and setups. "
    "ALL requires every condition; ANY requires at least one.",
}


def operand_editor(label: str, initial: Operand, key: str) -> dict:
    kind = st.selectbox(label, KINDS, index=KINDS.index(initial.kind), key=key + ":kind")
    result: dict = {"kind": kind}
    if kind in {"sma", "ema"}:
        result["period"] = st.number_input(
            f"{label} period (bars)", 2, 2000, initial.period or 20, key=key + ":period"
        )
    if kind == "number":
        result["value"] = st.number_input(
            f"{label} value", value=initial.value or 0.0, key=key + ":value"
        )
    else:
        result["offset"] = st.number_input(
            f"{label} bars earlier",
            0,
            100,
            initial.offset,
            key=key + ":offset",
            help="Zero means the just-completed bar. Offsets count observed bars, including "
            "across overnight and weekend gaps. ATR uses the protective-stop ATR period.",
        )
    return result


def group_editor(label: str, initial: ConditionGroup, key: str, help_text: str) -> dict:
    with st.expander(label):
        st.caption(help_text)
        first, second = st.columns(2)
        mode = first.selectbox(
            "Combine conditions",
            ["all", "any"],
            index=["all", "any"].index(initial.mode),
            format_func=str.upper,
            key=key + ":mode",
            help="ALL: every condition must pass. "
            "ANY: at least one must pass. All required indicator values must be available.",
        )
        count = second.number_input(
            "Number of conditions", 1, 8, len(initial.conditions), key=key + ":count"
        )
        rows = []
        for index in range(count):
            condition = (
                initial.conditions[index]
                if index < len(initial.conditions)
                else Condition(
                    id=f"condition_{key.split(':')[-2]}_{key.split(':')[-1]}_{index}",
                    left=Operand(kind="close"),
                    operator="above",
                    right=Operand(kind="number", value=0),
                )
            )
            row_key = f"{key}:row:{index}"
            with st.container(border=True):
                st.caption(f"Condition {index + 1} · {condition.id}")
                left, operator, right = st.columns([2, 1, 2])
                with left:
                    left_value = operand_editor("Left operand", condition.left, row_key + ":left")
                with operator:
                    comparison = st.selectbox(
                        "Comparison",
                        OPERATORS,
                        index=OPERATORS.index(condition.operator),
                        format_func=lambda value: value.replace("_", " "),
                        key=row_key + ":operator",
                        help="Crosses above: previously at/below, now strictly above. "
                        "Crosses below is the reverse. Above/below are strict comparisons.",
                    )
                with right:
                    right_value = operand_editor(
                        "Right operand", condition.right, row_key + ":right"
                    )
                rows.append(
                    dict(id=condition.id, left=left_value, operator=comparison, right=right_value)
                )
        return dict(mode=mode, conditions=rows)


def inactive_short() -> DirectionCriteria:
    """Safe starting fields for explicitly authored shorts, never mirrored longs."""
    return DirectionCriteria(
        **{
            name: ConditionGroup(
                conditions=[
                    Condition(
                        id=f"short_{name}",
                        left=Operand(kind="close"),
                        operator="above",
                        right=Operand(kind="close"),
                    )
                ]
            )
            for name in GROUP_HELP
        }
    )


def render_builder(base: StrategyConfig, key: str) -> tuple[BuilderConfig | None, str]:
    initial = base.builder or trend_pullback()
    st.subheader("Entry & exit criteria")
    st.caption(
        "Hourly completed bars · Editable trend-pullback template · "
        "Research defaults, not optimized settings"
    )
    direction = st.selectbox(
        "Builder direction",
        ["long", "short", "both"],
        index=["long", "short", "both"].index(base.direction if base.builder else "long"),
        key=key + ":direction",
    )
    sides = {}
    for side in ("long", "short"):
        if direction not in (side, "both"):
            continue
        criteria = getattr(initial, side)
        if criteria is None:
            criteria = inactive_short()
            st.info(
                "Short criteria must be authored separately. The initial Close above Close "
                "conditions never pass. Edit the short filter, setup, trigger and exit."
            )
        st.markdown(f"**{side.title()} criteria**")
        sides[side] = {
            name: group_editor(
                name.replace("_", " ").title(),
                getattr(criteria, name),
                f"{key}:{side}:{name}",
                explanation,
            )
            for name, explanation in GROUP_HELP.items()
        }
    first, second = st.columns(2)
    window = first.number_input(
        "Setup window (observed bars)",
        1,
        1000,
        initial.setup_window_bars,
        key=key + ":window",
        help="A setup activated at bar N can trigger at N+1 through N+window, inclusive. "
        "A failing market filter expires it immediately. Repeated setup signals do not "
        "extend an active window.",
    )
    enabled = second.checkbox(
        "Use maximum holding-bar exit",
        value=initial.max_holding_bars is not None,
        key=key + ":holding-enabled",
    )
    holding = second.number_input(
        "Maximum holding bars",
        1,
        10000,
        initial.max_holding_bars or 48,
        disabled=not enabled,
        key=key + ":holding",
        help="The first completed bar after the entry fill counts as one. "
        "Exit is submitted at the next observed open after the limit is reached.",
    )
    try:
        builder = BuilderConfig.model_validate(
            dict(**sides, setup_window_bars=window, max_holding_bars=holding if enabled else None)
        )
    except ValidationError as error:
        st.error(str(error))
        return None, direction
    with st.container(border=True):
        st.markdown("**Strategy summary**")
        for line in strategy_summary(builder):
            st.write(line)
    return builder, direction

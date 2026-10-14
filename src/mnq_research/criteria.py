"""Validated, declarative criteria. No user expressions or executable code."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Operand(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["open", "high", "low", "close", "sma", "ema", "atr", "number"]
    period: int | None = Field(default=None, ge=2, le=2000)
    offset: int = Field(default=0, ge=0, le=100)
    value: float | None = None

    @model_validator(mode="after")
    def validate_operand(self) -> Self:
        if (self.kind in {"sma", "ema"}) != (self.period is not None):
            raise ValueError("Only SMA and EMA operands require a period")
        if (self.kind == "number") != (self.value is not None):
            raise ValueError("Only a numeric operand requires a value")
        if self.kind == "number" and self.offset:
            raise ValueError("Numbers cannot have a previous-bar offset")
        return self

    @property
    def key(self) -> str:
        return f"{self.kind}_{self.period}" if self.period else self.kind


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
    left: Operand
    operator: Literal["above", "below", "crosses_above", "crosses_below"]
    right: Operand


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1, max_length=8)


class DirectionCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_filter: ConditionGroup
    setup: ConditionGroup
    trigger: ConditionGroup
    signal_exit: ConditionGroup


class BuilderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    long: DirectionCriteria | None = None
    short: DirectionCriteria | None = None
    setup_window_bars: int = Field(default=5, ge=1, le=1000)
    max_holding_bars: int | None = Field(default=48, ge=1, le=10000)

    @model_validator(mode="after")
    def validate_conditions(self) -> Self:
        if self.long is None and self.short is None:
            raise ValueError("Define at least one trading direction")
        identifiers = [c.id for c in self.conditions()]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Condition identifiers must be unique across the strategy")
        return self

    def conditions(self) -> list[Condition]:
        return [
            condition
            for criteria in (self.long, self.short)
            if criteria is not None
            for group in (
                criteria.market_filter,
                criteria.setup,
                criteria.trigger,
                criteria.signal_exit,
            )
            for condition in group.conditions
        ]


def trend_pullback() -> BuilderConfig:
    """Illustrative long template; short criteria must be explicitly authored."""
    close = Operand(kind="close")
    slow = Operand(kind="ema", period=200)
    fast = Operand(kind="ema", period=20)
    return BuilderConfig(
        long=DirectionCriteria(
            market_filter=ConditionGroup(
                conditions=[
                    Condition(id="long_trend_price", left=close, operator="above", right=slow),
                    Condition(
                        id="long_trend_rising",
                        left=slow,
                        operator="above",
                        right=Operand(kind="ema", period=200, offset=5),
                    ),
                ]
            ),
            setup=ConditionGroup(
                conditions=[
                    Condition(id="long_pullback", left=close, operator="crosses_below", right=fast)
                ]
            ),
            trigger=ConditionGroup(
                conditions=[
                    Condition(id="long_recovery", left=close, operator="crosses_above", right=fast)
                ]
            ),
            signal_exit=ConditionGroup(
                conditions=[
                    Condition(id="long_trend_failure", left=close, operator="below", right=slow)
                ]
            ),
        ),
    )


def operand_text(operand: Operand) -> str:
    label = (
        f"{operand.value:g}"
        if operand.kind == "number" and operand.value is not None
        else f"{operand.kind.upper()}({operand.period})"
        if operand.period
        else operand.kind.title()
    )
    return label + (f" {operand.offset} observed bars earlier" if operand.offset else "")


def group_text(group: ConditionGroup) -> str:
    joiner = " AND " if group.mode == "all" else " OR "
    return joiner.join(
        f"{operand_text(c.left)} {c.operator.replace('_', ' ')} {operand_text(c.right)}"
        for c in group.conditions
    )


def strategy_summary(builder: BuilderConfig) -> list[str]:
    lines = []
    for label, criteria in (("Long", builder.long), ("Short", builder.short)):
        if criteria is None:
            continue
        lines.extend(
            [
                f"{label} market filter: {group_text(criteria.market_filter)}.",
                f"{label} setup: {group_text(criteria.setup)}.",
                f"{label} entry: after a setup, within the next {builder.setup_window_bars} "
                f"observed bars, require {group_text(criteria.trigger)} and the market filter.",
                f"{label} signal exit: {group_text(criteria.signal_exit)}.",
            ]
        )
    lines.append(
        f"Exit after {builder.max_holding_bars} observed bar closes with the position open."
        if builder.max_holding_bars
        else "No maximum holding-bar exit."
    )
    lines.append(
        "Signals use completed bars; entries and signal exits execute at the next "
        "observed open. Protective ATR stops remain active between bars."
    )
    return lines

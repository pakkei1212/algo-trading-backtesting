"""Versioned, explicit research assumptions shared by the UI and CLI."""

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mnq_research.criteria import BuilderConfig


class StrategyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1, 2] = 1
    name: str = "MNQ hourly SMA baseline"
    entry_rule: Literal["sma_cross", "criteria"] = "sma_cross"
    exit_rule: Literal["atr_stop_and_opposite", "criteria"] = "atr_stop_and_opposite"
    builder: BuilderConfig | None = None
    sizing_rule: Literal["percent_risk", "fixed_contracts"] = "percent_risk"
    fast_period: int = Field(default=20, ge=2, le=1000)
    slow_period: int = Field(default=50, ge=3, le=2000)
    atr_period: int = Field(default=14, ge=2, le=1000)
    atr_multiple: float = Field(default=1.0, gt=0)
    direction: Literal["both", "long", "short"] = "both"
    initial_equity_usd: float = Field(default=10000, gt=0)
    risk_fraction: float = Field(default=0.02, gt=0, le=1)
    max_contracts: int = Field(default=2, ge=1, le=100)
    fixed_contracts: int = Field(default=1, ge=1, le=100)
    max_drawdown_fraction: float = Field(default=0.1, gt=0, lt=1)
    # Required: there is no implicit free execution or assumed exchange margin.
    commission_per_side_usd: float = Field(ge=0)
    # The pinned native OHLC fill model supports zero or one adverse tick.
    slippage_ticks: int = Field(ge=0, le=1)
    margin_per_contract_usd: float = Field(gt=0)
    allow_rolling_proxy: bool = False
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def validate_periods(self) -> Self:
        if self.entry_rule == "criteria":
            if self.schema_version != 2 or self.exit_rule != "criteria" or self.builder is None:
                raise ValueError(
                    "Criteria strategies require schema version 2 and builder settings"
                )
            expected = {"long", "short"} if self.direction == "both" else {self.direction}
            actual = {side for side in ("long", "short") if getattr(self.builder, side) is not None}
            if actual != expected:
                raise ValueError("Define separate criteria for every enabled direction")
        elif self.builder is not None or self.exit_rule != "atr_stop_and_opposite":
            raise ValueError("SMA strategies cannot include builder criteria")
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        for value in (self.start, self.end):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Dates must include a timezone, for example 2026-01-01T00:00:00Z")
        if self.start and self.end and self.start >= self.end:
            raise ValueError("start must precede end")
        if self.fixed_contracts > self.max_contracts:
            raise ValueError("fixed_contracts cannot exceed max_contracts")
        return self

    def warmup_bars(self) -> int:
        if self.builder is None:
            return max(self.slow_period, self.atr_period) + 1
        requirements = [self.atr_period]
        for condition in self.builder.conditions():
            for operand in (condition.left, condition.right):
                requirements.append((operand.period or 1) + operand.offset + 1)
        return max(requirements)


def load_config(path: Path) -> StrategyConfig:
    return StrategyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_config(config: StrategyConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )

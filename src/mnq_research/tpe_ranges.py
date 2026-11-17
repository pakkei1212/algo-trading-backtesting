"""Validated numeric TPE ranges and lazy, stable candidate IDs."""

from bisect import bisect_right
from decimal import Decimal
from math import isfinite
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mnq_research.config import StrategyConfig


class TpeRanges(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    fast_min: int = Field(default=5, ge=2, le=1000)
    fast_max: int = Field(default=25, ge=2, le=1000)
    slow_min: int = Field(default=30, ge=3, le=2000)
    slow_max: int = Field(default=150, ge=3, le=2000)
    atr_min: Decimal = Field(default=Decimal("1.0"), gt=0)
    atr_max: Decimal = Field(default=Decimal("2.5"), gt=0)
    atr_step: Decimal = Field(default=Decimal("0.1"), gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if self.fast_min > self.fast_max or self.slow_min > self.slow_max:
            raise ValueError("SMA minimums must be less than or equal to their maximums.")
        if self.fast_min >= self.slow_max:
            raise ValueError("No valid combinations. Fast SMA must be shorter than Slow SMA.")
        if self.atr_min > self.atr_max or (self.atr_max - self.atr_min) % self.atr_step:
            raise ValueError("ATR maximum must be at or above the minimum and align with the step.")
        if self.atr_count > 10_000:
            raise ValueError("Use at most 10,000 stepped ATR values.")
        bounds = (float(self.atr_min), float(self.atr_max), float(self.atr_step))
        if not all(isfinite(value) and value > 0 for value in bounds):
            raise ValueError("ATR bounds and step must be positive finite floats.")
        if tuple(Decimal(str(value)) for value in bounds) != (
            self.atr_min,
            self.atr_max,
            self.atr_step,
        ):
            raise ValueError("ATR bounds or step exceed supported decimal precision.")
        if self.atr_count > 1 and float(self.atr_max - self.atr_step) >= bounds[1]:
            raise ValueError("ATR step is too small for these bounds.")
        return self

    @property
    def atr_count(self) -> int:
        return int((self.atr_max - self.atr_min) // self.atr_step) + 1

    def axis_values(self) -> tuple[range, range, list[float]]:
        return (
            range(self.fast_min, self.fast_max + 1),
            range(self.slow_min, self.slow_max + 1),
            [float(self.atr_min + self.atr_step * i) for i in range(self.atr_count)],
        )


class RangeCandidates:
    """Index valid combinations without creating a configuration per combination."""

    def __init__(self, base: StrategyConfig, ranges: TpeRanges) -> None:
        self.base = base
        self.ranges = ranges
        self.offsets = [0]
        for fast in range(ranges.fast_min, min(ranges.fast_max, ranges.slow_max - 1) + 1):
            slow_start = max(ranges.slow_min, fast + 1)
            self.offsets.append(self.offsets[-1] + ranges.slow_max - slow_start + 1)

    def __len__(self) -> int:
        return self.offsets[-1] * self.ranges.atr_count

    def parameters(self, index: int) -> tuple[int, int, float]:
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        pair, atr_tick = divmod(index, self.ranges.atr_count)
        row = bisect_right(self.offsets, pair) - 1
        fast = self.ranges.fast_min + row
        slow = max(self.ranges.slow_min, fast + 1) + pair - self.offsets[row]
        atr = float(self.ranges.atr_min + atr_tick * self.ranges.atr_step)
        return fast, slow, atr

    def index_for(self, fast: int, slow: int, atr: float) -> int | None:
        row = fast - self.ranges.fast_min
        slow_start = max(self.ranges.slow_min, fast + 1)
        if not (0 <= row < len(self.offsets) - 1 and slow_start <= slow <= self.ranges.slow_max):
            return None
        # Optuna float steps can contain binary representation noise.
        tick = round((Decimal(str(atr)) - self.ranges.atr_min) / self.ranges.atr_step)
        if not 0 <= tick < self.ranges.atr_count:
            return None
        return (self.offsets[row] + slow - slow_start) * self.ranges.atr_count + tick

    def __getitem__(self, index: int) -> StrategyConfig:
        fast, slow, atr = self.parameters(index)
        return StrategyConfig.model_validate(
            {
                **self.base.model_dump(),
                "fast_period": fast,
                "slow_period": slow,
                "atr_multiple": atr,
            }
        )

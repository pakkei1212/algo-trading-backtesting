"""Causal same-contract cash-session inputs for the walk-forward study."""

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict


class MomentumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    history_sessions: Literal[10, 20, 30] = 20
    band_multiplier: Literal[1.0, 1.5] = 1.0
    stop_atr: Literal[1.0, 1.5, 2.0] = 1.5
    confirm_vwap: bool = True


class ReversionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    band_atr: Literal[1.5, 2.0] = 1.5
    efficiency_max: Literal[0.20, 0.30] = 0.30
    stop_atr: Literal[1.0, 1.5, 2.0] = 1.5


class LateMomentumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_daily_atr: Literal[0.0, 0.25, 0.50] = 0.25
    stop_atr: Literal[1.0, 1.5, 2.0] = 1.5


@dataclass(frozen=True)
class PriorCash:
    slots: dict[int, dict[date, tuple[float, ...]]]
    daily: dict[date, tuple[float, float]]


def prior_cash_inputs(bars: pd.DataFrame, excluded: frozenset[date]) -> PriorCash:
    local = bars.tz_convert("America/New_York").between_time("09:35", "16:00")
    # A partial day cannot enter a later session's statistical sample.
    eligible = (
        (day, frame)
        for day, frame in local.groupby(local.index.date, sort=True)
        if day.weekday() < 5 and day not in excluded
        and len(frame) == 78 and frame.volume.gt(0).all()
    )
    daily = {}
    slots: dict[int, dict[date, tuple[float, ...]]] = {10: {}, 20: {}, 30: {}}
    history: list[pd.Series] = []
    previous_close = None
    seed: list[float] = []
    daily_atr = None
    for day, frame in eligible:
        for count, records in slots.items():
            if len(history) >= count:
                records[day] = tuple(pd.DataFrame(history[-count:]).mean())
        if previous_close is not None:
            if daily_atr is not None and daily_atr > 0:
                daily[day] = (previous_close, daily_atr)
            true_range = max(
                frame.high.max() - frame.low.min(),
                abs(frame.high.max() - previous_close),
                abs(frame.low.min() - previous_close),
            )
            if daily_atr is not None:
                daily_atr += (true_range - daily_atr) / 14
            else:
                seed.append(true_range)
                if len(seed) == 14:
                    daily_atr = sum(seed) / 14
        previous_close = float(frame.close.iloc[-1])
        history.append((frame.close.reset_index(drop=True) / float(frame.open.iloc[0]) - 1).abs())
    return PriorCash(slots, daily)

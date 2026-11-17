"""Cash-session policy for the first MNQ research fold."""

from collections import deque
from dataclasses import dataclass
from datetime import date, time
from math import ceil, floor
from typing import Literal

import pandas as pd
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from pydantic import BaseModel, ConfigDict

from mnq_research.cash_indicators import (
    LateMomentumConfig,
    MomentumConfig,
    PriorCash,
    ReversionConfig,
)
from mnq_research.config import StrategyConfig
from mnq_research.risk import TICK_SIZE
from mnq_research.strategy import BaselineStrategy, RollRiskState

CASH_ZONE = "America/New_York"
CASH_FIRST_CLOSE = time(9, 35)
CASH_LAST_CLOSE = time(16)
LIQUIDATION_OPEN = time(15, 55)


class OrbConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    opening_range_minutes: Literal[5, 15, 30] = 15
    stop_atr: Literal[1.0, 1.5, 2.0] = 1.5


CashConfig = OrbConfig | MomentumConfig | ReversionConfig | LateMomentumConfig


@dataclass
class OpeningRange:
    minutes: int
    high: float = float("-inf")
    low: float = float("inf")
    previous_close: float | None = None

    def update(self, elapsed_minutes: int, high: float, low: float, close: float) -> int:
        previous = self.previous_close
        self.previous_close = close
        if elapsed_minutes <= self.minutes:
            self.high = max(high, self.high)
            self.low = min(low, self.low)
            return 0
        if previous is None:
            return 0
        if previous <= self.high < close:
            return 1
        if previous >= self.low > close:
            return -1
        return 0


class CashSessionStrategy(BaselineStrategy):
    def __init__(
        self,
        settings: StrategyConfig,
        bar_type: BarType,
        *,
        roll_risk: RollRiskState,
        flatten_open_ns: int | None,
        sessions: frozenset[date],
        orb: OrbConfig | None,
        candidate: CashConfig | None = None,
        prior_cash: PriorCash | None = None,
        experimental_trail: bool = False,
        regime_rows: dict | None = None,
    ) -> None:
        super().__init__(settings, bar_type, roll_risk=roll_risk, flatten_open_ns=flatten_open_ns)
        self.sessions = sessions
        self.orb = orb
        self.candidate = candidate or orb
        self.prior_cash = prior_cash
        self.cash_open = 0.0
        self.cash_value = 0.0
        self.cash_volume = 0.0
        self.cash_closes: deque[float] = deque(maxlen=13)
        self.previous_breakout = 0
        self.excursion: tuple[int, int] | None = None
        self.session: date | None = None
        self.opening_range = None
        self.entries_in_session = 0
        self.last_exit_ns: int | None = None
        self.expected_open_ns: int | None = None
        self.experimental_trail = experimental_trail
        self.regime_rows = regime_rows or {}
        self.signal_context: dict = {}
        self.signal_atr = 0.0
        self.entry_atr = 0.0
        self.favorable_extreme: float | None = None
        self.trail_activated = False

    def record(self, reason: str, **details: object) -> None:
        if reason == "entry_signal":
            self.signal_atr = float(self.atr or 0)
            timestamp = self.clock.utc_now().isoformat()
            self.signal_context = {
                "cash_signal_id": f"{self.instrument_id}:{timestamp}",
                "signal_timestamp": timestamp,
                **self.regime_rows.get(timestamp, {}),
            }
            details.update(self.signal_context)
        super().record(reason, **details)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.pending and self.expected_open_ns is not None:
            if tick.ts_event != self.expected_open_ns:
                raise ValueError("Missing scheduled cash execution open")
            self.expected_open_ns = None
        super().on_quote_tick(tick)

    def on_order_filled(self, event: object) -> None:
        opening = event.client_order_id == self.entry_order_id
        if opening:
            self.entries_in_session += 1
            self.entry_atr = self.signal_atr
            self.favorable_extreme = float(event.last_px)
            self.trail_activated = False
        else:
            self.last_exit_ns = event.ts_event
            if self.pending and self.pending[0] == "exit":
                self.pending = None
                self.expected_open_ns = None
        super().on_order_filled(event)
        self.fill_log[-1].update(self.signal_context)

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        net = float(self.portfolio.net_position(self.instrument_id))
        if self.experimental_trail and net:
            self.update_extreme_trail(bar, 1 if net > 0 else -1)
        local = pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(CASH_ZONE)
        if (
            local.date() in self.sessions
            and local.time() == CASH_LAST_CLOSE
            and not self.portfolio.is_flat(self.instrument_id)
        ):
            raise ValueError("Cash-session liquidation failed")

    def update_extreme_trail(self, bar: Bar, side: int) -> None:
        stop = self.active_stop
        if stop is None or not stop.is_open or self.entry_atr <= 0:
            return
        extreme = float(bar.high) if side > 0 else float(bar.low)
        if self.favorable_extreme is None or side * (extreme - self.favorable_extreme) > 0:
            self.favorable_extreme = extreme
        close = float(bar.close)
        if self.pending or self.halted or self.flattened_for_roll:
            return
        if not self.trail_activated:
            if side * (close - self.entry_fill_points) < self.initial_r_points:
                return
            self.trail_activated = True
            self.record("extreme_trail_activated", entry_atr=self.entry_atr)
        raw = (self.favorable_extreme - side * 2 * self.entry_atr) / TICK_SIZE
        trigger = (floor(raw) if side > 0 else ceil(raw)) * TICK_SIZE
        if trigger <= 0 or side * (trigger - float(stop.trigger_price)) <= 0:
            return
        if side * (close - trigger) <= 0:
            self.exit_reason = "extreme_trail_crossed_close"
            self.pending = ("exit", 0, 0)
            self.expected_open_ns = bar.ts_event + 1
            self.record("extreme_trail_crossed_close")
        else:
            self.modify_order(stop, trigger_price=self.instrument.make_price(trigger))

    def on_sma_decision(self, bar: Bar, direction: int) -> None:
        local = pd.Timestamp(bar.ts_event, tz="UTC").tz_convert(CASH_ZONE)
        clock = local.time()
        if local.date() not in self.sessions or not CASH_FIRST_CLOSE <= clock <= CASH_LAST_CLOSE:
            return
        if local.date() != self.session:
            self.session = local.date()
            self.entries_in_session = 0
            self.last_exit_ns = None
            self.cash_open = float(bar.open)
            self.cash_value = self.cash_volume = 0.0
            self.cash_closes.clear()
            self.previous_breakout = 0
            self.excursion = None
            if self.orb:
                self.opening_range = OpeningRange(self.orb.opening_range_minutes)
        net = float(self.portfolio.net_position(self.instrument_id))
        if clock == LIQUIDATION_OPEN and net:
            self.pending = ("exit", 0, 0)
            self.exit_reason = "cash_session_exit"
            self.record("cash_session_exit")
        elif clock >= LIQUIDATION_OPEN:
            return
        elif self.candidate is None:
            super().on_sma_decision(bar, direction)
        elif self.orb:
            self.on_orb_decision(bar, clock, net)
        else:
            self.on_family_decision(bar, clock, net)
        if self.pending:
            self.expected_open_ns = bar.ts_event + 1

    def on_orb_decision(self, bar: Bar, clock: time, net: float) -> None:
        opening_range = self.opening_range
        assert opening_range is not None and self.atr is not None and self.orb is not None
        elapsed = clock.hour * 60 + clock.minute - 570
        close = float(bar.close)
        direction = opening_range.update(elapsed, float(bar.high), float(bar.low), close)
        if net:
            returned_inside = (
                (net > 0 and close <= opening_range.high)
                or (net < 0 and close >= opening_range.low)
            )
            if returned_inside:
                self.pending = ("exit", 0, 0)
                self.exit_reason = "opening_range_reentry"
                self.record("opening_range_reentry")
            return
        if (
            not direction
            or clock > time(11, 30)
            or self.entries_in_session >= 2
            or self.atr <= 0
            or (
                self.last_exit_ns is not None
                and bar.ts_event < self.last_exit_ns + pd.Timedelta(minutes=10).value - 1
            )
        ):
            return
        distance = ceil(self.atr * self.orb.stop_atr / TICK_SIZE) * TICK_SIZE
        self.pending = ("entry", direction, distance)
        self.record(
            "entry_signal",
            direction=direction,
            stop_distance=distance,
            range_high=opening_range.high,
            range_low=opening_range.low,
        )

    def on_family_decision(self, bar: Bar, clock: time, net: float) -> None:
        candidate = self.candidate
        assert candidate is not None and self.atr is not None and self.session is not None
        close = float(bar.close)
        volume = float(bar.volume)
        self.cash_volume += volume
        self.cash_value += (float(bar.high) + float(bar.low) + close) * volume / 3
        self.cash_closes.append(close)
        if self.cash_volume <= 0 or self.atr <= 0:
            return
        vwap = self.cash_value / self.cash_volume
        side = 1 if net > 0 else -1 if net < 0 else 0
        slot = (clock.hour * 60 + clock.minute - 575) // 5
        entry = 0
        exit_reason = None
        if isinstance(candidate, MomentumConfig):
            assert self.prior_cash is not None
            scales = self.prior_cash.slots[candidate.history_sessions].get(self.session)
            if scales is None or scales[slot] <= 0:
                return
            distance = self.cash_open * candidate.band_multiplier * scales[slot]
            upper, lower = self.cash_open + distance, self.cash_open - distance
            if side and (
                side * (close - (upper if side > 0 else lower)) <= 0
                or (candidate.confirm_vwap and side * (close - vwap) <= 0)
            ):
                exit_reason = "momentum_band_or_vwap_exit"
            if time(10) <= clock <= time(15) and clock.minute % 30 == 0:
                breakout = 1 if close > upper else -1 if close < lower else 0
                if candidate.confirm_vwap and breakout * (close - vwap) <= 0:
                    breakout = 0
                if breakout and breakout != self.previous_breakout:
                    entry = breakout
                self.previous_breakout = breakout
        elif isinstance(candidate, ReversionConfig):
            width = candidate.band_atr * self.atr
            deviation = close - vwap
            if side and side * deviation >= 0:
                exit_reason = "vwap_recovery"
            elif side and self.completed_bars - self.entry_bar_number >= 12:
                exit_reason = "vwap_time_exit"
            if self.excursion is not None:
                armed_side, armed_slot = self.excursion
                if slot - armed_slot > 6 or armed_side * deviation >= 0:
                    self.excursion = None
                elif abs(deviation) <= width:
                    self.excursion = None
                    closes = list(self.cash_closes)
                    path = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
                    if (
                        len(closes) == 13 and path > 0 and clock <= time(14, 30)
                        and abs(close - closes[0]) / path <= candidate.efficiency_max
                    ):
                        entry = armed_side
            if not side and self.excursion is None and abs(deviation) > width:
                self.excursion = (1 if deviation < 0 else -1, slot)
            if side:
                self.excursion = None
        elif isinstance(candidate, LateMomentumConfig) and clock == time(15, 30):
            assert self.prior_cash is not None
            prior = self.prior_cash.daily.get(self.session)
            if prior is not None:
                move = (close - prior[0]) / prior[1]
                threshold = candidate.signal_daily_atr
                entry = 1 if move > threshold else -1 if move < -threshold else 0
        if side and exit_reason:
            self.pending = ("exit", 0, 0)
            self.exit_reason = exit_reason
            self.record(exit_reason)
        limit = 1 if isinstance(candidate, LateMomentumConfig) else 2
        if side or not entry or self.entries_in_session >= limit:
            return
        if (
            self.last_exit_ns is not None
            and bar.ts_event < self.last_exit_ns + pd.Timedelta(minutes=10).value - 1
        ):
            return
        distance = ceil(self.atr * candidate.stop_atr / TICK_SIZE) * TICK_SIZE
        self.pending = ("entry", entry, distance)
        self.record("entry_signal", direction=entry, stop_distance=distance, vwap=vwap)

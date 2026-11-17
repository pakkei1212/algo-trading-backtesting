"""SMA and builder decisions executed by NautilusTrader at explicit next opens."""

import json
from collections import deque
from dataclasses import dataclass
from math import ceil, floor

import pandas as pd
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.config import StrategyConfig as NativeStrategyConfig
from nautilus_trader.trading.strategy import Strategy

from mnq_research.config import StrategyConfig
from mnq_research.risk import TICK_SIZE, contract_quantity
from mnq_research.rule_engine import RuleEngine
from mnq_research.rules import ENTRY_RULES


@dataclass
class RollRiskState:
    high_water: float
    halted: bool = False


class BaselineStrategy(Strategy):
    def __init__(
        self,
        settings: StrategyConfig,
        bar_type: BarType,
        *,
        roll_risk: RollRiskState | None = None,
        flatten_open_ns: int | None = None,
    ) -> None:
        if roll_risk is None:
            super().__init__()
        else:
            super().__init__(NativeStrategyConfig(order_id_tag=str(bar_type.instrument_id.symbol)))
        self.roll_risk = roll_risk
        self.flatten_open_ns = flatten_open_ns
        self.flattened_for_roll = False
        self.settings = settings
        self.bar_type = bar_type
        self.instrument_id = bar_type.instrument_id
        self.closes: deque[float] = deque(maxlen=settings.slow_period)
        self.true_ranges: deque[float] = deque(maxlen=settings.atr_period)
        self.previous_close: float | None = None
        self.previous_averages: tuple[float, float] | None = None
        self.atr: float | None = None
        self.pending: tuple[str, int, float] | None = None
        self.entry_order_id = None
        self.stop_distance = 0.0
        self.active_stop = None
        self.entry_fill_points = 0.0
        self.initial_r_points = 0.0
        self.best_trailing_close: float | None = None
        self.halted = False
        self.above_drawdown_threshold = False
        self.high_water = settings.initial_equity_usd
        self.decisions: list[dict] = []
        self.equity: list[dict] = []
        self.fill_log: list[dict] = []
        self.observations: list[dict] = []
        self.protective_orders: list[dict] = []
        self.stop_orders: dict[str, dict] = {}
        self.exit_reason = "opposite_signal"
        self.rule_engine = (
            RuleEngine(settings.builder, settings.atr_period) if settings.builder else None
        )
        self.completed_bars = 0
        self.entry_bar_number = 0
        self.signal_id: str | None = None
        self.exit_reasons: list[str] = []

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)
        self.subscribe_quote_ticks(self.instrument_id)

    def current_equity(self) -> float:
        account = self.portfolio.account(self.instrument_id.venue)
        unrealized = self.portfolio.unrealized_pnl(self.instrument_id)
        return float(account.balance_total(USD)) + (float(unrealized) if unrealized else 0.0)

    def record(self, reason: str, **details: object) -> None:
        self.decisions.append(
            {"timestamp": self.clock.utc_now().isoformat(), "reason": reason, **details}
        )

    def on_quote_tick(self, tick: QuoteTick) -> None:
        """The runner supplies only actual observed bar opens as quote events."""
        if self.flatten_open_ns is not None and tick.ts_event >= self.flatten_open_ns:
            self.pending = None
            if not self.flattened_for_roll:
                self.flattened_for_roll = True
                self.exit_reason = "contract_roll"
                self.exit_reasons = ["contract_roll"]
                self.record("contract_roll", contract=str(self.instrument_id.symbol))
                self.cancel_all_orders(self.instrument_id)
                if not self.portfolio.is_flat(self.instrument_id):
                    self.close_all_positions(self.instrument_id)
            return
        if self.pending is None:
            return
        action, direction, distance = self.pending
        self.pending = None
        if action == "exit":
            if self.rule_engine and self.portfolio.is_flat(self.instrument_id):
                self.record("redundant_exit_discarded")
                return
            self.cancel_all_orders(self.instrument_id)
            self.close_all_positions(self.instrument_id)
            return
        if self.halted or not self.portfolio.is_flat(self.instrument_id):
            return
        if direction == 1 and float(tick.ask_price) - distance <= 0:
            self.record("entry_skipped_invalid_stop", stop_distance=distance)
            return
        quantity = contract_quantity(self.current_equity(), distance, self.settings)
        if quantity == 0:
            self.record("entry_skipped_risk_or_margin", stop_distance=distance)
            return
        self.stop_distance = distance
        order = self.order_factory.market(
            self.instrument_id,
            OrderSide.BUY if direction == 1 else OrderSide.SELL,
            Quantity.from_int(quantity),
        )
        self.entry_order_id = order.client_order_id
        details = (
            {"signal_id": self.signal_id, "order_id": str(order.client_order_id)}
            if self.rule_engine
            else {}
        )
        self.record("entry_submitted", direction=direction, quantity=quantity, **details)
        self.submit_order(order)

    def on_order_filled(self, event: object) -> None:
        opening = event.client_order_id == self.entry_order_id
        protective = str(event.client_order_id) in self.stop_orders
        if self.rule_engine and protective and self.pending and self.pending[0] == "exit":
            self.pending = None
            self.record("redundant_exit_discarded", cause="protective_stop_filled")
        self.fill_log.append(
            {
                "timestamp": pd.Timestamp(event.ts_event, tz="UTC").isoformat(),
                "side": "BUY" if event.order_side == OrderSide.BUY else "SELL",
                "quantity": float(event.last_qty),
                "price": float(event.last_px),
                "commission_usd": float(event.commission),
                "order_id": str(event.client_order_id),
                "exit_reason": (
                    "entry"
                    if event.client_order_id == self.entry_order_id
                    else self.stop_orders[str(event.client_order_id)].get(
                        "exit_reason", "protective_stop"
                    )
                    if protective
                    else self.exit_reason
                ),
                **(
                    {
                        "signal_id": self.signal_id,
                        "matched_exit_reasons": "protective_stop"
                        if protective
                        else ""
                        if opening
                        else ";".join(self.exit_reasons),
                    }
                    if self.rule_engine
                    else {}
                ),
            }
        )
        self.record_stop_event(event, "filled")
        if event.client_order_id != self.entry_order_id:
            return
        opening_order_id = str(self.entry_order_id)
        self.entry_order_id = None
        self.entry_bar_number = self.completed_bars
        direction = 1 if event.order_side == OrderSide.BUY else -1
        stop_price = float(event.last_px) - direction * self.stop_distance
        stop = self.order_factory.stop_market(
            self.instrument_id,
            OrderSide.SELL if direction == 1 else OrderSide.BUY,
            event.last_qty,
            trigger_price=self.instrument.make_price(stop_price),
            time_in_force=TimeInForce.GTC,
            reduce_only=True,
        )
        self.stop_orders[str(stop.client_order_id)] = {
            "price": float(stop.trigger_price),
            "entry_order_id": opening_order_id,
        }
        self.active_stop = stop
        self.entry_fill_points = float(event.last_px)
        self.initial_r_points = abs(self.entry_fill_points - float(stop.trigger_price))
        self.best_trailing_close = None
        self.record_stop_event(stop, "submitted", timestamp=event.ts_event)
        self.submit_order(stop)

    def record_stop_event(self, event: object, state: str, timestamp: int | None = None) -> None:
        order_id = str(event.client_order_id)
        if order_id in self.stop_orders:
            self.protective_orders.append(
                {
                    "timestamp": pd.Timestamp(
                        timestamp if timestamp is not None else event.ts_event, tz="UTC"
                    ).isoformat(),
                    "order_id": order_id,
                    "event": state,
                    **self.stop_orders[order_id],
                }
            )

    def on_order_accepted(self, event: object) -> None:
        self.record_stop_event(event, "accepted")

    def on_order_updated(self, event: object) -> None:
        metadata = self.stop_orders.get(str(event.client_order_id))
        if metadata is not None and event.trigger_price is not None:
            metadata["price"] = float(event.trigger_price)
            metadata["exit_reason"] = "trailing_stop"
            self.record_stop_event(event, "updated")

    def on_order_modify_rejected(self, event: object) -> None:
        self.record_stop_event(event, "modify_rejected")

    def tighten_trailing_stop(self, close: float, direction: int) -> None:
        """Ratchet the native stop after the close has been processed by the exchange."""
        stop = self.active_stop
        if stop is None or not stop.is_open or self.initial_r_points <= 0:
            return
        reward_points = direction * (close - self.entry_fill_points)
        if self.best_trailing_close is None:
            if reward_points < self.settings.trailing_activation_r * self.initial_r_points:
                return
            metadata = self.stop_orders[str(stop.client_order_id)]
            metadata.update(
                initial_r_points=self.initial_r_points,
                activation_r=self.settings.trailing_activation_r,
                distance_r=self.settings.trailing_distance_r,
            )
            self.record_stop_event(stop, "trail_activated", timestamp=self.clock.timestamp_ns())
            self.best_trailing_close = close
        elif direction * (close - self.best_trailing_close) > 0:
            self.best_trailing_close = close

        distance = self.settings.trailing_distance_r * self.initial_r_points
        raw_ticks = (self.best_trailing_close - direction * distance) / TICK_SIZE
        ticks = floor(raw_ticks) if direction == 1 else ceil(raw_ticks)
        trigger = ticks * TICK_SIZE
        if (
            trigger > 0
            and direction * (close - trigger) > 0
            and direction * (trigger - float(stop.trigger_price)) > 0
        ):
            self.modify_order(stop, trigger_price=self.instrument.make_price(trigger))

    def on_order_canceled(self, event: object) -> None:
        self.record_stop_event(event, "canceled")

    def on_order_expired(self, event: object) -> None:
        self.record_stop_event(event, "expired")

    def on_order_rejected(self, event: object) -> None:
        self.record_stop_event(event, "rejected")
        self.exit_reason = "order_rejected"
        self.exit_reasons = ["order_rejected"]
        self.record("order_rejected", detail=str(event))
        # A failed protective order must never silently leave the strategy trading.
        self.halted = True
        if self.roll_risk is not None:
            self.roll_risk.halted = True
        self.pending = ("exit", 0, 0)

    def on_order_denied(self, event: object) -> None:
        self.on_order_rejected(event)

    def on_bar(self, bar: Bar) -> None:
        self.completed_bars += 1
        timestamp = pd.Timestamp(bar.ts_event, tz="UTC")
        warming = self.roll_risk is not None and timestamp < self.settings.start
        just_halted = False
        if not warming:
            if self.roll_risk is not None:
                self.high_water = self.roll_risk.high_water
                self.halted = self.roll_risk.halted
            equity = self.current_equity()
            self.high_water = max(self.high_water, equity)
            drawdown = 1 - equity / self.high_water
            self.equity.append(
                {
                    "timestamp": pd.Timestamp(bar.ts_event, tz="UTC").isoformat(),
                    "equity_usd": equity,
                    "drawdown": drawdown,
                    "in_position": not self.portfolio.is_flat(self.instrument_id),
                }
            )
            breached = drawdown >= self.settings.max_drawdown_fraction
            if (
                breached and not self.above_drawdown_threshold
                and not self.settings.drawdown_halt_enabled
            ):
                self.record("drawdown_threshold_breach", drawdown=drawdown)
            self.above_drawdown_threshold = breached
            just_halted = breached and self.settings.drawdown_halt_enabled and not self.halted
            if just_halted:
                self.halted = True
                self.pending = ("exit", 0, 0)
                self.exit_reason = "drawdown_halt"
                self.record("drawdown_halt", drawdown=drawdown)

            if self.roll_risk is not None:
                self.roll_risk.high_water = self.high_water
                self.roll_risk.halted = self.halted

        if self.rule_engine:
            self.on_criteria_bar(bar, just_halted)
            return

        close, high, low = float(bar.close), float(bar.high), float(bar.low)
        true_range = high - low
        if self.previous_close is not None:
            true_range = max(
                true_range, abs(high - self.previous_close), abs(low - self.previous_close)
            )
        self.previous_close = close
        self.closes.append(close)
        self.true_ranges.append(true_range)
        if self.atr is None and len(self.true_ranges) == self.settings.atr_period:
            self.atr = sum(self.true_ranges) / self.settings.atr_period
        elif self.atr is not None:
            self.atr += (true_range - self.atr) / self.settings.atr_period
        if len(self.closes) < self.settings.slow_period or self.atr is None:
            return
        fast = sum(list(self.closes)[-self.settings.fast_period :]) / self.settings.fast_period
        slow = sum(self.closes) / self.settings.slow_period
        self.observations.append(
            {
                "timestamp": pd.Timestamp(bar.ts_event, tz="UTC").isoformat(),
                "fast": fast,
                "slow": slow,
                "atr": self.atr,
            }
        )
        previous = self.previous_averages
        self.previous_averages = (fast, slow)
        if previous is None or self.halted or self.flattened_for_roll:
            return
        timestamp = pd.Timestamp(bar.ts_event, tz="UTC")
        if self.settings.start and timestamp < self.settings.start:
            return
        direction = ENTRY_RULES[self.settings.entry_rule](*previous, fast, slow)
        self.on_sma_decision(bar, direction)

    def on_sma_decision(self, bar: Bar, direction: int) -> None:
        close = float(bar.close)
        if not direction and not self.settings.trailing_stop_enabled:
            return
        net = float(self.portfolio.net_position(self.instrument_id))
        if not direction and not net:
            return
        if net * direction < 0:
            self.pending = ("exit", 0, 0)
            self.exit_reason = "opposite_signal"
            self.record("opposite_signal_exit")
        elif net and self.settings.trailing_stop_enabled:
            self.tighten_trailing_stop(close, 1 if net > 0 else -1)
        elif net == 0:
            assert self.atr is not None
            if (direction == 1 and self.settings.direction == "short") or (
                direction == -1 and self.settings.direction == "long"
            ):
                self.record("direction_filtered", direction=direction)
                return
            distance = ceil(self.atr * self.settings.atr_multiple / TICK_SIZE) * TICK_SIZE
            self.pending = ("entry", direction, distance)
            self.record("entry_signal", direction=direction, stop_distance=distance)

    def on_criteria_bar(self, bar: Bar, just_halted: bool) -> None:
        engine = self.rule_engine
        assert engine is not None
        values = engine.update(
            {name: float(getattr(bar, name)) for name in ("open", "high", "low", "close")}
        )
        self.observations.append({"timestamp": self.clock.utc_now().isoformat(), **values})
        timestamp = pd.Timestamp(bar.ts_event, tz="UTC")
        if self.settings.start and timestamp < self.settings.start:
            return
        if self.flattened_for_roll or (self.halted and not just_halted):
            engine.setups.clear()
            return
        net = float(self.portfolio.net_position(self.instrument_id))
        holding = self.completed_bars - self.entry_bar_number if net else 0
        events = engine.evaluate(net, holding)
        if just_halted:
            for event in events:
                if event["reason"] == "criteria_evaluation":
                    self.record(
                        "criteria_evaluation",
                        conditions_json=json.dumps(event["groups"], allow_nan=False),
                    )
            matched = ["drawdown_halt"]
            matched.extend(
                reason
                for event in events
                if event["reason"] == "exit_signal"
                for reason in event["matched_reasons"]
            )
            self.exit_reasons = matched
            self.record("exit_signal", primary_reason="drawdown_halt", matched_reasons=matched)
            engine.setups.clear()
            return
        for event in events:
            reason = event.pop("reason")
            if reason == "entry_signal":
                assert engine.atr is not None
                distance = ceil(engine.atr * self.settings.atr_multiple / TICK_SIZE) * TICK_SIZE
                self.signal_id = f"{timestamp.isoformat()}:{event['direction']}"
                self.exit_reasons = []
                self.pending = ("entry", event["direction"], distance)
                event.update(signal_id=self.signal_id, stop_distance=distance)
            elif reason == "exit_signal":
                self.exit_reason = event["primary_reason"]
                self.exit_reasons = event["matched_reasons"]
                self.pending = ("exit", 0, 0)
            if reason == "criteria_evaluation":
                # JSON avoids Python repr and keeps the report portable between runtimes.
                event = {"conditions_json": json.dumps(event["groups"], allow_nan=False)}
            self.record(reason, **event)

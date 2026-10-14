"""SMA and builder decisions executed by NautilusTrader at explicit next opens."""

import json
from collections import deque
from math import ceil

import pandas as pd
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from mnq_research.config import StrategyConfig
from mnq_research.risk import TICK_SIZE, contract_quantity
from mnq_research.rule_engine import RuleEngine
from mnq_research.rules import ENTRY_RULES


class BaselineStrategy(Strategy):
    def __init__(self, settings: StrategyConfig, bar_type: BarType) -> None:
        super().__init__()
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
        self.halted = False
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
                    else "protective_stop"
                    if str(event.client_order_id) in self.stop_orders
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
        self.pending = ("exit", 0, 0)

    def on_order_denied(self, event: object) -> None:
        self.on_order_rejected(event)

    def on_bar(self, bar: Bar) -> None:
        self.completed_bars += 1
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
        just_halted = drawdown >= self.settings.max_drawdown_fraction and not self.halted
        if just_halted:
            self.halted = True
            self.pending = ("exit", 0, 0)
            self.exit_reason = "drawdown_halt"
            self.record("drawdown_halt", drawdown=drawdown)

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
        if previous is None or self.halted:
            return
        timestamp = pd.Timestamp(bar.ts_event, tz="UTC")
        if self.settings.start and timestamp < self.settings.start:
            return
        direction = ENTRY_RULES[self.settings.entry_rule](*previous, fast, slow)
        if not direction:
            return
        net = float(self.portfolio.net_position(self.instrument_id))
        if net * direction < 0:
            self.pending = ("exit", 0, 0)
            self.exit_reason = "opposite_signal"
            self.record("opposite_signal_exit")
        elif net == 0:
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
        if self.halted and not just_halted:
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

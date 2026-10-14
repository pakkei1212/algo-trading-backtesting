"""Completed-bar indicators, condition evaluation and sequential setup state."""

from collections import deque
from dataclasses import dataclass

from mnq_research.criteria import BuilderConfig, Condition, ConditionGroup, Operand


@dataclass
class GroupResult:
    passed: bool
    available: bool
    conditions: list[dict]


class RuleEngine:
    """Pure state machine: produces decisions, never submits or simulates orders."""

    def __init__(self, builder: BuilderConfig, atr_period: int) -> None:
        self.builder = builder
        self.atr_period = atr_period
        operands = [o for c in builder.conditions() for o in (c.left, c.right)]
        self.averages = {o.key: o for o in operands if o.period is not None}
        self.closes: deque[float] = deque(
            maxlen=max([atr_period, *(o.period or 1 for o in operands)])
        )
        self.history: deque[dict[str, float]] = deque(maxlen=max(o.offset for o in operands) + 2)
        self.ema: dict[str, float] = {}
        self.true_ranges: deque[float] = deque(maxlen=atr_period)
        self.previous_close: float | None = None
        self.atr: float | None = None
        self.bar_number = 0
        self.setups: dict[int, int] = {}

    def update(self, prices: dict[str, float]) -> dict[str, float]:
        close, high, low = prices["close"], prices["high"], prices["low"]
        true_range = high - low
        if self.previous_close is not None:
            true_range = max(
                true_range, abs(high - self.previous_close), abs(low - self.previous_close)
            )
        self.previous_close = close
        self.closes.append(close)
        self.true_ranges.append(true_range)
        if self.atr is None and len(self.true_ranges) == self.atr_period:
            self.atr = sum(self.true_ranges) / self.atr_period
        elif self.atr is not None:
            self.atr += (true_range - self.atr) / self.atr_period
        values = dict(prices)
        if self.atr is not None:
            values["atr"] = self.atr
        for key, operand in self.averages.items():
            period = operand.period
            assert period is not None
            if len(self.closes) < period:
                continue
            if operand.kind == "sma":
                values[key] = sum(list(self.closes)[-period:]) / period
            else:
                # EMA starts with an N-close SMA; no partial-window initialization.
                self.ema[key] = (
                    self.ema[key] + 2 / (period + 1) * (close - self.ema[key])
                    if key in self.ema
                    else sum(list(self.closes)[-period:]) / period
                )
                values[key] = self.ema[key]
        self.history.append(values)
        self.bar_number += 1
        return values

    def value(self, operand: Operand, previous: int = 0) -> float | None:
        if operand.kind == "number":
            return operand.value
        offset = operand.offset + previous
        if offset >= len(self.history):
            return None
        return self.history[-1 - offset].get(operand.key)

    def condition(self, condition: Condition) -> dict:
        left, right = self.value(condition.left), self.value(condition.right)
        prior_left, prior_right = self.value(condition.left, 1), self.value(condition.right, 1)
        available = left is not None and right is not None
        crossing = condition.operator.startswith("crosses")
        if crossing:
            available = available and prior_left is not None and prior_right is not None
        passed = False
        if available:
            assert left is not None and right is not None
            if crossing:
                assert prior_left is not None and prior_right is not None
                passed = (
                    prior_left <= prior_right and left > right
                    if condition.operator == "crosses_above"
                    else prior_left >= prior_right and left < right
                )
            else:
                passed = left > right if condition.operator == "above" else left < right
        return dict(
            id=condition.id,
            operator=condition.operator,
            left=left,
            right=right,
            previous_left=prior_left if crossing else None,
            previous_right=prior_right if crossing else None,
            available=available,
            passed=passed,
        )

    def group(self, group: ConditionGroup) -> GroupResult:
        rows = [self.condition(c) for c in group.conditions]
        available = all(row["available"] for row in rows)
        passed = (all if group.mode == "all" else any)(row["passed"] for row in rows)
        return GroupResult(available and passed, available, rows)

    def evaluate(self, net_position: float, holding_bars: int) -> list[dict]:
        """Return trace events and at most one entry or exit per completed bar."""
        events: list[dict] = []
        groups = {}
        for direction, criteria in ((1, self.builder.long), (-1, self.builder.short)):
            if criteria is None:
                continue
            groups[direction] = {
                name: self.group(getattr(criteria, name))
                for name in ("market_filter", "setup", "trigger", "signal_exit")
            }
        if self.atr is None or not all(
            result.available for side in groups.values() for result in side.values()
        ):
            return events
        events.append(
            dict(
                reason="criteria_evaluation",
                groups={
                    str(side): {
                        name: dict(passed=result.passed, conditions=result.conditions)
                        for name, result in values.items()
                    }
                    for side, values in groups.items()
                },
            )
        )
        if net_position:
            self.setups.clear()
            direction = 1 if net_position > 0 else -1
            reasons = []
            if self.builder.max_holding_bars and holding_bars >= self.builder.max_holding_bars:
                reasons.append("holding_limit")
            if groups[direction]["signal_exit"].passed:
                reasons.append("signal_exit")
            if reasons:
                events.append(
                    dict(
                        reason="exit_signal",
                        primary_reason=reasons[0],
                        matched_reasons=reasons,
                        holding_bars=holding_bars,
                        matched_condition_ids=(
                            [
                                row["id"]
                                for row in groups[direction]["signal_exit"].conditions
                                if row["passed"]
                            ]
                            if groups[direction]["signal_exit"].passed
                            else []
                        ),
                    )
                )
            return events
        triggered = []
        for direction, values in groups.items():
            if not values["market_filter"].passed:
                if direction in self.setups:
                    self.setups.pop(direction)
                    events.append(
                        dict(
                            reason="setup_expired",
                            direction=direction,
                            cause="market_filter_failed",
                        )
                    )
                events.append(dict(reason="market_filter_failed", direction=direction))
                continue
            activated = self.setups.get(direction)
            if activated is not None:
                age = self.bar_number - activated
                if age > self.builder.setup_window_bars:
                    self.setups.pop(direction)
                    events.append(
                        dict(
                            reason="setup_expired",
                            direction=direction,
                            cause="window_elapsed",
                            age_bars=age,
                        )
                    )
                elif age >= 1 and values["trigger"].passed:
                    self.setups.pop(direction)
                    triggered.append(direction)
                    events.append(dict(reason="setup_triggered", direction=direction, age_bars=age))
                    continue
            if direction not in self.setups and values["setup"].passed:
                self.setups[direction] = self.bar_number
                events.append(dict(reason="setup_activated", direction=direction))
        if len(triggered) == 2:
            events.append(dict(reason="entry_skipped_conflicting_directions"))
        elif triggered:
            # Consume both directions' state when an entry is selected.
            self.setups.clear()
            events.append(dict(reason="entry_signal", direction=triggered[0]))
        return events

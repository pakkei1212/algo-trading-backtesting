"""Pure USD risk calculations. MNQ is $2 per point and $0.50 per tick."""

from math import floor, isfinite

from mnq_research.config import StrategyConfig

POINT_VALUE_USD = 2.0
TICK_SIZE = 0.25
TICK_VALUE_USD = POINT_VALUE_USD * TICK_SIZE


def maximum_stop_by_risk(equity_usd: float, config: StrategyConfig) -> float:
    """Largest whole-tick stop fitting one contract's percentage-risk budget.

    This is an affordability ceiling, not a recommended stop; margin is separate.
    """
    if not isfinite(equity_usd) or equity_usd < 0:
        raise ValueError("Equity must be finite and nonnegative")
    costs = 2 * (config.commission_per_side_usd + config.slippage_ticks * TICK_VALUE_USD)
    available = max(0.0, equity_usd * config.risk_fraction - costs)
    return floor(available / TICK_VALUE_USD) * TICK_SIZE


def contract_quantity(equity_usd: float, stop_distance: float, config: StrategyConfig) -> int:
    if not isfinite(equity_usd) or not isfinite(stop_distance):
        raise ValueError("Equity and stop distance must be finite")
    if equity_usd <= 0 or stop_distance <= 0:
        return 0
    cost = 2 * (config.commission_per_side_usd + config.slippage_ticks * TICK_VALUE_USD)
    risk_quantity = floor(
        equity_usd * config.risk_fraction / (stop_distance * POINT_VALUE_USD + cost)
    )
    requested = risk_quantity if config.sizing_rule == "percent_risk" else config.fixed_contracts
    # Reserve entry fees as well as margin. This is a configured research approximation.
    margin_quantity = floor(equity_usd / (config.margin_per_contract_usd + cost / 2))
    return max(0, min(requested, config.max_contracts, margin_quantity))

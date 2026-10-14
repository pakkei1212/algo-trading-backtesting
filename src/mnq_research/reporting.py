"""Typed, read-only adaptation of native saved reports for the research UI."""

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from mnq_research.config import StrategyConfig, load_config


@dataclass
class TradeRecord:
    id: str
    number: int
    direction: str
    status: str
    entry_time: str
    exit_time: str | None
    contracts: float
    entry_price: float
    exit_price: float | None
    realized_pnl: float
    unrealized_pnl: float
    commission: float
    duration_hours: float
    exit_reason: str
    order_ids: list[str]
    entry_explanation: str = "Unavailable in this legacy run"
    exit_explanation: str = "Unavailable in this legacy run"
    signal_time: str | None = None


@dataclass
class RunEvent:
    timestamp: str
    reason: str
    detail: dict


@dataclass
class ChartSeries:
    candles: list[dict]
    indicators: list[dict]
    markers: list[dict]
    stops: list[dict]
    performance: list[dict]
    indicator_definitions: list[dict] = field(default_factory=list)


@dataclass
class RunReport:
    run_id: str
    config: StrategyConfig
    summary: dict
    manifest: dict
    trades: list[TradeRecord]
    events: list[RunEvent]
    chart: ChartSeries
    metrics: dict[str, float | int | None]
    unavailable: list[str]


def read_report(folder: Path, name: str) -> pd.DataFrame:
    path = folder / f"{name}.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def money(value: object) -> float:
    """Native USD reports serialize money as '1,234.50 USD'."""
    if pd.isna(value):
        return 0.0
    return float(str(value).split()[0].replace(",", ""))


def utc(value: object) -> pd.Timestamp:
    return pd.Timestamp(value).tz_convert("UTC")


def seconds(value: object) -> int:
    return int(utc(value).value // 1_000_000_000)


def display_time(value: str | None, timezone: str) -> str:
    if value is None:
        return "—"
    return utc(value).tz_convert(timezone).strftime("%Y-%m-%d %H:%M:%S %Z")


def candle_index(close_times: pd.DatetimeIndex, timestamp: str) -> int:
    """Close callbacks belong to the closing candle; +1ns open fills to the next one."""
    stamp = utc(timestamp)
    index = int(close_times.as_unit("ns").searchsorted(stamp, side="left"))
    return min(max(index, 0), len(close_times) - 1)


def trade_metrics(trades: list[TradeRecord], equity: pd.DataFrame, initial: float) -> dict:
    closed = [trade for trade in trades if trade.status == "Closed"]
    pnl = [trade.realized_pnl for trade in closed]
    wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p < 0]
    peak = equity.equity_usd.cummax().clip(lower=initial)

    def average(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    return {
        "closed_net": sum(pnl),
        "unrealized": sum(t.unrealized_pnl for t in trades),
        "closed_count": len(closed),
        "open_count": len(trades) - len(closed),
        "winners": len(wins),
        "losers": len(losses),
        "breakevens": sum(p == 0 for p in pnl),
        "win_rate": len(wins) / len(pnl) if pnl else None,
        "profit_factor": sum(wins) / -sum(losses) if losses else None,
        "average_trade": average(pnl),
        "average_winner": average(wins),
        "average_loser": average(losses),
        "largest_winner": max(wins) if wins else None,
        "largest_loser": min(losses) if losses else None,
        "holding_hours": average([t.duration_hours for t in closed]),
        "drawdown_usd": float((peak - equity.equity_usd).max()),
        "drawdown_pct": float(((peak - equity.equity_usd) / peak).max()),
    }


def load_run(folder: Path) -> RunReport:
    config = load_config(folder / "config.yaml")
    summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    positions, fills = read_report(folder, "positions"), read_report(folder, "fills")
    decisions, observations = read_report(folder, "decisions"), read_report(folder, "observations")
    lifecycle = read_report(folder, "protective_orders")
    equity = read_report(folder, "equity")
    bars = pd.read_parquet(folder / "bars.parquet")
    bars = bars.loc[utc(summary["first_bar"]) : utc(summary["last_bar"])]
    close_times = pd.DatetimeIndex(bars.index)
    candles = [
        dict(
            time=seconds(ts) - 3600,
            close_time=ts.isoformat(),
            **{k: float(row[k]) for k in ("open", "high", "low", "close", "volume")},
        )
        for ts, row in bars.iterrows()
    ]
    events = [
        RunEvent(
            str(row["timestamp"]), str(row["reason"]), {k: v for k, v in row.items() if pd.notna(v)}
        )
        for row in decisions.to_dict("records")
    ]
    trades: list[TradeRecord] = []
    if not positions.empty:
        positions = positions.sort_values("ts_opened")
    for number, row in enumerate(positions.to_dict("records"), 1):
        closed = pd.notna(row["ts_closed"])
        entry = utc(row["ts_opened"])
        end = utc(row["ts_closed"]) if closed else utc(summary["last_bar"])
        order_ids = [str(row["opening_order_id"])]
        if closed:
            order_ids.append(str(row["closing_order_id"]))
        linked = fills[fills.order_id.isin(order_ids)] if not fills.empty else fills
        reason = "Unavailable in this legacy run" if closed else "Still open"
        if closed and "exit_reason" in linked:
            reasons = linked.exit_reason.dropna()
            if not reasons.empty:
                reason = str(reasons.iloc[-1]).replace("_", " ").capitalize()
        commission = sum(money(x) for x in ast.literal_eval(str(row["commissions"])))
        direction = "Long" if row["entry"] == "BUY" else "Short"
        unrealized = (
            0.0
            if closed
            else (
                (float(bars.close.iloc[-1]) - float(row["avg_px_open"]))
                * float(row["quantity"])
                * 2
                * (1 if direction == "Long" else -1)
            )
        )
        trades.append(
            TradeRecord(
                str(row["position_id"]),
                number,
                direction,
                "Closed" if closed else "Open",
                entry.isoformat(),
                end.isoformat() if closed else None,
                float(row["peak_qty"]),
                float(row["avg_px_open"]),
                float(row["avg_px_close"]) if closed else None,
                money(row["realized_pnl"]),
                unrealized,
                commission,
                (end - entry).total_seconds() / 3600,
                reason,
                order_ids,
            )
        )
        trade = trades[-1]
        if config.builder and "signal_id" in linked:
            signals = linked.signal_id.dropna()
            signal = next(
                (
                    e
                    for e in events
                    if e.reason == "entry_signal"
                    and not signals.empty
                    and e.detail.get("signal_id") == signals.iloc[0]
                ),
                None,
            )
            if signal:
                trade.signal_time = signal.timestamp
                setup = next(
                    (
                        e
                        for e in events
                        if e.reason == "setup_triggered"
                        and e.timestamp == signal.timestamp
                        and e.detail.get("direction") == signal.detail.get("direction")
                    ),
                    None,
                )
                age = int(setup.detail["age_bars"]) if setup else None
                trade.entry_explanation = (
                    f"{trade.direction} filter and trigger passed at {signal.timestamp}; "
                    + (f"setup triggered after {age} observed bars. " if age is not None else "")
                    + "Entry filled at the next observed open."
                )
            if closed:
                matched = (
                    linked.matched_exit_reasons.dropna()
                    if "matched_exit_reasons" in linked
                    else pd.Series(dtype=str)
                )
                reasons = (
                    str(matched.iloc[-1]).replace("_", " ").replace(";", ", ")
                    if not matched.empty
                    else reason
                )
                trade.exit_explanation = f"Matched exit reasons: {reasons}. Primary: {reason}."
            else:
                trade.exit_explanation = "Position remains open at the evaluation end."
    markers = []
    for trade in trades:
        linked = fills[fills.order_id.isin(trade.order_ids)]
        for fill in linked.to_dict("records"):
            index = candle_index(close_times, fill["timestamp"])
            label = f"{trade.direction} entry" if fill["order_id"] == trade.order_ids[0] else "Exit"
            markers.append(
                dict(
                    time=candles[index]["time"],
                    trade_id=trade.id,
                    label=label,
                    **{k: v for k, v in fill.items() if pd.notna(v)},
                )
            )
    indicators = []
    for row in observations.to_dict("records"):
        # Put close-known values at their availability time, never at the candle open.
        indicators.append(
            {
                "time": seconds(row["timestamp"]),
                **{
                    k: float(value)
                    for k, value in row.items()
                    if k != "timestamp" and pd.notna(value)
                },
            }
        )
    stops = []
    if not lifecycle.empty:
        for order_id, history in lifecycle.groupby("order_id", sort=False):
            active = history[history.event.eq("accepted")]
            if active.empty:
                continue
            first = active.iloc[0]
            terminal = history[history.event.isin(["filled", "canceled", "expired", "rejected"])]
            last = str(terminal.timestamp.iloc[-1]) if not terminal.empty else summary["last_bar"]
            stops.append(
                dict(
                    order_id=str(order_id),
                    start=str(first.timestamp),
                    end=last,
                    price=float(first.price),
                    entry_order_id=str(first.entry_order_id),
                )
            )
    performance = [
        dict(
            time=seconds(r["timestamp"]),
            equity=float(r["equity_usd"]),
            pnl=float(r["equity_usd"]) - config.initial_equity_usd,
            drawdown=float(r["drawdown"]) * 100,
        )
        for r in equity.to_dict("records")
    ]
    unavailable = []
    if not (folder / "observations.csv").exists():
        unavailable.append("Indicator values were not recorded in this legacy run.")
    if not (folder / "protective_orders.csv").exists():
        unavailable.append("Protective-stop history was not recorded in this legacy run.")
    return RunReport(
        folder.name,
        config,
        summary,
        manifest,
        trades,
        events,
        ChartSeries(
            candles, indicators, markers, stops, performance, indicator_definitions(config)
        ),
        trade_metrics(trades, equity, config.initial_equity_usd),
        unavailable,
    )


def chart_payload(report: RunReport) -> dict:
    return dict(
        version=2,
        run_id=report.run_id,
        symbol=report.summary["symbol"],
        **asdict(report.chart),
        trades=[asdict(t) for t in report.trades],
        events=[asdict(e) for e in report.events],
        halt_limit=report.config.max_drawdown_fraction * 100,
    )


def indicator_definitions(config: StrategyConfig) -> list[dict]:
    if config.builder is None:
        return [
            dict(key="fast", label=f"SMA({config.fast_period})", pane="price"),
            dict(key="slow", label=f"SMA({config.slow_period})", pane="price"),
            dict(key="atr", label=f"ATR({config.atr_period})", pane="atr"),
        ]
    operands = [
        operand
        for condition in config.builder.conditions()
        for operand in (condition.left, condition.right)
        if operand.period
    ]
    definitions = {
        operand.key: dict(
            key=operand.key, label=f"{operand.kind.upper()}({operand.period})", pane="price"
        )
        for operand in operands
    }
    return [*definitions.values(), dict(key="atr", label=f"ATR({config.atr_period})", pane="atr")]


def trade_table(trades: list[TradeRecord], timezone: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Trade": t.number,
                "Direction": t.direction,
                "Status": t.status,
                "Entry time": display_time(t.entry_time, timezone),
                "Exit time": display_time(t.exit_time, timezone),
                "Contracts": t.contracts,
                "Entry price": t.entry_price,
                "Exit price": t.exit_price,
                "Realized P&L (USD)": t.realized_pnl,
                "Unrealized P&L (USD)": t.unrealized_pnl,
                "Commission (USD)": t.commission,
                "Holding hours": round(t.duration_hours, 2),
                "Exit reason": t.exit_reason,
            }
            for t in trades
        ]
    )

"""NautilusTrader adapter. All fills and account balances belong to the engine."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, PerContractFeeModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.enums import AccountType, AssetClass, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Money, Price, Quantity

from mnq_research.cash_indicators import PriorCash
from mnq_research.cash_strategy import (
    CASH_LAST_CLOSE,
    CASH_ZONE,
    CashConfig,
    CashSessionStrategy,
    OrbConfig,
)
from mnq_research.config import StrategyConfig, save_config
from mnq_research.data import interval_duration, load_dataset
from mnq_research.execution_stress import AdverseTickFill
from mnq_research.strategy import BaselineStrategy, RollRiskState

if TYPE_CHECKING:
    from mnq_research.roll_backtest import RollSegment


class CashMarketCache:
    """Reuse unchanged market events across trials with identical split dates."""

    def __init__(self) -> None:
        self.segments: dict[tuple, list[RollSegment]] = {}
        self.events: dict[tuple, list] = {}


def build_instrument(frame: pd.DataFrame, metadata: dict) -> FuturesContract:
    symbol = metadata["symbol"] if metadata["kind"] == "dated_contract" else "MNQ-PROXY"
    expiry = pd.Timestamp(metadata.get("expiry_utc", frame.index[-1] + pd.Timedelta(days=30)))
    return FuturesContract(
        instrument_id=InstrumentId.from_str(f"{symbol}.CME"),
        raw_symbol=Symbol(symbol),
        asset_class=AssetClass.INDEX,
        currency=USD,
        price_precision=2,
        price_increment=Price.from_str("0.25"),
        multiplier=Quantity.from_int(2),
        lot_size=Quantity.from_int(1),
        underlying="NQ",
        activation_ns=int((frame.index[0] - pd.Timedelta(days=1)).value),
        expiration_ns=int(expiry.value),
        ts_event=0,
        ts_init=0,
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
    )


def engine_data(
    frame: pd.DataFrame,
    instrument: FuturesContract,
    bar_type: BarType,
    bar_interval: str = "1h",
) -> list:
    """An observed open quote enables next-open orders; the full bar arrives at close.

    No high/low/close is sent to the strategy before its bar closes. Quote spread is
    zero; configured adverse slippage is applied by the fill model, not the data.
    """
    interval_ns = int(interval_duration(bar_interval).value)
    events = []
    for timestamp, row in frame.iterrows():
        close_ns = int(timestamp.value)
        # One ns separates the preceding bar-close decision from the following open.
        open_ns = close_ns - interval_ns + 1
        opening = instrument.make_price(row.open)
        events.append(
            QuoteTick(
                instrument.id,
                opening,
                opening,
                Quantity.from_int(100000),
                Quantity.from_int(100000),
                open_ns,
                open_ns,
            )
        )
        events.append(
            Bar(
                bar_type,
                *(instrument.make_price(row[key]) for key in ("open", "high", "low", "close")),
                Quantity.from_int(int(row.volume)),
                close_ns,
                close_ns,
            )
        )
    return sorted(events, key=lambda event: event.ts_init)


def load_backtest_dataset(dataset: Path) -> tuple[pd.DataFrame, dict]:
    """Load the same dated or continuous dataset for runs and optimization."""
    if (dataset / "manifest.json").is_file() and not (dataset / "metadata.json").exists():
        from mnq_research.roll_backtest import load_continuous

        return load_continuous(dataset)
    return load_dataset(dataset)


def run_backtest(config: StrategyConfig, dataset: Path, output_root: Path = Path("runs")) -> Path:
    frame, metadata = load_backtest_dataset(dataset)
    result = simulate_backtest(config, frame, metadata, output_root)
    assert isinstance(result, Path)
    return result


def simulate_backtest(
    config: StrategyConfig,
    frame: pd.DataFrame,
    metadata: dict,
    output_root: Path | None = None,
    *,
    cash_sessions: frozenset[date] | None = None,
    orb: OrbConfig | None = None,
    candidate: CashConfig | None = None,
    prior_cash: dict[str, PriorCash] | None = None,
    cash_market: CashMarketCache | None = None,
    experimental_trail: bool = False,
    stress_ticks: int | None = None,
    persistent_risk: RollRiskState | None = None,
    regime_rows: dict | None = None,
) -> Path | dict:
    """Run native execution against prepared bars, optionally saving full reports."""
    bar_interval = metadata.get("bar_interval", "1h")
    continuous = metadata.get("provider") == "continuous_mnq"
    if cash_sessions is not None and (
        not cash_sessions or not continuous or config.builder or config.trailing_stop_enabled
    ):
        raise ValueError("Cash research requires verified continuous windows and plain SMA or ORB")
    if (orb is not None or candidate is not None) and cash_sessions is None:
        raise ValueError("Cash candidates require a verified cash session calendar")
    roll_risk = None
    if continuous:
        from mnq_research.roll_backtest import prepare_segments

        split_key = (config.start, config.end, config.warmup_bars())
        segments = cash_market.segments.get(split_key) if cash_market else None
        if segments is None:
            segments = prepare_segments(config, frame, metadata)
            if cash_market is not None:
                cash_market.segments[split_key] = segments
        roll_risk = persistent_risk or RollRiskState(config.initial_equity_usd)
        inputs = [
            (
                segment.bars,
                segment.metadata,
                segment.flatten_open_ns,
                config.model_copy(update={"start": segment.first_evaluation_close}),
            )
            for segment in segments
        ]
        frame = frame.loc[(frame.index >= config.start) & (frame.index <= config.end)]
        frame = frame.reindex(
            pd.DatetimeIndex(
                [
                    timestamp
                    for segment in segments
                    for timestamp in segment.bars.index
                    if timestamp >= segment.first_evaluation_close
                ]
            )
        )
    else:
        if metadata.get("provider") == "amp_mt5":
            from mnq_research.amp_mt5 import execution_window

            frame = execution_window(frame, metadata, config)
        if metadata["kind"] == "rolling_proxy" and not config.allow_rolling_proxy:
            raise ValueError(
                "Yahoo is a rolling proxy; explicitly enable exploratory proxy research"
            )
        if config.end:
            frame = frame[frame.index <= config.end]
        if config.start:
            warmup = config.warmup_bars()
            # Preserve the causal prefix for recursive builder indicators.
            prefix = frame[frame.index < config.start]
            frame = pd.concat(
                [
                    prefix if config.builder else prefix.tail(warmup),
                    frame[frame.index >= config.start],
                ]
            )
        if len(frame) <= config.warmup_bars():
            raise ValueError("Not enough bars for warm-up and evaluation")
        inputs = [(frame, metadata, None, config)]

    bar_type_name = {
        "5m": "5-MINUTE",
        "15m": "15-MINUTE",
        "30m": "30-MINUTE",
        "1h": "1-HOUR",
    }[bar_interval]
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    strategies = []
    try:
        engine.add_venue(
            venue=Venue("CME"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(config.initial_equity_usd, USD)],
            fee_model=PerContractFeeModel(Money(config.commission_per_side_usd, USD)),
            fill_model=(
                AdverseTickFill(
                    stress_ticks, engine.kernel.clock.timestamp_ns,
                    frozenset(int(t.value) for data, _, _, _ in inputs for t in data.index),
                ) if stress_ticks is not None
                else FillModel(prob_slippage=float(config.slippage_ticks), random_seed=0)
            ),
            bar_adaptive_high_low_ordering=True,
            reject_stop_orders=False,
        )
        for bars, native, flatten_open_ns, settings in inputs:
            instrument = build_instrument(bars, native)
            bar_type = BarType.from_str(f"{instrument.id}-{bar_type_name}-LAST-EXTERNAL")
            if cash_sessions is None:
                strategy = BaselineStrategy(
                    settings, bar_type, roll_risk=roll_risk, flatten_open_ns=flatten_open_ns
                )
            else:
                assert roll_risk is not None
                strategy = CashSessionStrategy(
                    settings,
                    bar_type,
                    roll_risk=roll_risk,
                    flatten_open_ns=flatten_open_ns,
                    sessions=cash_sessions,
                    orb=orb,
                    candidate=candidate,
                    prior_cash=prior_cash.get(native["symbol"]) if prior_cash else None,
                    experimental_trail=experimental_trail,
                    regime_rows=regime_rows,
                )
            engine.add_instrument(instrument)
            events_key = (split_key, instrument.id) if continuous else None
            events = cash_market.events.get(events_key) if cash_market else None
            if events is None:
                events = engine_data(bars, instrument, bar_type, bar_interval)
                if cash_market is not None:
                    cash_market.events[events_key] = events
            engine.add_data(events)
            engine.add_strategy(strategy)
            strategies.append(strategy)
        engine.run()
        for strategy in strategies[:-1]:
            if (
                not strategy.flattened_for_roll
                or not engine.portfolio.is_flat(strategy.instrument_id)
                or engine.cache.orders_open(instrument_id=strategy.instrument_id)
            ):
                raise ValueError(f"Outgoing contract failed to flatten: {strategy.instrument_id}")

        def rows(attribute: str) -> pd.DataFrame:
            records = []
            for strategy in strategies:
                for row in getattr(strategy, attribute):
                    if (
                        continuous
                        and attribute == "observations"
                        and pd.Timestamp(row["timestamp"]) < strategy.settings.start
                    ):
                        continue
                    records.append(
                        {**row, "contract": str(strategy.instrument_id.symbol)}
                        if continuous
                        else row
                    )
            return pd.DataFrame(records)

        equity = rows("equity")
        if config.start:
            equity = equity[pd.to_datetime(equity.timestamp, format="ISO8601") >= config.start]
        if equity.empty:
            raise ValueError("No evaluation bars in the requested period")
        positions = engine.trader.generate_positions_report()
        fills = rows("fill_log")
        summary = summarize(equity, positions, fills, config)
        if stress_ticks is not None:
            summary["slippage_cost_usd"] = (
                float(fills.quantity.sum()) * stress_ticks * 0.5 if not fills.empty else 0
            )
        summary.update(
            dataset_kind=metadata["kind"],
            symbol=metadata["symbol"],
            bar_interval=bar_interval,
            first_bar=equity.timestamp.iloc[0],
            last_bar=equity.timestamp.iloc[-1],
            halted=roll_risk.halted if roll_risk else strategy.halted,
            pending_at_end=strategy.pending is not None,
        )
        if cash_sessions is not None:
            if summary["open_positions"]:
                raise ValueError("Cash research ended with open positions")
            local_closes = pd.to_datetime(equity.timestamp, format="ISO8601").dt.tz_convert(
                CASH_ZONE
            )
            marks = equity.loc[local_closes.dt.time.eq(CASH_LAST_CLOSE), "equity_usd"]
            daily_equity = dict(zip(local_closes.loc[marks.index].dt.date, marks, strict=True))
            if cash_sessions - daily_equity.keys():
                raise ValueError("Missing end-of-session equity marks")
            daily_pnl = {}
            previous = config.initial_equity_usd
            for session in sorted(cash_sessions):
                mark = daily_equity[session]
                daily_pnl[session.isoformat()] = mark - previous
                previous = mark
            active_days = 0
            if not fills.empty:
                entries = fills.loc[fills.exit_reason.eq("entry"), "timestamp"]
                active_days = (
                    pd.to_datetime(entries, format="ISO8601")
                    .dt.tz_convert(CASH_ZONE)
                    .dt.date.nunique()
                )
            decisions = rows("decisions")
            halt_times = (
                decisions.loc[decisions.reason.eq("drawdown_halt"), "timestamp"].tolist()
                if not decisions.empty else []
            )
            summary.update(
                daily_pnl_usd=daily_pnl,
                active_days=active_days,
                flat_sessions=len(cash_sessions) - active_days,
                halt_times_utc=halt_times,
                flat_time_fraction=1 - (
                    pd.to_numeric(positions.duration_ns).sum() if not positions.empty else 0
                ) / (len(cash_sessions) * pd.Timedelta(minutes=390).value),
                fills_sha256=hashlib.sha256(fills.to_csv(index=False).encode()).hexdigest(),
                decisions_sha256=hashlib.sha256(
                    decisions.to_csv(index=False).encode()
                ).hexdigest(),
            )
        if output_root is None:
            return summary
        orders = engine.trader.generate_orders_report()
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        folder = output_root / run_id
        folder.mkdir(parents=True)
        save_config(config, folder / "config.yaml")
        for name, report in {
            "equity": equity,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "observations": rows("observations"),
            "protective_orders": rows("protective_orders"),
            "decisions": rows("decisions"),
        }.items():
            report.to_csv(folder / f"{name}.csv", index=name in {"positions", "orders"})
        frame.index.name = "timestamp"
        frame.to_parquet(folder / "bars.parquet")
        source_hash = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            source_hash.update(path.name.encode())
            source_hash.update(path.read_bytes())
        manifest = {
            "report_version": 3 if config.builder else 2,
            "run_id": run_id,
            "engine": "nautilus_trader==1.231.0",
            "source_sha256": source_hash.hexdigest(),
            "dataset": metadata,
            "config_sha256": hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
            "execution": "Next observed open; native adaptive OHLC path; adverse tick slippage",
            "margin": "Configured entry sizing cap, not exchange maintenance-margin liquidation",
            "end_policy": "Open positions marked to market; no invented terminal liquidation",
        }
        if cash_sessions is not None:
            manifest.update(
                cash_sessions=sorted(session.isoformat() for session in cash_sessions),
                cash_policy=config.name,
                experimental_exit="extreme_1R_2entryATR_v1" if experimental_trail else "original",
                stress_ticks=stress_ticks,
                cash_parameters=(orb or candidate).model_dump(mode="json")
                if orb or candidate else None,
                orb=orb.model_dump(mode="json") if orb else None,
                end_policy="Flat by the end of each cash session",
            )
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return folder
    finally:
        engine.dispose()


def summarize(
    equity: pd.DataFrame, positions: pd.DataFrame, fills: pd.DataFrame, config: StrategyConfig
) -> dict:
    closed = positions
    if not positions.empty:
        closed = positions[positions.ts_closed.notna()]
    pnl = pd.Series(dtype=float)
    if not closed.empty:
        pnl = closed.realized_pnl.map(lambda value: float(str(value).split()[0].replace(",", "")))
    gains, losses = float(pnl[pnl > 0].sum()), -float(pnl[pnl < 0].sum())
    return {
        "final_equity_usd": float(equity.equity_usd.iloc[-1]),
        "net_pnl_usd": float(equity.equity_usd.iloc[-1]) - config.initial_equity_usd,
        "max_drawdown_fraction": float(equity.drawdown.max()),
        "closed_trades": len(closed),
        "average_closed_trade_usd": float(pnl.mean()) if len(pnl) else None,
        "open_positions": len(positions) - len(closed),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "profit_factor": gains / losses if losses else None,
        "commission_usd": float(fills.commission_usd.sum()) if not fills.empty else 0,
        "slippage_cost_usd": float(fills.quantity.sum()) * config.slippage_ticks * 0.5
        if not fills.empty
        else 0,
        "bar_close_exposure": float(equity.in_position.mean()),
        "bars": len(equity),
        "sample_note": "Small samples are not evidence of generalization",
    }

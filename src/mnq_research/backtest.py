"""NautilusTrader adapter. All fills and account balances belong to the engine."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
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

from mnq_research.config import StrategyConfig, save_config
from mnq_research.data import load_dataset
from mnq_research.strategy import BaselineStrategy


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


def engine_data(frame: pd.DataFrame, instrument: FuturesContract, bar_type: BarType) -> list:
    """An observed open quote enables next-open orders; the full bar arrives at close.

    No high/low/close is sent to the strategy before its bar closes. Quote spread is
    zero; configured adverse slippage is applied by the fill model, not the data.
    """
    events = []
    for timestamp, row in frame.iterrows():
        close_ns = int(timestamp.value)
        # One ns separates the preceding bar-close decision from the following open.
        open_ns = close_ns - 3600 * 1_000_000_000 + 1
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


def run_backtest(config: StrategyConfig, dataset: Path, output_root: Path = Path("runs")) -> Path:
    frame, metadata = load_dataset(dataset)
    if metadata["kind"] == "rolling_proxy" and not config.allow_rolling_proxy:
        raise ValueError("Yahoo is a rolling proxy; explicitly enable exploratory proxy research")
    if config.end:
        frame = frame[frame.index <= config.end]
    if config.start:
        warmup = max(config.slow_period, config.atr_period) + 1
        frame = pd.concat(
            [frame[frame.index < config.start].tail(warmup), frame[frame.index >= config.start]]
        )
    if len(frame) <= max(config.slow_period, config.atr_period) + 1:
        raise ValueError("Not enough bars for warm-up and evaluation")
    instrument = build_instrument(frame, metadata)
    bar_type = BarType.from_str(f"{instrument.id}-1-HOUR-LAST-EXTERNAL")
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    strategy = BaselineStrategy(config, bar_type)
    try:
        engine.add_venue(
            venue=Venue("CME"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            base_currency=USD,
            starting_balances=[Money(config.initial_equity_usd, USD)],
            fee_model=PerContractFeeModel(Money(config.commission_per_side_usd, USD)),
            fill_model=FillModel(prob_slippage=float(config.slippage_ticks), random_seed=0),
            bar_adaptive_high_low_ordering=True,
            reject_stop_orders=False,
        )
        engine.add_instrument(instrument)
        engine.add_data(engine_data(frame, instrument, bar_type))
        engine.add_strategy(strategy)
        engine.run()
        equity = pd.DataFrame(strategy.equity)
        if config.start:
            equity = equity[pd.to_datetime(equity.timestamp, format="ISO8601") >= config.start]
        if equity.empty:
            raise ValueError("No evaluation bars in the requested period")
        positions = engine.trader.generate_positions_report()
        orders = engine.trader.generate_orders_report()
        fills = pd.DataFrame(strategy.fill_log)
        summary = summarize(equity, positions, fills, config)
        summary.update(
            dataset_kind=metadata["kind"],
            symbol=metadata["symbol"],
            first_bar=equity.timestamp.iloc[0],
            last_bar=equity.timestamp.iloc[-1],
            halted=strategy.halted,
            pending_at_end=strategy.pending is not None,
        )
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        folder = output_root / run_id
        folder.mkdir(parents=True)
        save_config(config, folder / "config.yaml")
        for name, report in {
            "equity": equity,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "observations": pd.DataFrame(strategy.observations),
            "protective_orders": pd.DataFrame(strategy.protective_orders),
            "decisions": pd.DataFrame(strategy.decisions),
        }.items():
            report.to_csv(folder / f"{name}.csv", index=name in {"positions", "orders"})
        frame.to_parquet(folder / "bars.parquet")
        source_hash = hashlib.sha256()
        for path in sorted(Path(__file__).parent.glob("*.py")):
            source_hash.update(path.name.encode())
            source_hash.update(path.read_bytes())
        manifest = {
            "report_version": 2,
            "run_id": run_id,
            "engine": "nautilus_trader==1.231.0",
            "source_sha256": source_hash.hexdigest(),
            "dataset": metadata,
            "config_sha256": hashlib.sha256(config.model_dump_json().encode()).hexdigest(),
            "execution": "Next observed open; native adaptive OHLC path; adverse tick slippage",
            "margin": "Configured entry sizing cap, not exchange maintenance-margin liquidation",
            "end_policy": "Open positions marked to market; no invented terminal liquidation",
        }
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

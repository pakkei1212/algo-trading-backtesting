"""Local AMP MT5 historical discovery, extraction and immutable publication."""

import hashlib
import json
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from mnq_research.config import StrategyConfig
from mnq_research.data import load_dataset, validate_bars

SERVER = "AMPGlobalUSA-Demo"
BAR = pd.Timedelta(minutes=5)
SYMBOL = re.compile(r"MNQ[HMUZ]\d{2}")
EXPIRY_DATES = {
    "MNQZ25": "2025-12-19",
    "MNQH26": "2026-03-20",
    "MNQM26": "2026-06-18",
    "MNQU26": "2026-09-18",
    "MNQZ26": "2026-12-18",
}
# Special sessions remain in the raw archive. Until individually verified, they
# break eligible windows rather than being treated as ordinary trading days.
SPECIAL_DATES = {
    "2025-01-01",
    "2025-01-20",
    "2025-02-17",
    "2025-04-18",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-11-27",
    "2025-11-28",
    "2025-12-24",
    "2025-12-25",
    "2025-12-26",
    "2025-12-31",
    "2026-01-01",
    "2026-01-02",
    "2026-01-19",
    "2026-02-16",
    "2026-04-03",
    "2026-05-25",
    "2026-06-19",
    "2026-07-03",
    "2026-09-07",
    "2026-11-26",
    "2026-11-27",
    "2026-12-24",
    "2026-12-25",
    "2026-12-31",
}
CALENDAR = "CME MNQ regular Globex 2025–2026, special sessions excluded v1"
SOURCE = "https://www.cmegroup.com/trading-hours.html"


def utc(value: str) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        raise ValueError("Supply an explicit timezone offset in the timestamp.")
    return stamp.tz_convert("UTC")


@contextmanager
def terminal(path: Path | None):
    try:
        import MetaTrader5 as mt5
    except ImportError as error:
        raise ValueError("Install the optional MetaTrader5 client on Windows.") from error
    if not mt5.initialize(*(str(path),) if path else (), timeout=15000):
        raise ValueError(f"MT5 connection failed (code {mt5.last_error()[0]}).")
    try:
        account, info = mt5.account_info(), mt5.terminal_info()
        if not account or not info or not info.connected or account.server != SERVER:
            raise ValueError(f"Open the connected {SERVER} terminal first.")
        yield (
            mt5,
            {
                "server": SERVER,
                "terminal_build": info.build,
                "max_bars": info.maxbars,
                "client_version": mt5.__version__,
            },
        )
    finally:
        mt5.shutdown()


def discover(mt5) -> list[str]:
    symbols = mt5.symbols_get(group="MNQ*")
    if symbols is None:
        raise ValueError(f"MT5 symbol discovery failed (code {mt5.last_error()[0]}).")
    return sorted(symbol.name for symbol in symbols if SYMBOL.fullmatch(symbol.name))


def contract_details(mt5, symbol: str) -> dict:
    if not SYMBOL.fullmatch(symbol) or symbol not in EXPIRY_DATES:
        raise ValueError(f"No reviewed CME expiry for {symbol}.")
    info = mt5.symbol_info(symbol)
    if not info or not mt5.symbol_select(symbol, True):
        raise ValueError(f"Cannot select {symbol}.")
    expiry = pd.Timestamp(f"{EXPIRY_DATES[symbol]} 08:30", tz=ZoneInfo("America/Chicago"))
    vendor_expiry = pd.Timestamp(info.expiration_time, unit="s", tz="UTC")
    if (
        (
            info.exchange not in {"CME", "XCME"}
            and not info.path.startswith("Exchange-Futures\\CME\\")
        )
        or info.currency_profit != "USD"
        or info.trade_tick_size != 0.25
        or info.trade_tick_value != 0.50
        or info.chart_mode != 1
        or vendor_expiry.tz_convert("America/Chicago").date() != expiry.date()
    ):
        raise ValueError(f"Unverified CME trade-bar specification for {symbol}.")
    return {
        "symbol": symbol,
        "vendor_symbol": symbol,
        "exchange": "CME",
        "vendor_path": info.path,
        "vendor_exchange": info.exchange,
        "expiry_utc": expiry.tz_convert("UTC").isoformat(),
        "vendor_expiry_utc": vendor_expiry.isoformat(),
        "activation_utc": (
            pd.Timestamp(info.start_time, unit="s", tz="UTC").isoformat()
            if info.start_time
            else None
        ),
    }


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_file(path: Path, payload: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def expected_closes(
    start: pd.Timestamp, end: pd.Timestamp, *, legacy_pause: bool = True
) -> pd.DataFrame:
    if start.year < 2025 or end.year > 2026:
        raise ValueError("The reviewed regular-session calendar covers 2025–2026.")
    grid = pd.date_range(start + BAR, end, freq=BAR)
    opens = (grid - BAR).tz_convert("America/New_York")
    minutes = opens.hour * 60 + opens.minute
    evening = minutes >= 18 * 60
    weekday = opens.weekday
    regular = (evening & ((weekday == 6) | (weekday <= 3))) | ((weekday <= 4) & (minutes < 17 * 60))
    if legacy_pause:
        regular &= ~((minutes >= 16 * 60 + 15) & (minutes < 16 * 60 + 30))
    trade_dates = opens.tz_localize(None).normalize() + pd.to_timedelta(
        evening.astype(int), unit="D"
    )
    special = trade_dates.strftime("%Y-%m-%d").isin(SPECIAL_DATES)
    return pd.DataFrame({"special": special[regular]}, index=grid[regular])


def observed_coverage_grid(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    """Exclude dates before the first returned bar from coverage expectations."""
    grid = expected_closes(start, end)
    return grid.loc[grid.index >= frame.index.min()] if len(frame) else grid.iloc[:0]


def regular_coverage_counts(
    frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp
) -> dict:
    grid = observed_coverage_grid(frame, start, end)
    observed = frame.reindex(grid.index)
    present = observed.close.notna()
    missing = grid.index[~present & ~grid.special]
    monthly = (
        pd.DataFrame(
            {
                "expected": ~grid.special,
                "observed": present & ~grid.special,
                "zero_volume": present & observed.volume.eq(0) & ~grid.special,
            }
        )
        .groupby(grid.index.tz_convert("America/New_York").strftime("%Y-%m"))
        .sum()
    )
    return {
        "coverage_start_utc": frame.index.min().isoformat() if len(frame) else None,
        "expected_regular_bars": int((~grid.special).sum()),
        "missing_regular_bars": len(missing),
        "missing_regular_closes": [t.isoformat() for t in missing],
        "monthly": monthly.to_dict("index"),
    }


def coverage(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    grid = observed_coverage_grid(frame, start, end)
    observed = frame.reindex(grid.index)
    present = observed.close.notna()
    eligible = present & observed.volume.gt(0) & ~grid.special
    runs = []
    group = eligible.ne(eligible.shift()).cumsum()
    for _, segment in grid.loc[eligible].groupby(group[eligible]):
        runs.append([segment.index[0].isoformat(), segment.index[-1].isoformat()])
    missing = grid.index[~present & ~grid.special]
    returns = observed.close.pct_change(fill_method=None).abs()
    returns = returns.where(grid.index.to_series().diff().eq(BAR))
    trailing = returns.shift().rolling(20, min_periods=20).median()
    outliers = returns.gt(0.01) | (trailing.gt(0) & returns.gt(10 * trailing))
    return {
        "calendar": CALENDAR,
        "calendar_source": SOURCE,
        "first_observed": frame.index.min().isoformat() if len(frame) else None,
        "last_observed": frame.index.max().isoformat() if len(frame) else None,
        "observed_bars": len(frame),
        "special_session_slots": int(grid.special.sum()),
        "observations_outside_regular_grid": int((~frame.index.isin(grid.index)).sum()),
        "zero_real_volume": int(frame.volume.eq(0).sum()),
        "zero_tick_positive_real": int((frame.tick_volume.eq(0) & frame.volume.gt(0)).sum()),
        "flat_positive_real": int((frame.high.eq(frame.low) & frame.volume.gt(0)).sum()),
        "price_move_flags": [t.isoformat() for t in grid.index[outliers]],
        **regular_coverage_counts(frame, start, end),
        "eligible_ranges": runs,
        "status": "FAIL" if not runs else "WARN" if len(missing) or not eligible.all() else "PASS",
    }


def fetch_chunk(mt5, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    for attempt in range(3):
        rates = mt5.copy_rates_range(
            symbol,
            mt5.TIMEFRAME_M5,
            start.to_pydatetime(),
            (end - pd.Timedelta(seconds=1)).to_pydatetime(),
        )
        if rates is not None and (len(rates) or attempt == 2):
            return pd.DataFrame(rates)
        time.sleep(0.5 * (attempt + 1))
    raise ValueError(f"MT5 history failed for {symbol} at {start} (code {mt5.last_error()[0]}).")


def audit(mt5, terminal_info: dict, symbol: str, start: str, end: str, folder: Path) -> dict:
    details = contract_details(mt5, symbol)
    requested_start, requested_end = utc(start), utc(end)
    first, cutoff = requested_start, min(requested_end, utc(details["expiry_utc"]))
    if details["activation_utc"]:
        first = max(first, utc(details["activation_utc"]).ceil("5min"))
    cutoff = min(cutoff, pd.Timestamp(datetime.now(UTC)).floor("5min"))
    if first >= cutoff or first != first.floor("5min") or cutoff != cutoff.floor("5min"):
        raise ValueError("Specify an increasing range aligned to five-minute UTC boundaries.")
    folder.mkdir(parents=True, exist_ok=True)
    ledger = []
    chunks = []
    begin = first
    while begin < cutoff:
        stop = min(begin + pd.Timedelta(days=7), cutoff)
        name = f"{begin:%Y%m%dT%H%M}-{stop:%Y%m%dT%H%M}"
        raw = folder / f"{name}.parquet"
        receipt = folder / f"{name}.json"
        if receipt.exists():
            entry = json.loads(receipt.read_text(encoding="utf-8"))
            if sha(raw) != entry["sha256"] or entry["symbol"] != symbol:
                raise ValueError(f"Raw checkpoint mismatch: {raw}")
        else:
            try:
                result = fetch_chunk(mt5, symbol, begin, stop)
            except ValueError as error:
                ledger.append(
                    {
                        "start_open": begin.isoformat(),
                        "end_open_exclusive": stop.isoformat(),
                        "error": str(error),
                    }
                )
                begin = stop
                continue
            if raw.exists():
                raw.rename(raw.with_name(f"{raw.stem}-orphan-{uuid4().hex}.parquet"))
            partial = raw.with_suffix(".partial")
            result.to_parquet(partial, index=False)
            partial.replace(raw)
            entry = {
                "symbol": symbol,
                "start_open": begin.isoformat(),
                "end_open_exclusive": stop.isoformat(),
                "rows": len(result),
                "first_returned_open": int(result.time.min()) if len(result) else None,
                "last_returned_open": int(result.time.max()) if len(result) else None,
                "sha256": sha(raw),
                "file": raw.name,
            }
            json_file(receipt, entry)
        ledger.append(entry)
        result = pd.read_parquet(raw)
        if len(result):
            opens = pd.DatetimeIndex(pd.to_datetime(result.pop("time"), unit="s", utc=True))
            in_window = (opens >= begin) & (opens < stop) & (opens == opens.floor("5min"))
            entry["rejected_timestamps"] = int((~in_window).sum())
            result = result.loc[in_window].copy()
            result.index = opens[in_window] + BAR
            chunks.append(result)
        begin = stop
    columns = ["open", "high", "low", "close", "real_volume", "tick_volume", "spread"]
    bars = (
        pd.concat(chunks)
        if chunks
        else pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], tz="UTC"))
    )
    duplicates = int(bars.index.duplicated().sum())
    if duplicates and bars.groupby(level=0).nunique(dropna=False).gt(1).any().any():
        raise ValueError(f"Conflicting duplicate bars for {symbol}.")
    bars = bars.loc[~bars.index.duplicated()].sort_index()
    bars = bars.loc[(bars.index <= cutoff) & (bars.index < utc(details["expiry_utc"]))].copy()
    bars["volume"] = bars.real_volume
    if len(bars):
        validate_bars(bars, "5m")
        tick_volume = bars.tick_volume
        if not (tick_volume.ge(0) & tick_volume.mod(1).eq(0)).all():
            raise ValueError("MT5 tick volume must be a nonnegative integer.")
    report = coverage(bars, first, cutoff if cutoff < utc(details["expiry_utc"]) else cutoff - BAR)
    report["duplicate_rows"] = duplicates
    report["rejected_timestamps"] = sum(entry.get("rejected_timestamps", 0) for entry in ledger)
    if report["status"] == "PASS" and (
        report["rejected_timestamps"]
        or report["zero_tick_positive_real"]
        or report["flat_positive_real"]
        or report["price_move_flags"]
    ):
        report["status"] = "WARN"
    report["requests"] = ledger
    report["requested_start_utc"] = requested_start.isoformat()
    report["requested_end_utc"] = requested_end.isoformat()
    report["effective_end_utc"] = cutoff.isoformat()
    bars.index.name = "timestamp"
    # A new audit version is committed only after all source rows validate.
    digest = hashlib.sha256(json.dumps(ledger, sort_keys=True).encode()).hexdigest()
    version = folder / f"{digest[:16]}-coverage2"
    if not version.exists():
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            stage = Path(tmp) / "audit"
            stage.mkdir()
            bars.to_parquet(stage / "bars.parquet")
            json_file(stage / "coverage.json", report)
            json_file(
                stage / "metadata.json",
                {
                    **details,
                    **terminal_info,
                    "provider": "amp_mt5",
                    "kind": "dated_contract",
                    "source": "AMP Futures MT5 historical trade bars",
                    "bar_interval": "5m",
                    "timestamp_semantics": "bar_close",
                    "volume_field": "real_volume",
                    "price_adjustment": "none",
                    "schema_version": 2,
                    "rows": len(bars),
                    "sha256": sha(stage / "bars.parquet"),
                    "raw_manifest_sha256": digest,
                    "coverage_sha256": sha(stage / "coverage.json"),
                    "extracted_at_utc": datetime.now(UTC).isoformat(),
                    "gaps_over_interval": int((bars.index.to_series().diff() > BAR).sum()),
                    "first_close_utc": report["first_observed"],
                    "last_close_utc": report["last_observed"],
                    "quality": report["status"],
                    "eligible_ranges": report["eligible_ranges"],
                },
            )
            stage.rename(version)
    return {
        "audit": str(version),
        "symbol": symbol,
        **{k: report[k] for k in ("status", "observed_bars", "missing_regular_bars")},
    }


def publish(audit_folder: Path, data_root: Path) -> Path:
    _, metadata = load_dataset(audit_folder)
    if metadata.get("provider") != "amp_mt5" or not metadata.get("eligible_ranges"):
        raise ValueError("The audit has no eligible AMP MT5 backtest window.")
    quality = audit_folder / "coverage.json"
    if sha(quality) != metadata["coverage_sha256"]:
        raise ValueError("Coverage report checksum changed.")
    target = data_root / f"amp-{metadata['symbol'].lower()}-5m-{audit_folder.name}"
    if target.exists():
        _, saved = load_dataset(target)
        if saved != metadata or sha(target / "coverage.json") != metadata["coverage_sha256"]:
            raise ValueError(f"An existing dataset differs: {target}")
        return target
    data_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_root) as tmp:
        stage = Path(tmp) / "dataset"
        stage.mkdir()
        for name in ("bars.parquet", "coverage.json", "metadata.json"):
            (stage / name).write_bytes((audit_folder / name).read_bytes())
        stage.rename(target)
    return target


def execution_window(frame: pd.DataFrame, metadata: dict, config: StrategyConfig) -> pd.DataFrame:
    """Restrict the native runner to one audited interval including its warm-up."""
    if config.start is None or config.end is None:
        raise ValueError("AMP backtests require an explicit audited start and end.")
    start = pd.Timestamp(config.start).tz_convert("UTC")
    end = pd.Timestamp(config.end).tz_convert("UTC")
    if end >= utc(metadata["expiry_utc"]):
        raise ValueError("The AMP evaluation must end before contract expiration.")
    for left, right in metadata["eligible_ranges"]:
        first, last = utc(left), utc(right)
        if first <= start < end <= last:
            grid = expected_closes(first - BAR, end)
            prepared = frame.reindex(grid.index)
            if grid.special.any() or not prepared.volume.gt(0).all():
                raise ValueError("AMP window has missing or zero-volume trading bars.")
            if (prepared.index < start).sum() < config.warmup_bars():
                raise ValueError("Leave more audited bars before the AMP evaluation start.")
            return prepared
    raise ValueError("The AMP evaluation crosses an unaudited session or a data gap.")

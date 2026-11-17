"""Import market bars, preserve provenance, and accumulate Yahoo rolling history."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

import numpy as np
import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close"]
BAR_COLUMNS = [*PRICE_COLUMNS, "volume"]
BAR_INTERVALS = {
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
}
_ARCHIVE_LOCK = threading.Lock()


def interval_duration(bar_interval: str) -> pd.Timedelta:
    """Return a supported bar duration or reject unknown intervals."""
    try:
        return BAR_INTERVALS[bar_interval]
    except KeyError as error:
        raise ValueError(f"Unsupported bar interval: {bar_interval}") from error


def validate_bars(frame: pd.DataFrame, bar_interval: str = "1h") -> pd.DataFrame:
    """Require aligned UTC bar-close timestamps and genuine positive OHLC observations."""
    duration = interval_duration(bar_interval)
    frame = frame.copy()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("Bars require a timezone-aware DatetimeIndex of bar-close times")
    frame.index = frame.index.tz_convert("UTC")
    frame.index.name = "timestamp"
    if (
        frame.empty
        or frame.index.hasnans
        or frame.index.has_duplicates
        or not frame.index.is_monotonic_increasing
    ):
        raise ValueError("Bars must be nonempty, unique and sorted by timestamp")
    if (frame.index.to_series().diff().dropna() < duration).any():
        raise ValueError(f"{bar_interval} bars must not overlap")
    if (frame.index.as_unit("ns").asi8 % duration.value != 0).any():
        raise ValueError(f"Bar-close timestamps must align to {bar_interval} boundaries")
    if not set(BAR_COLUMNS).issubset(frame.columns):
        raise ValueError(f"Required columns: {BAR_COLUMNS}")
    frame = frame[BAR_COLUMNS].astype(float)
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Null or non-finite OHLCV values are not allowed")
    if (frame[PRICE_COLUMNS] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError("Prices must be positive and volume nonnegative")
    if (frame.volume % 1 != 0).any():
        raise ValueError("MNQ volume must be whole contracts")
    if (frame.high < frame[PRICE_COLUMNS].max(axis=1)).any():
        raise ValueError("High is below another OHLC price")
    if (frame.low > frame[PRICE_COLUMNS].min(axis=1)).any():
        raise ValueError("Low is above another OHLC price")
    ticks = frame[PRICE_COLUMNS].to_numpy() / 0.25
    if not np.allclose(ticks, np.round(ticks), atol=1e-5, rtol=0):
        raise ValueError("MNQ prices must fall on the 0.25-point tick grid")
    return frame


def _dataset_metadata(frame: pd.DataFrame, parquet_path: Path, metadata: dict) -> dict:
    """Build versioned metadata for one parquet file."""
    bar_interval = metadata.get("bar_interval", "1h")
    duration = interval_duration(bar_interval)
    frame = validate_bars(frame, bar_interval)
    gaps = frame.index.to_series().diff() > duration
    return {
        **metadata,
        "schema_version": 2,
        "bar_interval": bar_interval,
        "timestamp_semantics": "bar_close",
        "rows": len(frame),
        "first_close_utc": frame.index[0].isoformat(),
        "last_close_utc": frame.index[-1].isoformat(),
        "gaps_over_interval": int(gaps.sum()),
        "gap_note": "Includes exchange closures; gaps are reported, never filled.",
        "sha256": hashlib.sha256(parquet_path.read_bytes()).hexdigest(),
        "imported_at_utc": datetime.now(UTC).isoformat(),
    }


def _write_dataset_contents(frame: pd.DataFrame, folder: Path, metadata: dict) -> None:
    """Write a validated dataset into an already-created folder."""
    parquet_path = folder / "bars.parquet"
    frame = validate_bars(frame, metadata.get("bar_interval", "1h"))
    frame.to_parquet(parquet_path)
    saved_metadata = _dataset_metadata(frame, parquet_path, metadata)
    (folder / "metadata.json").write_text(json.dumps(saved_metadata, indent=2), encoding="utf-8")


def write_dataset(frame: pd.DataFrame, folder: Path, metadata: dict) -> Path:
    """Write a new immutable dataset directory; existing data is never replaced."""
    if folder.exists():
        raise ValueError("Dataset folder already exists; choose a new name to preserve provenance")
    folder.mkdir(parents=True)
    _write_dataset_contents(frame, folder, metadata)
    return folder


def load_dataset(folder: Path) -> tuple[pd.DataFrame, dict]:
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    checksum = hashlib.sha256((folder / "bars.parquet").read_bytes()).hexdigest()
    if checksum != metadata["sha256"]:
        raise ValueError("Dataset checksum changed; import it as a new dataset")
    bar_interval = metadata.get("bar_interval", "1h")
    interval_duration(bar_interval)
    if metadata.get("timestamp_semantics", "bar_close") != "bar_close":
        raise ValueError("Datasets must use bar-close timestamps")
    if metadata.get("kind") not in {"dated_contract", "rolling_proxy", "test_fixture"}:
        raise ValueError("Unknown dataset kind")
    if metadata.get("provider") == "amp_mt5":
        coverage = (folder / "coverage.json").read_bytes()
        if hashlib.sha256(coverage).hexdigest() != metadata["coverage_sha256"]:
            raise ValueError("AMP coverage report checksum changed")
        if json.loads(coverage)["eligible_ranges"] != metadata["eligible_ranges"]:
            raise ValueError("AMP eligible ranges differ from the audited coverage report")
    return validate_bars(pd.read_parquet(folder / "bars.parquet"), bar_interval), metadata


def import_csv(
    path: Path,
    folder: Path,
    symbol: str,
    source: str,
    expiry: str,
    bar_interval: str = "1h",
) -> Path:
    """Import one dated MNQ contract with timezone-aware bar-close timestamps."""
    interval_duration(bar_interval)
    if not symbol.startswith("MNQ") or symbol in {"MNQ=F", "MNQ1!"}:
        raise ValueError("Specify a dated MNQ contract, e.g. MNQZ6")
    expiry_date = pd.Timestamp(expiry)
    if expiry_date.tz is None:
        raise ValueError("Expiry must include a timezone")
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError("CSV contains no bars")
    parsed = [pd.Timestamp(value) for value in frame.pop("timestamp")]
    if any(value.tz is None for value in parsed):
        raise ValueError("Every timestamp must include a timezone")
    frame.index = pd.DatetimeIndex(pd.to_datetime(parsed, utc=True))
    if frame.index[-1] >= expiry_date:
        raise ValueError("Dataset contains bars at or beyond contract expiration")
    return write_dataset(
        frame,
        folder,
        {
            "kind": "dated_contract",
            "symbol": symbol,
            "source": source,
            "bar_interval": bar_interval,
            "expiry_utc": expiry_date.tz_convert("UTC").isoformat(),
            "original_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )


def yahoo_frame(
    payload: dict, now: datetime, bar_interval: str = "1h"
) -> tuple[pd.DataFrame, dict]:
    """Exclude null observations and unfinished bars, with explicit exclusion counts."""
    duration = interval_duration(bar_interval)
    chart = payload["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Yahoo returned no data: {chart.get('error')}")
    result = chart["result"][0]
    if result["meta"]["symbol"] != "MNQ=F":
        raise ValueError("Yahoo returned an unexpected symbol")
    frame = pd.DataFrame(result["indicators"]["quote"][0])
    opens = pd.to_datetime(result["timestamp"], unit="s", utc=True)
    frame.index = opens + duration
    raw_count = len(frame)
    # Yahoo can append a current-price timestamp that is not a regular bar boundary.
    interval_minutes = int(duration.total_seconds() // 60)
    regular = (opens.minute % interval_minutes == 0) & (opens.second == 0)
    complete = frame.index <= pd.Timestamp(now)
    good_values = frame[BAR_COLUMNS].notna().all(axis=1)
    frame = frame.loc[regular & complete & good_values, BAR_COLUMNS]
    return validate_bars(frame, bar_interval), {
        "kind": "rolling_proxy",
        "symbol": "MNQ=F",
        "source": "Yahoo Finance chart endpoint",
        "bar_interval": bar_interval,
        "provider_timezone": result["meta"].get("exchangeTimezoneName"),
        "excluded_rows": raw_count - len(frame),
        "exclusion_rule": (
            "Null OHLCV, unfinished bars, or timestamps not aligned to the requested interval"
        ),
        "limitations": [
            "Rolling series: dated-contract identity and roll adjustments are unverified.",
            "Exploratory research only; not evidence of executable dated-contract returns.",
            "OHLC bars cannot identify the true intrabar fill sequence.",
        ],
    }


def fetch_yahoo(bar_interval: str, period: str) -> tuple[bytes, pd.DataFrame, dict, str]:
    """Fetch and validate one Yahoo chart response without writing it to disk."""
    interval_duration(bar_interval)
    if bar_interval in {"5m", "15m", "30m"} and period != "60d":
        raise ValueError(f"Yahoo {bar_interval} history is limited to a 60d request")
    if bar_interval == "1h" and period not in {"1mo", "3mo", "6mo", "1y", "2y"}:
        raise ValueError("Choose 1mo, 3mo, 6mo, 1y or 2y for hourly Yahoo history")
    parameters = {"interval": bar_interval}
    request_time = datetime.now(UTC)
    request_window: dict[str, str] = {}
    if period == "60d":
        end_seconds = int(request_time.timestamp())
        start_seconds = end_seconds - 60 * 24 * 60 * 60
        parameters.update(
            period1=start_seconds,
            period2=end_seconds,
        )
        request_window = {
            "requested_start_utc": datetime.fromtimestamp(start_seconds, UTC).isoformat(),
            "requested_end_utc": datetime.fromtimestamp(end_seconds, UTC).isoformat(),
        }
    else:
        parameters["range"] = period
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MNQ%3DF?" + urlencode(parameters)
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 MNQResearch/0.1"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    frame, metadata = yahoo_frame(json.loads(raw), datetime.now(UTC), bar_interval)
    metadata.update(
        requested_period=period,
        request_url=url,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        **request_window,
    )
    return raw, frame, metadata, url


def download_yahoo(folder: Path, period: str = "1mo", bar_interval: str = "1h") -> Path:
    """Save a standalone Yahoo dataset. Use refresh_yahoo_archive to accumulate 5m history."""
    raw, frame, metadata, _ = fetch_yahoo(bar_interval, period)
    write_dataset(frame, folder, metadata)
    (folder / "source.json").write_bytes(raw)
    return folder


def refresh_yahoo_archive(folder: Path) -> tuple[Path, dict]:
    """Append a fresh Yahoo 5m window while retaining all prior bars and raw snapshots."""
    with _ARCHIVE_LOCK:
        folder.mkdir(parents=True, exist_ok=True)
        has_metadata = (folder / "metadata.json").exists()
        if (folder / "bars.parquet").exists() != has_metadata:
            raise ValueError("Archive is incomplete; existing files were retained")
        if has_metadata:
            old_bars, old_metadata = load_dataset(folder)
            if old_metadata.get("kind") != "rolling_proxy" or old_metadata.get("symbol") != "MNQ=F":
                raise ValueError("Archive path already contains a different dataset")
            if old_metadata.get("bar_interval", "1h") != "5m":
                raise ValueError("Archive path contains data at an interval other than 5m")
        else:
            old_bars, old_metadata = None, {}
            other_files = [path for path in folder.iterdir() if path.name != "snapshots"]
            if other_files:
                raise ValueError("Archive path is not empty and has no valid dataset metadata")

        raw, newest, snapshot_metadata, _ = fetch_yahoo("5m", "60d")
        snapshots = folder / "snapshots"
        snapshots.mkdir(exist_ok=True)
        snapshot_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        snapshot_folder = snapshots / snapshot_id
        snapshot_folder.mkdir()
        _write_dataset_contents(newest, snapshot_folder, snapshot_metadata)
        (snapshot_folder / "source.json").write_bytes(raw)
        snapshot_count = sum(path.is_dir() for path in snapshots.iterdir())

        if old_bars is not None:
            overlapping = old_bars.index.intersection(newest.index)
            revised = int(
                (
                    old_bars.loc[overlapping, BAR_COLUMNS].to_numpy()
                    != newest.loc[overlapping, BAR_COLUMNS].to_numpy()
                ).any(axis=1).sum()
            )
            added = int(len(newest.index.difference(old_bars.index)))
            merged = pd.concat([old_bars, newest])
            merged = merged.loc[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = newest
            added = len(newest)
            revised = 0

        merged_metadata = {
            **snapshot_metadata,
            "archive_kind": "accumulated_yahoo_history",
            "snapshot_count": snapshot_count,
            "latest_snapshot": snapshot_id,
            "snapshot_policy": "Raw responses and each fetched bar window are immutable.",
            "overlap_policy": (
                "Latest downloaded values win in the merged view; old snapshots remain."
            ),
        }
        with NamedTemporaryFile(
            dir=folder, prefix="bars-", suffix=".parquet", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        metadata_temp: Path | None = None
        try:
            merged.to_parquet(temporary_path)
            saved_metadata = _dataset_metadata(merged, temporary_path, merged_metadata)
            metadata_temp = folder / f"metadata-{uuid4().hex}.tmp"
            metadata_temp.write_text(json.dumps(saved_metadata, indent=2), encoding="utf-8")
            temporary_path.replace(folder / "bars.parquet")
            metadata_temp.replace(folder / "metadata.json")
        finally:
            temporary_path.unlink(missing_ok=True)
            if metadata_temp is not None:
                metadata_temp.unlink(missing_ok=True)
        return folder, {
            "added_bars": added,
            "revised_overlap_bars": revised,
            "total_bars": len(merged),
            "snapshot_count": snapshot_count,
            "first_close_utc": merged.index[0].isoformat(),
            "last_close_utc": merged.index[-1].isoformat(),
        }

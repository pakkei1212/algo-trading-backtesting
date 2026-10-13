"""Import hourly bars and preserve their provenance. No TradingView scraping."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

PRICE_COLUMNS = ["open", "high", "low", "close"]
BAR_COLUMNS = [*PRICE_COLUMNS, "volume"]


def validate_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Require UTC bar-close timestamps and genuine positive OHLC observations."""
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
    if (frame.index.to_series().diff().dropna() < pd.Timedelta(hours=1)).any():
        raise ValueError("Hourly bars must not overlap")
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


def write_dataset(frame: pd.DataFrame, folder: Path, metadata: dict) -> Path:
    frame = validate_bars(frame)
    if folder.exists():
        raise ValueError("Dataset folder already exists; choose a new name to preserve provenance")
    folder.mkdir(parents=True)
    frame.to_parquet(folder / "bars.parquet")
    gaps = frame.index.to_series().diff() > pd.Timedelta(hours=1)
    metadata = {
        **metadata,
        "schema_version": 1,
        "bar_interval": "1h",
        "timestamp_semantics": "bar_close",
        "rows": len(frame),
        "first_close_utc": frame.index[0].isoformat(),
        "last_close_utc": frame.index[-1].isoformat(),
        "gaps_over_one_hour": int(gaps.sum()),
        "gap_note": "Includes exchange closures; gaps are reported, never filled.",
        "sha256": hashlib.sha256((folder / "bars.parquet").read_bytes()).hexdigest(),
        "imported_at_utc": datetime.now(UTC).isoformat(),
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return folder


def load_dataset(folder: Path) -> tuple[pd.DataFrame, dict]:
    metadata = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    checksum = hashlib.sha256((folder / "bars.parquet").read_bytes()).hexdigest()
    if checksum != metadata["sha256"]:
        raise ValueError("Dataset checksum changed; import it as a new dataset")
    if metadata.get("bar_interval") != "1h" or metadata.get("timestamp_semantics") != "bar_close":
        raise ValueError("MVP requires hourly bars labeled at their close")
    if metadata.get("kind") not in {"dated_contract", "rolling_proxy", "test_fixture"}:
        raise ValueError("Unknown dataset kind")
    return validate_bars(pd.read_parquet(folder / "bars.parquet")), metadata


def import_csv(path: Path, folder: Path, symbol: str, source: str, expiry: str) -> Path:
    """Import one dated MNQ contract. CSV timestamps must contain UTC offsets."""
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
            "expiry_utc": expiry_date.tz_convert("UTC").isoformat(),
            "original_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )


def yahoo_frame(payload: dict, now: datetime) -> tuple[pd.DataFrame, dict]:
    """Exclude null observations and unfinished bars, with explicit exclusion counts."""
    chart = payload["chart"]
    if chart.get("error") or not chart.get("result"):
        raise ValueError(f"Yahoo returned no data: {chart.get('error')}")
    result = chart["result"][0]
    if result["meta"]["symbol"] != "MNQ=F":
        raise ValueError("Yahoo returned an unexpected symbol")
    frame = pd.DataFrame(result["indicators"]["quote"][0])
    opens = pd.to_datetime(result["timestamp"], unit="s", utc=True)
    frame.index = opens + pd.Timedelta(hours=1)
    raw_count = len(frame)
    # Yahoo can append a current-price timestamp that is not a regular bar boundary.
    regular = (opens.minute == 0) & (opens.second == 0)
    complete = frame.index <= pd.Timestamp(now)
    good_values = frame[BAR_COLUMNS].notna().all(axis=1)
    frame = frame.loc[regular & complete & good_values, BAR_COLUMNS]
    return validate_bars(frame), {
        "kind": "rolling_proxy",
        "symbol": "MNQ=F",
        "source": "Yahoo Finance chart endpoint",
        "provider_timezone": result["meta"].get("exchangeTimezoneName"),
        "excluded_rows": raw_count - len(frame),
        "exclusion_rule": "Null OHLCV, unfinished hourly bars, or non-hour-boundary timestamps",
        "limitations": [
            "Rolling series: dated-contract identity and roll adjustments are unverified.",
            "Exploratory research only; not evidence of executable dated-contract returns.",
            "Hourly OHLC cannot identify the true intrabar fill sequence.",
        ],
    }


def download_yahoo(folder: Path, period: str = "1mo") -> Path:
    if period not in {"1mo", "3mo", "6mo", "1y", "2y"}:
        raise ValueError("Choose 1mo, 3mo, 6mo, 1y or 2y; Yahoo may reject unavailable history")
    url = "https://query1.finance.yahoo.com/v8/finance/chart/MNQ%3DF?" + urlencode(
        {
            "range": period,
            "interval": "1h",
        }
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 MNQResearch/0.1"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    frame, metadata = yahoo_frame(json.loads(raw), datetime.now(UTC))
    metadata.update(
        requested_period=period, request_url=url, response_sha256=hashlib.sha256(raw).hexdigest()
    )
    write_dataset(frame, folder, metadata)
    (folder / "source.json").write_bytes(raw)
    return folder

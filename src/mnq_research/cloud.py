"""Session-owned research storage and bounded cloud execution."""

import hashlib
import io
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd

from mnq_research.backtest import run_backtest
from mnq_research.config import StrategyConfig
from mnq_research.data import BAR_COLUMNS, validate_bars, write_dataset

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_BARS = 10_000
MAX_RUNS = 5
BACKTEST_LOCK = Lock()


class CloudWorkspace:
    """The session retains this owner; releasing it removes its temporary directory."""

    def __init__(self) -> None:
        self._temporary = TemporaryDirectory(prefix="mnq-session-")
        self.root = Path(self._temporary.name)
        for name in ("data", "configs", "runs"):
            (self.root / name).mkdir()

    def close(self) -> None:
        self._temporary.cleanup()


def import_upload(
    content: bytes,
    workspace: CloudWorkspace,
    symbol: str,
    source: str,
    kind: str,
    expiry: str = "",
    acknowledge_proxy: bool = False,
) -> Path:
    """Validate fully before writing; filenames and paths are generated internally."""
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("Upload a nonempty CSV no larger than 10 MB")
    if not source.strip():
        raise ValueError("Enter the market-data source")
    frame = pd.read_csv(io.BytesIO(content), nrows=MAX_BARS + 1)
    if len(frame) > MAX_BARS:
        raise ValueError("Cloud uploads are limited to 10,000 hourly bars")
    if set(frame.columns) != {"timestamp", *BAR_COLUMNS}:
        raise ValueError("CSV requires exactly timestamp, open, high, low, close, volume")
    parsed = [pd.Timestamp(value) for value in frame.pop("timestamp")]
    if not parsed or any(value.tz is None for value in parsed):
        raise ValueError("Every bar-close timestamp must include a timezone offset")
    frame.index = pd.DatetimeIndex(pd.to_datetime(parsed, utc=True))
    frame = validate_bars(frame)
    symbol = symbol.strip()
    metadata = dict(kind=kind, symbol=symbol, source=source.strip())
    if kind == "dated_contract":
        if not re.fullmatch(r"MNQ[HMUZ]\d{1,2}", symbol):
            raise ValueError("Enter a dated MNQ symbol, for example MNQZ6")
        expiration = pd.Timestamp(expiry)
        if pd.isna(expiration) or expiration.tz is None:
            raise ValueError("Expiration must include a timezone offset")
        if frame.index[-1] >= expiration:
            raise ValueError("Dataset contains bars at or beyond contract expiration")
        metadata["expiry_utc"] = expiration.tz_convert("UTC").isoformat()
    elif kind == "rolling_proxy":
        if symbol not in {"MNQ=F", "MNQ1!", "MNQ-PROXY"}:
            raise ValueError("Choose MNQ=F, MNQ1! or MNQ-PROXY for rolling data")
        if not acknowledge_proxy:
            raise ValueError("Acknowledge that rolling-contract data is exploratory")
    else:
        raise ValueError("Choose dated contract or rolling proxy")
    metadata["original_file_sha256"] = hashlib.sha256(content).hexdigest()
    folder = workspace.root / "data" / uuid4().hex
    # One uploaded dataset per session bounds storage. Completed runs retain their own bars.
    result = write_dataset(frame, folder, metadata)
    for old in folder.parent.iterdir():
        if old != folder:
            shutil.rmtree(old)
    return result


def run_cloud_backtest(
    config: StrategyConfig,
    dataset: Path,
    workspace: CloudWorkspace,
    runner: Callable[[StrategyConfig, Path, Path], Path] = run_backtest,
) -> Path:
    if dataset.parent.resolve() != (workspace.root / "data").resolve():
        raise ValueError("Dataset does not belong to this session")
    if not BACKTEST_LOCK.acquire(blocking=False):
        raise ValueError("Another backtest is running. Please retry shortly.")
    runs = workspace.root / "runs"
    existing = set(runs.iterdir())
    try:
        result = runner(config, dataset, runs)
        completed = sorted(runs.glob("*/summary.json"), key=lambda p: p.parent.name)
        for report in completed[:-MAX_RUNS]:
            shutil.rmtree(report.parent)
        return result
    except Exception:
        # Remove only newly created partial outputs, preserving earlier successful runs.
        for partial in set(runs.iterdir()) - existing:
            shutil.rmtree(partial)
        raise
    finally:
        BACKTEST_LOCK.release()


def run_archive(folder: Path) -> bytes:
    """Export every saved engine report plus data and reproduction metadata."""
    if not (folder / "summary.json").is_file():
        raise ValueError("Select a completed run")
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        for file in sorted(folder.iterdir()):
            if file.is_file() and not file.is_symlink():
                archive.write(file, arcname=f"{folder.name}/{file.name}")
    return stream.getvalue()

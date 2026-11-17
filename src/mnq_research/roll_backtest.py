"""Prepare verified windows for dated-instrument execution of an MNQ stitch."""

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from mnq_research.amp_mt5 import BAR, expected_closes, sha, utc
from mnq_research.config import StrategyConfig
from mnq_research.data import load_dataset, validate_bars

# AMP publishes the CME Globex Control Center schedules for these dates.
# Values are exchange closing times expressed in New York local minutes.
HOLIDAY_CLOSES = {
    "2025-11-27": 13 * 60,
    "2025-11-28": 13 * 60 + 15,
    "2025-12-24": 13 * 60 + 15,
    "2025-12-25": None,
    "2026-01-01": None,
    "2026-01-19": 13 * 60,
    "2026-02-16": 13 * 60,
    "2026-04-03": 9 * 60 + 15,
    "2026-05-25": 13 * 60,
    "2026-06-19": 13 * 60,
    "2026-07-03": 13 * 60,
    "2026-09-07": 13 * 60,
}
REVIEWED_NORMAL_DATES = {
    "2025-12-26",
    "2025-12-31",
    "2026-01-02",
    "2026-06-18",
}
ROLL_CALENDAR = "CME MNQ Globex plus reviewed 2025–2026 holidays and outage v1"
OUTAGE_LAST_CLOSE = "2025-11-28T02:45:00Z"
OUTAGE_REOPEN = "2025-11-28T13:30:00Z"


def roll_grid(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    # CME removed the 15:15–15:30 Chicago pause in June 2021.
    grid = expected_closes(start, end, legacy_pause=False)
    opened = (grid.index - BAR).tz_convert("America/New_York").tz_localize(None)
    evening = opened.hour >= 18
    dates = (opened.normalize() + pd.to_timedelta(evening.astype(int), unit="D")).strftime(
        "%Y-%m-%d"
    )
    minutes = opened.hour * 60 + opened.minute
    keep = pd.Series(True, index=grid.index)
    grid.loc[dates.isin(REVIEWED_NORMAL_DATES), "special"] = False
    for date, cutoff in HOLIDAY_CLOSES.items():
        session = dates == date
        grid.loc[session, "special"] = False
        keep.loc[session] = False if cutoff is None else (evening | (minutes < cutoff))[session]
    # CME's documented data-center outage. Both dated archives agree on the last
    # traded candle and the first traded candle after the 07:30 Chicago reopen.
    outage = (grid.index > utc(OUTAGE_LAST_CLOSE)) & (grid.index <= utc(OUTAGE_REOPEN))
    return grid.loc[keep & ~outage]


@dataclass
class RollSegment:
    bars: pd.DataFrame
    metadata: dict
    first_evaluation_close: pd.Timestamp
    flatten_open_ns: int | None


def load_continuous(folder: Path) -> tuple[pd.DataFrame, dict]:
    metadata = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if metadata["kind"] != "continuous_research" or metadata["bar_interval"] != "5m":
        raise ValueError("Expected a five-minute continuous MNQ research dataset.")
    path = folder / "bars.parquet"
    if sha(path) != metadata["parquet_sha256"]:
        raise ValueError("Continuous bars checksum changed.")
    bars = pd.read_parquet(path)
    validate_bars(bars, "5m")
    grid = roll_grid(bars.index[0] - BAR, bars.index[-1])
    usable = bars.reindex(grid.index).volume.gt(0) & ~grid.special
    group = usable.ne(usable.shift()).cumsum()
    ranges = [
        (part.index[0].isoformat(), part.index[-1].isoformat())
        for _, part in grid.loc[usable].groupby(group[usable])
    ]
    return bars, {
        **metadata,
        "provider": "continuous_mnq",
        "symbol": "MNQ continuous",
        "sha256": metadata["parquet_sha256"],
        "study_checksum": sha(folder / "manifest.json"),
        "source": "AMP MT5 lead contracts with tagged IBKR repair",
        "verified_ranges": ranges,
        "source_root": str(folder.parent.parent.resolve()),
        "execution_enabled": True,
        "execution_calendar": ROLL_CALENDAR,
        "missing_execution_bars": int(
            (~bars.reindex(grid.index).volume.gt(0) & ~grid.special).sum()
        ),
        "unverified_execution_slots": int(grid.special.sum()),
        "execution_policy": "Flatten at the open of the last outgoing trading session",
    }


def prepare_segments(
    config: StrategyConfig, stitched: pd.DataFrame, metadata: dict
) -> list[RollSegment]:
    if config.start is None or config.end is None:
        raise ValueError(
            "Choose explicit start and end dates inside one verified continuous window."
        )
    start, end = (pd.Timestamp(t).tz_convert("UTC") for t in (config.start, config.end))
    if not any(
        utc(left) <= start < end <= utc(right) for left, right in metadata["verified_ranges"]
    ):
        raise ValueError("The evaluation crosses a missing bar or unverified special session.")
    wanted = roll_grid(stitched.index[0] - BAR, end)
    wanted = wanted.loc[wanted.index >= start]
    data_root = Path(metadata["source_root"])
    prepared = []
    for item in metadata["segments"]:
        symbol = item["contract"]
        first_open = utc(item["segment_open_utc"])
        boundary = utc(item["segment_end_utc"])
        closes = wanted.index[(wanted.index > first_open) & (wanted.index <= boundary)]
        if closes.empty:
            continue
        source, native = load_dataset(data_root / item["source_dataset"])
        if native["sha256"] != item["source_sha256"] or native["symbol"] != symbol:
            raise ValueError(f"Continuous source changed for {symbol}.")
        selected = stitched.reindex(closes)
        if (
            not selected.contract.eq(symbol).all()
            or not selected.volume.gt(0).all()
            or (closes >= utc(native["expiry_utc"])).any()
        ):
            raise ValueError(f"Invalid execution bars for {symbol}.")
        # Preserve only consecutive verified native history before the first decision.
        prior_start = source.index[0] - BAR
        if len(prepared) == 0 and start > first_open:
            prior_start = max(prior_start, first_open)
        prior = roll_grid(prior_start, closes[0] - BAR)
        history = source.reindex(prior.index)
        # Use the same documented repairs if they occur during warm-up.
        patch = stitched.loc[stitched.contract.eq(symbol) & stitched.source.eq("IBKR")]
        overlap = history.index.intersection(patch.index)
        history.loc[overlap, source.columns] = patch.loc[overlap, source.columns]
        bad = prior.index[prior.special | ~history.volume.gt(0)]
        if len(bad):
            history = history.loc[history.index > bad[-1]]
        if len(history) < config.warmup_bars():
            raise ValueError(f"Insufficient verified same-contract warm-up for {symbol}.")
        if not config.builder:
            history = history.tail(config.warmup_bars())
        flatten_ns = None
        if boundary < wanted.index[-1]:
            if closes[-1] != roll_grid(first_open, boundary).index[-1]:
                raise ValueError(f"Missing last outgoing session for {symbol}.")
            outgoing_date = closes[-1].tz_convert("America/New_York").date()
            session_open = (
                (pd.Timestamp(outgoing_date) - pd.Timedelta(days=1) + pd.Timedelta(hours=18))
                .tz_localize("America/New_York")
                .tz_convert("UTC")
            )
            if session_open + BAR not in closes:
                raise ValueError(
                    f"Evaluation must include the scheduled flatten open for {symbol}."
                )
            flatten_ns = int(session_open.value) + 1
        prepared.append(
            RollSegment(
                pd.concat([history, selected[source.columns]]), native, closes[0], flatten_ns
            )
        )
    if sum((s.bars.index >= s.first_evaluation_close).sum() for s in prepared) != len(wanted):
        raise ValueError("Roll manifest does not cover the complete evaluation window.")
    return prepared

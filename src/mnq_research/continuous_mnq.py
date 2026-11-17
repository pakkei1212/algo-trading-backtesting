"""Build a local, unadjusted MNQ research series from dated contracts."""

import argparse
import hashlib
import json
import tempfile
from pathlib import Path

import pandas as pd

from mnq_research.amp_mt5 import BAR, CALENDAR, SOURCE, expected_closes, sha, utc
from mnq_research.data import load_dataset, validate_bars

ROLL_SOURCE = "https://www.cmegroup.com/trading/equity-index/rolldates.html"
# CME roll trade dates, expressed as the corresponding Globex session opens in UTC.
SEGMENTS = (
    ("MNQZ25", "2025-09-14T22:00:00Z"),
    ("MNQH26", "2025-12-14T23:00:00Z"),
    ("MNQM26", "2026-03-15T22:00:00Z"),
    ("MNQU26", "2026-06-14T22:00:00Z"),
    ("MNQZ26", "2026-09-13T22:00:00Z"),
)
CUTOFF = "2026-11-11T03:05:00Z"
GAP_START = "2026-08-05T05:10:00Z"
GAP_END = "2026-08-05T05:55:00Z"


def repair_gap(amp: pd.DataFrame, ibkr: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Approve only the ten documented gaps, using exact surrounding comparisons."""
    start, end = utc(GAP_START), utc(GAP_END)
    gap = pd.date_range(start, end, freq=BAR, name=amp.index.name)
    comparison_index = pd.date_range(start - 6 * BAR, end + 6 * BAR, freq=BAR).difference(gap)
    left, right = amp.reindex(comparison_index), ibkr.reindex(comparison_index)
    replacement = ibkr.reindex(gap)
    if (
        gap.isin(amp.index).any()
        or replacement.isna().any().any()
        or not replacement.volume.gt(0).all()
        or left.isna().any().any()
        or right.isna().any().any()
        or not left.volume.gt(0).all()
        or not right.volume.gt(0).all()
    ):
        raise ValueError("MNQU26 repair preconditions failed. No patch was applied.")
    replacement = validate_bars(replacement, "5m")
    differences = left - right
    exact = differences.eq(0).all(axis=1)
    # These explicit tolerances apply only to this reviewed 12-bar overlap.
    if (
        not differences[["high", "low"]].eq(0).all().all()
        or int(exact.sum()) < 9
        or differences[["open", "close"]].abs().max().max() > 2
        or (differences.volume.abs() / right.volume).max() > 0.10
    ):
        raise ValueError("MNQU26 overlap differs beyond the reviewed repair limits.")
    return replacement, {
        "replaced_missing_closes_utc": [t.isoformat() for t in gap],
        "overlap_bars": len(exact),
        "exact_ohlcv_matches": int(exact.sum()),
        "max_absolute_difference": differences.abs().max().to_dict(),
        "max_relative_volume_difference": float((differences.volume.abs() / right.volume).max()),
        "nonidentical_overlap": [
            {"close_utc": t.isoformat(), "amp_minus_ibkr": differences.loc[t].to_dict()}
            for t in exact.index[~exact]
        ],
    }


def build_continuous(data_root: Path, output_root: Path) -> Path:
    """Publish a separate research artifact with source and coverage records."""
    cutoff = utc(CUTOFF)
    parts = []
    segments = []
    repair = {}
    originals = {}
    for i, (symbol, opened) in enumerate(SEGMENTS):
        start = utc(opened)
        end = utc(SEGMENTS[i + 1][1]) if i + 1 < len(SEGMENTS) else cutoff
        matches = sorted(data_root.glob(f"amp-{symbol.lower()}-5m-*"))
        if len(matches) != 1:
            raise ValueError(f"Expected exactly one pinned AMP source for {symbol}.")
        folder = matches[0]
        bars, meta = load_dataset(folder)
        if (
            meta["symbol"] != symbol
            or meta["kind"] != "dated_contract"
            or meta.get("provider") != "amp_mt5"
            or meta["bar_interval"] != "5m"
            or end >= utc(meta["expiry_utc"])
        ):
            raise ValueError(f"Unexpected source identity or expiration for {symbol}.")
        for name in ("bars.parquet", "coverage.json", "metadata.json"):
            originals[folder / name] = sha(folder / name)
        segment = bars.loc[(bars.index > start) & (bars.index <= end)].copy()
        if segment.empty:
            raise ValueError(f"No observations for the {symbol} lead segment.")
        segment["source"] = "AMP MT5"
        segment["source_symbol"] = symbol
        if symbol == "MNQU26":
            ibkr_folder = data_root / "ibkr-mnqu6-5m"
            backup, ibkr = load_dataset(ibkr_folder)
            if (
                ibkr["symbol"] != "MNQU6"
                or ibkr["kind"] != "dated_contract"
                or ibkr["bar_interval"] != "5m"
                or utc(ibkr["expiry_utc"]) != utc(meta["expiry_utc"])
                or not ibkr["source"].startswith("IBKR")
            ):
                raise ValueError("IBKR replacement source is not the same dated CME contract.")
            for name in ("bars.parquet", "metadata.json"):
                originals[ibkr_folder / name] = sha(ibkr_folder / name)
            patch, repair = repair_gap(bars, backup)
            if not ((patch.index > start) & (patch.index <= end)).all():
                raise ValueError("Repair rows fall outside the MNQU26 segment.")
            repair.update(
                amp_sha256=meta["sha256"],
                ibkr_sha256=ibkr["sha256"],
                reason="AMP returned no bars on the original and targeted requests",
            )
            segment = pd.concat(
                [segment, patch.assign(source="IBKR", source_symbol="MNQU6")]
            ).sort_index()
        segment["contract"] = symbol
        validate_bars(segment, "5m")
        grid = expected_closes(start, end)
        regular = grid.index[~grid.special]
        absent = regular.difference(segment.index)
        unverified = grid.index[grid.special]
        local_opens = (unverified - BAR).tz_convert("America/New_York").tz_localize(None)
        trade_dates = local_opens.normalize() + pd.to_timedelta(
            (local_opens.hour >= 18).astype(int), unit="D"
        )
        zero = segment.index[segment.volume.eq(0) & segment.index.isin(regular)]
        segments.append(
            {
                "contract": symbol,
                "source_dataset": folder.name,
                "source_sha256": meta["sha256"],
                "segment_open_utc": start.isoformat(),
                "segment_end_utc": end.isoformat(),
                "first_close_utc": segment.index[0].isoformat(),
                "last_close_utc": segment.index[-1].isoformat(),
                "rows": len(segment),
                "expected_regular_bars": len(regular),
                "missing_regular_closes_utc": [t.isoformat() for t in absent],
                "zero_volume_regular_closes_utc": [t.isoformat() for t in zero],
                "unverified_special_session_slots": len(unverified),
                "unverified_special_trade_dates": sorted(set(trade_dates.strftime("%Y-%m-%d"))),
                "observations_outside_calendar_grid": int((~segment.index.isin(grid.index)).sum()),
            }
        )
        parts.append(segment)
    stitched = pd.concat(parts)
    validate_bars(stitched, "5m")
    for path, checksum in originals.items():
        if sha(path) != checksum:
            raise ValueError(f"Input changed during build: {path}")
    manifest = {
        "kind": "continuous_research",
        "symbol": "MNQ",
        "bar_interval": "5m",
        "timestamp_semantics": "UTC bar close",
        "price_adjustment": "none",
        "execution_enabled": False,
        "roll_rule": "CME roll trade date Globex open",
        "roll_calendar_source": ROLL_SOURCE,
        "session_calendar": CALENDAR,
        "session_calendar_source": SOURCE,
        "segments": segments,
        "repair": repair,
        "source_files": {
            str(path.relative_to(data_root)): digest for path, digest in originals.items()
        },
    }
    fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    target = output_root / f"amp-mnq-continuous-5m-{fingerprint[:16]}"
    output_root.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=output_root) as temp:
            stage = Path(temp) / "dataset"
            stage.mkdir()
            stitched.to_parquet(stage / "bars.parquet")
            manifest["parquet_sha256"] = sha(stage / "bars.parquet")
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            pd.DataFrame(
                [
                    {
                        **{
                            key: item[key]
                            for key in (
                                "contract",
                                "first_close_utc",
                                "last_close_utc",
                                "rows",
                                "expected_regular_bars",
                                "unverified_special_session_slots",
                                "observations_outside_calendar_grid",
                            )
                        },
                        "missing_regular_bars": len(item["missing_regular_closes_utc"]),
                        "zero_volume_regular_bars": len(item["zero_volume_regular_closes_utc"]),
                    }
                    for item in segments
                ]
            ).to_csv(stage / "coverage.csv", index=False)
            stage.rename(target)
    saved = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if sha(target / "bars.parquet") != saved["parquet_sha256"]:
        raise ValueError(f"Saved continuous dataset checksum mismatch: {target}")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("data/continuous-research"))
    args = parser.parse_args()
    print(build_continuous(args.data, args.output))


if __name__ == "__main__":
    main()

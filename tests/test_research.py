"""Synthetic scenarios verify software behavior, not market profitability."""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from mnq_research.backtest import run_backtest
from mnq_research.config import StrategyConfig, load_config, save_config
from mnq_research.data import load_dataset, validate_bars, write_dataset
from mnq_research.risk import contract_quantity, maximum_stop_by_risk
from mnq_research.rules import sma_cross


def settings(**changes) -> StrategyConfig:
    return StrategyConfig(
        **{
            "commission_per_side_usd": 1,
            "slippage_ticks": 0,
            "margin_per_contract_usd": 1000,
            "fast_period": 2,
            "slow_period": 3,
            "atr_period": 2,
            "atr_multiple": 10,
            "risk_fraction": 0.005,
            "sizing_rule": "fixed_contracts",
            **changes,
        }
    )


def market_bars(closes: list[float], bar_interval: str = "1h") -> pd.DataFrame:
    frequency = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "h"}[bar_interval]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [value + 0.25 for value in closes],
            "low": [value - 0.25 for value in closes],
            "close": closes,
            "volume": [100] * len(closes),
        },
        index=pd.date_range("2026-01-01", periods=len(closes), freq=frequency, tz="UTC"),
    )


def dataset(
    tmp_path: Path,
    closes: list[float],
    name: str = "market",
    bar_interval: str = "1h",
) -> Path:
    return write_dataset(
        market_bars(closes, bar_interval),
        tmp_path / name,
        {
            "kind": "test_fixture",
            "symbol": "MNQ-TEST",
            "source": "unit test",
            "bar_interval": bar_interval,
        },
    )


def read_run(folder: Path, report: str) -> pd.DataFrame:
    return pd.read_csv(folder / f"{report}.csv")


def test_config_rejects_lookalike_mistakes() -> None:
    with pytest.raises(ValidationError):
        settings(fast_period=50)
    with pytest.raises(ValidationError):
        settings(risk_fraction=float("nan"))
    with pytest.raises(ValidationError):
        settings(start="2026-01-01")
    with pytest.raises(ValidationError):
        settings(unknown_rule=True)


def test_risk_skips_unaffordable_contract_and_respects_margin() -> None:
    config = settings(sizing_rule="percent_risk", slippage_ticks=1)
    assert contract_quantity(10000, 24, config) == 0  # $48 stop + $3 round trip > $50.
    assert contract_quantity(10000, 20, config) == 1
    assert contract_quantity(500, 1, config) == 0
    assert contract_quantity(10000, 1, config) == 2


def test_affordability_ceiling_matches_sizing_boundary() -> None:
    config = settings(sizing_rule="percent_risk", slippage_ticks=1)
    ceiling = maximum_stop_by_risk(10000, config)
    assert ceiling == 23.5
    assert contract_quantity(10000, ceiling, config) == 1
    assert contract_quantity(10000, ceiling + 0.25, config) == 0
    assert maximum_stop_by_risk(100, config) == 0


def test_baseline_defaults_allow_affordable_stops_without_rounding_up() -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "baseline.yaml")
    defaults = StrategyConfig(
        commission_per_side_usd=1, slippage_ticks=1, margin_per_contract_usd=3000
    )
    assert config.risk_fraction == defaults.risk_fraction == 0.02
    assert config.atr_multiple == defaults.atr_multiple == 1.0
    assert maximum_stop_by_risk(10000, config) == 98.5
    assert contract_quantity(10000, 75, config) == 1
    assert contract_quantity(10000, 100, config) == 0


def test_cross_requires_a_new_transition() -> None:
    assert sma_cross(10, 11, 12, 11) == 1
    assert sma_cross(12, 11, 10, 11) == -1
    assert sma_cross(12, 11, 13, 11) == 0


def test_bad_data_is_not_repaired_silently(tmp_path: Path) -> None:
    bars = market_bars([100, 101, 102, 103])
    bars.iloc[0, bars.columns.get_loc("high")] = 99
    with pytest.raises(ValueError, match="High"):
        validate_bars(bars)
    bars = market_bars([100, 101, 102, 103])
    with pytest.raises(ValueError, match="unique"):
        validate_bars(pd.concat([bars, bars.iloc[-1:]]))
    folder = dataset(tmp_path, [100, 101, 102, 103])
    with (folder / "bars.parquet").open("ab") as file:
        file.write(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        load_dataset(folder)


def test_next_open_and_futures_accounting(tmp_path: Path) -> None:
    # First long cross occurs at bar 4 close=103; entry must wait for bar 5 open=110.
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112])
    run = run_backtest(settings(), folder, tmp_path / "runs")
    fills = read_run(run, "fills")
    assert len(fills) == 1
    assert fills.price.iloc[0] == 110
    assert fills.quantity.iloc[0] == 1
    summary = json.loads((run / "summary.json").read_text())
    # One contract, $2 per point, final close=112; one $1 entry commission.
    assert summary["final_equity_usd"] == pytest.approx(10003)
    assert summary["open_positions"] == 1
    assert summary["commission_usd"] == 1


def test_slippage_applies_to_actual_fill(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112])
    run = run_backtest(settings(slippage_ticks=1), folder, tmp_path / "runs")
    assert read_run(run, "fills").price.iloc[0] == 110.25


def test_stop_gap_and_halt(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 104, 90, 91])
    run = run_backtest(
        settings(atr_multiple=1, max_drawdown_fraction=0.001), folder, tmp_path / "runs"
    )
    fills = read_run(run, "fills")
    assert len(fills) == 2
    assert fills.price.iloc[0] == 104
    assert fills.price.iloc[1] == 90  # Gap loss, not an invented stop-price fill.
    summary = json.loads((run / "summary.json").read_text())
    assert summary["final_equity_usd"] == pytest.approx(9970)
    assert summary["closed_trades"] == 1
    assert summary["halted"] is True


def test_future_changes_do_not_change_earlier_decisions(tmp_path: Path) -> None:
    prefix = [103, 102, 101, 102, 103, 104, 105, 106]
    first = dataset(tmp_path, prefix + [107, 108, 109], "first")
    second = dataset(tmp_path, prefix + [90, 80, 70], "second")
    run_a = run_backtest(settings(), first, tmp_path / "runs")
    run_b = run_backtest(settings(), second, tmp_path / "runs")
    cutoff = market_bars(prefix).index[-1]
    for report in ("decisions", "fills", "equity"):
        left, right = read_run(run_a, report), read_run(run_b, report)
        left = left[pd.to_datetime(left.timestamp, format="ISO8601") <= cutoff].drop(
            columns=["order_id"], errors="ignore"
        )
        right = right[pd.to_datetime(right.timestamp, format="ISO8601") <= cutoff].drop(
            columns=["order_id"], errors="ignore"
        )
        pd.testing.assert_frame_equal(left.reset_index(drop=True), right.reset_index(drop=True))


def test_reproducibility(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112])
    first = run_backtest(settings(), folder, tmp_path / "runs")
    second = run_backtest(settings(), folder, tmp_path / "runs")
    assert (first / "summary.json").read_bytes() == (second / "summary.json").read_bytes()
    assert (first / "equity.csv").read_bytes() == (second / "equity.csv").read_bytes()


def test_rolling_proxy_requires_opt_in(tmp_path: Path) -> None:
    folder = write_dataset(
        market_bars([103, 102, 101, 102, 103, 110, 111, 112]),
        tmp_path / "proxy",
        {"kind": "rolling_proxy", "symbol": "MNQ=F"},
    )
    with pytest.raises(ValueError, match="rolling proxy"):
        run_backtest(settings(), folder, tmp_path / "runs")


def test_holdout_starts_flat_and_does_not_trade_warmup(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 111, 112])
    run = run_backtest(settings(start="2026-01-01T06:00:00Z"), folder, tmp_path / "runs")
    summary = json.loads((run / "summary.json").read_text())
    assert summary["first_bar"] == "2026-01-01T06:00:00+00:00"
    assert summary["closed_trades"] == 0
    assert summary["open_positions"] == 0
    assert summary["final_equity_usd"] == 10000


def test_drawdown_liquidates_on_next_open_without_reentry(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 104, 103, 102, 101, 110, 120])
    run = run_backtest(
        settings(atr_multiple=10, max_drawdown_fraction=0.00025), folder, tmp_path / "runs"
    )
    fills = read_run(run, "fills")
    assert fills.price.tolist() == [104, 102]
    decisions = read_run(run, "decisions")
    assert "drawdown_halt" in decisions.reason.tolist()
    summary = json.loads((run / "summary.json").read_text())
    assert summary["final_equity_usd"] == 9994


def test_stop_slippage_and_two_contract_commission(tmp_path: Path) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 104, 90, 91])
    run = run_backtest(
        settings(atr_multiple=1, slippage_ticks=1, fixed_contracts=2, direction="long"),
        folder,
        tmp_path / "runs",
    )
    fills = read_run(run, "fills")
    assert fills.price.tolist() == [104.25, 89.75]
    assert fills.quantity.tolist() == [2, 2]
    summary = json.loads((run / "summary.json").read_text())
    assert summary["commission_usd"] == 4
    assert summary["final_equity_usd"] == 9938


def test_intrabar_stop_fills_at_trigger_not_bar_extreme(tmp_path: Path) -> None:
    bars = market_bars([103, 102, 101, 102, 103, 104, 104, 105])
    bars.iloc[5, bars.columns.get_loc("low")] = 90
    folder = write_dataset(
        bars,
        tmp_path / "intrabar",
        {
            "kind": "test_fixture",
            "symbol": "MNQ-TEST",
            "source": "unit test",
        },
    )
    run = run_backtest(settings(atr_multiple=1, direction="long"), folder, tmp_path / "runs")
    fills = read_run(run, "fills")
    assert fills.price.tolist() == [104, 102.75]


def test_yahoo_excludes_null_snapshot_and_unfinished_rows() -> None:
    from mnq_research.data import yahoo_frame

    opens = pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC")
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "MNQ=F", "exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(value.timestamp()) for value in opens],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100, None, 101, 102],
                                "high": [101, None, 102, 103],
                                "low": [99, None, 100, 101],
                                "close": [100, None, 101, 102],
                                "volume": [10, None, 10, 10],
                            }
                        ]
                    },
                }
            ],
        }
    }
    frame, metadata = yahoo_frame(payload, opens[-1].to_pydatetime())
    assert len(frame) == 2
    assert metadata["excluded_rows"] == 2
    assert frame.index[-1] == opens[-1]


def test_five_minute_yahoo_conversion_and_validation() -> None:
    from mnq_research.data import yahoo_frame

    opens = pd.date_range("2026-01-01", periods=4, freq="5min", tz="UTC")
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "MNQ=F", "exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(value.timestamp()) for value in opens],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100, 101, 102, 103],
                                "high": [101, 102, 103, 104],
                                "low": [99, 100, 101, 102],
                                "close": [100, 101, 102, 103],
                                "volume": [10, 10, 10, 10],
                            }
                        ]
                    },
                }
            ],
        }
    }
    frame, metadata = yahoo_frame(
        payload, (opens[-1] + pd.Timedelta(minutes=5)).to_pydatetime(), "5m"
    )
    assert frame.index.tolist() == list(opens + pd.Timedelta(minutes=5))
    assert metadata["bar_interval"] == "5m"

    invalid = frame.copy()
    invalid.index = invalid.index + pd.Timedelta(minutes=1)
    with pytest.raises(ValueError, match="align"):
        validate_bars(invalid, "5m")


def test_yahoo_fetch_requests_a_bounded_five_minute_window(monkeypatch) -> None:
    from io import BytesIO
    from urllib.parse import parse_qs, urlparse

    import mnq_research.data as data

    opens = pd.date_range("2026-01-01", periods=1, freq="5min", tz="UTC")
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {"symbol": "MNQ=F", "exchangeTimezoneName": "America/New_York"},
                    "timestamp": [int(opens[0].timestamp())],
                    "indicators": {
                        "quote": [
                            {
                                "open": [100],
                                "high": [101],
                                "low": [99],
                                "close": [100],
                                "volume": [10],
                            }
                        ]
                    },
                }
            ],
        }
    }
    def response(*_args, **_kwargs):
        return BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(data, "urlopen", response)

    raw, frame, metadata, url = data.fetch_yahoo("5m", "60d")
    query = parse_qs(urlparse(url).query)
    assert query["interval"] == ["5m"]
    assert int(query["period2"][0]) - int(query["period1"][0]) == 60 * 24 * 60 * 60
    assert metadata["requested_period"] == "60d"
    assert metadata["requested_start_utc"] and metadata["requested_end_utc"]
    assert hashlib.sha256(raw).hexdigest() == metadata["response_sha256"]
    assert len(frame) == 1


def test_yahoo_refresh_accumulates_bars_and_keeps_immutable_snapshots(
    tmp_path, monkeypatch
) -> None:
    import mnq_research.data as data

    first = market_bars([100, 101, 102], "5m")
    second = market_bars([111, 102, 103], "5m").astype(float)
    second.index = pd.date_range(first.index[1], periods=3, freq="5min")
    second.loc[second.index[0], ["open", "high", "low", "close"]] = [111, 111.5, 110.75, 111.25]
    responses = iter(
        [
            (
                b"first-provider-response",
                first,
                {"kind": "rolling_proxy", "symbol": "MNQ=F", "bar_interval": "5m"},
                "url",
            ),
            (
                b"second-provider-response",
                second,
                {"kind": "rolling_proxy", "symbol": "MNQ=F", "bar_interval": "5m"},
                "url",
            ),
        ]
    )
    monkeypatch.setattr(data, "fetch_yahoo", lambda *_: next(responses))
    archive = tmp_path / "data" / "yahoo-mnq-5m"

    first_path, first_result = data.refresh_yahoo_archive(archive)
    first_snapshot = next((archive / "snapshots").iterdir())
    old_snapshot_bytes = (first_snapshot / "bars.parquet").read_bytes()
    assert first_path == archive and first_result["total_bars"] == 3

    _, second_result = data.refresh_yahoo_archive(archive)
    accumulated, metadata = load_dataset(archive)
    assert len(accumulated) == 4
    assert second_result["added_bars"] == 1
    assert second_result["revised_overlap_bars"] == 1
    assert second_result["snapshot_count"] == 2
    assert accumulated.index[0] == first.index[0]
    assert accumulated.close.iloc[1] == 111.25
    assert (first_snapshot / "bars.parquet").read_bytes() == old_snapshot_bytes
    assert (first_snapshot / "source.json").read_bytes() == b"first-provider-response"
    assert len(list((archive / "snapshots").iterdir())) == 2
    assert metadata["archive_kind"] == "accumulated_yahoo_history"
    saved_bars = (archive / "bars.parquet").read_bytes()
    def offline(*_args):
        raise OSError("offline")

    monkeypatch.setattr(data, "fetch_yahoo", offline)
    with pytest.raises(OSError, match="offline"):
        data.refresh_yahoo_archive(archive)
    assert (archive / "bars.parquet").read_bytes() == saved_bars
    assert len(list((archive / "snapshots").iterdir())) == 2


@pytest.mark.parametrize("bar_interval", ["5m", "15m", "30m", "1h"])
def test_interval_backtest_uses_next_interval_open_and_chart_times(
    tmp_path: Path, bar_interval: str
) -> None:
    folder = dataset(
        tmp_path,
        [103, 102, 101, 102, 103, 110, 111, 112],
        bar_interval=bar_interval,
    )
    run = run_backtest(settings(), folder, tmp_path / "runs")
    fills = read_run(run, "fills")
    assert len(fills) == 1
    assert fills.price.iloc[0] == 110
    summary = json.loads((run / "summary.json").read_text())
    assert summary["bar_interval"] == bar_interval

    from mnq_research.reporting import load_run

    report = load_run(run)
    duration_seconds = int(
        {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600}[bar_interval]
    )
    assert report.chart.candles[0]["time"] == int(
        pd.Timestamp(report.chart.candles[0]["close_time"]).timestamp()
    ) - duration_seconds


@pytest.mark.parametrize("direction", [1, -1])
def test_r_trail_tightens_at_close_and_fills_later(tmp_path: Path, direction: int) -> None:
    prices = [103, 102, 101, 102, 103, 110, 112.5, 113.5, 113.25, 111]
    folder = dataset(tmp_path, [110 + direction * (price - 110) for price in prices])
    run = run_backtest(
        settings(atr_multiple=1, trailing_stop_enabled=True, trailing_distance_r=0.9),
        folder,
        tmp_path / "runs",
    )
    lifecycle = read_run(run, "protective_orders")
    assert list(lifecycle.event) == [
        "submitted", "accepted", "trail_activated", "updated", "updated", "filled"
    ]
    updates = lifecycle[lifecycle.event.eq("updated")]
    assert list(updates.price) == [110 + direction * 1.25, 110 + direction * 2.25]
    fills = read_run(run, "fills")
    assert list(fills.exit_reason) == ["entry", "trailing_stop"]
    assert pd.Timestamp(fills.timestamp.iloc[-1]) > pd.Timestamp(updates.timestamp.iloc[-1])


@pytest.mark.parametrize("activation_r", [2.0, 20.0])
def test_r_trail_preserves_opposite_crossover(tmp_path: Path, activation_r: float) -> None:
    folder = dataset(tmp_path, [103, 102, 101, 102, 103, 110, 112.5, 112, 111.75, 111.75])
    config = settings(
        atr_multiple=1,
        trailing_stop_enabled=True,
        trailing_activation_r=activation_r,
        trailing_distance_r=2,
    )
    run = run_backtest(config, folder, tmp_path / "runs")
    assert list(read_run(run, "fills").exit_reason) == ["entry", "opposite_signal"]
    lifecycle = read_run(run, "protective_orders")
    assert ("updated" in lifecycle.event.values) == (activation_r == 2.0)


def test_r_trail_config_round_trip(tmp_path: Path) -> None:
    config = settings(trailing_stop_enabled=True, trailing_activation_r=1.5)
    path = tmp_path / "strategy.yaml"
    save_config(config, path)
    assert load_config(path) == config
    for value in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            settings(trailing_stop_enabled=True, trailing_distance_r=value)

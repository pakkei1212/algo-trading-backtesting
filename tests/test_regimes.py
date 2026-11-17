from datetime import date

import pandas as pd

from mnq_research.cash_indicators import PriorCash
from mnq_research.regimes import classify, feature_frame, fit_thresholds


def test_efficiency_clock_boundaries_and_frozen_thresholds() -> None:
    index = pd.date_range("2026-04-01T13:35Z", periods=78, freq="5min")
    bars = pd.DataFrame({"open": 100., "close": range(100, 178)}, index=index)
    history = PriorCash({}, {date(2026, 4, 1): (100., 2.)})
    features = feature_frame(bars, history)
    assert pd.isna(features.loc["2026-04-01T14:30Z", "efficiency"])
    assert features.loc["2026-04-01T14:35Z", "efficiency"] == 1
    assert features.loc["2026-04-01T15:00Z", "time_bucket"] == "11_to_14"
    assert features.loc["2026-04-01T18:00Z", "time_bucket"] == "from_14"
    fitted = fit_thresholds(features, frozenset({date(2026, 4, 1)}), "2026-04-01", "2026-04-01")
    assert fitted["thresholds"]["volatility"]["count"] == 1
    assert fitted["thresholds"]["efficiency"]["count"] == 66
    classified = classify(features, fitted)
    assert set(classified.efficiency_label) == {"unavailable", "low"}
    future = features.copy()
    future.index += pd.Timedelta(days=1)
    future["session"] = "2026-04-02"
    future["volatility"] = 500
    assert fit_thresholds(
        pd.concat([features, future]), frozenset({date(2026, 4, 1)}),
        "2026-04-01", "2026-04-01",
    ) == fitted


def test_flat_price_path_is_unavailable() -> None:
    index = pd.date_range("2026-04-01T13:35Z", periods=78, freq="5min")
    bars = pd.DataFrame({"open": 100., "close": 100.}, index=index)
    features = feature_frame(bars, PriorCash({}, {}))
    assert features.efficiency.isna().all()
    assert features.efficiency_unavailable_reason.iloc[12] == "zero_price_path"
    assert features.volatility_unavailable_reason.eq("missing_prior_daily_atr_or_close").all()

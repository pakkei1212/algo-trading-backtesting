"""Descriptive cash regimes with causal inputs and frozen training thresholds."""

from datetime import date

import numpy as np
import pandas as pd

from mnq_research.cash_indicators import PriorCash

FEATURE_VERSION = "cash_regimes_v1"


def feature_frame(bars: pd.DataFrame, history: PriorCash) -> pd.DataFrame:
    local = bars.tz_convert("America/New_York").between_time("09:35", "16:00")
    pieces = []
    for day, session in local.groupby(local.index.date, sort=True):
        close = session.close.astype(float)
        path = close.diff().abs().rolling(12, min_periods=12).sum()
        efficiency = (close - close.shift(12)).abs() / path.where(path > 0)
        prior = history.daily.get(day)
        volatility = prior[1] / prior[0] if prior and prior[0] > 0 else np.nan
        minutes = session.index.hour * 60 + session.index.minute
        direction = np.sign(close - float(session.open.iloc[0]))
        result = pd.DataFrame({
            "session": day.isoformat(),
            "volatility": volatility,
            "efficiency": efficiency,
            "direction": direction.map({-1: "down", 0: "unchanged", 1: "up"}),
            "time_bucket": np.where(minutes < 660, "before_11",
                                    np.where(minutes < 840, "11_to_14", "from_14")),
        }, index=session.index)
        result["efficiency_unavailable_reason"] = np.where(
            close.shift(12).isna(), "fewer_than_13_cash_closes",
            np.where(path.eq(0), "zero_price_path", ""),
        )
        result["volatility_unavailable_reason"] = (
            "" if np.isfinite(volatility) else "missing_prior_daily_atr_or_close"
        )
        pieces.append(result)
    if not pieces:
        raise ValueError("No cash observations for regime features")
    return pd.concat(pieces).tz_convert("UTC")


def fit_thresholds(
    features: pd.DataFrame, eligible_dates: frozenset[date], start: str, end: str
) -> dict:
    dates = pd.to_datetime(features.session).dt.date
    selected = features.loc[
        dates.isin(eligible_dates) & features.session.between(start, end)
    ]
    if selected.empty:
        raise ValueError("No eligible training observations for thresholds")
    thresholds = {}
    for name in ("volatility", "efficiency"):
        sample = (
            selected.drop_duplicates("session")[name] if name == "volatility" else selected[name]
        )
        sample = sample[np.isfinite(sample)]
        thresholds[name] = {
            "count": len(sample),
            "q33": float(sample.quantile(.33, interpolation="linear")) if len(sample) else None,
            "q67": float(sample.quantile(.67, interpolation="linear")) if len(sample) else None,
        }
    return {
        "feature_version": FEATURE_VERSION, "training_start": start, "training_end": end,
        "quantiles": [.33, .67], "method": "linear", "thresholds": thresholds,
        "boundaries": "low <= q33; middle > q33 and <= q67; high > q67",
        "ties": "equal thresholds produce an empty middle category",
    }


def classify(features: pd.DataFrame, fitted: dict) -> pd.DataFrame:
    result = features.copy()
    for name, limits in fitted["thresholds"].items():
        value = result[name]
        if limits["q33"] is None or limits["q67"] is None:
            result[f"{name}_label"] = "unavailable"
            result[f"{name}_unavailable_reason"] = "no_valid_training_threshold"
        else:
            result[f"{name}_label"] = np.where(
                ~np.isfinite(value), "unavailable",
                np.where(value <= limits["q33"], "low",
                         np.where(value <= limits["q67"], "middle", "high")),
            )
    return result

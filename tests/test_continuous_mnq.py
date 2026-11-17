"""Guard against replacing observed AMP bars or accepting inconsistent overlap."""

import pandas as pd
import pytest

from mnq_research.amp_mt5 import BAR, utc
from mnq_research.continuous_mnq import GAP_END, GAP_START, repair_gap


def test_repair_only_fills_documented_gap() -> None:
    start, end = utc(GAP_START), utc(GAP_END)
    gap = pd.date_range(start, end, freq=BAR, name="timestamp")
    index = pd.date_range(start - 6 * BAR, end + 6 * BAR, freq=BAR, name="timestamp")
    ibkr = pd.DataFrame(
        dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=100.0), index=index
    )
    amp = ibkr.drop(gap)
    patch, report = repair_gap(amp, ibkr)
    pd.testing.assert_frame_equal(patch, ibkr.loc[gap])
    assert report["exact_ohlcv_matches"] == 12
    with pytest.raises(ValueError, match="preconditions"):
        repair_gap(ibkr, ibkr)
    different = ibkr.copy()
    different.loc[index[0], "high"] += 0.25
    with pytest.raises(ValueError, match="overlap"):
        repair_gap(amp, different)

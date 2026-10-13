# MVP verification — 12 October 2026

## Environment

- Python 3.12.14, NautilusTrader 1.231.0, Windows x64.
- Isolated project virtual environment; exact packages in `requirements.lock`.
- 18 pytest tests passed, including temporary-workspace dashboard save/run tests.
- Ruff lint/format and the configured three-module mypy check passed.
- `pip check` reports no broken requirements.
- NautilusTrader emits an upstream pandas `Timestamp.utcnow` deprecation warning;
  the backtests complete. Do not upgrade the engine/pandas without rerunning the tests.

## Actual market data

Yahoo's `MNQ=F` endpoint supplied 5,691 valid hourly bars, from
2025-10-12 23:00 UTC through 2026-10-12 08:00 UTC. The one-year response included
1,531 excluded null, unfinished, or irregular snapshot rows. There are 253 gaps longer
than one hour; scheduled exchange closures are included in this count.

Raw response and metadata are under `data/yahoo-mnq-1y/`. Nothing was gap-filled.
The series is a **rolling proxy**, not verified dated-contract history. It is suitable
for exploring software behavior and strategy ideas, not proving executable MNQ returns.

## Original baseline result

`runs/20261012T085712-3c0ecf13/` uses the original USD 10,000 / 0.5% risk profile, now preserved in
`configs/conservative_baseline.yaml`.
It takes **zero trades**: each qualifying ATR stop plus estimated costs exceeds the
USD 50 starting risk budget. Final equity is USD 10,000. This is not evidence of profitability
or robustness. The application records the skipped-entry reasons.

## Separate execution diagnostic

`runs/20261012T085721-01f0299d/` uses `fixed_contract_diagnostic.yaml`, one contract per
entry. This explicitly bypasses percentage-risk sizing and is not a recommended risk profile.
It closes three losing trades, pays USD 6 commission, and ends at USD 9,240.50
(USD -759.50 net P&L). The drawdown halt activates; maximum observed drawdown is about
10.245% from the equity high-water mark. Costs and margin are illustrative assumptions.

These runs verify the workflow; they are **not an independent holdout experiment**.
No optimization or parameter selection was performed. A useful strategy evaluation requires
enough actual trades and a reserved period selected before further tuning.

Earlier development runs are retained under `runs/development_archive/` and are excluded
from the dashboard. Their custom slippage model was superseded by the verified native model.

## Scope remaining outside this MVP

Dated-contract acquisition, rollover, exchange maintenance-margin modeling, more than one
tick of slippage, hybrid switching, Docker deployment, Pine parity, and IB paper execution.
The research application, import paths, saved-run comparisons, and coding guides are present.

## Revised exploratory default

At the user's request, `configs/baseline.yaml` and model defaults now use 2% risk
and a 1×ATR stop. Initial equity remains USD 10,000 and the drawdown halt remains 10%.
These settings were changed for affordability, not selected through an optimization search.

One verification run, `runs/20261012T093731-1759ea12/`, completed 10 trades with no
positions remaining open. Final equity was USD 11,889 (USD 1,889 net P&L), win rate
30%, and maximum observed drawdown 10.204%; the drawdown halt activated. The run
paid USD 22 commissions. This small exploratory sample on previously inspected data
is not evidence of profitability or an independent evaluation.

The dashboard shows the saved configuration's one-contract affordability ceiling,
entry signals, skipped entries, submitted entries, completed trades and positions
still open at the end. Existing saved configurations and old run results are unchanged.

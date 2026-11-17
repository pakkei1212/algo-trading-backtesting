# Strategy parameter optimization plan

The goal is to compare MNQ strategy parameters on earlier data, then see how
the selected settings perform on later data. Optimization uses the same
NautilusTrader runner as ordinary backtests, with the preset's fees, slippage,
position sizing, and strategy drawdown halt.

## Implemented milestones

| Milestone | Design and acceptance |
|---|---|
| 1. Share the native runner | Load and validate the dataset, then use the same execution and accounting for compact trial results and ordinary saved reports. Check parity on identical settings and dates. |
| 2. Local grid search | Enumerate valid Fast SMA, Slow SMA, and ATR stop multiplier combinations in stable order. Reject Fast SMA values greater than or equal to Slow SMA for each pair. Run one sequential worker with durable SQLite results, progress, and cancellation between trials. Resume from completed trials while the data and code fingerprints still match. |
| 3. Optimize Parameters tab | Select the dataset, SMA preset, search values, objective, qualification rules, and UTC periods. Show results, drawdown versus P&L, the SMA sensitivity heatmap, training and validation comparisons, and candidate export or reuse. |
| 4. Optuna TPE | Select a training budget and random seed. Sample numeric SMA periods and stepped ATR values sequentially with Optuna 4.6.0. Avoid duplicate simulations and replay saved training results to resume the same seeded sequence. Grid Search remains the default. |
| Continuous MNQ extension | Load the versioned continuous dataset and reuse the ordinary flatten-before-roll path. Check each period and its longest required warm-up, along with missing execution bars and scheduled flattening bars, before launching a study. Recheck in the worker. |

## Search and qualification

The starting grid is Fast SMA **5, 10, 15, 20, 25**, Slow SMA
**30, 50, 75, 100, 150**, and ATR multiplier **1, 1.5, 2, 2.5**.
Users can enter lists or stepped ranges. The default grid contains 100
combinations. The app calculates the trial count and caps the grid at 200.

TPE uses inclusive integer SMA bounds and a stepped ATR multiplier.
The defaults are Fast SMA 5–25 bars, Slow SMA 30–150 bars, and ATR 1.0–2.5
in 0.1 increments. These define 40,656 valid combinations. The SMA ranges
can overlap, with only Fast < Slow pairs eligible. The ATR maximum must align
with the step. The optimizer creates a configuration only when it needs one.

The default budget is 30 unique training trials, capped at 200, and the
default seed is 0. Install the optional local dependency with
`python -m pip install -e ".[optimization]"` and select **Optuna TPE** under
**Optimization method**. Existing candidate-list studies remain readable.

The sampler sees only completed training results and their qualification status.
It skips previously evaluated combinations and falls back to an unseen one if
repeated suggestions stall. SQLite stores evaluation order and scores. On
resume, the worker replays the saved sequence using the same seed and pinned
Optuna version. Validation and holdout use the same rules as Grid Search.

The default objective ranks qualifying candidates by training net P&L.
Return divided by maximum drawdown is also available. Ties favor lower
drawdown, then stable candidate order. Keep capital, sizing, commission,
slippage, margin, and the preset's exit settings fixed during the study.

Default qualification requires no strategy halt, at most 10% maximum
drawdown, and at least 30 closed training trades or 15 closed validation
trades. The strategy's drawdown halt controls when trading stops during a
simulation. The optimizer's drawdown ceiling controls which results qualify.

## Chronological evaluation

1. **Training** evaluates the grid on earlier bars and ranks qualifying
   results using the selected objective.
2. **Validation** evaluates the original baseline and up to five training
   qualifiers on later bars. It preserves the training rank. Validation may
   inform the user's final candidate choice.
3. **Optional holdout** reserves a third, later period. The study locks one
   candidate that reached validation before revealing its holdout result.

Each period starts flat with the preset's starting equity. Indicator warm-up
uses preceding observations. Open positions at the end are marked to market.
In continuous runs, equity and halt state carry through rolls within each
period. The current grid targets the SMA baseline.

For short histories, treat the study as exploratory and review coverage, trade
counts, drawdown, and neighboring settings. The original longer-history
proposal was a chronological 60/20/20 training, validation, and holdout split.
The app lets users choose the periods explicitly.

## Deferred milestones

| Milestone | Proposed work |
|---|---|
| 5. Longer-history validation | Add walk-forward evaluation with each search restricted to its preceding training window. Test stability across periods, costs, and position sizing. Preserve the final holdout until the candidate selection is locked. |

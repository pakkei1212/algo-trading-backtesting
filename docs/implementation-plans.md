# Implementation plans and status

This index records the plans carried out in the MNQ research project and the work
still open. Status refers to the local `develop/post-mvp` work, with cloud scope
called out separately. Local datasets, runs, and research artifacts are stored
outside Git.

| Plan | Status | Reference |
|---|---|---|
| MNQ research MVP | Implemented | [MVP validation](mvp-validation.md), [baseline rules](strategy.md) |
| Strategy Tester and chart workspace | Implemented | [Strategy Tester](strategy-tester.md), [verification](strategy-tester-verification.md) |
| GitHub publishing and branch workflow | Established | [Contribution guide](../CONTRIBUTING.md) |
| Streamlit Community Cloud sharing | Implemented for the hosted app workflow | [Cloud setup](streamlit-cloud.md) |
| Swing strategy condition builder | Implemented | [Builder design](strategy-builder.md), [verification](strategy-builder-verification.md) |
| Multiple timeframes and Yahoo archive | Implemented | [Data setup](../README.md#get-actual-market-data) |
| R-based trailing exit | Implemented | [Baseline rules](strategy.md) |
| Strategy parameter grid search and Optuna TPE | Implemented locally | [Optimization plan](optimization-plan.md) |
| IBKR historical backfill | Completed for the reviewed contracts | [IBKR plan](#ibkr-dated-contract-backfill) |
| AMP MT5 ingestion and audit, M1–M4 | Implemented locally | [AMP plan](#amp-mt5-ingestion-and-audit) |
| Continuous MNQ, stages 1–2 | Implemented locally | [Continuous MNQ plan](#continuous-mnq-and-roll-aware-execution) |
| Continuous MNQ optimization | Implemented locally | [Optimization plan](optimization-plan.md) |

## MVP, chart, and deployment

- Use validated real market data, YAML strategy presets, and the pinned
  NautilusTrader engine. Record fees, slippage, position sizing, equity, and
  the permanent drawdown halt. Keep CLI and dashboard runs on the same engine.
- Present a price chart with linked trades, timeframe and timezone controls,
  performance metrics, run settings, and diagnostics. Keep saved runs
  read-only and allow explicit reuse of their settings. Use descriptive
  run labels and metric definition tooltips in Overview and Performance.
- Keep supplied presets in Git and personal configurations, downloaded data,
  and run outputs local. Use `main` for the hosted MVP and
  `develop/post-mvp` for development.
- Run the hosted app with session-specific uploads and outputs, data and
  workload limits, and downloads for results. Follow the deployment guide for
  access settings. Keep broker terminals and local broker archives outside the
  hosted workflow.

## Strategy construction and risk

- Start the swing strategy builder with editable trend pullback criteria,
  separate long and short rules, market filters, and ALL/ANY condition groups.
  Keep lookbacks in bars and show the nominal duration for the chosen interval.
- Keep the SMA crossover baseline available. Add the optional R-based trailing
  stop alongside the initial ATR stop and opposite crossover exit. Derive 1R
  from the entry fill and initial stop. Use completed closes for activation
  and tightening. Default to disabled, with 2R activation and a 1R trail
  distance when enabled. Record activation, accepted updates, and exit reason.
- Preserve the distinction between the per-trade sizing risk, the simulation's
  drawdown halt, and the optimizer's qualifying drawdown ceiling.

## Historical data and timeframes

- Accept 5m, 15m, 30m, and 1h data. Store UTC bar closes and derive the next
  observed opening event from the selected interval. Preserve source metadata,
  contract expiry, checksums, and gap counts.
- Accumulate Yahoo's 5m MNQ rolling data by retaining original snapshots and
  updating the merged view from new observations. Keep Yahoo labeled as a
  rolling research proxy. Keep dated broker contracts separately identifiable.

### IBKR dated-contract backfill

1. Use an authenticated paper TWS session and its existing data access to
   request active and expired CME MNQ 5m trade bars. Verify the contract
   identity, timestamps, and overnight coverage.
2. Download historical windows per quarterly contract, checkpoint each response,
   retry timeouts and empty windows, and deduplicate by timestamp. Save one
   archive per dated contract and preserve the original samples.
3. Publish separate app datasets with native OHLCV and expiry metadata. Exclude
   observations at or beyond the verified trading cutoff from the app copies.

The reviewed backfill covered nine quarters from MNQZ4 through MNQZ6. Provider
availability and the presence of individual bars are different checks. Far-dated
months can contain extended gaps and flat, low-volume observations, so use
nearer-expiry periods when assessing historical execution quality.

### AMP MT5 ingestion and audit

1. Discover AMP demo quarterly MNQ symbols through the local Windows terminal
   and verify their exchange, expiry, tick size, and tick value. The initial
   audited set is MNQZ25, MNQH26, MNQM26, MNQU26, and MNQZ26.
2. Request M5 trade bars in bounded windows. Keep raw Parquet checkpoints and
   hashes. Convert UTC opens to UTC closes, preserve real and tick volume
   separately, and exclude incomplete or out-of-window observations from
   published data.
3. Audit expected regular-session bars, missing observations, special sessions,
   and quality flags. Display the audit request and actual observed range
   separately. Count missing bars from the first observed close through the
   effective cutoff. Preserve the original reports when displaying revised counts.
4. Publish immutable dated datasets. Require evaluation and indicator warm-up
   inside one verified interval. Use real contract volume and the existing
   native backtester. Show source and coverage in the local app.

Individual AMP datasets use a conservative regular-session audit. The continuous
runner has its own reviewed execution calendar for the stitched range.

## Continuous MNQ and roll-aware execution

1. **Build and audit.** Stitch the five lead contracts at the first scheduled
   Globex open of each CME roll trade date. Keep native OHLCV and attach the
   original contract and provider to every row. Publish a separate Parquet
   artifact, roll manifest, and coverage report. The reviewed dataset has
   82,202 rows from September 2025 through the November 2026 archive cutoff.
2. **Repair the one known source gap.** AMP returned no bars after retries for
   ten MNQU26 closes on August 5, 2026, at 05:10–05:55 UTC. Use the same dated
   IBKR contract for those ten rows after comparing the surrounding OHLCV.
   Tag the replacements and record the overlap differences and source hashes.
3. **Verify the execution calendar.** Review holiday hours, daily breaks, and
   the November 28, 2025 exchange outage. Block windows that cross an
   unexplained missing execution bar or unverified session. The reviewed
   stitched archive passed the execution coverage check.
4. **Execute by dated instrument.** On one native NautilusTrader account, stop
   entries and close any outgoing position at the open of its last scheduled
   trading session. Cancel outstanding outgoing orders. Warm up the incoming
   contract on its own prior bars and require a fresh signal. Carry account
   equity, its high-water mark, and any drawdown halt through the roll. Show
   switches on the price chart and identify the contract on trade reports.
5. **Verify behavior.** Compare a single-contract slice with the dated runner
   on identical bars. Check native roll fills and costs, risk state carry,
   all four switch markers, and preservation of input checksums.

See the [calendar sources and execution notes](../README.md#continuous-mnq-backtests).

## Local parameter optimization

1. **Share the native runner.** Prepare the selected historical dataset, run
   each candidate with the same simulation as an ordinary backtest, and store
   compact trial summaries. Pin dataset identity and source versions to the study.
2. **Search an explicit grid.** Enumerate valid Fast SMA, Slow SMA, and ATR
   multiplier combinations, with Fast SMA shorter than Slow SMA. Keep costs
   and sizing fixed to the preset. Show the exact trial count and cap the
   grid at 200 combinations. Use a sequential local worker with durable
   results, cancellation between trials, and resume support.
3. **Train and validate chronologically.** Rank eligible training results by
   net profit or return divided by drawdown. Check trade minimums, drawdown,
   and halt status. Evaluate the original baseline and up to five training
   qualifiers on later validation bars. Preserve the training rank. For a
   reserved holdout, lock one validated candidate before revealing results.
4. **Expose results and settings.** Show ranked trials, risk and P&L, a
   sensitivity heatmap, paired training and validation results, and the
   baseline validation metric. Export study data and copy selected settings
   into a draft for a separate run.
5. **Add continuous support.** Use the shared flatten-before-roll runner for
   trials, validation, and generated reports. Before launch, verify chronological
   windows, the largest required warm-up, source integrity, and the needed
   execution bars at each roll. Identify the failing period and disable Start
   when those checks fail. Recheck in the worker.

## Deferred plans

The current local workflow ends at dated and continuous research, backtesting,
and parameter search. Later work discussed includes walk-forward
optimization, longer-history robustness studies, additional swing templates
and higher timeframe signals, and IBKR paper execution. Each requires its own
scope decision and verification plan. No active implementation is recorded
for these items in this index.

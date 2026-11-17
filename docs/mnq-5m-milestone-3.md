# Milestone 3: historical diagnostics and execution experiments

This is research on a development-exposed archive, not locked or prospective evidence.

## Reference and source identity

Work continued on `develop/post-mvp` from commit
`b112173511c3f9b574108fc1d173d93f217fd296`, with existing uncommitted changes.
The continuation's starting file list is
`runs/cash-research/m3-completion-start-tree.txt`. Existing files and studies were
preserved. The ending source fingerprint is stored in the final study manifest;
the Git commit alone does not identify the uncommitted implementation.

The original Milestone 2 comparison remains in
`runs/cash-research/milestone-2-verified`. Its eight independently reset outer
windows returned policy −$784.50 versus SMA +$559.00. April selected slot
momentum, May selected VWAP reversion, and the other six months selected cash.
The original study's SHA-256 is embedded in every new study manifest.

## Experiment recipe

Recipe `m3_fixed_trail_stress_persistent_v1` uses the frozen Milestone 2 calendar,
windows, base settings, qualification gates and 10% permanent drawdown halt.
Each run starts with $10,000 unless it explicitly carries a continuous account.
All execution and accounting use pinned NautilusTrader 1.231.0.

For each as-of date, the two highest training-P&L configurations for each of ORB
and slot momentum are shortlisted (tie breaks: lower drawdown, then stable key).
The shortlist deliberately includes rejected training leaders to diagnose the
effect of exits. Each gets an off/on trailing comparison: eight training trials
per fold, with no expanded parameter search. Only configurations passing the
unchanged gates can advance; at most two per family enter validation. Original
VWAP reversion and late-momentum validation candidates remain available to the
experimental selector. Selection uses validation only before the outer window.
This bounded shortlist is a separately identified recipe; it does not replace
the original Milestone 2 selections.

Both off/on versions of all four shortlisted configurations also run in each
outer window as descriptive comparisons, including those rejected earlier.
Their retrospectively profitable outcomes never feed selection. The original
base-selected monthly policy and normalized SMA are rerun at all three cost
levels. There is no cost-specific reselection.

## Signal-time regimes

Feature definition `cash_regimes_v1` uses prior completed same-contract cash ATR(14)
divided by prior close, current 12-interval efficiency requiring 13 cash closes,
direction since the cash open, and nonoverlapping New York buckets before 11:00,
11:00–before 14:00, and 14:00 onward. The first efficiency observation is 10:35.

Linear 33rd/67th percentiles are fitted only on each fold's inner training data.
Volatility contributes one observation per eligible session; efficiency uses the
fixed five-minute cash grid. Low includes q33, middle includes q67, and equal
thresholds leave no middle category. Missing values carry reasons. Thresholds
are frozen for validation and the outer window.

Entry decision records and associated fills retain a common signal identifier,
timestamp, raw features, labels and unavailable reasons. Trade reports link
these to native positions. A dimension is reported separately with trade/day
counts, net P&L, expectancy, win rate and daily block-bootstrap uncertainty.
The daily series includes zero contribution on other eligible days. Groups with
fewer than 30 trades or 20 active days are explicitly inconclusive. No regime
filter is enabled by these descriptive reports.

## Trailing exit conventions

`extreme_1R_2entryATR_v1` preserves the initial protective stop. R is actual fill
to the rounded initial stop. Signal ATR is captured once as the fixed scale.
The first completed close at or beyond +1R activates the trail. Longs use the
highest post-entry high; shorts use the lowest low, including the entry bar.
The stop trails that extreme by two signal ATR. Long stops round down and short
stops round up to valid ticks, preserving at least the requested distance.
Stops never loosen.

Native execution tests the prior stop before the completed bar can amend it.
An amendment is effective from the next bar. A proposed stop at/beyond the
current close schedules a next-open exit while retaining the existing protection.
An already-filled stop wins; otherwise pending signal, scheduled, roll or risk
exits take precedence over an amendment. Rejected amendments retain the old
native stop and its event history. This is distinct from the SMA best-close/R
trailing policy.

Favourable-excursion giveback is a bar estimate, not an intrabar measurement.
It includes the entry bar and excludes an exit bar whose internal chronology
cannot be established. A stop filled at the bar-close timestamp therefore
excludes that entire bar. Giveback subtracts native net P&L, including fees.

## Costs and accounting

| Scenario | Fee per side | Adverse ticks per fill |
|---|---:|---:|
| Base | $1.00 | 1 |
| Middle | $1.50 | 2 |
| High | $2.00 | 4 |

The native order-book fill hook (`adverse_tick_liquidity_v1`) shifts buys upward
and sells downward. MNQ is $2 per point and $0.50 per tick per contract. In-bar
stop fills reference the trigger; next-open gaps reference the observed open.
There is no second spread charge or additive P&L deduction. Middle/high are full
execution reruns, so changed fills can alter stops, trades and halt timing.
Base preserves the original pinned native one-tick model, with hand-calculated
parity fixtures for ordinary and gap-through stops.

Drawdown uses running marked-equity high-water as denominator, observed each
five-minute close, including unrealized P&L. A 10% threshold queues next-open
liquidation, so observed drawdown may exceed 10%. April SMA's $10,341 peak and
$9,293.50 marked equity at 2026-04-29 17:15 UTC produce 10.12958% drawdown.
The subsequent liquidation and fees are native records; the threshold is not a
guaranteed maximum loss or a per-trade risk amount. Flat dates after a halt
remain in the paired daily series.

Reset-fold results restart capital/high-water every outer window. Continuous
accounts transfer actual flat closing cash/equity, high-water and permanent-halt
state into the next native window. They use the original monthly selections,
not the trailing selector. Cash or already-halted windows have explicit zero
daily P&L, unchanged equity and no fills. No overnight positions are carried;
the existing intraday liquidation and flatten-before-roll rules remain active.

## Uncertainty and periods

`circular_stationary_bootstrap_v1` samples paired eligible days, including flat
days, with geometric mean block lengths 3, 5 and 10; 2,000 replications; seed 0;
linear 2.5th/97.5th percentiles. Fold intervals remain separate. The chronological
series allows blocks to cross fold boundaries and circularly wrap the sample.
Reset and continuous-account series are separately labeled. April–October
(148 sessions) is the primary complete-month report; adding November 2–10
(seven sessions) is supplemental. These overlapping reports are not independent
evidence. Short folds and intervals spanning zero are inconclusive.

## Persistence and UI

Completed trials use the existing SQLite result/failure tables and stable keys.
A new optional `snapshot` table commits the complete multi-fold summary atomically.
The UI reads that snapshot when available; older studies remain supported. JSON
is a recoverable export. A Windows file-replacement denial falls back to writing
the export after the atomic database commit; readers skip incomplete exports.
An exact manifest match is required to resume, including source/data fingerprints.
Changing source or inputs requires a new study folder.

The local app's **Research studies** tab provides Qualification, Regimes, Trailing
exits, Execution costs, Account replay, Uncertainty, Trades and chart, and
Provenance. Select the study and as-of date. Qualification distinguishes original
checks from the trailing recipe. Trade drill-down opens the existing Strategy
Tester and recorded order events. Locked and prospective stages remain pending.

## Files

- `cash_strategy.py`: signal context and native experimental trailing lifecycle.
- `backtest.py`: optional regime context, stressed fills and persistent risk state.
- `execution_stress.py`: pinned native adverse-tick fill extension.
- `regimes.py`: causal features and training-only threshold fitting.
- `research_diagnostics.py`: qualification and deterministic paired bootstrap.
- `milestone3.py`: registered experiments, resume and atomic snapshots.
- `experiment_ui.py`, `research_ui.py`: study comparison and existing report links.
- `tests/test_milestone3_execution.py`, `tests/test_regimes.py`,
  `tests/test_research_diagnostics.py`: focused verification.

Generated artifacts remain local and outside Git. Original downloads, contracts,
Milestone 1/2 studies, and partial Milestone 3 attempts are preserved.

## Final comparison

All eight folds completed, with 148 April–October sessions and seven supplemental
November sessions. The table includes November. P&L is net of modeled execution
costs. Reset-account drawdown is the worst individual fold.

| Account and costs | Selected policy | Cash-session SMA | Paired difference |
|---|---:|---:|---:|
| Reset, base | −$784.50 | +$559.00 | −$1,343.50 |
| Reset, middle | −$820.00 | +$379.50 | −$1,199.50 |
| Reset, high | −$872.00 | −$661.00 | −$211.00 |
| Continuous, base | −$784.50 | −$704.50 | −$80.00 |

| Outer period | Sessions | Selection | Policy P&L | Reset SMA P&L |
|---|---:|---|---:|---:|
| Apr 1–30 | 21 | Slot momentum, original exit | −$259.00 | −$704.50 |
| May 1–29 | 20 | VWAP reversion | −$525.50 | −$719.50 |
| Jun 1–30 | 21 | Cash | $0 | +$1,029.50 |
| Jul 1–31 | 22 | Cash | $0 | −$105.50 |
| Aug 3–31 | 21 | Cash | $0 | +$79.00 |
| Sep 1–30 | 21 | Cash | $0 | +$652.00 |
| Oct 1–30 | 22 | Cash | $0 | +$340.50 |
| Nov 2–10 | 7 | Cash | $0 | −$12.50 |

The original recipe had training qualifiers for June and July (13 and 7), but
none qualified in validation. August through November had zero training
qualifiers. The experimental ORB/momentum shortlist produced 12 training
qualifiers out of 64 trials and one validation qualifier out of eight trials.
It selected exactly the original monthly choices, with neither trailing variant
selected. Across 16 descriptive pairs per family, the trail improved 5 ORB pairs
and 8 momentum pairs. Summed changes across those overlapping comparisons were
−$8,920.50 for ORB and −$2,966.00 for momentum. These sums describe comparative
runs on repeated dates rather than portfolio returns.

The continuous SMA finished at $9,295.50 after 30 trades and stayed halted after
April 29. Its worst drawdown was 10.13%. The selected policy finished at
$9,215.50 after 19 trades, with 8.34% drawdown and no halt. Under monthly resets,
SMA took 182 base-cost trades and halted in five windows. At middle and high
costs it took 174 and 171 trades and halted in six windows each.

| Paired mean daily P&L, policy minus SMA | April–October 95% interval | Including November 95% interval |
|---|---|---|
| Reset accounts | −$9.16 [−$38.05, +$19.63] | −$8.67 [−$38.40, +$19.32] |
| Continuous accounts | −$0.54 [−$10.86, +$9.54] | −$0.52 [−$9.75, +$8.60] |

These are five-session mean block intervals. All four include zero. The 104
regime groups for the base selected policy and SMA were inconclusive at the
fold level. The selected policy was active on 17 of 155 sessions, which limits
the evidence for regime effects. The study found no durable improvement across
windows and costs. Locked and prospective evaluation remain pending.

## Completion evidence

- [x] Eight final historical folds in `runs/cash-research/milestone-3-final`.
  SQLite contains 64 training, eight validation, and 96 historical simulations,
  plus eight selection records and zero failed trials.
- [x] Original base-cost P&L, drawdown, halt timing, trades, fees, modeled
  slippage and daily P&L reconciled across all eight folds. Original files retained.
- [x] Signal labels and training-only thresholds, regime reports, 32 paired
  trailing comparisons, three native cost scenarios and two account conventions
  saved with their provenance. See `m3-final-comparison.json` and the `m3-*.csv`
  reports under `runs/cash-research`.
- [x] Independent April replay reproduced fills, decisions and daily P&L with
  and without later-bar mutation. Resume reproduced summary SHA-256
  `c7c730aa566d0b108e2d8a95a11892bb1277cea484cf858d8108351558b900cf`.
- [x] 123 Python tests, Ruff, configured mypy, seven chart tests and TypeScript
  checks passed. Automated UI verification covered 72 historical fold/view checks
  and concurrent Strategy Testers in the full app.
- [x] Browser smoke test opened the final study and displayed the April ORB
  five-minute candlestick chart with its Strategy Tester controls.

The simulation source fingerprint was
`4eee29de19b834969c4bc6d320456361f7b0fdbbbdde41c8b1e4b4261362503f`.
The browser smoke test found a duplicate panel key when the Research studies
chart and the main Strategy Tester loaded together. `results_ui.py` now scopes
those keys by run ID. The verified application's fingerprint is
`c1224e7eed855ec2203603169d2e109d62859b4beb6f238079326124179c11b4`.
The saved simulation used the prior presentation file retained at
`runs/cash-research/m3-results-ui-at-simulation.py`. The UI fix modifies no
execution inputs. The working tree retains its existing uncommitted changes.

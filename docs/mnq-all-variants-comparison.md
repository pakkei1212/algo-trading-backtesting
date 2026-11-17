# Matched MNQ five-minute variant comparison

In the local app, open **Research studies → All variants comparison** and choose
`all-variants-v1`. The table starts with every fold and configuration. Click a
column heading to sort, use the four filters to narrow the rows, or enable
**Show detailed metrics**. **Download full matched CSV** always exports all rows.
Select a row for dates, settings, five-minute equity and drawdown charts, and
individual trade and candlestick inspection. Use **Selection-policy studies**
to reopen the original Milestone 2 and 3 reports.

OOT means out-of-time evaluation. The OOT window follows validation: April 2026
is the first OOT fold, May is the second, and November 2–10 is the supplemental
eighth fold. The underlying archive is development-exposed. These results are
retrospective research diagnostics.

## Registered recipe

`all_cash_variants_no_drawdown_halt_v1` starts from Milestone 3 commit
`ef0d20a6bf66f040fec8143f3d90069835b80d33`. It registers the 22 original coarse
configurations (nine ORB, six slot momentum, four VWAP reversion, three late
momentum), their 15 approved ORB and slot momentum trailing counterparts, and
the normalized cash-session SMA 20/50 with its original 1× ATR stop. Expanded
Milestone 2 selection candidates stay in their original studies. No additional
configurations were needed to reach 38 distinct IDs.

Each ID hashes the family, version, typed parameters, exit version, and common
execution settings. The same ID applies to training, validation and OOT within
each fold. Qualification is an optional diagnostic and never controls eligibility.
The registry contains 304 matched rows and 912 logical phase results. There are
646 unique execution inputs because seven OOT windows exactly match the next
fold's validation window. Both logical results remain present in the table.

Each run starts at $10,000 and trades one MNQ with a $3,000 margin reserve,
$1 commission per side, and one adverse tick per fill. It uses the original
eligible session calendar, same-contract warm-up and flatten-before-roll rule.
The account drawdown halt is disabled for this recipe. Marked-equity drawdown
and upward crossings of its former 10% threshold remain recorded. Initial
protective stops, configured trailing rules, signal exits, session liquidation,
margin restrictions and protective-order rejection handling stay active.
Existing YAML without an explicit halt setting continues to enable the halt.

Return is net P&L divided by starting capital. Maximum drawdown is measured
at five-minute closes from the running peak, using marked equity including
unrealized P&L. Daily Sharpe uses zero reference return and an annualization
factor of √252, with sample standard deviation and all eligible days including
zero-P&L days. It is undefined at zero variance. Return/drawdown is undefined
at zero drawdown. Costs show fees plus modeled execution friction already
reflected in native net P&L. Expectancy is the mean native net closed-trade P&L.

OOT summaries sum net P&L across independently reset, non-overlapping folds.
Worst drawdown is the largest individual-fold drawdown. Training and validation
periods overlap and their results stay at fold level.

## Local artifacts and resume

From the repository root, start or resume with the same inputs and output folder:

```powershell
.venv/Scripts/python.exe -m mnq_research.variant_comparison `
  runs/cash-research/milestone-2-verified/summary.json `
  data/continuous-research/amp-mnq-continuous-5m-cf199ecf6c638623 `
  runs/cash-research/all-variants-v1 --workers 4
```

The output contains `manifest.json`, `comparison.sqlite`, `comparison.csv` and
native reports. The manifest records the configuration registry, exact phase
windows and eligible dates, source and data fingerprints, and costs. SQLite
tracks queued, running, completed and failed executions, plus attempt history.
The complete native payload is committed atomically. Re-running with the exact
manifest retries incomplete or failed work and reuses completed results.
Presentation-only edits do not affect the execution source fingerprint. New
simulation inputs require a new output folder. Historical results stay local.

The execution source SHA-256 is

```text
15595372336a50b39e6e809caf7c346173d75352b05a50cde042ae1494e2ed5d
```

## Historical OOT results

The eight OOT windows contain 155 sessions, including seven supplemental
November sessions. Sixteen of 38 fixed configurations had positive total OOT
P&L. Across the 304 OOT rows, 117 reached 10% maximum drawdown or more. Individual
fold returns ranged from −20.29% to +33.44%, and maximum drawdown ranged from
0.62% to 24.51%.

| Fixed configuration | Total OOT net P&L | Profitable folds | Trades | Worst fold drawdown |
|---|---:|---:|---:|---:|
| Original ORB, 30-minute range, 1.5× ATR stop | +$4,671.00 | 6 of 8 | 180 | 20.14% |
| Original ORB, 30-minute range, 2× ATR stop | +$4,630.00 | 6 of 8 | 180 | 20.49% |
| Cash-session SMA 20/50, 1× ATR stop | +$938.00 | 4 of 8 | 216 | 13.20% |

The first row has the highest total OOT P&L in this retrospective comparison.
The figures use separate $10,000 accounts for each fold. They describe the
halt-disabled study and retain the development-exposed archive label.

## Verification

All 912 phase results completed from 646 unique native simulations, with zero
failed trials. The 304-row export matches SQLite, and all phase returns,
drawdowns and costs reconcile with native summaries. Resume found zero pending
executions and reproduced the exact CSV. Mutating later bars reproduced the
first fold's native fills, decisions and P&L for two trailing configurations
across all three phases. The 128 Python tests, Ruff, configured mypy checks,
seven frontend tests, TypeScript checks, automated UI and browser checks passed.

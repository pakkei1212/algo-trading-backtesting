# MNQ five-minute walk-forward implementation

## Starting state

Implementation began on `develop/post-mvp` at `b112173511c3f9b574108fc1d173d93f217fd296`. The working tree already contained modified app, backtest, strategy, data and reporting files, as well as untracked Milestone 1, roll-aware execution and optimization modules. The complete pre-edit status is in `runs/cash-research/m2-start-tree.txt`. No branch switch was needed. The study records the committed base and a separate source hash for the working code.

## Milestone 1 drawdown audit

The starting balance was **$10,000**. The strategy marks equity at every observed five-minute close in the verified execution grid. Equity is the native account balance plus any open unrealized P&L. The running high-water mark includes initial equity and carries across dated contracts along with the halt. Drawdown is `1 − current equity / high-water equity`. At the first close at or above 10%, entries are disabled for the rest of that simulation and a liquidation is queued for the next observed open if a position remains. Each training, validation and outer simulation starts independently from $10,000.

On **29 April 2026 at 17:10 UTC**, SMA equity was $9,330.50, down 9.77%. At **17:15 UTC**, equity including the open short was **$9,293.50**, against the **$10,341.00** high-water mark. The $1,047.50 peak-to-current loss was **10.129581%**. The bar-close check samples discrete prices, so the first breach can exceed the threshold. The next-open buy to close was stamped **17:15:00.000000001 UTC** at **27,204.75** for one MNQM26 contract. The simulated exit charged **$1.00** in fees and **$0.50** in adverse slippage. The trade lost **$14.00** inclusive of its $2 round-trip commissions. Equity at the next mark was $9,295.50 and stayed there. April 30 remained a scored session with **$0 P&L**. The April replay had 21 session marks, including five with no entry, and $60 in commissions plus $30 in modeled slippage over 30 round trips. The slippage is embedded in executed prices.

The recorded breach and subsequent liquidation matched the plan. No correction to the 10% rule or original April result was required. The original artifacts are in `runs/cash-research/first-fold-verified`. `runs/cash-research/milestone-1-halt-audit` is a separate instrumentation replay and preserves the original outputs.

| Milestone 1 case | First halt close (UTC) | Additional halt liquidation |
|---|---|---|
| ORB 5 minutes, ATR 1×, 1.5×, 2× | 21 Nov 2025 at 15:30, 15:00, 15:05 respectively | Already flat |
| ORB 15 minutes, ATR 1× | 5 Dec 2025 at 15:55 | Already flat |
| ORB 15 minutes, ATR 1.5× or 2× | 21 Nov 2025 at 15:30 | Already flat |
| ORB 30 minutes, ATR 1× | 29 Jan 2026 at 17:25 | Already flat |
| ORB 30 minutes, ATR 1.5× | 29 Jan 2026 at 17:30 | Bought 1 MNQH26 at 25,839.50 |
| ORB 30 minutes, ATR 2× | 29 Jan 2026 at 17:25 | Bought 1 MNQH26 at 25,845.25 |
| SMA March | 9 Mar 2026 at 19:20 | Already flat |
| SMA April | 29 Apr 2026 at 17:15 | Bought 1 MNQM26 at 27,204.75 |

Each additional liquidation occurred at the next observed open, one nanosecond after the
decision timestamp under the runner's event convention. Each charged $1 in commission and
filled 0.25 points ($0.50 per contract) above the reference opening trade price. For each
row, the independent detailed rerun matched the corresponding audit summary's P&L,
drawdown, halt time and fill hash. See
`runs/cash-research/milestone-1-halt-audit/liquidation-audit.json` for exact fills and
report references.

## Implementation and replay

- `cash_indicators.py` defines the B/C/D schemas and constructs prior same-contract slot volatility and daily Wilder ATR. Today’s bars are added to histories only after emitting today’s lagged feature values. Tick volume is excluded from VWAP.
- `cash_strategy.py` adds the scheduled slot momentum, VWAP reversion and late-session momentum decisions to the existing cash and native order path. Family identifiers carry the `_cash_v1` version. Cash SMA and ORB retain their Milestone 1 definitions.
- `backtest.py` records halt times, flat time and deterministic native fill and decision hashes. It reuses the checked split market data across trials. Cash session coverage and the 10% rule remain common.
- `walk_forward.py` runs the eight dated folds, beginning with the 22 preset combinations in each training window. It refines stop distances for up to two qualifying B/C/D shapes per family and validates at most two training-ranked configurations per family. The committed as-of selection precedes all outer simulations. Each family also has a fixed-default outer diagnostic if it had no validated candidate. These diagnostic results are excluded from selection.
- `research_exposure.py` imports recorded prior split uses from local optimization, ordinary runs and cash replay artifacts. Exposure records describe the available local history. Manual analysis outside these artifacts is only covered by the archive-wide development-exposed designation.
- `research_ui.py` and `app.py` add a local Research studies tab with fold, validation and prior-exposure views. Locked evaluation and prospective are separate stages with empty status displays.

Run from the repo root with the pinned environment. Supply the same output directory to resume unchanged inputs.

```powershell
$sha = git rev-parse HEAD
.venv/Scripts/python.exe -m mnq_research.walk_forward data/continuous-research/amp-mnq-continuous-5m-cf199ecf6c638623 runs/cash-research/my-walk-forward --code-commit $sha
```

The study SQLite file commits each result by fold, phase and configuration and appends failure records before aborting a failed trial. Resume retries incomplete trials and verifies source, versions, data, capital assumptions and calendar. The summary is replaced atomically after each fold and contains its phase dates, trial outcomes, rejection reasons, halt timestamps, costs, historical family diagnostics, SMA and paired daily P&L. Flat time uses total observed position duration divided by all scored cash-session minutes. This is a historical experiment with fully exposed archive dates. Final locked and prospective scoring remain pending verified newer data and registration.

## Verification and bounds

The future-mutation check added 1,000 points to every OHLC field on 37,922 stitched bars
from 1 May 2026 onward and applied the same change to native history from that cutoff.
The first fold independently recomputed all training, validation and April outcomes in a
new SQLite database. Its entire result payload matched, including candidate selection and
trial decision and fill hashes. An indicator test also changes the current and future days
and checks that the current day's slot scales and lagged daily ATR are identical.

Resuming the completed study reproduced the same summary SHA-256
`2f16578e54a34cffe3e1bf0b2c49a70844c117da3f52474b5831c5ab3c129790f`
and the same 212 training, 26 validation, 40 historical and eight selection rows.
The training and validation counts include one SMA reference per fold. The database
integrity check passed, and all 40 outer runs shared their fold's daily session keys
with SMA and ended flat. A Streamlit AppTest also exercised all four stage views.

The historical simulator keeps the 10% halt. No alternative risk limit was tested. Regime
filters, post-entry trailing experiments and finer execution validation follow the approved
later milestones. The scope here includes historical stage separation and exposure records
for known artifacts. It does not claim an unseen backtest, a registered prospective study,
or operational paper trading.

## Historical experiment results

The table uses the recorded outer policy. Each fold starts from $10,000. Dollar totals across folds are sums of independent simulations.

| Outer month | Cash sessions | Training evaluated / qualified | Validation evaluated / qualified | Selected family | Policy P&L | SMA P&L | Policy minus SMA |
|---|---:|---:|---:|---|---:|---:|---:|
| 2026-04 | 21 | 26 / 5 | 2 / 1 | Slot momentum | −$259.00 | −$704.50 | $445.50 |
| 2026-05 | 20 | 30 / 10 | 4 / 1 | VWAP reversion | −$525.50 | −$719.50 | $194.00 |
| 2026-06 | 21 | 32 / 13 | 6 / 0 | Cash | $0.00 | $1,029.50 | −$1,029.50 |
| 2026-07 | 22 | 28 / 7 | 6 / 0 | Cash | $0.00 | −$105.50 | $105.50 |
| 2026-08 | 21 | 22 / 0 | 0 / 0 | Cash | $0.00 | $79.00 | −$79.00 |
| 2026-09 | 21 | 22 / 0 | 0 / 0 | Cash | $0.00 | $652.00 | −$652.00 |
| 2026-10 | 22 | 22 / 0 | 0 / 0 | Cash | $0.00 | $340.50 | −$340.50 |
| 2026-11 | 7 | 22 / 0 | 0 / 0 | Cash | $0.00 | −$12.50 | $12.50 |

Summed across the eight independently reset folds, the policy returned **−$784.50**, the cash SMA returned **$559.00**, and the paired difference was **−$1,343.50**.

The complete machine-readable record is in `runs/cash-research/milestone-2-verified/summary.json` and `study.sqlite`. Fixed-default diagnostics were evaluated for comparison after the selection was committed.


### 2026-04 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-02-27 | 83 |
| Validation | 2026-03-02 | 2026-03-31 | 22 |
| Historical | 2026-04-01 | 2026-04-30 | 21 |

Selected Slot momentum with `band_multiplier=1.5`, `confirm_vwap=True`, `history_sessions=30`, `stop_atr=2.0`.

Policy fully flat sessions **15**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | −$704.50 | 10.13% | 30 | $90.00 | 77.2% | 2026-04-29T17:15:00+00:00 |
| Late momentum | Fixed-default diagnostic | $363.50 | 2.01% | 14 | $42.00 | 96.0% | — |
| ORB | Fixed-default diagnostic | $1,653.00 | 4.51% | 31 | $93.00 | 59.0% | — |
| Slot momentum | Selected policy | −$259.00 | 3.19% | 6 | $18.00 | 93.7% | — |
| VWAP reversion | Fixed-default diagnostic | −$185.00 | 9.04% | 23 | $69.00 | 91.8% | — |

Training rejected 21. Rejection reasons can overlap. Drawdown limit or halt (18), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-16 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1).

Validation rejected 1. Rejection reasons can overlap. Insufficient net p&l (1). Recorded halt dates in UTC were none.

### 2026-05 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-03-31 | 105 |
| Validation | 2026-04-01 | 2026-04-30 | 21 |
| Historical | 2026-05-01 | 2026-05-29 | 20 |

Selected VWAP reversion with `band_atr=2.0`, `efficiency_max=0.2`, `stop_atr=2.0`.

Policy fully flat sessions **9**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | −$719.50 | 10.67% | 18 | $54.00 | 81.2% | 2026-05-20T14:15:00+00:00 |
| Late momentum | Fixed-default diagnostic | −$2.50 | 3.13% | 13 | $39.00 | 96.5% | — |
| ORB | Fixed-default diagnostic | $14.50 | 10.05% | 21 | $63.00 | 71.9% | 2026-05-20T14:30:00+00:00 |
| Slot momentum | Fixed-default diagnostic | −$669.00 | 9.61% | 27 | $81.00 | 74.9% | — |
| VWAP reversion | Selected policy | −$525.50 | 7.37% | 13 | $39.00 | 92.3% | — |

Training rejected 20. Rejection reasons can overlap. Drawdown limit or halt (18), insufficient net p&l (8), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-16 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1).

Validation rejected 3. Rejection reasons can overlap. Insufficient net p&l (2), insufficient trades or active days (2). Recorded halt dates in UTC were none.

### 2026-06 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-04-30 | 126 |
| Validation | 2026-05-01 | 2026-05-29 | 20 |
| Historical | 2026-06-01 | 2026-06-30 | 21 |

The selected policy stayed in cash.

Policy fully flat sessions **21**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | $1,029.50 | 11.26% | 6 | $18.00 | 94.7% | 2026-06-09T18:25:00+00:00 |
| Late momentum | Fixed-default diagnostic | −$222.00 | 5.86% | 15 | $45.00 | 95.9% | — |
| ORB | Fixed-default diagnostic | $1,041.00 | 11.25% | 11 | $33.00 | 85.2% | 2026-06-09T18:25:00+00:00 |
| Slot momentum | Fixed-default diagnostic | $680.00 | 10.28% | 9 | $27.00 | 91.7% | 2026-06-09T17:50:00+00:00 |
| VWAP reversion | Fixed-default diagnostic | −$1,042.50 | 10.42% | 10 | $30.00 | 96.8% | 2026-06-17T18:30:00+00:00 |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 19. Rejection reasons can overlap. Drawdown limit or halt (18), insufficient net p&l (7), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-16 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1).

Validation rejected 6. Rejection reasons can overlap. Drawdown limit or halt (1), insufficient net p&l (6). Recorded halt dates in UTC were 2026-05-29 (1).

### 2026-07 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-05-29 | 146 |
| Validation | 2026-06-01 | 2026-06-30 | 21 |
| Historical | 2026-07-01 | 2026-07-31 | 22 |

The selected policy stayed in cash.

Policy fully flat sessions **22**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | −$105.50 | 10.38% | 31 | $93.00 | 74.9% | 2026-07-28T19:05:00+00:00 |
| Late momentum | Fixed-default diagnostic | $245.00 | 6.71% | 16 | $48.00 | 95.3% | — |
| ORB | Fixed-default diagnostic | −$191.00 | 10.13% | 10 | $30.00 | 90.3% | 2026-07-09T14:15:00+00:00 |
| Slot momentum | Fixed-default diagnostic | −$150.00 | 10.02% | 13 | $39.00 | 90.9% | 2026-07-17T16:30:00+00:00 |
| VWAP reversion | Fixed-default diagnostic | −$453.00 | 9.57% | 26 | $78.00 | 90.7% | — |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 21. Rejection reasons can overlap. Drawdown limit or halt (19), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1), 2026-05-07 (1), 2026-05-29 (1).

Validation rejected 6. Rejection reasons can overlap. Drawdown limit or halt (2), insufficient net p&l (4), insufficient trades or active days (2). Recorded halt dates in UTC were 2026-06-09 (2).

### 2026-08 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-06-30 | 167 |
| Validation | 2026-07-01 | 2026-07-31 | 22 |
| Historical | 2026-08-03 | 2026-08-31 | 21 |

The selected policy stayed in cash.

Policy fully flat sessions **21**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | $79.00 | 9.94% | 36 | $108.00 | 63.8% | — |
| Late momentum | Fixed-default diagnostic | $218.50 | 2.49% | 11 | $33.00 | 96.8% | — |
| ORB | Fixed-default diagnostic | $112.00 | 10.24% | 18 | $54.00 | 78.4% | 2026-08-19T15:00:00+00:00 |
| Slot momentum | Fixed-default diagnostic | $8.00 | 7.65% | 15 | $45.00 | 88.6% | — |
| VWAP reversion | Fixed-default diagnostic | $695.00 | 2.84% | 23 | $69.00 | 88.8% | — |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 22. Rejection reasons can overlap. Drawdown limit or halt (21), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1), 2026-05-29 (1), 2026-06-05 (1), 2026-06-08 (1), 2026-06-17 (1).

Validation rejected 0. Rejection reasons can overlap. None. Recorded halt dates in UTC were none.

### 2026-09 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-07-31 | 189 |
| Validation | 2026-08-03 | 2026-08-31 | 21 |
| Historical | 2026-09-01 | 2026-09-30 | 21 |

The selected policy stayed in cash.

Policy fully flat sessions **21**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | $652.00 | 6.40% | 26 | $78.00 | 75.5% | — |
| Late momentum | Fixed-default diagnostic | $203.50 | 2.39% | 13 | $39.00 | 96.1% | — |
| ORB | Fixed-default diagnostic | $327.50 | 6.60% | 25 | $75.00 | 66.1% | — |
| Slot momentum | Fixed-default diagnostic | −$82.00 | 8.56% | 27 | $81.00 | 74.4% | — |
| VWAP reversion | Fixed-default diagnostic | −$239.00 | 4.58% | 27 | $81.00 | 88.3% | — |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 22. Rejection reasons can overlap. Drawdown limit or halt (22), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1), 2026-05-29 (1), 2026-06-05 (1), 2026-06-08 (1), 2026-06-17 (1), 2026-07-14 (1).

Validation rejected 0. Rejection reasons can overlap. None. Recorded halt dates in UTC were none.

### 2026-10 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-08-31 | 210 |
| Validation | 2026-09-01 | 2026-09-30 | 21 |
| Historical | 2026-10-01 | 2026-10-30 | 22 |

The selected policy stayed in cash.

Policy fully flat sessions **22**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | $340.50 | 7.96% | 24 | $72.00 | 76.6% | — |
| Late momentum | Fixed-default diagnostic | −$660.50 | 7.05% | 18 | $54.00 | 95.5% | — |
| ORB | Fixed-default diagnostic | −$1,153.00 | 11.53% | 15 | $45.00 | 95.5% | 2026-10-14T13:55:00+00:00 |
| Slot momentum | Fixed-default diagnostic | $233.50 | 6.49% | 25 | $75.00 | 71.9% | — |
| VWAP reversion | Fixed-default diagnostic | −$283.50 | 8.18% | 23 | $69.00 | 91.6% | — |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 22. Rejection reasons can overlap. Drawdown limit or halt (22), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1), 2026-05-29 (1), 2026-06-05 (1), 2026-06-08 (1), 2026-06-17 (1), 2026-07-14 (1).

Validation rejected 0. Rejection reasons can overlap. None. Recorded halt dates in UTC were none.

### 2026-11 outer fold

| Phase | Actual first session | Actual last session | Sessions |
|---|---|---|---:|
| Training | 2025-10-27 | 2026-09-30 | 231 |
| Validation | 2026-10-01 | 2026-10-30 | 22 |
| Historical | 2026-11-02 | 2026-11-10 | 7 |

The selected policy stayed in cash.

Policy fully flat sessions **7**.

| Outer run | Basis | Net P&L | Drawdown | Trades | Costs | Time flat | Halt UTC |
|---|---|---:|---:|---:|---:|---:|---|
| Cash SMA | Paired baseline | −$12.50 | 10.82% | 11 | $33.00 | 74.2% | 2026-11-10T20:05:00+00:00 |
| Late momentum | Fixed-default diagnostic | −$396.00 | 6.28% | 7 | $21.00 | 94.0% | — |
| ORB | Fixed-default diagnostic | $1,276.00 | 7.90% | 8 | $24.00 | 67.9% | — |
| Slot momentum | Fixed-default diagnostic | $609.50 | 7.74% | 9 | $27.00 | 78.6% | — |
| VWAP reversion | Fixed-default diagnostic | $523.50 | 5.34% | 7 | $21.00 | 91.6% | — |
| Cash | Selected policy | $0.00 | 0.00% | 0 | $0.00 | 100% | — |

Training rejected 22. Rejection reasons can overlap. Drawdown limit or halt (22), insufficient net p&l (9), insufficient trades or active days (5). Recorded halt dates in UTC were 2025-11-21 (5), 2025-12-01 (1), 2025-12-05 (1), 2026-01-09 (1), 2026-01-29 (4), 2026-01-30 (2), 2026-02-02 (1), 2026-02-04 (1), 2026-02-26 (1), 2026-05-29 (1), 2026-06-05 (1), 2026-06-08 (1), 2026-06-17 (1), 2026-07-14 (1).

Validation rejected 0. Rejection reasons can overlap. None. Recorded halt dates in UTC were none.

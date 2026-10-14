# Hourly entry and exit builder

Choose `trend_pullback.yaml` under **Saved configuration**, or switch **Strategy
construction** to **Condition builder**. The supplied template is long-only and uses
EMA(200) for trend and EMA(20) for pullbacks. These are illustrative research settings;
they have not been optimized or established as profitable for MNQ.

## Constructing criteria

Each direction has four editable sections:

| Section | Meaning |
|---|---|
| Market filter | Permits looking for a new trade. Failure expires an active setup. |
| Setup | Activates an opportunity window. It does not submit an entry. |
| Trigger | Selects an entry on a later bar, while the window and filter permit it. |
| Signal exit | Selects an exit independently of entry filters and setups. |

Expand a section, choose **ALL** or **ANY**, and set the number of conditions (one
through eight). Increase the count to add a condition; decrease it to remove conditions
from the end. Groups are flat: there are no nested expressions in this release.

Each row compares a left operand with a right operand. Choose Open, High, Low, Close,
SMA, EMA, ATR, or a numeric value. SMA and EMA have separate periods. ATR uses the
**ATR bars** setting in Protective stop. Prices and ATR are in index points.
**Bars earlier** counts observed bars, not elapsed hours. Zero is the completed bar.

- **Above/below** are strict comparisons. Equality does not pass.
- **Crosses above** means previously at or below and now strictly above.
- **Crosses below** means previously at or above and now strictly below.
- A crossing compares both operands on each of the two bars, including their offsets.
- All required values must be available before the strategy can trade, even for ANY.

Read the generated **Strategy summary**, then review the protective stop, sizing,
account limits and execution assumptions. Criteria edits update the summary immediately.
Risk and execution settings inside the form are submitted when you save or run.
Changing criteria, opening a report, or navigating charts does not itself run a simulation.

Long and short have separate conditions. Selecting Short or Both exposes short fields
that initially compare Close above Close, which never passes. Author all four short
sections explicitly; long rules are not mirrored. Opposing entry triggers on the same
bar are consumed and skipped, with an explanation in Diagnostics.

## Supplied pullback sequence

1. Close must be above EMA(200), and EMA(200) must be above its value five observed bars ago.
2. Close crossing below EMA(20) activates a setup while that filter passes.
3. A cross back above EMA(20) within the next five observed bars triggers an entry,
   provided the filter still passes. A setup at N can trigger at N+1 through N+5.
4. The order executes at the next observed open, after sizing and margin checks.
5. The fixed protective stop uses ATR(14) times the configured multiple, rounded up
   to a whole tick and anchored to the actual entry fill.
6. A close below EMA(200), or 48 observed bar closes with the position open, schedules
   an exit at the next observed open. The first completed bar after entry counts as one.

A failed filter immediately expires a setup. A repeated setup signal does not extend
its window. After expiry, a new qualifying setup may activate. A trigger consumes the
setup even if risk, margin or order checks subsequently prevent entry. A fresh setup is
required to enter again. There is one position at a time and no immediate reversal.
Turning off the holding limit leaves signal exits and protective stops in effect.

Filters do not automatically close positions. Configure a signal exit when a trend
failure should also terminate a trade. ALL exits require every exit condition together;
ANY exits require at least one. The holding limit is independent of that group.

## Timing, protection and diagnostics

Indicators and conditions use completed bars only. SMA uses a full close window.
EMA starts with an N-close SMA, then uses alpha = 2/(N+1). ATR starts with the mean of
N true ranges, then uses Wilder smoothing. Evaluation starts retain the full available
indicator prefix for builder strategies, but do not carry setups or positions from it.
Hourly lookbacks are not daily lookbacks; no daily session aggregation is included.

Protective stops remain active while an exit is pending. An already-filled stop discards
the redundant pending exit. Gap losses can exceed the estimated risk. Native Nautilus
OHLC simulation determines fills; hourly candles cannot establish intrabar chronology.
At the last data bar, pending orders remain pending and open positions are marked to
market. No terminal liquidation is invented.

Account halt takes precedence over a holding-limit exit, which takes precedence over
the signal-exit group. Reports record every matched reason and the primary reason.
The existing drawdown halt permanently blocks new entries for the rest of the run.

**Trades** explains the selected entry and exit. **Diagnostics** contains setup events,
filter failures, skipped entries and completed-bar condition evaluations with stable
IDs, current values, crossing values and pass/fail status. The chart displays the saved
strategy's averages and optional ATR. Old runs are read unchanged; missing explanations
are labeled unavailable rather than reconstructed.

Save personal settings with a new filename and download YAML/run reports in cloud mode.
Session files are temporary. The supplied preset is tracked in Git; personal settings
remain ignored. The existing SMA presets and their execution rules remain available.

Trailing stops, targets, higher timeframes, breakout/mean-reversion templates and
automatic regime switching are deferred. Evaluate candidate rules on unseen chronological
data and compare them under identical data, costs, capital and account limits.

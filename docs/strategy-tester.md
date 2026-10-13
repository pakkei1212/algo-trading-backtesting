# Strategy Tester workspace

The price chart stays above a collapsible **Strategy Tester**. Select a saved run to
inspect its immutable results; chart navigation and trade filters never recalculate
performance or change the simulation period.

## Read the report

- **Overview**: net P&L and return on initial equity, maximum bar-close drawdown,
  winners/closed trades, profit factor, starting/final equity, positions and commissions.
  Account equity and net P&L charts include unrealized P&L and paid fees. Optional
  drawdown is measured from the running equity peak.
- **Performance**: grouped monetary, risk, trade and holding-time statistics. Hover
  over or focus the question-mark icon beside each metric for its definition.
  Dollar and percentage maximum
  drawdown are separate maxima and can occur at different times.
- **Trades**: one row per native position, sorted by entry. Filter direction, status or
  outcome; select a row or use Previous/Next to focus the price chart. The Selected
  column and detail panel identify the chart's selected trade, including when chart
  selection changes. Exact UTC execution timestamps remain available in the details.
- **Run settings**: the saved configuration, dataset and execution assumptions.
  **Use settings for a new run** fills Configure & run; it never submits a backtest.
  Select another saved configuration to discard the loaded draft. Save under a new name.
- **Diagnostics**: decisions, skipped-entry counts, orders, fills, recorded indicators,
  stop lifecycle and original metadata. CSV files remain available for independent analysis.

Closed-trade realized P&L already includes commissions. Profit factor is the sum of
positive closed-trade net P&L divided by absolute losses. No losing trades means an
undefined denominator, displayed as **—**, including an all-winning sample. Win rate
includes breakevens in the closed-trade count. Open positions are excluded from these
closed-trade metrics. Their realized entry fees and unrealized P&L still affect equity.

Drawdown monitoring remains hourly at bar closes. A halt blocks new entries for the
rest of that run and schedules liquidation at the next observed open. The report shows
the trigger time, observed drawdown and configured limit. The evaluation continues
through the saved data period, even when trading has stopped.

## Chart controls

- Drag to pan; wheel or pinch to zoom. **+ / −** provide explicit zoom controls.
- **1D / 1W / 1M / 3M / 6M / 1Y** show trailing calendar windows ending at the last
  available bar (month shortcuts mean 30/90/180 days). **All** fits the available data.
- **Activity** frames the first through last execution with ten observed bars of
  context. A run with no executions defaults to its last 200 observed bars.
- **Selected trade**, **Go to halt**, and **Go to date** focus the relevant history.
  A date falling in a closure advances to the next available bar. There is no gap filling.
- **Reset** restores the initial viewport and automatic price scaling.
- **Auto price scale** fits visible values. Turn it off to retain a manually adjusted
  price scale; dragging the right scale adjusts the vertical view.
- Toggle candles/close line, SMA, executed trades, protective stops, volume and ATR.
  All displayed indicators come from recorded strategy observations.
- Hover for OHLC, volume, last completed-bar indicator values and executions. Multiple
  trades on a clicked candle expose individual trade choices. Entries and exits use
  separate labels; buy/sell direction alone does not identify an entry.
- **Fullscreen** expands the chart. **PNG** exports symbol, timeframe, timezone and run ID.
  A preview and download link remain available if an embedded browser suppresses the
  automatic download; you can also save the preview image from its context menu.
- **Link time ranges** synchronizes the price and performance chart after navigation.
  Statistics always cover the entire run. Without linking, each chart keeps its own view.

The default display timezone is **America/New_York**, including daylight saving. Singapore
and UTC are available. UTC timestamps retain ordering; changing timezone changes labels
only. Candles are labeled by open time; recorded close-known indicator values are shown
at their availability time. Exact nanosecond fill times are retained in details, while
fill markers attach to the associated hourly candle. No intrabar tick chronology is implied.
Stop segments run only between recorded acceptance and terminal events (or evaluation end).

View ranges and overlay settings persist per run and chart in the browser session.
Trade selection persists in the Streamlit session. Refreshing starts a new Streamlit
session; it does not delete saved runs. No account credentials or market data are sent
to TradingView by this component; its attribution links are ordinary outbound links.

## Legacy runs and reproducibility

Old runs are read without rewriting their files. Unrecorded indicators, protective-stop
histories and exit reasons are labeled unavailable rather than reconstructed as facts.
New runs include `observations.csv`, `protective_orders.csv`, fill exit reasons and
`report_version: 2` in the manifest. The added records do not alter simulation decisions.

The existing full-year baseline and the first instrumented run reconcile exactly on
summary, equity, decisions, fill prices, quantities and costs. Hourly rolling MNQ history
remains exploratory; this interface does not add dated-contract rollover economics.

## Frontend maintenance

Python reporting models are in `src/mnq_research/reporting.py`. Streamlit presentation
uses `results_ui.py`; `chart_component.py` defines the component boundary. TypeScript
source lives in `frontend/src/`. Keep calculations in Python and high-frequency pointer
interaction in the browser. Component payload version 1 contains candles, indicators,
markers, stop intervals, performance and run events; one-time focus commands use unique
IDs. Settled viewport updates are debounced; selection events return native position IDs.

The bundled JavaScript/CSS and license notices under `src/mnq_research/chart_assets/`
are included in the Python package. Users need only Python to run the application.
No frontend development server or external CDN is used at runtime.

To rebuild with Node.js installed:

```powershell
cd frontend
npm ci
npm run format:check
npm test
npm run build
```

Commit TypeScript source, the npm lockfile and rebuilt chart assets together. Do not edit
bundled JavaScript manually. `npm run format` formats the source. Dependencies are pinned;
Lightweight Charts 5.0.9 is used under Apache-2.0 with its included TradingView notice/link.
For Python development, set `$env:PYTHONPATH = "$PWD\src"` before launching Streamlit so
changes to editable source modules are included in its watcher.

Drawings, replay, alternative simulation timeframes, advanced ratios, benchmarks and
monthly/distribution analytics remain outside this release.

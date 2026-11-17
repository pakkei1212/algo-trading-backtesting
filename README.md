# MNQ Research MVP

Local Python strategy research with **NautilusTrader 1.231.0** and Streamlit.
No AI agent, live trading, or broker credentials. The existing `tradingview-mcp/`
is a separate project and is not used to extract backtest history.

The optional [TradingView MCP](https://github.com/tradesdontlie/tradingview-mcp)
checkout is excluded from this repository. Install it separately using its upstream
instructions if needed; this Python application runs without it. Historical datasets,
saved runs, credentials and local dependencies are also excluded from Git.

For browser sharing, see [Streamlit Community Cloud deployment](docs/streamlit-cloud.md).
Cloud mode supports session-isolated CSV uploads, backtests and downloadable results.

For filters, pullback setups and configurable entry/exit groups, see the
[strategy builder guide](docs/strategy-builder.md). Choose the supplied
`trend_pullback.yaml` preset in Configure & run to start editing.

## Setup (Windows PowerShell)

Install Python **3.12** and run these commands from this directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

During initial development, `pip install -e '.[dev]'` installs the declared dependencies.
The committed lock records the environment used for verification. Do not use current
NautilusTrader v2 documentation with the pinned 1.x runtime.

## Get actual market data

Yahoo offers `MNQ=F`, a rolling series. It is useful for exploratory research, but
dated-contract identity and rollover adjustments are unverified. The application labels
this limitation and requires `allow_rolling_proxy: true` to run it. Provider coverage can
change; imported metadata reports actual dates. Yahoo's 5-minute history is limited to the
latest 60 days, so use it to exercise the pipeline and explore ideas, not to validate
generalization. Yahoo's data must not be redistributed; the public cloud app therefore
uses visitor-provided CSV files instead of fetching Yahoo data.

In the local app, open **Yahoo rolling MNQ 5-minute archive** and select **Refresh Yahoo
MNQ 5-minute history**. Each refresh appends any new timestamps, records a raw response
snapshot, and keeps the previous snapshots. Overlapping bars use the newest Yahoo value in
the merged view; earlier versions remain in the snapshot folders. Gaps are shown and never
filled. Repeated refreshes grow the archive, but missed bars cannot be fetched after they
fall outside Yahoo's 60-day window.

The equivalent command is:

```powershell
.\.venv\Scripts\python.exe -m mnq_research.cli refresh-yahoo
```

To save a standalone dataset instead of updating the archive, use
`download-yahoo data\yahoo-mnq-5m --interval 5m --period 60d`.

All supported datasets are labeled with their bar interval: 5m, 15m, 30m or 1h. The
download archive saves the raw Yahoo response and its hash for each refresh. Null
observations, incomplete bars, and irregular timestamps are excluded and counted. Other
invalid prices are rejected. Gaps are never filled; counts include scheduled exchange
closures. Yahoo access is not an exchange-grade feed or a redistribution license.

For a dated contract, supply your provider's CSV with these columns:

```text
timestamp,open,high,low,close,volume
2026-09-21T01:00:00Z,25000.00,25010.00,24990.00,25005.00,100
```

The example row illustrates the format only. Each timestamp must identify the **close of
the selected bar interval**, include a timezone, and preserve the provider's session alignment.

```powershell
.\.venv\Scripts\python.exe -m mnq_research.cli import-csv path\to\history.csv data\mnq-dated --symbol MNQZ6 --source "Your licensed provider" --expiry "2026-12-18T14:30:00Z"
```

Supply verified contract expiry metadata; the example is not an automatic contract resolver.
Do not pass a continuous series as a dated contract. Automatic rolling is outside the MVP.

## Run

Review `configs/baseline.yaml` first. Its USD 1 per-side commission and USD 3,000 margin
reserve are illustrative assumptions, **not verified broker fees or margin requirements**.

The exploratory default is **2% risk per trade with a 1×ATR stop**. At USD 10,000 equity,
the USD 200 budget permits one contract with a stop up to 98.5 points including configured
costs. Wider stops are skipped. The original 0.5% / 2×ATR configuration is preserved as
`configs/conservative_baseline.yaml`. Neither preset is a validated profitable strategy.

```powershell
.\.venv\Scripts\python.exe -m mnq_research.cli backtest configs\baseline.yaml data\yahoo-mnq-1y
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

The dashboard supports configuring, saving, running, inspecting, and comparing results.
Local data ingestion is explicit through the Yahoo refresh button or the CLI; cloud mode
accepts visitor-uploaded CSVs only. Each run saves configuration, data/code
hashes, bars used, equity, orders, fills, positions, decisions, and a summary under `runs/`.
Datasets and runs are local ignored artifacts. Keep your own backups.

## Evaluate a frozen strategy

First inspect actual coverage and trade count. Select development and reserved evaluation
periods before tuning. Set timezone-aware `start` and `end` fields in separate configuration
files. The engine uses preceding bars for warm-up but starts each evaluation flat. A signal
on the first evaluation bar can only trade on its following open.

Freeze the rules and parameters before running the reserved period. Compare saved runs in
the dashboard, including a separate run with higher cost assumptions. This MVP does not
automatically optimize, rank, or label a strategy profitable. A short period or few trades
is insufficient evidence of generalization. Do not repeatedly tune against the holdout.

## Limits

- Bar simulation follows NautilusTrader's adaptive OHLC path; actual intrabar chronology
  is unknown. Stop fills and entry/stop sequences must not be mistaken for tick-level evidence.
- Drawdown is monitored at bar closes; liquidation occurs at the next observed open.
- The configured margin is an entry-sizing reserve, not a historical exchange margin model.
- Open positions at the end are marked to market; no terminal fill is invented.
- Fills assume sufficient liquidity for these small quantities; no queue/partial-fill model.
- Native slippage supports zero or one adverse tick per fill in this MVP. Larger slippage
  scenarios require a separately verified execution-model extension.
- No paper/live broker integration or data subscriptions are configured.

See `docs/strategy.md`, `CONTRIBUTING.md`, and `AGENTS.md` for behavior and coding rules.

## Reading results

Inspect results now provides an interactive price chart above a collapsible Strategy
Tester: Overview, Performance, Trades, Run settings and Diagnostics. It uses locally
bundled TradingView Lightweight Charts, with trade navigation, date shortcuts, New York
(default)/Singapore/UTC display, strategy overlays and optional volume/ATR panes.

See [the Strategy Tester guide](docs/strategy-tester.md) for controls, metric definitions,
legacy-run limitations and the reproducible frontend build. Normal app use needs no Node
server or external chart service. Display changes never recalculate saved backtests.

### AMP Futures MT5 historical data (local Windows)

Use the authenticated AMPGlobalUSA-Demo terminal and the optional Windows client
(`python -m pip install -e ".[amp-mt5]"`). The extractor reads trade bars without
login arguments or order access. The initial reviewed CME expiries cover MNQZ25,
MNQH26, MNQM26, MNQU26, and MNQZ26.

```powershell
mnq mt5 discover --terminal-path "C:\Program Files\MetaTrader 5\terminal64.exe"
mnq mt5 audit --symbols MNQZ25,MNQH26,MNQM26,MNQU26,MNQZ26 --start 2025-09-01T00:00:00Z --end 2026-11-11T03:05:00Z
mnq mt5 export --audit runs/amp-mt5/MNQZ26/<audit-id>
```

Repeat an audit with the same bounds to reuse its raw checkpoints. Use a separate
`--archive` for fresh requests of suspect windows. Audit reports distinguish missing
regular-session bars from unverified special sessions and preserve the vendor's
separate real and tick volume. The app consumes real contract volume and UTC bar
closes. Each export is a separate immutable dated contract.

In **Configure & run**, select an **AMP MT5** dataset and a verified interval.
The app suggests dates that leave room for indicator warm-up. A run needs explicit
start and end dates inside one eligible interval. Holiday and other special sessions
are conservatively excluded from eligibility in the initial 2025–2026 calendar.
The raw archive retains observations from those sessions. No gaps are filled.

### Continuous MNQ backtests

Select the local **MNQ continuous · 5m** dataset and a verified interval. The
runner uses the native dated contracts on one simulated account. It flattens at
the scheduled open of the last outgoing session, stops entries in that contract,
and starts the incoming contract with same-contract indicator warm-up. Cash,
equity high-water mark, and any drawdown halt carry through the switch.

The execution calendar uses the [CME roll schedule](https://www.cmegroup.com/trading/equity-index/rolldates.html)
and the [CME notice removing the former afternoon pause](https://www.cmegroup.com/notices/electronic-trading/2021/06/20210621.html).
Special hours were checked against AMP's published Globex summaries for
[Thanksgiving](https://www.ampfutures.com/news/holiday-trading-schedule-thanksgiving),
[Christmas](https://www.ampfutures.com/news/christmas-holiday-trading-schedule),
[New Year](https://www.ampfutures.com/news/new-year-holiday-trading-schedule),
[MLK Day](https://www.ampfutures.com/news/holiday-trading-schedule-martin-luther-king-day),
[Presidents Day](https://www.ampfutures.com/news/holiday-trading-schedule-presidents-day),
[Good Friday](https://www.ampfutures.com/news/holiday-trading-schedule-good-friday),
[Memorial Day](https://www.ampfutures.com/news/holiday-trading-schedule-memorial-day),
[Juneteenth](https://www.ampfutures.com/news/holiday-trading-schedule-juneteenth),
[Independence Day](https://www.ampfutures.com/news/holiday-trading-schedule-independence-day),
and [Labor Day](https://www.ampfutures.com/news/holiday-trading-schedule-labor-day).
For the November 2025 [CME outage](https://investor.cmegroup.com/node/55006/ixbrl-viewer),
the AMP and IBKR archives agree on the last traded bar at 02:45 UTC. CME reported
reopening at [13:30 UTC](https://www.investing.com/news/stock-market-news/cme-says-globex-futures-and-options-markets-to-open-at-730-ct-4381915).
The runner excludes the outage interval and blocks any remaining unexplained gap.

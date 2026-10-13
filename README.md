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
change; the imported metadata reports actual dates rather than promising a history length.

```powershell
.\.venv\Scripts\python.exe -m mnq_research.cli download-yahoo data\yahoo-mnq-1y --period 1y
.\.venv\Scripts\python.exe -m mnq_research.cli validate-data data\yahoo-mnq-1y
```

Downloads preserve the source response and SHA-256 hashes. Null observations, incomplete
hourly bars, and irregular snapshot timestamps are excluded and counted. Other invalid
prices are rejected. Gaps are never filled; counts include scheduled exchange closures.
Yahoo access is not an exchange-grade feed or a redistribution license.

For a dated contract, supply your provider's CSV with these columns:

```text
timestamp,open,high,low,close,volume
2026-09-21T01:00:00Z,25000.00,25010.00,24990.00,25005.00,100
```

The example row illustrates the format only. Each timestamp must identify the **close of
a one-hour bar**, include a timezone, and preserve the provider's session alignment.

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
Dataset ingestion remains an explicit CLI step. Each run saves configuration, data/code
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

- Hourly simulation follows NautilusTrader's adaptive OHLC path; actual intrabar chronology
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

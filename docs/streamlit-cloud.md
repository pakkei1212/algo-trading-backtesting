# Share the app on Streamlit Community Cloud

Deploy the GitHub repository `pakkei1212/algo-trading-backtesting`, branch `main`,
entrypoint `app.py`. In Advanced settings choose Python **3.12**. Paste this into
the Secrets setting (it is a mode flag, not a credential):

```toml
deployment_mode = "cloud"
```

For the selected public-link workflow, open Settings → Sharing after deployment
and choose **This app is public and searchable**. Share the generated
`*.streamlit.app` URL; viewers do not need an email invitation. The GitHub repository
can remain private. Check application visibility explicitly because deployment
from a private repository initially creates a private app.

To restrict access later, choose **Only specific people can view this app** and add
the intended viewers. Account login and any new GitHub permission prompts must be
completed by the account owner.

## Your friend's workflow

1. Wait for one year of hourly rolling Yahoo `MNQ=F` history to preload. Its coverage
   dates are displayed. Alternatively, upload a real CSV and provide its source and MNQ symbol.
2. For a CSV, choose a dated contract and enter its actual expiration, or choose a rolling
   proxy and acknowledge its exploratory nature.
3. Choose a preset, review the strategy settings, and click Run backtest.
4. Inspect charts/trades; download strategy YAML and the complete run ZIP to keep them.

The CSV requires exactly these columns:
`timestamp,open,high,low,close,volume`. Use completed hourly bars labeled at their
close, with a timezone offset on every timestamp. Prices must use the MNQ 0.25-point
tick grid; volume is nonnegative whole contracts. Bars must be sorted, unique,
nonoverlapping and valid OHLC observations. Exchange closures remain gaps.
Use your provider's actual contract expiration rather than copying an example.
No market dataset is redistributed through GitHub.

## Session storage and limits

Cloud mode stores files in a generated temporary directory for each Streamlit
session. It never reads the desktop `data/` or `runs/` directories. Repository
configuration presets are read-only; saved configurations belong to the session.
One session cannot select another session's uploaded data or run results. The public
Yahoo history is cached in memory for one hour and copied into each session's workspace.
It is a rolling research proxy, not verified dated-contract history. Unfinished bars
and invalid provider rows are excluded by the existing importer. Preload runs once
per session and never replaces an uploaded dataset. On provider failure, retry or upload
your own CSV. Datasets remain excluded from GitHub.

Uploads are limited to 10 MiB and 10,000 bars. A new valid upload replaces the
session's previous dataset. Existing runs contain their own data snapshot. Only
the latest five successful runs are retained. One backtest runs at a time per
app process; other sessions receive a retry message. Failed partial runs are removed.

Downloads contain the saved YAML or a ZIP with bars, config, manifest, summary,
equity, positions, orders, fills, decisions, indicators and protective-stop history.
Download form settings after saving or submitting; unsent edits are not included.
Data and results may disappear when the session expires, the app sleeps/restarts,
or you click Clear my session. There is no permanent cloud storage.

## Local development and verification

Local mode remains the default. To test cloud mode locally in PowerShell:

```powershell
$env:MNQ_DEPLOYMENT_MODE = "cloud"
python -m streamlit run app.py
```

To return to local mode, remove that environment variable. It takes precedence
over the Secrets mode setting. No Node server or Docker is needed for deployment;
the chart bundle is included in the Python package. A disposable Linux container
is used for development verification only.

`requirements.txt` loads the pinned `requirements.lock` and installs this package
in editable mode, so source updates are used even when pinned dependencies do not change.
Validate a clean Linux Python 3.12 installation before deployment. Native Nautilus
wheels require compatible Linux/glibc; inspect the cloud build log if installation
fails instead of silently changing the simulation engine version.

## Verified before publication

- Clean `python:3.12-slim` Linux installation succeeded; 38 tests passed in that snapshot.
- Final Windows suite: 39 tests passed, including two independent Streamlit test sessions.
- Ruff, configured type checks, additional cloud-module type checks and frontend checks passed.
- A cloud CSV import of the existing 5,691-bar real dataset reproduced the original
  baseline summary and equity exactly.
- Browser checks covered CSV upload, successful import/backtest, chart rendering and
  the run download control. A second browser tab had no access to the first tab's run.
- Run ZIP contents and temporary-file cleanup were checked by automated tests.

GitHub Actions runs the full checks on Ubuntu 24.04/Python 3.12 for future changes.

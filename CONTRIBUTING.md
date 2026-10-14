# Development guide

## Layout

- `config.py`: validated, versioned strategy settings.
- `data.py`: data import, provenance, and validation.
- `rules.py` / `risk.py`: pure decision functions and unit-aware sizing.
- `strategy.py`: explicit event handling; no provider or dashboard dependencies.
- `backtest.py`: NautilusTrader setup and saved reports, not an alternative simulator.
- `cli.py` / `app.py`: thin interfaces using the same services.

Use descriptive names, small functions, and explicit control flow. Prefer composition
over inheritance. Introduce a new abstraction only when a real second implementation needs
it. Type public functions and explain why non-obvious financial decisions exist.

## Add a strategy component

1. Write the human-readable criteria, units, signal timing, and edge cases.
2. Implement a pure function where possible and register it in the appropriate registry.
3. Add its explicit configuration fields and validation. Unknown settings must fail.
4. Add focused tests for positive, negative, and boundary scenarios.
5. Connect the component to the event-driven strategy and add a small engine test.
6. Expose the configuration in the dashboard without duplicating trading calculations.

The original SMA entry registry remains available. `criteria.py` defines validated
declarative conditions; `rule_engine.py` calculates completed-bar indicators and tracks
setup windows. Extend those explicit models/evaluators for new supported operands or
operators. Strategy decisions never submit simulated fills; Nautilus handles execution.
Plugins are developer-authored Python, not dynamically executed user text. Keep the
generated strategy summary and `docs/strategy-builder.md` consistent with each new rule.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests app.py
.\.venv\Scripts\python.exe -m ruff format --check src tests app.py
.\.venv\Scripts\python.exe -m mypy
```

Static typing covers the pure configuration/risk/rule and builder-evaluation layers. NautilusTrader's
Cython event boundary is verified by integration tests; do not claim whole-project strict
typing. Fixtures are synthetic **software test cases**, not strategy performance evidence.

Pin dependencies and record the exact runtime before upgrades. Current online NautilusTrader
docs cover a different major version; inspect the pinned package's source/docstrings. Verify
next-open timing, stops, costs and futures accounting after any engine update.

Do not commit market datasets, broker secrets, run artifacts, or virtual environments.
Do not alter the independent `tradingview-mcp/` tree for ordinary Python changes.

## Strategy configurations and branches

Git tracks only the supplied presets: `configs/baseline.yaml`,
`configs/conservative_baseline.yaml`, `configs/fixed_contract_diagnostic.yaml`, and
`configs/trend_pullback.yaml`.
All other files and directories under `configs/` are ignored. Personal configurations
remain available to the local app; removing a file from Git tracking does not delete
its local copy. Keep your own backups of personal configurations. A new supplied
preset requires an explicit change to the ignore exceptions.

Keep the deployed MVP on `main`. Develop the next phase on `develop/post-mvp`, then
merge verified changes into `main` through reviewed pull requests. Streamlit remains
connected to `main`. Previously committed configurations remain in Git history;
ignoring and untracking them does not rewrite existing commits.

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

The initial entry registry contains one SMA rule. Exit and risk logic remain explicit
functions; introduce registries for them when a second implementation is actually added.
Plugins are developer-authored Python, not dynamically executed user text.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests app.py
.\.venv\Scripts\python.exe -m ruff format --check src tests app.py
.\.venv\Scripts\python.exe -m mypy
```

Static typing initially covers the pure configuration/risk/rule layer. NautilusTrader's
Cython event boundary is verified by integration tests; do not claim whole-project strict
typing. Fixtures are synthetic **software test cases**, not strategy performance evidence.

Pin dependencies and record the exact runtime before upgrades. Current online NautilusTrader
docs cover a different major version; inspect the pinned package's source/docstrings. Verify
next-open timing, stops, costs and futures accounting after any engine update.

Do not commit market datasets, broker secrets, run artifacts, or virtual environments.
Do not alter the independent `tradingview-mcp/` tree for ordinary Python changes.

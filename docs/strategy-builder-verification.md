# Strategy builder verification — 14 October 2026

- Python: 57 tests pass, including the original dashboard, accounting and cloud tests.
- Ruff and configured mypy checks pass. Mypy now includes criteria and rule evaluation.
- Frontend: seven tests pass; TypeScript/build and Prettier checks pass. Availability
  tests ensure close-known indicators are never displayed early, including across gaps.
- Native baseline regression on the original 5,691-bar Yahoo dataset: summary, equity,
  fills, decisions, orders, positions, observations and protective-order lifecycle match
  the captured pre-change run exactly, excluding native random initialization/position UUIDs.
- Builder scenarios cover EMA/ATR seeding, offsets, equality/crossings, ALL/ANY,
  unavailable values, setup-window boundaries, filter expiry, conflicting directions,
  short criteria, next-open fills, stops/gaps, exit precedence, end-of-data open positions,
  evaluation starts and no future-data leakage.
- Streamlit AppTest verifies criteria editing, adding conditions, YAML saving, save-then-run,
  selected trade explanations, no implicit simulation on edits, and cloud session isolation.
- Browser verified the supplied template and summary, condition editor, keyboard focus,
  trade selection/focus, EMA labels and recorded condition diagnostics. At 480px width,
  controls stack readably and the page has no horizontal overflow. The viewport override
  was reset. Actual touch hardware was not tested.

The supplied pullback preset is an illustrative research template. On the existing
Yahoo proxy sample it produced six losing closed trades, net P&L -USD 947.50 and a
drawdown halt. This is not evidence of profitability. Existing risk defaults were
retained rather than tuned to improve that result. No market dataset or saved run is
included in Git.

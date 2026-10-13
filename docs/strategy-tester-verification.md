# Strategy Tester verification

Verified locally on 13 October 2026 with the 5,691-bar Yahoo MNQ research dataset.

## Automated checks

- Python suite: 28 tests passed, covering report edge cases, timestamp alignment,
  configuration, risk, native execution and Streamlit run/settings actions.
- Dashboard test rerun after presentation/selection fixes: passed.
- Ruff: passed. Configured mypy checks: passed (config, risk and rules modules).
- TypeScript strict compilation and bundled frontend build: passed.
- Five frontend range tests: passed. Prettier source checks: passed.
- Native engine emits an upstream pandas `Timestamp.utcnow` deprecation warning.

## Saved-run reconciliation

Compared the original `20261012T093731-1759ea12` run with the instrumented
`20261012T152523-a2ebafd1` run. Summary, equity, decisions, fill prices, quantities
and costs were unchanged. Native order identifiers and added observation fields
were excluded from execution comparisons.

The run has 10 closed trades, $1,889 net P&L, $11,889 final equity and $22 commissions.
The drawdown trigger is 9 December 2025 at 19:00 UTC; the later full-period performance
curve remains flat. New reports contain 5,642 indicator observations and 10 protective
stop intervals. Older saved files were not rewritten.

## Browser interaction checks

Verified trade-row selection, marker-to-report selection, previous/next navigation,
short-trade filtering, selected-trade focus, halt focus, full-period reset, linked and
independent chart ranges, close-line/candle switching, volume/ATR panes and fullscreen.
Checked readable controls and summary values at narrow and desktop widths. Chart
settings/ranges survive reruns. Summary cards wrap instead of truncating equity values.

PNG generation was verified through the rendered preview (776 × 475 pixels in the
tested viewport), with symbol, timeframe, timezone and run identity. The in-app browser
download-event check timed out; a visible download link and image preview now provide
a fallback. The generated example is `chart-export-example.png`.

The browser checks above were interactive checks, not a separate Playwright test suite.
DST and execution-to-candle alignment are covered by Python tests. Crosshair, pan and
zoom use the bundled Lightweight Charts implementation.

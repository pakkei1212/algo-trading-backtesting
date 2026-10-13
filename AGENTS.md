# Coding rules

Build a local MNQ research application. Do not add AI strategy agents, live order routing,
automatic optimization, or TradingView scraping without a new scope decision.

- Prefer explicit functions, descriptive trading names, and small modules over frameworks.
- Separate strategy decisions, risk calculations, data ingestion, simulation, and UI.
- Use NautilusTrader for order simulation and accounting; do not write a second simulator.
- Annotate public functions. Validate configuration at the boundary; reject unknown fields.
- Name units: prices are index points, risk and fees are USD, quantities are whole contracts.
- Store UTC timestamps and explain bar open/close timing. Never use future information.
- Preserve source metadata and distinguish rolling research proxies from dated contracts.
- Never silently fill missing market bars, replace invalid prices, or assume free trading.
- Explain why a rule exists in comments; keep the plain-language strategy spec in sync.
- Changes to trading behavior require focused tests, including boundary/failure cases.
- Run pytest, Ruff, and the configured type checks before reporting a change as verified.
- Keep generated datasets, virtual environments, and run outputs out of source control.
- Use the pinned NautilusTrader 1.x installed source/API; current online v2 examples differ.
- Do not change the vendored tradingview-mcp project as part of ordinary Python work.

No delegation is required. Keep implementation and documentation understandable to a human.

# Hourly SMA / ATR baseline

For configurable filters, sequential setups and independent exits, see
[the hourly strategy builder](strategy-builder.md). The original SMA rules below
remain available through the existing baseline presets.

This is a transparent research baseline, not a claim of market advantage.

The default uses 2% equity risk per trade and a 1×ATR stop. With the starting USD 10,000
account and configured costs, one contract permits a stop of at most 98.5 points.
Wider stops still skip entries. These are exploratory settings, not optimized or validated
for profitability. The original 0.5% / 2×ATR preset is kept in
`configs/conservative_baseline.yaml`; it skipped every signal on the initial Yahoo sample.

## Decisions

SMA is the arithmetic mean of completed closes. A long signal occurs when the fast SMA
was at or below the slow SMA and is now above it; a short signal is the reverse.
Indicators warm up before entries. ATR is seeded from the first N true ranges and then
updated by Wilder smoothing. True range includes gaps from the previous close.

An entry executes at the next observed open. The ATR distance from the signal is rounded
up to a whole MNQ tick. A fixed stop is anchored to the actual slipped entry fill. An
opposite crossover exits at the next open; it does not immediately reverse. A fresh cross
is required to re-enter. Stops remain active overnight and across weekends.

Percentage sizing floors equity times the risk fraction divided by stop distance times
USD 2 plus estimated round-trip commissions and slippage. A fixed-contract option is also
available. Both obey the maximum-contract and configured margin-reserve caps. A zero
quantity is a skipped trade, never rounded up. Gaps can lose more than the estimated risk.

At each bar close, marked equity is compared to the high-water mark (including starting
equity). A drawdown breach blocks entries for the rest of the run and schedules liquidation
at the next observed open. Rejected orders halt trading and schedule liquidation as well.

## Engine assumptions

The runner sends each observed open as a zero-spread quote at bar-open time plus one ns.
It sends the complete OHLC bar only at close. The strategy submits pending orders on those
opening quote callbacks. NautilusTrader matches all orders, calculates commissions and
maintains balances. The native fill model adds zero or one adverse tick per fill.

Native adaptive OHLC ordering controls stop simulation. High/low timestamps inside each
hour are unknown; these fills are estimates, not reconstructed trades. No future OHLC is
used to form an entry signal. At period end, positions remain open and equity includes
unrealized P&L. Exit fees on those still-open positions have not yet been charged.

Yahoo `MNQ=F` is explicitly a rolling research proxy. A simulated instrument with MNQ's
multiplier/tick is used; this does not establish dated-contract identity or roll economics.

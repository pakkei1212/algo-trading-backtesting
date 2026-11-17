# Milestone 3 work status

Milestone 3 is complete. The historical notes below record the earlier checkpoints.
See [the final comparison and verification](mnq-5m-milestone-3.md) for current results.

The starting committed source is b112173511c3f9b574108fc1d173d93f217fd296 on
develop/post-mvp. The initial dirty working tree is recorded in
`runs/cash-research/m3-start-tree.txt`. Existing studies remain intact.

Milestone 3 is incomplete. The current addition implements qualification check
reconstruction and deterministic paired stationary bootstrap calculations.
It verifies the eight-fold Milestone 2 policy total of −$784.50, SMA total of
$559.00 and paired difference of −$1,343.50. The original database contains
zero failed trials. The generated diagnostic artifact retains the SHA-256 of
the source summary and every evaluable failed gate.

Bootstrap version `circular_stationary_bootstrap_v1` uses geometric block lengths
with means 3, 5 and 10 sessions, 2,000 replications, seed zero and linear 2.5th
and 97.5th quantiles. Every paired day, including flat days, remains in the
sample. All eight five-session-block intervals include zero. These are exposed
historical results and short-fold uncertainty remains substantial.

Artifacts are in `runs/cash-research/milestone-3-diagnostics/diagnostics.json`.
Each configuration is counted once per fold and phase. Elapsed duration to halt
is explicitly calendar time from the first evaluated bar, distinct from the
number of active trading days.

Remaining work includes signal-time regime features and frozen training thresholds,
the ORB and momentum trailing policy, native multi-tick execution stress,
continuous-account replay, integrated trade/chart drill-down, experiment persistence
and historical reruns, complete UI integration and targeted verification for those
changes. No cost-stress, trailing or continuous-account results have been produced.

## Continuation progress

The continuation started at the same commit. Its full working-tree snapshot is
in runs/cash-research/m3-continuation-start-tree.txt.

Added regimes.py and two tests for causal feature histories, the first valid
efficiency value at 10:35 New York time, time-bucket boundaries, zero price paths,
tied thresholds and future-data independence. Eight sets of frozen thresholds
were fitted using the original eligible training sessions. Volatility uses one
observation per session. Efficiency uses the fixed five-minute cash grid.
Quantiles use linear interpolation at 0.33 and 0.67. Low includes q33 and middle
includes q67. Equal thresholds leave the middle category empty. Missing features
carry explicit reasons.

Feature files and fitting metadata are in
runs/cash-research/milestone-3-regimes. Trade attribution and signal-record
integration remain pending, so these files are labelled feature computation only.

Paired uncertainty now also covers the chronological reset-fold series. For the
148 April–October sessions, mean policy-minus-SMA daily P&L is −$9.16, with the
five-session mean block 95% interval [−$38.05, $19.63]. Including the seven
supplemental November sessions gives 155 sessions, mean −$8.67 and interval
[−$38.40, $19.32]. Three- and ten-session block sensitivities are saved too.
Blocks can cross monthly boundaries. These results describe independently reset
accounts and are distinct from the pending continuous-account experiment.

Milestone 3 remains incomplete. No technical blocker has been established for
the remaining components.

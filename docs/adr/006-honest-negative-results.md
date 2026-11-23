# 006: Publish negative results

**Context.** The tempting story is "power data predicts NVIDIA's earnings". A fair point-in-time backtest says it
mostly doesn't (grid beats naive for ~5 of 24 companies).

**Decision.** Report every model's error against a naive baseline, for every company, as first reported, with noise
bands on all correlations. Findings on the dashboard are generated from the data with explicit caveats.

**Consequences.** Less flashy, more credible. The defensible claim is the right one: the grid *confirms* the build-out
(capex leads baseload by ~1 quarter) rather than front-running earnings.

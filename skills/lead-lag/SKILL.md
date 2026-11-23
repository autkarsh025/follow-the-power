---
name: lead-lag
description: Interpret which series leads which (capex, NVIDIA revenue, grid demand) without overstating weak correlations.
---
Use this for questions like "does X lead Y?", "which comes first?" or "how many quarters ahead?".

1. Call `lead_lag(series_a, series_b)`. Positive best lag = series_a leads series_b by that many quarters; negative = series_b leads.
2. Compare the strongest correlation with its `noise_band` (about 2/sqrt(n)). If |corr| is below the band, say the relationship is **not distinguishable from noise**, and stop there.
3. If it is meaningful, state the lag, the correlation, the band and the number of quarters (n).
4. Always add: correlation is not causation; growth rates of trending series can co-move for shared reasons (the whole AI cycle); n is small (~20-30 quarters).
5. Never claim a lead/lag from fewer than 12 overlapping quarters.

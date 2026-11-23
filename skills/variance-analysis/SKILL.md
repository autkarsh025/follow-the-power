---
name: variance-analysis
description: Write a forecast-vs-actual (variance) note for a quarter, the way an FP&A team would.
---
Use this when asked how a quarter came in versus expectations, or to compare a forecast with the actual result.

1. Get the actual: call `quarterly_financials` and find the quarter.
2. Get the expectation: call `nowcast_scores` and read that quarter's `naive`, `capex` and `grid` predictions.
3. Compute each variance with `calculate` (actual minus forecast, in percentage points). Never do the subtraction in your head.
4. Write the note in this order:
   - **Headline:** actual vs. the best-performing forecast, with the variance.
   - **Drivers:** what moved (NVIDIA revenue growth, hyperscaler capex growth, Northern Virginia baseload growth that quarter).
   - **Model check:** which forecast was closest this quarter, and whether that matches its long-run error (MAE).
   - **Caveat:** one sentence on uncertainty (small sample, average vs. marginal effects).
5. Keep it under 150 words. Every number must come from a tool result or a `calculate` result.

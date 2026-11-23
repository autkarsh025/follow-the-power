---
name: power-signals
description: Read grid data as a data-center signal (overnight baseload, flatness, state commercial sales).
---
Use this when asked what the grid says about data centers or AI build-out.

1. Call `power_index`. Data centers run 24/7, so their growth shows up as a rising **overnight baseload** (median 1-5 AM load) and higher **flatness** (overnight / peak). Compare the same quarter year over year (`overnight_yoy_pct`), never adjacent quarters (seasons differ: winter heating and summer cooling).
2. Optionally call `state_sales` for VA (commercial sales include data centers) and compare with other states.
3. Grid data is published within days, while financial results arrive weeks after a quarter ends, so the grid is the earliest public signal, but say clearly whether it actually improved forecasts (check `nowcast_scores`).
4. Mention confounders: weather, population growth, electrification, and that one zone (Dominion) is not the whole data-center market.

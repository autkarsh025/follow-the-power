---
name: weather-adjustment
description: Separate data-center power growth from weather (hot summers, cold winters) in grid demand
---
1. Call `weather_adjusted` for the zone. It returns actual and weather-adjusted overnight baseload, the weather
   part in MW, YoY growth for both, and the model fit (R^2).
2. Lead with the WEATHER-ADJUSTED growth: that's growth temperature can't explain. Then say how much weather
   added or subtracted in the latest months (weather_part_mw), and whether it was a hot or mild period.
3. If actual and weather-adjusted growth differ a lot, explain why (e.g. "a mild winter hid 3 points of growth").
4. For "where are data centers growing", call `zone_overview` and compare zones on weather-adjusted 12-month growth.
5. Optionally call `step_changes` to point to sudden jumps (a large new load switching on); call them "consistent
   with" a new large load, never a confirmed data center.
6. Caveats: one weather station per zone; overnight load also includes industry and electrification (EVs, heat pumps).

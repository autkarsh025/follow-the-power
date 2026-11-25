daily_net_peak AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY region_code, day ORDER BY net_load_mwh DESC) AS rn,
           COUNT(*)     OVER (PARTITION BY region_code, day)                            AS hours
    FROM hourly
)
SELECT region_code,
       day,
       period_utc                                       AS net_peak_hour_utc,
       ROUND(demand_mwh)                                AS demand_mwh,
       ROUND(net_load_mwh)                              AS net_load_mwh,
       ROUND(battery_mwh)                               AS battery_mwh,
       ROUND(100.0 * battery_mwh / NULLIF(net_load_mwh, 0), 1) AS battery_share_of_net_peak_pct
FROM daily_net_peak
WHERE rn = 1 AND hours >= 22                             -- complete days only
ORDER BY region_code, day;

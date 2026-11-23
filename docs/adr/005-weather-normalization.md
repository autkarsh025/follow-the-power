# 005: Weather normalization with month fixed effects

**Context.** Load growth from data centers must be separated from hot summers and cold winters, without the model
confusing "it got hotter over the years" with "load grew".

**Decision.** Daily regression: overnight load = level[YYYY-MM] + quadratic HDD/CDD + weekend. Month fixed effects
absorb all growth, so weather coefficients are identified only from day-to-day variation within a month. Normalized
load = load at average-year weather (subtract the actual weather effect, add one constant average effect).

**Consequences.** R² 0.79–0.98 across zones; smooth series for trends, growth-regime breaks and projections. One
weather point per zone; humidity and wind ignored; non-weather seasonality (daylight) remains small but present.

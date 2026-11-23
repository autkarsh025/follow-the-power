# 001: Point-in-time storage for financials

**Context.** Companies revise numbers (restatements, recasts after divestitures), and backtests that use today's
values "know" things that weren't public yet: look-ahead bias.

**Decision.** `fin_quarterly` keys on (ticker, metric, fiscal year, quarter, **filed_date**). A changed value is a new
row; an unchanged re-report keeps its original date. `sec.as_of(df, date)` gives the view on any date. Grid data
gets an explicit publication lag (7 days; state sales 60 days).

**Consequences.** Every backtest, the time machine and the agent's time-travel evals share one rule. Derived
quarters only combine facts filed within ~a year, so a recast full year isn't mixed with an original 9-month figure.
Storage grows with revisions (cheap). Restatements become visible (Dell's VMware recast, Dominion's gas sale).

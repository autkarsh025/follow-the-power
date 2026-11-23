---
name: earnings-brief
description: Write a short earnings brief for one company: latest quarter, growth, trend, and what it signals for the AI build-out
---
1. Call `company_financials` for the company's revenue (and capex or gross_profit if relevant), last 8 quarters.
2. Headline: latest calendar quarter, value in $B, YoY growth. Compare with the previous quarter's YoY growth
   (accelerating or decelerating?) using `calculate`.
3. If gross_profit is available: gross margin = gross_profit / revenue (use `calculate`), and its change.
4. Context: call `supply_chain_snapshot` and place the company against its layer peers.
5. Check `restatements` for the company; mention any revised quarters.
6. Format: 3-5 bullets + one "what to watch" line. Under 150 words. Every number from a tool or `calculate`.

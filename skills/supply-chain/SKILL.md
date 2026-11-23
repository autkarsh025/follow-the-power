---
name: supply-chain
description: Analyze the AI build-out across companies and layers (cloud buyers, chips, servers, power gear, landlords, electricity)
---
1. Call `supply_chain_snapshot` to see every company's latest quarter and YoY growth, grouped by layer.
2. For each company you discuss, call `company_financials` for its recent quarters; note the CALENDAR quarter
   (fiscal calendars differ: NVIDIA's fiscal year ends in January, Micron's in August, Oracle's in May).
3. Read the chain in order: cloud capex (the spending) -> chips & memory -> servers & networking -> power & cooling
   gear -> data-center landlords -> electricity. Say where growth is strongest and where it is fading.
4. Compare like with like: revenue with revenue, capex with capex. Use `calculate` for any difference or ratio
   (e.g. capex intensity = capex / revenue).
5. Mention at least one caveat: growth from a small base looks huge; a company's AI business may be a fraction of
   its total revenue (XBRL gives totals, not segments); one quarter is noisy.
6. Every number must come from a tool or `calculate`.

# Architecture decision records

Short records of the decisions that shape follow-the-power: the context, the choice, and what it costs.

| # | Decision |
|---|---|
| [001](001-point-in-time-storage.md) | Store every financial value with its first filing date (point-in-time) |
| [002](002-sqlite-and-actions-cache.md) | SQLite in the GitHub Actions cache instead of a hosted database |
| [003](003-verify-numbers-not-prose.md) | Verify the agent's numbers in code, not with another LLM |
| [004](004-bm25-before-embeddings.md) | BM25 retrieval before embeddings |
| [005](005-weather-normalization.md) | Month fixed effects + degree days for weather normalization |
| [006](006-honest-negative-results.md) | Publish negative results (the grid doesn't beat naive) |

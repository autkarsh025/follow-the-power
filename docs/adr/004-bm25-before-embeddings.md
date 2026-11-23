# 004: BM25 before embeddings

**Context.** Filing search needs exact financial terms ("Data Center", "HBM", tickers), point-in-time filtering,
and no extra services.

**Decision.** Okapi BM25 over paragraph-aware MD&A chunks, with a small recency boost; an offline retrieval
benchmark (hit@3, MRR) runs daily.

**Consequences.** Transparent and dependency-free; it handles jargon well. It misses paraphrase ("AI accelerators"
vs. "GPUs"). The benchmark is the gate for trying embeddings or hybrid retrieval: adopt only if it beats BM25.

# Architecture Decision Records

---

## ADR-001 — Phase 2 (embeddings) before completing Phase 1 ingestors

**Date:** 2026-06-12  
**Status:** accepted

### Context

After implementing GDELT and RSS (49 sources, 7 geopolitical blocks), the question arose: complete Phase 1 ingestors (PortWatch, Comtrade, ACLED, EIA, USGS/FIRMS) or start Phase 2 semantics.

Pending Phase 1 ingestors fall into two categories:
- **Structured/numeric** (PortWatch, Comtrade, EIA): no embedding needed; feed directly into trading signals
- **Text/conflict** (ACLED, USGS, FIRMS): could benefit from semantic pipeline

### Decision

Start Phase 2 (embedding pipeline) before adding more ingestors.

### Rationale

Without embeddings and semantic dedup, the existing RSS pipeline already generates thousands of near-duplicates per cycle (same story from 49 sources). The core principle "LLM sees only the best" is violated from day 1.

Embeddings are the blocking prerequisite for the entire value chain:

```
embeddings → semantic dedup → clustering → events → narrative_divergences → theses
```

More ingestors without Phase 2 = more noise, not more signal.

### Consequences

- PortWatch and OFAC SDN (fast, structured) will be implemented right after Phase 2 embedding pipeline
- Comtrade, ACLED, EIA added after Phase 2 is stable
- First useful output (morning brief) arrives sooner
- Trade-off: trading signals lack maritime/commodity data during Phase 2 development

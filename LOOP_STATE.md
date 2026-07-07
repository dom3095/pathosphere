# Loop State — Pathosphere Autonomous Dev

## Fase corrente: Predictions v2 — MERGIATO

| Subtask | Stato |
|---|---|
| Implementazione completa | ✅ DONE |
| Docs aggiornate | ✅ DONE (wiki §8.6, schema, roadmap, overview) |
| Test: 419 verdi | ✅ DONE |
| Merge su main | ✅ DONE (2026-07-05) |

## Fase successiva: 4 — Dashboard Streamlit

## Ultima azione completata
Fix Wikidata linking + fix IODA mergiati in locale su main (gh non autenticato → niente PR GitHub), riportati su branch docs/quality-study-notebooks. Creati ed eseguiti 3 notebook studio qualità (`notebooks/study_01_embed.ipynb`, `study_02_extract.ipynb`, `study_03_graph.ipynb`) sul DB reale (176k raw_documents), zero errori. Discussione con utente → diagnosi causa radice: 98.8% del corpus (`origin=gdelt`) sono documenti sintetici da metadata CAMEO (non prosa), spinti nella stessa pipeline NLP della prosa reale (1.1% del corpus) → spiega entità povere/generiche, tassonomia piatta, grafo hairball (94.8% in 1 componente), cluster senza separazione narrativa. CP-014...CP-017 aggiunti a CRITICAL_POINTS.md, CP-016 = causa radice con fix proposto, CP-017 = gap cadenza ingest RSS (non gap catalogo — 48 feed già configurati). Handoff completo scritto per il collega in HANDOFF.md.

## Prossima azione: colleague apre branch `fix/gdelt-numeric-split` (o simile) — split pipeline NLP (solo prosa) vs percorso numerico GDELT (riuso `ingest/anomaly.py`, template `ingest/portwatch.py:175-214`) — poi schedulare `pathos cycle run` per volume RSS, poi Fase 4 Dashboard Streamlit

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (423 verdi)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- Scoring: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge

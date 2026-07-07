# Loop State — Pathosphere Autonomous Dev

## Fase corrente: CP-016 fix (split GDELT numerico / prosa-NLP) — CODICE COMPLETO

| Subtask | Stato |
|---|---|
| `embedder.py` esclude origin gdelt/comtrade dalla pipeline NLP | ✅ DONE |
| `ingest/gdelt_anomaly.py` — aggregazione + anomalie Goldstein → events | ✅ DONE |
| Migration `gdelt_events.action_geo_country` | ✅ DONE |
| CLI `pathos ingest gdelt-anomalies` | ✅ DONE |
| Wired in `cycle/orchestrator.py::_phase_ingest` (dopo `ingest_gdelt`) | ✅ DONE |
| Test: 436 verdi (12 nuovi) | ✅ DONE |
| Docs (wiki §5.1/§6.3, schema.md, roadmap.md, CRITICAL_POINTS CP-016) | ✅ DONE |
| `backfill_action_geo_country` + `--backfill-country` (bug trovato nel backfill reale) | ✅ DONE |
| Verificato su DB reale: 583 eventi `gdelt_anomaly` creati | ✅ DONE |
| Notebook verifica post-fix (`study_04_post_fix_verification.ipynb`) | ✅ DONE |
| `extract.py` — stesso filtro origin di embedder.py (gap trovato dal notebook) | ✅ DONE |
| Rilanciare `pathos extract` sul DB reale con la query corretta | ⬜ da fare (utente, da terminale) |
| Cleanup DB reale (174k doc gdelt già embedded/estratti pre-fix) | ⬜ NON FATTO — scelta esplicita utente, solo codice questa sessione |
| Commit fix codice (embedder+anomaly+backfill) | ✅ DONE (push su refactor/gdelt-numeric-split) |
| Commit fix extract.py + PR | ⬜ da fare |

## Fase successiva: Fase 4 — Dashboard Streamlit (dopo commit/PR di questo fix)

## Ultima azione completata
Fix Wikidata linking (2026-07-07, branch fix/wikidata-linking): delay 1 req/s rispettato anche su errore (prima `continue` su exception saltava sleep → 429 auto-amplificato), abort run su 429 (entità restanti ritentate ciclo successivo), stoplist ~110 nomi generici (`CRIMINAL`, `MILITARY`, `MALE`…) marcati checked senza lookup + strip QID legacy sbagliati. 423 test verdi. Dettagli in HANDOFF.md.

## Prossima azione: PR fix Wikidata → poi Fase 4 — Dashboard Streamlit

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (423 verdi)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- `pathos ingest gdelt-anomalies [--full] [--baseline-days N] [--z-threshold N] [--min-events-per-day N] [--backfill-country]`
- **`gdelt-history` su range già ingerito NON aggiorna colonne nuove su righe esistenti** (`INSERT OR IGNORE` su `global_event_id`) — ogni nuova colonna su `gdelt_events` va backfillata a mano se serve sullo storico
- Scoring predictions: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge

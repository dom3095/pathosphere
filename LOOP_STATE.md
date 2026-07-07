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
| Cleanup DB reale (174k doc gdelt già embedded/estratti pre-fix) | ⬜ NON FATTO — scelta esplicita utente, solo codice questa sessione |
| Commit fix codice | ✅ DONE (push su refactor/gdelt-numeric-split) |
| Commit backfill-country + PR | ⬜ da fare |

## Fase successiva: Fase 4 — Dashboard Streamlit (dopo commit/PR di questo fix)

## Ultima azione completata

Fix CP-016 implementato (sessione 2026-07-07, branch `refactor/gdelt-numeric-split`):
1. `semantic/embedder.py`: `NON_PROSE_ORIGINS = ("gdelt", "comtrade")`, esclusi dalla query candidati di `embed_documents` — restano `embedded=0` per sempre, il che li esclude automaticamente anche da `extract.py`/`cluster.py` (entrambi richiedono `embedded=1`), senza dover toccare quei moduli.
2. Nuovo `ingest/gdelt_anomaly.py` + comando `pathos ingest gdelt-anomalies`: aggrega `gdelt_events` per giorno+paese (nuova colonna `action_geo_country`, popolata in `gdelt.py::store_rows` da `ActionGeo_CountryCode`)+quad_class, riusa `ingest/anomaly.py::find_anomalies` (stesso trailing-baseline no-lookahead di PortWatch/FIRMS/IODA) per promuovere deviazioni Goldstein a `events` (`event_type='gdelt_anomaly'`). Wired nel ciclo notturno.
3. **Bug scoperto durante i test**: `find_anomalies` ha `min_value=0.0` default (floor pensato per metriche non-negative come conteggi PortWatch) — su Goldstein (range -10..+10) scartava silenziosamente tutti i valori negativi, cioè quelli destabilizzanti. Fix locale in `gdelt_anomaly.py`: `min_value=-10.0` passato esplicitamente. Default condiviso non toccato (PortWatch/FIRMS ne dipendono).

Dettagli completi in CRITICAL_POINTS.md (CP-016, ora marcato ✅ risolto) e HANDOFF.md.

**Follow-up stessa sessione**: utente ha lanciato il backfill reale (`gdelt-history --start 2021-01-01` + `gdelt-anomalies --full`) → 0 eventi anomalia. Causa: `store_rows` usa `INSERT OR IGNORE` su `global_event_id`, quindi rilanciare `gdelt-history` su range già presente non aggiorna la nuova colonna `action_geo_country` sulle righe vecchie (230k/234k restavano NULL). Fix: `backfill_action_geo_country()` recupera il country dall'ultimo campo di `events.title` (era già lì, mai perso), esposto via `--backfill-country`. Verificato: 583 eventi `gdelt_anomaly` creati sul DB reale dopo `pathos ingest gdelt-anomalies --backfill-country --full`.

**Scope deciso con l'utente**: solo codice, NO cleanup del DB reale in questa sessione (i 174k doc gdelt già `embedded=1`/`ner_done=1` da run precedenti al fix, e le entità/eventi/cluster derivati, restano contaminati finché non si lancia un reset manuale — non ancora scritto).

## Prossima azione: commit del backfill-country fix + PR di tutto su `refactor/gdelt-numeric-split` → poi CP-017 (schedulare `pathos cycle run`) → poi Fase 4 Dashboard Streamlit. Se si vuole ripulire il DB reale, scrivere prima uno script/comando di reset (vedi CRITICAL_POINTS CP-016, sezione "non incluso").

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (436 verdi)
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

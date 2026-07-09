# Loop State — Pathosphere Autonomous Dev

## Fase corrente: CP-016 chiuso, DB reale ripulito e verificato — MERGIATO su main

**Nota di sincronizzazione (2026-07-08/09)**: due sessioni Claude hanno lavorato in parallelo su questa fase. Una (branch `refactor/gdelt-numeric-split`, poi eliminato) ha diagnosticato e fixato CP-016 (split GDELT numerico/prosa-NLP). L'altra ha ramificato da quel branch e aggiunto canonicalizzazione entità via Wikidata QID + fix CP-015 (strip HTML pre-NER) + reset GDELT proprio, mergiando tutto su `main` con squash (PR #8 wikidata, #9 ioda, #10 entity-canonicalization). Il branch `refactor/gdelt-numeric-split` è stato verificato ridondante (tutto il suo contenuto già in `main`) ed eliminato locale+remoto. Da qui in avanti `main` è l'unica fonte di verità.

| Subtask | Stato |
|---|---|
| CP-016 — split embed/extract GDELT vs prosa NLP | ✅ DONE (in main) |
| CP-015 — strip HTML dal body pre-NER | ✅ DONE (in main, altra sessione) |
| Canonicalizzazione entità via Wikidata QID (`canonical_entity_id`) | ✅ DONE (in main, altra sessione) |
| `ingest/gdelt_anomaly.py` — anomalie Goldstein → events | ✅ DONE (in main) |
| Demonimi (Israeli/Russian/Chinese→location) — codice | ✅ DONE (in main) |
| Reset completo GDELT sul DB reale (`pathos ingest gdelt-reset --yes`) | ✅ ESEGUITO 2026-07-09 — 0 righe `origin=gdelt` residue, RSS/entità condivise intatte |
| Backfill demonimi sul DB reale (`pathos extract --backfill-demonyms`) | ✅ ESEGUITO 2026-07-09 — 49 entità riclassificate a location |
| Notebook verifica post-fix (`study_04`-`07`) | ✅ DONE (in main) |
| Artifact visivo (grafo entità + cluster + mappa segnali fisici) | ✅ DONE — snapshot dati pre-reset, valido come storico |
| Re-ingest GDELT da zero (`gdelt-history` + `gdelt-anomalies`) | ⬜ da fare (utente, da terminale — DB ora vuoto per origin=gdelt) |

## Fase successiva: Fase 4 — Dashboard Streamlit

## Ultima azione completata

Sessione 2026-07-09: risolta ambiguità di stato tra due branch paralleli (vedi nota sopra), poi su `main`:
1. Eseguito `pathos ingest gdelt-reset --yes` sul DB reale (494MB) — cancellati 177.281 `raw_documents`, 234.502 `gdelt_events`, 118.166 `events`, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità orfane, 27.734 `entity_links`, 4.836 righe `gdelt_file_log`. RSS/Comtrade/PortWatch/USGS/FIRMS/IODA intatti (verificato).
2. Eseguito `pathos extract --backfill-demonyms` — 49 entità (Israeli/Russian/Chinese/American/Ukrainian…) riclassificate da `other` a `location` con `canonical_name` = paese.
3. Costruito artifact visivo (grafo entità force-directed canvas, 3 cluster reali con blocchi geopolitici, mappa segnali fisici USGS/PortWatch/FIRMS) — dati presi PRIMA del reset GDELT, quindi rappresentano lo stato "as-is" pre-pulizia (incluso un caso onesto di topic-drift nel clustering, evento 122013).
4. Eliminato branch `refactor/gdelt-numeric-split` (locale+remoto) — ridondante, contenuto già in `main`.

444 test verdi su `main`.

## Prossima azione: utente rilancia (da terminale) `pathos ingest gdelt-history --start <data>` per ripopolare GDELT da zero con la pipeline pulita, poi `pathos ingest gdelt-anomalies --backfill-country --full` per il segnale numerico. Poi CP-017 (schedulare `pathos cycle run`), poi Fase 4 Dashboard Streamlit.

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (444 verdi su main)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- `pathos ingest gdelt-anomalies [--full] [--baseline-days N] [--z-threshold N] [--min-events-per-day N] [--backfill-country]`
- `pathos ingest gdelt-reset [--yes]` — senza `--yes` fa solo preview (nessuna cancellazione)
- `pathos extract [--backfill-demonyms] [--limit N] [--skip-geocode] [--skip-wikidata]`
- **`gdelt-history` su range già ingerito NON aggiorna colonne nuove su righe esistenti** (`INSERT OR IGNORE` su `global_event_id`) — ogni nuova colonna su `gdelt_events` va backfillata a mano se serve sullo storico
- File innocuo da ignorare: `pathosphere.db` (0 byte, root, scarto di un comando lanciato da cwd sbagliata in passato) — il DB vero è `data/db/pathosphere.db`
- Scoring predictions: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge (eccezione operativa di questa sessione: reset/backfill dati eseguiti direttamente, nessun cambio di codice fuori branch)

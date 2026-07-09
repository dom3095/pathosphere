# Loop State — Pathosphere Autonomous Dev

## Fase corrente: CP-017 orchestration loop + launchd automation — COMPLETO

**CP-017 — Orchestration loop (2026-07-10)**:
- Nuovo modulo `pathosphere/cycle/loop.py` — `LoopState` per persistenza stato, `run_autonomous_loop` core loop
- CLI: `pathos loop [--max-retries N] [--sleep-hours H] [--state-file PATH]`
- Stato salvato in `data/cycle_state.json`: fase completata, timestamp, ultimi 100 errori
- Retry con backoff esponenziale (5s, 10s, 20s prima di pausa 5min)
- Resumable da crash — rilancia dal `next_phase_after(last_completed)`
- Cicli completi: riparte da INGEST dopo BRIEF, sleep configurable tra cicli (default 1h)
- Graceful shutdown: Ctrl+C salva stato + esci
- Nuovo comando CLI standalone: `pathos cluster` (prima era solo dentro `pathos embed`)
- Script setup launchd: `scripts/setup_launchd.sh` — installa daemon che lancia loop ogni 12h automatico
  - `./scripts/setup_launchd.sh` (installa, default 12h)
  - `./scripts/setup_launchd.sh --interval 21600` (6h)
  - `./scripts/setup_launchd.sh --uninstall` (rimuovi)
- 8 test nuovi + 452 verdi totali

**Uso manuale:**
```bash
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Runs forever, state saved at data/cycle_state.json
# Monitor: tail -f data/logs/*.log
```

**Uso automatico (launchd):**
```bash
./scripts/setup_launchd.sh  # Installa una volta sola
tail -f data/logs/launchd.log  # Monitor
./scripts/setup_launchd.sh --uninstall  # Disattiva
```

Da qui — prossimi step prima di Fase 4:

| Subtask | Stato |
|---|---|
| CP-016/CP-015 — split GDELT + HTML strip | ✅ DONE (in main) |
| Canonicalizzazione entità via Wikidata QID | ✅ DONE (in main) |
| Demonimi (Israeli/Russian/Chinese→location) | ✅ DONE (in main) |
| Reset completo GDELT sul DB reale | ✅ ESEGUITO 2026-07-09 |
| Backfill demonimi su DB reale | ✅ ESEGUITO 2026-07-09 |
| Re-ingest GDELT da zero + pipeline pulita | 🔄 IN PROGRESS (background) |
| Notebook verifica post-re-ingest | ⬜ da fare dopo pipeline |
| **CP-017 — Loop resiliente** | ✅ **DONE 2026-07-10** |
| Fase 4 — Dashboard Streamlit | ⬜ PROSSIMO |

## Fase successiva: Fase 4 — Dashboard Streamlit

## Ultima azione completata

Sessione 2026-07-10 (ciclo 2 — loop + launchd + tests): 
- ✅ CP-017 loop autonomo: `pathosphere/cycle/loop.py` + `pathos loop` comando + state persistence JSON
- ✅ Launchd automation: `scripts/setup_launchd.sh` setup script (genera plist, installa, supports --uninstall)
- ✅ `pathos cluster` comando standalone (prima solo via `pathos embed`)
- ✅ Wiki aggiornata (sezione 7 ciclo notturno + CLI reference)
- ✅ HANDOFF/LOOP_STATE aggiornati
- ✅ 6 test nuovi launchd setup validation → 458 totali verdi
- 🔄 GDELT re-ingest in background (PID ~46142, log: data/logs/gdelt_history_2025-07-10.log, ETA ~12:30 UTC 2026-07-10)

**Prossima sessione (quando history finisce):**
1. Pipeline semantica: `gdelt-anomalies --backfill-country --full` → `embed` → `extract` → `cluster` → `graph` (~1.5h)
2. Notebook verifica (study_08): hairball/contaminazione/topic-drift su GDELT pulito
3. **TEST grafo + clustering** (verifica entità RSS sensate, cluster topic-coherent)
4. **TEST tesi + predizioni** (causal chain valid, scoring calibrato, paper trading agent vs random)
5. Fase 4 Dashboard (dopo verifica 3-4)

Precedente (2026-07-09):

Sessione 2026-07-09: risolta ambiguità di stato tra due branch paralleli (vedi nota sopra), poi su `main`:
1. Eseguito `pathos ingest gdelt-reset --yes` sul DB reale (494MB) — cancellati 177.281 `raw_documents`, 234.502 `gdelt_events`, 118.166 `events`, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità orfane, 27.734 `entity_links`, 4.836 righe `gdelt_file_log`. RSS/Comtrade/PortWatch/USGS/FIRMS/IODA intatti (verificato).
2. Eseguito `pathos extract --backfill-demonyms` — 49 entità (Israeli/Russian/Chinese/American/Ukrainian…) riclassificate da `other` a `location` con `canonical_name` = paese.
3. Costruito artifact visivo (grafo entità force-directed canvas, 3 cluster reali con blocchi geopolitici, mappa segnali fisici USGS/PortWatch/FIRMS) — dati presi PRIMA del reset GDELT, quindi rappresentano lo stato "as-is" pre-pulizia (incluso un caso onesto di topic-drift nel clustering, evento 122013).
4. Eliminato branch `refactor/gdelt-numeric-split` (locale+remoto) — ridondante, contenuto già in `main`.

444 test verdi su `main`.

## Prossima azione (quando gdelt-history finisce — ~12h da 2026-07-10 00:29 UTC)

1. **Verifica completamento history**: `tail -f data/logs/gdelt_history_2025-07-10.log` finché non vedi "GDELT ingest complete" o simile
2. **Anomalie Goldstein** (segnale numerico GDELT): `uv run pathos ingest gdelt-anomalies --backfill-country --full` (~5 min)
3. **Pipeline semantica pulita** (in sequenza):
   - `uv run pathos embed` (~20 min per tutti i doc RSS+GDELT)
   - `uv run pathos extract` (~1 ora con NER spacy multilingua)
   - `uv run pathos cluster` (~5 min)
   - `uv run pathos graph` (~10 min)
4. **Verifica finale**: notebook nuovo (study_08 o simile) con stessa metodologia di study_04-07, ma su dati GDELT puliti da zero — confrontare se canonicalizzazione+CP-015 riducono davvero il rumore vs snapshot pre-reset
5. **Poi CP-017**: orchestrazione loop (farsi aiutare da un collega agent B, restando su questo branch)

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

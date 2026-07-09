# Handoff Document — Pathosphere

*Aggiornato: 2026-07-10 — CP-017 loop autonomo implementato, pronto per il ciclo notturno persistente*

## Re-ingest GDELT da zero, pipeline pulita (2026-07-10 — in corso)

**Stato**: `pathos ingest gdelt-history --start 2025-07-10` lanciato in background (PID ~46142, 2026-07-10 00:29 UTC) con `caffeinate` (non spegne il Mac durante le ~12h). Log monitora via:
```bash
tail -f data/logs/gdelt_history_2025-07-10.log
```

**Timeline atteso**:
- **~12h da 00:29 UTC** (fine history): 2026-07-10 ~12:30 UTC
- **Poi, in sequenza** (nessuna pausa tra):
  1. `uv run pathos ingest gdelt-anomalies --backfill-country --full` (~5 min)
  2. `uv run pathos embed` (~20 min)
  3. `uv run pathos extract` (~1 ora)
  4. `uv run pathos cluster` (~5 min)
  5. `uv run pathos graph` (~10 min)

**Monitorare il progresso**: nel corso della history, il log cresce; quando finisce, avremo GDELT con:
- ✅ CP-016 fix: `origin='gdelt'` già escluso da embed/extract/cluster
- ✅ CP-015 fix: HTML strippato dal body prima di NER
- ✅ Canonicalizzazione: entità collegate a Wikidata QID via `canonical_entity_id`
- ✅ Demonimi: Israeli/Russian/Chinese reclassificati a location

**Dopo il completamento**: nuovo notebook (study_08 o simile) che replichi la metodologia di study_04-07 per quantificare il miglioramento — hairball grafo, contaminazione entità, topic-drift clustering — tutto su GDELT pulito da zero, niente legacy.

**Nota**: se il re-ingest fallisce a metà (crash, rete), non c'è problema — `gdelt-history` è resumable: il prossimo run riprenderà da dove si era fermato (verifica di quale file era stato già fatto è interna).

---

## Reset GDELT + backfill demonimi su DB reale (2026-07-09)

**Contesto**: sessione precedente (branch `refactor/gdelt-numeric-split`, ora eliminato) aveva diagnosticato CP-016 (documenti sintetici GDELT trattati come prosa dalla pipeline NLP) e scritto il fix. In parallelo, un'altra sessione ha ramificato da quel branch aggiungendo canonicalizzazione entità via Wikidata QID (nuova colonna `entities.canonical_entity_id`) + fix CP-015 (strip HTML dal body prima del NER, dipendenza `bleach`) + una propria implementazione di reset GDELT in `cli.py`, mergiando tutto su `main` con squash (PR #8, #9, #10). Il branch originale è risultato ridondante (contenuto già in `main`) ed è stato eliminato.

**Azioni eseguite in questa sessione su `main`**:
1. `pathos ingest gdelt-reset --yes` sul DB reale (`data/db/pathosphere.db`, 494MB) — cancellati 177.281 `raw_documents` origin=gdelt, 234.502 `gdelt_events`, 118.166 `events` origin=gdelt, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità rimaste orfane (usate solo da doc gdelt), 27.734 `entity_links` coinvolti, 4.836 righe `gdelt_file_log` (per permettere ri-scaricamento pulito). RSS/Comtrade/PortWatch/USGS/FIRMS/IODA verificati intatti. Operazione confermata con l'utente via preview prima dell'esecuzione (comando supporta dry-run di default, `--yes` per eseguire davvero).
2. `pathos extract --backfill-demonyms --limit 0 --skip-geocode --skip-wikidata` — 49 entità (Israeli, Russian, Chinese, American, Ukrainian…) riclassificate da `entity_type='other'` a `location` con `canonical_name` = nome paese.
3. Costruito un artifact visivo (HTML standalone, canvas force-directed graph + card cluster + mappa) usando dati catturati PRIMA del reset — quindi rappresenta lo stato GDELT-contaminato "as-is", utile come confronto storico. Include un esempio onesto di topic-drift nel clustering (evento 122013 "Armenia's top court…" i cui documenti sono in realtà quasi tutti su Netanyahu/Israele — sintomo di chain-collapse, non ancora fixato).

**Stato DB reale dopo questa sessione**: `origin=gdelt` completamente vuoto (0 righe in tutte le tabelle derivate). Prossimo passo per chi riprende: rilanciare `pathos ingest gdelt-history --start <data>` per ripopolare da zero con la pipeline già pulita (CP-016+CP-015+canonicalizzazione tutti attivi dal primo giorno, niente contaminazione da smaltire questa volta), poi `pathos ingest gdelt-anomalies --backfill-country --full`.

**Punti di attenzione per la prossima sessione**:
- Se lavori in parallelo con un'altra sessione Claude sullo stesso repo, **verifica sempre `git log main` e `git branch -a` prima di assumere che il tuo branch sia l'unica fonte di verità** — in questa sessione un reset/backfill è stato lanciato mentre in background un `git checkout` cambiava branch, e serviva ricostruire la sequenza da `git reflog` per capire cosa fosse successo. Nessun danno (i processi in esecuzione non sono affetti da checkout successivi, il DB è file-based non branch-based), ma ha richiesto un giro di verifica non banale.
- File innocuo da ignorare se lo vedi in `git status`: `pathosphere.db` (0 byte, root del repo) — scarto di un comando lanciato dalla cwd sbagliata in una sessione precedente, non il DB vero (`data/db/pathosphere.db`).

Dettagli completi CP-016/CP-015/canonicalizzazione: vedi commit `3566dbc` e PR #8/#9/#10 su GitHub, più `CRITICAL_POINTS.md`.

---

## Fix Wikidata linking (2026-07-07)

Run `pathos extract` produceva 40 errori 429 su 50 lookups Wikidata (10 QIDs). Due cause, fixate in `pathosphere/semantic/extract.py` (`link_wikidata`):

1. **Sleep saltato su errore**: `continue` su exception bypassava `time.sleep(delay_s)` → dopo primo 429 richieste a raffica senza pausa (~8 req/s), 429 auto-amplificato. Ora delay a inizio iterazione, rispettato sempre. `WIKIDATA_DELAY_S` 0.2→1.0 (limite anonimo Wikimedia ~1 req/s).
2. **Budget bruciato su entità spazzatura**: top-mentioned erano nomi generici ALL CAPS (`CRIMINAL`, `MILITARY`, `MALE`…) → link inutili o sbagliati (`MALE`→Malé). Nuova `GENERIC_ENTITY_STOPLIST` (~110 nomi comuni/ruoli/demonimi, match case-insensitive): marcati `wikidata_checked=1` senza lookup a inizio run, contati in `WikidataResult.stoplisted`. La stessa UPDATE azzera QID sbagliati assegnati pre-fix (es. `PRESIDENT`→Q30461 trovato nel DB reale).

In più: su 429 il run si interrompe subito (`WikidataResult.rate_limited=True`), entità restanti restano `wikidata_checked=0` → ritentate ciclo successivo. Errori non-429 continuano come prima. Output CLI e orchestrator mostrano stoplisted + flag rate limited. +4 test (stoplist, strip QID legacy, abort su 429, errore non-429 continua). 423 test verdi.

Smoke test reale (subagent, DB di produzione): 146 generici ritirati, 3 lookups a ~1 req/s, ISRAEL→Q801, US→Q30, `rate_limited=False`.

Run `pathos extract` completo post-fix: 9 QIDs validi (PAKISTAN→Q843, UKRAINE→Q212, RUSSIA→Q159…), poi 429 dopo 10 lookups anche a 1 req/s → abort pulito (1 warning vs 40 pre-fix), 40 entità rimandate. Probabile penalità residua IP dal run storm mattutino; se 429 persiste a IP pulito nei cicli successivi, alzare `WIKIDATA_DELAY_S` o onorare `Retry-After`. `SCHOOL`→Q3914 sfuggito → aggiunto a stoplist (QID verrà azzerato automaticamente al prossimo run dallo strip legacy).

## Fix IODA (2026-07-06)

`pathos ingest ioda --start 2026-01-01` crashava con `JSONDecodeError`. Tre cause, tutte fixate in `pathosphere/ingest/ioda.py`:

1. **Base URL sbagliato**: `ioda.inetintel.cc.gatech.edu/api/v2` è frontend SPA → HTML con 200. Corretto: `https://api.ioda.inetintel.cc.gatech.edu/v2`
2. **Limite API <100 giorni** per query singola → chunking automatico 90gg (`IODA_MAX_CHUNK_DAYS`), delay 1s tra chunk
3. **Shape reale annidata** `{"data": [[{...}]]}` → flatten un livello (vecchie shape restano supportate)

In più: risposta non-JSON ora → `RuntimeError` pulito in `IODAResult.errors` invece di crash. +3 test (chunking, shape annidata, non-JSON). Smoke test reale: IR 2026-01-01→07-05, 185 metriche, 3 chunk, 0 errori, 5 eventi outage.

## Stato al momento del handoff

**Branch:** fix/wikidata-linking (da pushare + PR)
**Test:** 423 verdi (22 in test_extract.py)
**Docs:** complete e allineate (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md)

---

## Cosa è stato fatto in questa sessione

### Predictions v2 — implementazione completa

**Schema** (`pathosphere/db/schema.py`, migration idempotenti in `_MIGRATIONS`):
- 10 colonne nuove su `predictions`: `macro_area` (NOT NULL DEFAULT 'world'), `prediction_type` (NOT NULL DEFAULT 'geopolitical'), `outcome_eventual`, `outcome_on_time`, `resolved_date`, `time_adjusted_score`, `origin_scope`, `impact_scope`, `time_horizon_class`, `trade_id`
- Backfill legacy: `outcome_on_time = outcome` E `outcome_eventual = outcome` (guardie IS NULL, idempotenti)
- Tabelle nuove: `prediction_domains(prediction_id, domain, is_primary)`, `prediction_revisions(id, prediction_id, probability, rationale, revised_at)`
- `theses.prediction_id` FK opzionale (catena predizione world → tesi)

**Config:** `timing_penalty_alpha: float = 0.001`

**`pathosphere/agent/predictions.py`** (riscritto):
- Costanti esportate: `VALID_MACRO_AREAS`, `VALID_PREDICTION_TYPES`, `TYPES_BY_MACRO_AREA`, `VALID_DOMAINS` (10), `VALID_SCOPES` (5)
- `add_prediction(...)` — valida coerenza macro_area/type, world richiede scope+domini, economic richiede thesis_id; inserisce prediction_domains; time_horizon_class derivato (breve ≤30gg, medio ≤180gg, lungo; UTC)
- `revise_prediction(id, probability, rationale)` — logga in prediction_revisions
- `resolve_prediction(id, outcome_eventual, resolved_date, alpha=None)` — brier su outcome_eventual; outcome_on_time derivato; legacy `outcome` specchia on_time; time_adjusted_score = 0 se mai accaduto, altrimenti (1−brier)×max(0, 1−alpha×|delta gg|)
- `get_calibration()` — dual metric, bucket con accuracy su outcome_eventual (fallback legacy), per-bucket mean_time_adjusted_score, breakdown by_macro_area/by_prediction_type
- `create_thesis_prediction(conn, thesis)` — auto-predizione economic per tesi approvata; clampa confidence a [0,1], default p=0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade(conn, thesis_id, trade_id)` — aggancia SOLO la più vecchia predizione economic aperta e non collegata

**CLI** (`pathosphere/cli.py`):
- `predict add` — flag v2 completi, click.Choice da costanti (inclusi --domain)
- `predict revise <id> --probability --rationale` — NUOVO
- `predict resolve <id> --outcome-eventual true|false --resolved-date YYYY-MM-DD`
- `predict list` — filtri --macro-area/--prediction-type/--domain; colonna Out con fallback legacy
- `predict calibration` — dual metric + breakdown per area e tipo
- `thesis approve` — auto-crea predizione economic (protetta: fallimento non maschera approvazione)
- `trade open` — aggancia predizione via domain function
- Gestione `sqlite3.IntegrityError` su FK inesistenti

### Review (8 angoli multi-agente) — 10 finding, 9 fixati

Fix principali: calibration accuracy usava `outcome` mentre brier usava `outcome_eventual` (metriche contraddittorie); backfill mancante di outcome_eventual (righe legacy mostravano '—'); auto-create non protetta dopo commit approvazione; UPDATE unbounded in trade open; business logic spostata da CLI a domain layer; timezone UTC coerente; alpha parametrico.

Non fixato (documentato): CP-010 — migration girano solo con `pathos db init`.

### Nuovi punti critici
- **CP-007**: headroom (compressione token) — opzione futura se credito Claude stretto
- **CP-008**: ruff F821 `sqlite3` undefined in 9 punti moduli ingest (pre-esistente, branch dedicato)
- **CP-009**: cambio timing_penalty_alpha invalida comparabilità score storici
- **CP-010**: dopo pull con modifiche schema serve `uv run pathos db init`

---

## Stato esatto al cut-off

- Codice + test: **COMPLETI**, 419 verdi
- Docs (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md): agent haiku in aggiornamento
- LOOP_STATE.md, CRITICAL_POINTS.md: aggiornati
- **Nessun commit ancora fatto** sul branch

---

## Prossima azione raccomandata

**Fase 4 — Dashboard Streamlit**

Scope:
- Mappa mondiale eventi (folium)
- Confronto narrazioni per blocco geopolitico
- Curva equity tre portafogli (agent/random/benchmark)
- Tesi aperte (status pending/approved/rejected)
- Storico brief mattutini
- Grafico calibrazione Tetlock (bucket vs accuracy)

CLI: `pathos serve` → `localhost:8501`

Dipende da Fase 3 (predictions v2) completa. DB popolo via:
```
uv run pathos cycle run           # ciclo notturno completo
uv run pathos brief              # brief mattutino
uv run pathos thesis generate    # tesi
uv run pathos thesis approve <id> # auto-crea economic prediction
```

---

## Setup automazione (launchd)

```bash
# Una volta sola: installa daemon che lancia loop ogni 12h
./scripts/setup_launchd.sh
# Opzioni:
#   --interval SECONDS    (default 43200 = 12h)
#   --uninstall           (disattiva e rimuovi)

# Monitor il daemon
tail -f data/logs/launchd.log
launchctl list | grep pathosphere

# Disattiva
./scripts/setup_launchd.sh --uninstall
```

## Comandi utili

```bash
# Stato / DB
uv run pytest tests/ -q                    # 452 verdi
uv run pathos db init                      # OBBLIGATORIO dopo pull con modifiche schema
uv run pathos db info                      # Row counts per tabella

# Loop autonomo manuale (CP-017) — corre il ciclo notturno forever con stato persistente
# Interruzione sicura: Ctrl+C salva state + esci
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Monitor:
tail -f data/logs/*.log
tail -f data/cycle_state.json  # Stato ultimo ciclo + error log (ultimi 100)

# Ciclo una volta (per debug/test)
uv run pathos cycle
uv run pathos cycle --from-phase embed       # Resume da EMBED
uv run pathos cycle --dry-run                # Simula solo

# Fasi singole (tutte standalone ora)
uv run pathos ingest gdelt --max-goldstein 5
uv run pathos ingest gdelt-anomalies --backfill-country --full
uv run pathos ingest rss
uv run pathos ingest portwatch
uv run pathos embed
uv run pathos cluster
uv run pathos extract
uv run pathos graph
uv run pathos brief
# etc.

# Predictions v2
uv run pathos predict add "Desc" --macro-area world --prediction-type geopolitical \
  --probability 0.65 --horizon 2026-08-10 --domain conflitto_armato \
  --origin-scope regionale --impact-scope globale
uv run pathos predict revise <id> --probability 0.7 --rationale "..."
uv run pathos predict resolve <id> --outcome-eventual true --resolved-date 2026-08-05
uv run pathos predict list --open --macro-area world --domain commercio
uv run pathos predict calibration

# Thesis / trading (v2: approve auto-crea predizione economic, trade open la aggancia)
uv run pathos thesis list
uv run pathos thesis approve <id>
uv run pathos thesis reject <id> --reason "..."
uv run pathos trade open <thesis_id>
uv run pathos portfolio status
```

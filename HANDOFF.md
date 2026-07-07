# Handoff Document — Pathosphere

*Aggiornato: 2026-07-07 (quarto aggiornamento), sessione fix CP-016 — notebook di verifica ha scoperto un secondo gap (extract.py), fixato (branch refactor/gdelt-numeric-split)*

## ⏭ PROSSIMA AZIONE — Lanciare `pathos extract` sul DB reale (utente, da terminale), poi PR di tutto, poi CP-017, poi Fase 4 Dashboard

### Quarto giro: notebook `study_04_post_fix_verification.ipynb` + fix `extract.py`

Creato notebook nuovo (non sovrascrive study_01/02/03) che verifica il fix CP-016 sui dati reali dopo che l'utente ha lanciato `gdelt-history` + `gdelt-anomalies --backfill-country --full` + `rss` + `embed` + `extract` + `graph`. Ha trovato un gap non previsto dal fix originale: **`semantic/extract.py` non filtrava per `origin`**, solo `embedded=1 AND ner_done=0` — quindi 46.196 documenti `origin='gdelt'` già `embedded=1` da prima del fix embedder.py restavano candidati NER validi, e ogni `pathos extract` futuro avrebbe continuato a iniettare entità generiche (`GDELT`, `POLICE`, `PRESIDENT`...). Non era contaminazione solo storica/congelata, era attiva.

Verificato quantitativamente: entità da doc `origin='rss'` pulite (19/20 nomi propri sensati), entità da doc `origin='gdelt'` ancora rumore (19/20 ALL CAPS generico). Hairball grafo isolando solo rss: 92-93% vs 94.8% baseline — miglioramento marginale perché la contaminazione attiva compensava il beneficio dell'isolamento.

**Fix**: `extract.py::extract_entities` importa `NON_PROSE_ORIGINS` da `embedder.py`, stesso filtro applicato alla query NER. 1 nuovo test. 437 test verdi. Non lanciato io `pathos extract` sul DB reale (convenzione: operazioni pesanti da terminale utente) — **da fare**: rilanciare `pathos extract` per smaltire i 46.196 doc `ner_done=0` con la query ora corretta (li salterà, non li processerà — la coda "in attesa" da extract calerà mostrando solo i doc rss/legacy-NULL residui).

**Nota di processo**: durante la creazione del notebook, 3-4 tentativi di delega a subagent sembravano fallire rapidamente (notifiche con testo placeholder tipo "sto aspettando..."), inducendo a rilanciarli inutilmente in parallelo. In realtà almeno 2 di quei lanci stavano lavorando per davvero in background e hanno completato con successo dopo diversi minuti — le notifiche brevi erano checkpoint intermedi, non fallimenti. Risultato: 2 notebook duplicati/vuoti (`study_05`, `study_06`, mai eseguiti) creati per errore e poi cancellati. Nessun danno permanente, ma da tenere a mente: non interpretare notifiche rapide con risultato generico come fallimento se il task è lungo — aspettare la notifica di completamento reale prima di rilanciare.

---

*Sezione precedente (stesso giorno, terzo aggiornamento):*

## ⏭ Fix CP-016 codice + backfill reale (terzo aggiornamento)

Sessione precedente (stesso giorno) aveva prodotto solo diagnosi + notebook (vedi sezione sotto). Poi implementato il fix in codice (secondo aggiornamento) e committato. **Questo terzo aggiornamento** documenta un bug trovato lanciando il backfill storico reale (`gdelt-history` + `gdelt-anomalies --full`), fixato e verificato: ora **583 eventi anomalia** nel DB reale. Scope concordato con l'utente resta: **solo codice, niente cleanup del DB reale** (i 174k documenti `origin=gdelt` già `embedded=1` da run precedenti al fix restano contaminati — vedi "Cosa NON è stato fatto" sotto).

### Follow-up: bug trovato lanciando il backfill reale (0 eventi anomalia al primo giro)

Utente ha lanciato `pathos ingest gdelt-history --start 2021-01-01` (fallito prima volta per colonna mancante → `pathos db init` mancante, poi rilanciato ok) e `pathos ingest gdelt-anomalies --full` → **0 eventi creati**. Causa: `gdelt.py::store_rows` fa `INSERT OR IGNORE` su `global_event_id` (chiave primaria) — rilanciare `gdelt-history` su range già ingerito **non aggiorna** righe esistenti. La nuova colonna `action_geo_country` (aggiunta da questo stesso fix) restava quindi NULL su 230.941/234.502 righe storiche (98.5%) — solo le righe della sessione di ingest più recente l'avevano. Ogni serie (paese+quad_class) aveva perciò 1-2 giorni di dati reali, mai i 10 minimi richiesti dal baseline.

Il country code non era perso: incastonato nell'ultimo campo di `events.title` (chiave dedup `Actor1CC|Actor2CC|EventRootCode|SQLDATE|ActionGeoCC`). Fix: `gdelt_anomaly.py::backfill_action_geo_country(conn)` — UPDATE mirato via join `gdelt_events.event_id → events.id`, parse ultimo campo del title, idempotente. Esposto via `pathos ingest gdelt-anomalies --backfill-country` (gira prima del sweep). 4 nuovi test.

**Verificato sul DB reale**: 201.860/234.502 righe recuperate (resto ha `ActionGeo_CountryCode` vuoto anche nel CSV GDELT originale — non recuperabile senza ri-scaricare). Sweep `--full` post-backfill: **324 serie, 583 eventi `gdelt_anomaly` creati**. Comando completo lanciato: `pathos ingest gdelt-anomalies --backfill-country --full`.

**Da ricordare per il futuro**: ogni volta che si aggiunge una colonna a `gdelt_events` (o si cambia la logica di popolamento) e poi si ri-lancia `gdelt-history` su uno storico già presente, quella colonna resterà NULL sulle righe vecchie — `INSERT OR IGNORE` non fa update. Serve sempre un backfill esplicito per le colonne nuove, non basta rilanciare l'ingest.

### Cosa è stato fatto

1. **`pathosphere/semantic/embedder.py`** — `NON_PROSE_ORIGINS = ("gdelt", "comtrade")`, esclusi dalla query candidati di `embed_documents` (`WHERE embedded=0 AND (origin IS NULL OR origin NOT IN (...))`). Questi documenti restano `embedded=0` per sempre → **si escludono automaticamente anche da `extract.py` e `cluster.py`**, che richiedono entrambi `embedded=1` come precondizione. Non serve toccare quei due moduli.

2. **`pathosphere/ingest/gdelt_anomaly.py`** (nuovo) — percorso numerico per GDELT:
   - `_aggregate_series`: raggruppa `gdelt_events` per `(action_geo_country, quad_class, day)`, media Goldstein/tone, conta righe grezze
   - `detect_gdelt_anomalies`: per ogni serie (country, quad_class) ordinata per giorno, riusa `ingest/anomaly.py::find_anomalies` (trailing-baseline no-lookahead, stesso modulo di PortWatch/FIRMS/IODA) sul valore Goldstein; deviazioni |z|≥soglia → INSERT su `events` (`event_type='gdelt_anomaly'`, `origin='gdelt'`), dedup by title, skip se già esiste
   - `whole_history=False` (default, incrementale): controlla solo l'ultimo giorno per serie. `whole_history=True` (`--full`): sweep intera storia (usare dopo `gdelt-history`)
   - `min_events_per_day` (default 3): filtro rumore, ignora celle paese/giorno/quad con troppo pochi eventi grezzi

3. **Migration schema** (`pathosphere/db/schema.py`): `ALTER TABLE gdelt_events ADD COLUMN action_geo_country TEXT` + indice `(action_geo_country, date_added)`. Popolata in `ingest/gdelt.py::store_rows` da `row["ActionGeo_CountryCode"]`.

4. **CLI**: `pathos ingest gdelt-anomalies [--baseline-days 30] [--z-threshold 2.0] [--min-events-per-day 3] [--full]`

5. **Orchestrator** (`cycle/orchestrator.py::_phase_ingest`): `detect_gdelt_anomalies(conn)` chiamato subito dopo `ingest_gdelt(...)`, log del conteggio.

6. **Bug trovato scrivendo i test**: `find_anomalies` (`ingest/anomaly.py`) ha `min_value=0.0` di default — un floor sensato per metriche non-negative (conteggi transiti PortWatch) ma che su Goldstein (range -10..+10) **scartava silenziosamente ogni valore negativo**, cioè esattamente quelli destabilizzanti che questo detector deve trovare. Fix: `gdelt_anomaly.py` chiama `find_anomalies(..., min_value=-10.0)` esplicitamente. Il default della funzione condivisa non è stato cambiato (PortWatch/FIRMS dipendono dal floor a 0). **Attenzione per detector futuri su metriche con range negativo**: stesso accorgimento necessario, altrimenti falsi negativi silenziosi (nessun errore, semplicemente zero anomalie rilevate).

7. **Test**: `tests/test_gdelt_anomaly.py` (8 test: aggregazione multi-serie, dedup, whole_history vs incrementale, filtro min_events_per_day, no-anomaly su baseline stabile), + `test_semantic.py::test_embed_excludes_gdelt_and_comtrade_origin`, + `test_gdelt.py::test_store_rows_action_geo_country_stored`. **432 test verdi totali** (era 423).

8. **Docs aggiornate**: `docs/wiki.md` (§5.1 GDELT — nuova sezione percorso anomalie; §6.3 clustering — nota GDELT riscritta; tabella tabelle; CLI reference), `docs/schema.md` (colonna `action_geo_country`, riga `gdelt_events`), `docs/roadmap.md` (nuova riga Fase 1, data aggiornata), `CRITICAL_POINTS.md` (CP-016 marcato ✅ risolto, dettaglio fix + bug min_value + nota cleanup non fatto).

### Cosa NON è stato fatto (scelta esplicita, vedi risposta utente a inizio sessione)

**Cleanup del DB reale.** Il fix impedisce che il problema *si ripeta* andando avanti, ma il DB reale (`data/db/pathosphere.db`, 176k `raw_documents`) contiene ancora, da run precedenti al fix:
- Doc `origin=gdelt` con `embedded=1`/`ner_done=1` (elaborati dalla pipeline NLP prima che questo fix esistesse)
- Entità generiche (`GDELT`, ruoli CAMEO) in `entities`/`document_entities` derivate da quei doc
- Cluster `events` derivati via `cluster.py` da quei doc (da distinguere dagli eventi creati direttamente in `gdelt.py::store_rows`, che restano validi — sono la 5-tupla Actor1/Actor2/EventRootCode/SQLDATE/ActionGeoCC, non passano da NLP)
- Archi `entity_links` inquinati nel grafo (hairball 94.8%, vedi notebook studio sessione precedente)

Se si vuole un DB pulito: scrivere un comando/script di reset che azzeri `embedded`/`ner_done`/`dedup_checked` su `origin IN ('gdelt','comtrade')` e ripulisca `entities`/`document_entities`/`entity_links` derivati (NON gli `events` creati da `store_rows`). Non fatto in questa sessione — non richiesto, e serve giudizio su come distinguere "eventi validi da store_rows" da "eventi spuri da cluster.py" (probabilmente via `event_documents` → se i doc collegati sono `origin=gdelt` E l'evento non ha un `gdelt_events.event_id` diretto, è spurio).

### Verificare prima di procedere

- `uv run pytest tests/ -q` → 432 verdi (già verificato in sessione)
- `ruff check` sui file toccati → nessun nuovo errore (errori pre-esistenti in `cli.py`/`test_gdelt.py`/`test_semantic.py`, non toccati da questa modifica)
- **Non ancora committato** — branch `refactor/gdelt-numeric-split` ha modifiche in working tree, nessun commit di questa sessione

---

*Sezione precedente (stessa giornata, prima di questo fix):*

## Split pipeline GDELT-numerico/prosa-NLP — diagnosi + notebook (sessione precedente, 2026-07-07)

**Per il collega che riprende**: questa sessione ha prodotto 3 notebook di studio qualità (as-is, nessun fix) e una diagnosi di causa radice discussa con l'utente. La diagnosi è il lavoro prioritario da trasformare in codice — leggi tutto prima di aprire la Fase 4 Dashboard, altrimenti costruisci sopra dati inaffidabili.

### Diagnosi — perché la qualità semantica sembra debole

L'utente ha notato: entità poche/generiche, tassonomia troppo piatta, grafo senza componenti sensate, cluster che non sembrano separare storie diverse. **Causa unica, confermata coi dati reali** (non 4 problemi scollegati):

`pathos ingest gdelt` costruisce documenti sintetici da metadata strutturato CAMEO, non da prosa — `title = f"GDELT: {Actor1Name} → {Actor2Name} [{EventCode}]"` (`ingest/gdelt.py:284`). Quando GDELT non identifica un attore specifico, `Actor1Name`/`Actor2Name` sono **codici di ruolo generici** (`PRESIDENT`, `POLICE`, `MILITARY`...), non nomi propri. Composizione reale del corpus (`raw_documents` per origin): **gdelt 174.286 (98.8%), rss 1.939 (1.1%), comtrade 252 (0.1%)**. La pipeline NLP (`embed`→`extract`→`cluster`→`graph`) tratta tutte le origin allo stesso modo, come se fossero prosa.

Questo spiega, in un colpo solo:
- Entità dominate da ruoli generici e dalla parola letterale `GDELT` stessa (leak del prefisso titolo nel NER — CP-014, 128.082 documenti coinvolti, 73.5% dei doc gdelt)
- Tassonomia piatta: `LABEL_MAP` (`extract.py`) mappa solo su person/company/location/other; `country`/`commodity`/`infrastructure` previsti dallo schema DB ma mai implementati — nessuna logica dedicata li popola
- Grafo hairball: 94.8% dei nodi collegati sta in un'unica componente connessa. Non solo colpa di `GDELT` come nodo — ogni evento GDELT porta 2-3 entità generiche che co-occorrono ovunque (`min_cooccurrences=1`, nessun decadimento)
- Cluster senza vera separazione narrativa: i cluster più grandi raggruppano ricorrenze di ruoli generici (es. evento con 155 doc, titoli tutti `GDELT: CREDIT UNION → `, `GDELT: JUDGE → `...), non storie reali. Non abbiamo verificato visivamente (UMAP/PCA scatterplot dell'embedding space) — da fare se serve conferma ulteriore.

**Il valore reale di GDELT è inutilizzato**: `gdelt_events` ha campi numerici (`goldstein`, `avg_tone`, `quad_class`, `num_mentions`, `num_sources`) — grep conferma: usati SOLO come filtro a monte in ingest (`--max-goldstein`), mai aggregati/analizzati a valle. Il segnale quantitativo di intensità conflitto/cooperazione (la vera ragione per cui un progetto OSINT usa GDELT) è scritto e mai letto.

Dettagli completi, numeri, query e riferimenti: **CP-011...CP-017 in `CRITICAL_POINTS.md`** (CP-016 = causa radice, CP-017 = gap copertura prosa).

### Fix proposto (non applicato in questa sessione — solo diagnosi + notebook as-is)

**1. Escludere GDELT/Comtrade dalla pipeline NLP.** WHERE clause su `origin` nelle query candidate di `semantic/embedder.py`, `semantic/extract.py` (NER), `semantic/cluster.py`, `semantic/graph.py` — pipeline NLP ristretta a prosa reale (`origin='rss'` e simili).

**2. Dare a GDELT un percorso numerico proprio**, riusando `pathosphere/ingest/anomaly.py::find_anomalies` (trailing-baseline, no-lookahead — stesso modulo già usato da PortWatch/FIRMS/IODA). Template concreto: `ingest/portwatch.py:175-214` (`_detect_and_promote`) — query serie storica ordinata per data, `find_anomalies(points, value_key=..., baseline_days=..., z_threshold=..., direction="both")`, poi INSERT su `events` con dedup by title. Applicare lo stesso schema a `gdelt_events` aggregato per giorno+paese+quad_class su goldstein/avg_tone, promuovendo anomalie direttamente a `events` (origin='gdelt') **senza** passare da NER/embed/cluster.

**3. Copertura prosa (RSS) — il collo di bottiglia è la cadenza, non il catalogo.** 48 feed già configurati (`ingest/sources_seed.py`), copertura quasi completa della wishlist CLAUDE.md. `pathos ingest rss` è già nel ciclo notturno (`cycle/orchestrator.py:120`) ma finora lanciato solo a mano — 1.939 doc RSS in un mese riflette esecuzioni sporadiche, non scarsità di fonti (ogni run cattura solo le ultime 48h; il volume si accumula solo con run regolari). Priorità: **schedulare `pathos cycle run`** (cron/launchd). Secondario: ribilanciare blocchi deboli (latam=1, india=3 su 48 totali) con candidati verificati — MercoPress/teleSUR English/Buenos Aires Times (latam), The Wire (india). Verificare vivacità feed prima di aggiungere (precedente: Xinhua abbandonato, feed RSS congelati al 2018).

**Branch consigliato**: `fix/gdelt-numeric-split` (o `refactor/gdelt-pipeline`) — non ancora creato. Root cause architetturale, non un bug isolato: prevedere più di una sessione.

### Notebook di studio qualità (deliverable di questa sessione)

3 notebook in `notebooks/`, eseguiti con output reali sul DB di produzione (`data/db/pathosphere.db`, 176.477 `raw_documents`). Analisi **as-is**, nessun fix di codice durante lo studio (i fix Wikidata/IODA sono stati mergiati PRIMA come prerequisito dati puliti — vedi sezione sotto). Per rilanciarli: `uv run --with jupyter,nbconvert,ipykernel,pandas,numpy,matplotlib jupyter nbconvert --to notebook --execute --inplace <nb>`.

- `study_01_embed.ipynb` — copertura embedding/dedup/cluster, coerenza titoli, sensori fisici mescolati. Nota: copertura clustering 99.6% (ipotesi iniziale di bassa copertura per finestra 72h **smentita** dai dati — il backfill ha girato incrementalmente più volte). 77.4% dei cluster è singleton.
- `study_02_extract.ipynb` — entità/NER, copertura Wikidata QID (0.3% delle 11.467 entità ha un QID — collo di bottiglia rate limit, non risolvibile in una sessione), geocoding, CP-014/CP-015 scoperti qui.
- `study_03_graph.ipynb` — grado nodi, hairball (94.8% componente gigante), caso d'uso "se chiude Hormuz chi soffre?" (entità presente nel grafo, ma affogata nel rumore generico).

**Fatti utili tecnici (per chi rilancia i notebook o riprende il lavoro):**
- DB reale: `data/db/pathosphere.db` (NON `data/pathosphere.db` — file stray in root, non toccato)
- `vec_documents` è tabella virtuale sqlite-vec → connessione via `pathosphere.db.schema.get_connection(path)` (carica estensione), path assoluto (cwd notebook ≠ repo root, i notebook gestiscono questo con `REPO_ROOT` auto-detect)
- Cluster (`semantic/cluster.py`): union-find greedy, similarity 0.85, finestra 72h su `COALESCE(published_at, fetched_at)` calcolata al momento del run (non relativa ai dati), KNN 20, `max_cluster_size=30`
- Graph (`semantic/graph.py::build_entity_links`): SOLO co-occorrenza entità in eventi condivisi, `relation_type='co-occurs'`, `strength=min(1, cooc/10)`, `min_cooccurrences=1`, DELETE+rebuild a ogni run. Le relazioni tipate dello schema (`depends_on`, `supplies`…) NON sono mai popolate
- Jupyter NON in dipendenze del progetto → sempre `uv run --with jupyter,nbconvert,ipykernel,pandas,numpy,matplotlib jupyter nbconvert ...`
- Geocode cache in questo run: 28 query totali cachate, 4 mai risolte (miss permanenti, nessuna scadenza) — il numero "731 eventi non geocodabili" di handoff precedenti si riferiva a `events.location_name`/`lat`, non alla cache raw; da riconciliare se serve un numero preciso aggiornato

---

## Fix Wikidata linking (2026-07-07) — branch `fix/wikidata-linking`, MERGIATO in locale su main (2026-07-07)

Run `pathos extract` produceva 40 errori 429 su 50 lookups Wikidata (10 QIDs). Due cause, fixate in `pathosphere/semantic/extract.py` (`link_wikidata`):

1. **Sleep saltato su errore**: `continue` su exception bypassava `time.sleep(delay_s)` → dopo primo 429 richieste a raffica senza pausa (~8 req/s), 429 auto-amplificato. Ora delay a inizio iterazione, rispettato sempre. `WIKIDATA_DELAY_S` 0.2→1.0 (limite anonimo Wikimedia ~1 req/s).
2. **Budget bruciato su entità spazzatura**: top-mentioned erano nomi generici ALL CAPS (`CRIMINAL`, `MILITARY`, `MALE`…) → link inutili o sbagliati (`MALE`→Malé). Nuova `GENERIC_ENTITY_STOPLIST` (~110 nomi comuni/ruoli/demonimi, match case-insensitive): marcati `wikidata_checked=1` senza lookup a inizio run, contati in `WikidataResult.stoplisted`. La stessa UPDATE azzera QID sbagliati assegnati pre-fix (es. `PRESIDENT`→Q30461 trovato nel DB reale).

In più: su 429 il run si interrompe subito (`WikidataResult.rate_limited=True`), entità restanti restano `wikidata_checked=0` → ritentate ciclo successivo. Errori non-429 continuano come prima. Output CLI e orchestrator mostrano stoplisted + flag rate limited. +4 test (stoplist, strip QID legacy, abort su 429, errore non-429 continua). 423 test verdi.

Smoke test reale (subagent, DB di produzione): 146 generici ritirati, 3 lookups a ~1 req/s, ISRAEL→Q801, US→Q30, `rate_limited=False`.

Run `pathos extract` completo post-fix: 9 QIDs validi (PAKISTAN→Q843, UKRAINE→Q212, RUSSIA→Q159…), poi 429 dopo 10 lookups anche a 1 req/s → abort pulito (1 warning vs 40 pre-fix), 40 entità rimandate. Probabile penalità residua IP dal run storm mattutino; se 429 persiste a IP pulito nei cicli successivi, alzare `WIKIDATA_DELAY_S` o onorare `Retry-After`. `SCHOOL`→Q3914 sfuggito → aggiunto a stoplist (QID verrà azzerato automaticamente al prossimo run dallo strip legacy).

**`gh` non autenticato → merge fatto in locale** (main + fix/ioda-endpoint-chunking + fix/wikidata-linking), niente PR GitHub. Utente deve rilanciare `uv run pathos extract` per ripulire QID legacy ed entità generiche esistenti nel DB reale prima che i notebook studio-qualità le documentino come "attuali" (vedi vincolo network-call sotto).

---

*Sezioni precedenti (2026-07-06 e prima):*

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

## Comandi utili

```bash
# Stato
uv run pytest tests/ -q                    # 419 verdi
uv run pathos db init                      # OBBLIGATORIO dopo pull con modifiche schema

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

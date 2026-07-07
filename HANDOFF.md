# Handoff Document — Pathosphere

*Aggiornato: 2026-07-07, sessione fix Wikidata + avvio studio qualità (branch docs/quality-study-notebooks)*

## ⏭ PROSSIMA AZIONE — Studio qualità output embed/extract/graph (IN CORSO, notebook non ancora creati)

**Richiesta utente**: valutare la bontà di quanto prodotto da `pathos embed`, `pathos extract`, `pathos graph` sul DB reale. Deliverable: **notebook di studio** in `notebooks/`, con esempi concreti a supporto delle interpretazioni, atteggiamento agnostico, caccia alle criticità.

**Vincoli espliciti dell'utente** (non negoziabili):
1. **Analisi as-is**: non creare nulla che non esista già — niente fix, niente feature. Obiettivo: evidenziare apporti mancanti / criticità di ciò che c'è.
2. **Tutto dentro i notebook**: utente ha rifiutato query sqlite via terminale. Le esplorazioni vanno nei notebook stessi, eseguiti con output visibili.

**Stato**: branch `docs/quality-study-notebooks` creato (da main). Solo ricognizione fatta; **zero notebook scritti**. Nessuna modifica a codice.

**Fatti utili già raccolti (risparmiano ricognizione):**
- DB reale: `data/db/pathosphere.db` (NON `data/pathosphere.db`)
- ~130k `raw_documents`, ~11.5k+ entities, ~4.9k events, 311k+ mentions (numeri da run extract 2026-07-07)
- `vec_documents` è tabella virtuale sqlite-vec → connessione via `pathosphere.db.schema.get_connection(path)` (carica estensione), path assoluto (cwd notebook ≠ repo root)
- Cluster (`semantic/cluster.py`): union-find greedy, similarity 0.85 (commento in codice: 0.75 collassava tutto in mega-catena), finestra 72h su `COALESCE(published_at, fetched_at)`, KNN 20, `max_cluster_size=30`
- Graph (`semantic/graph.py::build_entity_links`): SOLO co-occorrenza entità in eventi condivisi, `relation_type='co-occurs'`, `strength=min(1, cooc/10)`, `min_cooccurrences=1`, DELETE+rebuild a ogni run. Le relazioni tipate dello schema (`depends_on`, `supplies`…) NON sono mai popolate
- Dedup: soglia 0.92 (`semantic/dedup.py`), flag `is_duplicate`/`duplicate_of`/`dedup_checked` su raw_documents
- Jupyter NON in dipendenze → eseguire con `uv run --with jupyter,nbconvert,ipykernel,pandas jupyter nbconvert --to notebook --execute --inplace <nb>`

**Piste di criticità già emerse (da verificare nei notebook con esempi):**
- Entità generiche ALL CAPS (`CRIMINAL`, `MILITARY`…) dominano le classifiche mentions → inquinano graph (co-occorrenza con tutto) e budget Wikidata (fix su branch separato, vedi sotto)
- 731 eventi non geocodabili (miss cachati) — quota alta su ~4.9k
- `min_cooccurrences=1` + entità generiche → rischio hairball nel grafo; strength satura a 10 co-occorrenze
- Eventi da sensori fisici (USGS/FIRMS/PortWatch/IODA) entrano in `events` direttamente senza clustering — mischiati ai cluster articoli
- Qualità NER `xx_ent_wiki_sm` su testo GDELT ALL CAPS mai misurata

**Struttura proposta** (3 notebook, da validare col collega): `notebooks/study_01_embed.ipynb` (coverage embedding, qualità dedup con coppie esempio, distribuzione dimensioni cluster + coerenza titoli), `study_02_extract.ipynb` (distribuzione tipi entità, rapporto segnale/rumore, copertura QID e link errati, copertura geocoding), `study_03_graph.ipynb` (grado nodi, hairball check, top archi sensati vs spazzatura, test caso d'uso "se chiude Hormuz chi soffre?").

---

## Fix Wikidata linking (2026-07-07) — branch `fix/wikidata-linking`, pushato, PR DA CREARE

Run reale `pathos extract` mostrava 40×429 su 50 lookups Wikidata. Due cause fixate in `pathosphere/semantic/extract.py` (dettagli nel HANDOFF.md di quel branch):
1. `continue` su exception saltava sleep → 429 auto-amplificato; delay 0.2→1.0s, rispettato sempre; su 429 abort run pulito (`rate_limited=True`), entità restanti ritentate a ciclo successivo
2. Budget bruciato su entità generiche → `GENERIC_ENTITY_STOPLIST` (~112 nomi), ritirate senza lookup + strip QID legacy errati (`PRESIDENT`→Q30461, `SCHOOL`→Q3914 trovati in DB reale)

423 test verdi. Run reale post-fix: PAKISTAN→Q843, UKRAINE→Q212, RUSSIA→Q159; 429 residuo dopo 10 lookups (probabile penalità IP dal run storm mattutino — se persiste, alzare delay o onorare `Retry-After`).

**BLOCCO: `gh` non autenticato** → PR non creata. Fare `gh auth login`, poi PR da `fix/wikidata-linking` (titolo/body pronti nei commit). NB: quel branch aggiorna anche HANDOFF/LOOP_STATE/CRITICAL_POINTS (CP-012) — al merge riconciliare con questo file.

---

*Sezioni precedenti (2026-07-06 e prima):*

## Fix IODA (2026-07-06)

`pathos ingest ioda --start 2026-01-01` crashava con `JSONDecodeError`. Tre cause, tutte fixate in `pathosphere/ingest/ioda.py`:

1. **Base URL sbagliato**: `ioda.inetintel.cc.gatech.edu/api/v2` è frontend SPA → HTML con 200. Corretto: `https://api.ioda.inetintel.cc.gatech.edu/v2`
2. **Limite API <100 giorni** per query singola → chunking automatico 90gg (`IODA_MAX_CHUNK_DAYS`), delay 1s tra chunk
3. **Shape reale annidata** `{"data": [[{...}]]}` → flatten un livello (vecchie shape restano supportate)

In più: risposta non-JSON ora → `RuntimeError` pulito in `IODAResult.errors` invece di crash. +3 test (chunking, shape annidata, non-JSON). Smoke test reale: IR 2026-01-01→07-05, 185 metriche, 3 chunk, 0 errori, 5 eventi outage.

## Stato al momento del handoff

**Branch:** main (predictions v2 mergiato)
**Test:** 419 verdi (80 in test_predictions.py)
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

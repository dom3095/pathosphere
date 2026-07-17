# Critical Points — Pathosphere

Punti critici aperti: bug noti, decisioni tecniche rimandati, rischi potenziali.
Aggiornare immediatamente quando emerge un nuovo punto. Rimuovere quando risolto.

---

## CP-001: Claude CLI — prerequisito non verificato a runtime

**Contesto:** backend `claude` chiama `claude -p "PROMPT"` via subprocess. Richiede CLI installata e autenticata sulla macchina.

**Workaround:** documentato come prerequisito. Se necessario, aggiungere `_claude_available()` check in `LLMClient.__init__` con fallback a `qwen-local`.

**Impatto:** tool inutilizzabile senza CLI Claude. Errore chiaro in subprocess se assente.

**Azione:** nessuna modifica necessaria ora. Documentare in README se si distribuisce.

**Mitigazione 2026-07-17:** `pathos doctor` verifica `claude` su PATH prima di
qualunque run — FAIL (exit 1) se `reasoning_model=claude`, WARN altrimenti.
Non risolve il caso "CLI presente ma non autenticata" (serve un run reale).

---

## CP-002: Ticker validation — LLM produce ticker non validi

**Contesto:** generatore tesi produce ticker US-centrici o inesistenti (es. "TSMC" invece di "TSM"). Validazione in `approve` con warn ma non blocca.

**Workaround:** `pathos thesis show <id>` → verificare ticker manualmente → correggere nel DB se necessario → poi `trade open`.

**Impatto:** trade aperto su ticker errato non ha prezzo reale; `portfolio status` mostra errore yfinance.

---

## CP-003: Qwen locale — Ollama non sempre attivo

**Contesto:** debate pipeline richiede `ollama serve` attivo. ConnectError se non disponibile.

**Workaround:** usare fast path (`pathos thesis generate`) senza Ollama. Solo la pipeline debate è disabilitata.

**Impatto:** `pathos thesis debate` fallisce con ConnectError se Ollama non è in esecuzione.

**Mitigazione 2026-07-17:** `pathos doctor` interroga `{ollama_base_url}/api/tags`
(timeout 3s): WARN se Ollama è giù o se il modello configurato non è pullato,
con hint (`ollama serve` / `ollama pull qwen3:4b`).

---

## CP-004: Predictions v2 — separazione tipi mancante nel modello — **RISOLTO 2026-07-05** (non marcato finora)

**Contesto:** design v2 ha formalizzato scoring e tassonomia ma NON la separazione tra predizioni geopolitiche/politiche/sociali ed economiche. Il campo `prediction_type` non è stato aggiunto allo schema.

**Decisione:** due colonne distinte che coesistono:
- `macro_area TEXT NOT NULL CHECK IN ('world','economic')` — guida workflow e metrica
- `prediction_type TEXT NOT NULL CHECK IN ('geopolitical','political','social','economic')` — granularità per filtri e calibrazione per tipo
Relazione: `world` → prediction_type IN ('geopolitical','political','social'); `economic` → prediction_type='economic'. FK `trade_id` opzionale solo per `macro_area='economic'`.

**Verificato ora (2026-07-14)**: entrambe le colonne presenti in `pathosphere/db/schema.py::_MIGRATIONS` (righe ~419-422), incluse in `feat/predictions-v2` (PR #6, mergiata 2026-07-05) come previsto. Bookkeeping mai aggiornato in questo file finché non notato durante un audit generale — nessun lavoro mancante, solo la spunta.

---

## CP-005: Predictions v2 — backward compat migration — **RISOLTO 2026-07-05** (non marcato finora)

**Contesto:** nuove colonne (`outcome_eventual`, `outcome_on_time`, `resolved_date`, `origin_scope`, `impact_scope`, `time_adjusted_score`) e nuova tabella `prediction_domains`. Predizioni esistenti avranno queste colonne a NULL.

**Workaround:** migration idempotente in `schema.py` via `_MIGRATIONS`. `get_calibration` deve escludere righe con `time_adjusted_score=NULL` o gestire NULL esplicitamente.

**Impatto:** dati storici pre-v2 esclusi da `time_adjusted_score`; `brier_score` ancora disponibile per confronto.

**Verificato ora (2026-07-14)**: `prediction_domains` presente nello schema, backfill `outcome_eventual` da `outcome` legacy presente (`_MIGRATIONS` riga ~438). Stesso caso di CP-004 — implementato in `feat/predictions-v2`, mai marcato qui.

---

## CP-006: `causal_chain` JSON schema — struttura fissa

**Contesto:** struttura fissa `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}`. Usata da `approval.py` (format_causal_chain) e `cli.py` (pathos thesis show).

**Workaround:** nessuno — non modificare la struttura senza aggiornare tutti i consumer.

**Impatto:** breaking change su tesi esistenti nel DB se la struttura cambia.

---

## CP-007: Headroom — compressione token per chiamate Claude (opzione futura)

**Contesto:** [headroom](https://headroomlabs-ai.github.io/headroom/integration-guide/) comprime messaggi prima dell'invio a LLM (funzione `compress()`, 2 righe con client Python; anche proxy standalone). Utile SOLO su chiamate Claude via `claude -p` (brief, tesi) per stirare il credito mensile $20. Inutile su Qwen locale (gratis illimitato).

**Decisione:** NON adottare ora. Pipeline già filtra a monte (~30-50 doc/giorno all'LLM); compressione = secondo strato su problema già risolto. Costi: 15-200ms latenza, dipendenza extra, integrazione con subprocess `claude -p` non banale.

**Trigger per riconsiderare:** credito mensile Claude esaurito prima di fine mese. Allora valutare `compress()` sul prompt del brief mattutino e della generazione tesi.

**Impatto:** nessuno finché il credito basta.

---

## CP-008: ruff F821 — `sqlite3` usato ma mai importato in moduli ingest — **RISOLTO 2026-07-13**

**Contesto:** `ruff check` segnala 9 F821 (`sqlite3` undefined) in `ingest/comtrade.py:186`, `ingest/gdelt.py:229,246,389`, `ingest/physical.py:159,404`, `ingest/portwatch.py:255`, `ingest/rss.py:88`, `ingest/sources_seed.py:405` — probabilmente type hints o except su `sqlite3.X` senza import. Più 37 violazioni minori (F401 import inutilizzati, F541, E741), 33 auto-fixabili con `ruff check --fix`.

**Workaround (superato):** i 403 test passano — i path incriminati o non vengono eseguiti nei test o il nome arriva per vie indirette. Rischio: NameError a runtime su path non coperti.

**Fix:** aggiunto `import sqlite3` nei 6 file (annotazione tipo `"sqlite3.Connection"` con `# type: ignore[name-defined]` invariata — bastava rendere il nome definito nel modulo). `uv run ruff check pathosphere/ --select F821` → 0 errori.

**Non incluso (fuori scope):** le 37 violazioni minori (F401/F541/E741) restano — 2 F401 pre-esistenti in `gdelt.py` (`date`, `Path` non usati) verificate come non introdotte da questo fix. Serve ancora `chore/ruff-cleanup` per il resto.

---

## CP-009: timing_penalty_alpha — cambio invalida comparabilità storica

**Contesto:** `time_adjusted_score` calcolato a risoluzione con alpha corrente (settings o parametro esplicito di `resolve_prediction`). Alpha NON persistito sulla riga: cambiarlo in `.env` rende i nuovi score incomparabili con quelli già risolti.

**Workaround:** gli input per ricalcolare sono tutti persistiti (`probability`, `brier_score`, `resolved_date`, `horizon_date`) — possibile script di ricalcolo retroattivo con alpha uniforme.

**Impatto:** medie di calibrazione miste se alpha cambia a metà storia. Non cambiare alpha senza ricalcolo.

---

## CP-010: migration v2 girano solo con `pathos db init` — **RISOLTO 2026-07-13**

**Contesto:** `get_connection` non esegue `migrate_db`; solo `init_db` (comando `pathos db init`, idempotente). DB pre-v2 + codice v2 senza re-init → `sqlite3.OperationalError: no column named macro_area` sui nuovi path.

**Workaround (superato):** dopo ogni pull con modifiche schema: `uv run pathos db init` (sicuro, idempotente).

**Fix:** `get_connection` (`pathosphere/db/schema.py`) ora chiama `migrate_db(conn)` dopo i PRAGMA, prima del `return conn` — ogni connessione auto-migra, non solo `pathos db init`. `migrate_db` resta idempotente (ogni ALTER in try/except che ignora `OperationalError`), costo trascurabile per tool CLI locale. `init_db` continua a chiamarlo 3 volte in totale (pre-DDL, post-DDL, dentro `get_connection`) — ridondante ma innocuo, non toccato per non introdurre rischio.

**Test:** nuovo `test_get_connection_auto_migrates_pre_v2_db` (`tests/test_db.py`) — DB con solo `CREATE TABLE entities` minimale, `get_connection` chiamato SENZA `init_db`/`migrate_db` espliciti, verifica che `canonical_entity_id` (colonna aggiunta via migration) compaia comunque.

**Impatto:** nessun più crash CLI su DB non re-inizializzato. `uv run pathos db init` resta comunque il modo esplicito consigliato dopo un pull, ma non più l'unico che previene il crash.

---

## CP-011: embed processa tutto il raw GDELT senza filtro a monte — **RISOLTO implicitamente** (side-effect CP-016, mai marcato)

**Contesto:** `pathos embed` embedda TUTTI i `raw_documents` con `embedded=0` (`semantic/embedder.py`, query senza filtro rilevanza/età). Backfill GDELT 6 mesi → ~169k doc → 1-3+ ore su M1 CPU. Viola il principio "filtraggio aggressivo a monte": la ridondanza GDELT si paga in ore di embedding invece di essere tagliata prima.

**Verificato ora (2026-07-14)**: il fix CP-016 (`NON_PROSE_ORIGINS = ("gdelt", "comtrade")` in `embedder.py`) esclude GDELT/Comtrade dalla query di `embed_documents` — i doc GDELT non vengono più embeddati affatto, il problema descritto qui (ore di CPU su backfill GDELT) non si presenta più. Scritto prima di CP-016, mai riletto/marcato dopo. Il commit-per-batch (32) resta comunque una buona proprietà per il volume RSS/Comtrade rimasto.

---

## CP-012: dedup — transazione unica, non riprendibile, nessun progresso — **RISOLTO 2026-07-13**

**Contesto:** `dedup_documents` (`semantic/dedup.py:70`) avvolge l'intero loop in un solo `with conn:` → una transazione per 169k doc. Ctrl+C o crash = rollback totale, riparte da zero (a differenza di embed, commit per batch). Nessun log di progresso tra inizio e fine; progresso invisibile anche da fuori (update uncommitted). KNN sqlite-vec è brute-force: 169k query × scan 169k vettori ≈ ore su backfill grossi.

**Workaround (superato):** non interrompere; lanciare con `caffeinate -i` e lasciar finire. Vitalità verificabile solo via `ps aux | grep pathos` (CPU ~100%).

**Fix:** stesso pattern di `embedder.py` — `dedup_documents` ora processa i doc candidati (query invariata, `WHERE dedup_checked = 0`) in chunk di `BATCH_SIZE=32`, con `with conn:` (commit) per batch invece che sull'intero run, e log INFO di progresso (`Dedup progress: N/tot checked, M duplicates so far`) ad ogni batch. La KNN query resta per-documento (non raggruppata) — solo il commit è a livello di batch. Interruzione/crash a metà: i doc dei batch già committati restano `dedup_checked=1` e non vengono ripresi al retry (garantito dal filtro `WHERE dedup_checked = 0` nella SELECT iniziale, invariato).

**Test:** nuovo `test_dedup_batch_commit_survives_later_batch_failure` (`tests/test_semantic.py`) — 3 doc, `batch_size=2`, eccezione simulata (monkeypatch `_parse_dt`) sul 3° doc (batch 2). Verificato: doc 1-2 (batch 1, già committato) restano `dedup_checked=1` dopo che batch 2 solleva e fa rollback; doc 3 resta `dedup_checked=0`.

**Impatto:** backfill grossi ora resilienti a interrupt — perdita massima = 1 batch (32 doc) invece dell'intero run. Progresso visibile a log INFO invece che solo a fine run.

---

## CP-013: stoplist Wikidata curata a mano — nuovi termini generici possono emergere

**Contesto:** `GENERIC_ENTITY_STOPLIST` (`semantic/extract.py`, ~110 voci) blocca lookup Wikidata per nomi comuni/ruoli/demonimi ALL CAPS prodotti dal NER su testo GDELT (`CRIMINAL`, `MILITARY`, `MALE`…). Lista statica: termini generici nuovi (altre lingue, plurali mancanti) passano il filtro e consumano budget lookup.

**Workaround:** controllare log `Wikidata linking` a inizio run; se compaiono nomi generici, aggiungerli alla stoplist. Entità già linkate male: azzerare `wikidata_qid`/`canonical_name` a mano nel DB.

**Impatto:** basso — 50 lookups/notte, qualche lookup sprecato al peggio. Fix futuro: euristica strutturale (es. skip mono-parola ALL CAPS con match in wordlist inglese) invece di lista enumerata.

---

## CP-014: entità "GDELT" — leak del prefisso titolo sintetico nel NER — **RISOLTO implicitamente** (side-effect CP-016, mai marcato)

**Contesto:** i titoli sintetici GDELT hanno formato `"GDELT: ACTOR → ACTOR2"` (`ingest/gdelt.py`). `_build_text` (`semantic/extract.py`) concatena title+body senza rimuovere il prefisso → il NER tagga la parola letterale `GDELT` come entità ORG/company su quasi ogni documento origin=gdelt. Scoperto in `notebooks/study_02_extract.ipynb`: **entità col maggior numero di mention in assoluto** — 128.082 documenti (73.5% dei doc origin=gdelt).

**Impatto sul grafo:** in `notebooks/study_03_graph.ipynb`, il nodo `GDELT` ha grado 3.962/89.838 archi (4.4%) — hub artificiale, causa diretta più probabile della componente connessa gigante osservata (9.666/10.192 nodi, 94.8%). Inquina anche budget Wikidata (voce già stoplistata come "generica" solo se aggiunta a mano — non matcha `GENERIC_ENTITY_STOPLIST` attuale).

**Verificato ora (2026-07-14)**: il "secondo follow-up" di CP-016 (vedi sotto) ha già esteso `NON_PROSE_ORIGINS` alla query candidati NER in `extract.py::extract_entities` — i doc `origin='gdelt'` non entrano più nel NER, quindi il leak del prefisso `"GDELT: "` non può più prodursi su documenti nuovi. Scritto prima di quel fix, mai riletto/marcato dopo. Il DB reale è stato anche ripulito da zero (reset GDELT completo, 2026-07-09, vedi CP-016) — nessun residuo storico noto.

---

## CP-015: frammenti HTML taggati come entità — body non ripulito da markup prima del NER — ✅ RISOLTO (main, PR entity-canonicalization)

**Contesto:** scoperto in `notebooks/study_02_extract.ipynb`: entità con `<`/`>` nel nome (es. `span><strong`, `said.</p`) presenti nel DB, generate dal NER su body RSS non sanitizzato. Compaiono anche tra i nodi ad alto grado nel grafo (`notebooks/study_03_graph.ipynb`).

**Fix (sessione parallela, mergiato su `main`):** `semantic/extract.py::_build_text` ora passa il body da `bleach.clean(body, tags=[], strip=True)` prima della NER — markup rimosso alla fonte. Nuova dipendenza `bleach`.

**Non incluso:** entità già inquinate nel DB reale prima del fix (es. quelle viste nel grafo dell'artifact di verifica di questa sessione, catturate PRIMA di questo fix) restano finché non si rilancia NER sui doc coinvolti o si fa cleanup mirato.

---

## CP-016: causa radice — pipeline NLP prosa applicata a documenti sintetici GDELT (98.8% del corpus) — ✅ RISOLTO 2026-07-07 (branch `refactor/gdelt-numeric-split`)

**Contesto:** `pathos ingest gdelt` costruisce documenti sintetici da metadata strutturato CAMEO, non da prosa: `title = f"GDELT: {Actor1Name} → {Actor2Name} [{EventCode}]"` (`ingest/gdelt.py:284`), body analogo (righe 330-332). Quando GDELT non identifica un attore specifico, `Actor1Name`/`Actor2Name` sono **codici di ruolo generici** (`PRESIDENT`, `POLICE`, `MILITARY`, `SCHOOL`…), non nomi propri. Il DB reale conferma lo squilibrio: `raw_documents` per origin — gdelt 174.286 (98.8%), rss 1.939 (1.1%), comtrade 252 (0.1%). Pipeline semantica (`embed`→`extract`→`cluster`→`graph`) tratta tutte le origin allo stesso modo, come se fossero prosa.

**Diagnosi (sessione 2026-07-07, studio qualità in `notebooks/`):** questa è la causa unica che spiega CP-014, CP-015, l'hairball nel grafo (94.8% nodi in 1 componente), la tassonomia entità povera (solo person/company/location/other popolati, mai country/commodity/infrastructure nonostante lo schema li preveda), e cluster di eventi che raggruppano ricorrenze di ruoli generici (es. "Evento 1191", 155 doc, titoli tutti tipo `GDELT: CREDIT UNION → ` `GDELT: JUDGE → `) invece di storie reali. **Non sono bug indipendenti**: sono sintomi dello stesso disallineamento architetturale.

**Il segnale numerico reale di GDELT è inutilizzato:** `gdelt_events` ha campi quantitativi (`goldstein`, `avg_tone`, `quad_class`, `num_mentions`, `num_sources`) — verificato via grep: usati SOLO come filtro a monte in ingest (`--max-goldstein`, `cli.py:177`), mai aggregati/analizzati a valle. Il valore vero di GDELT (intensità conflitto/cooperazione nel tempo/spazio) è scritto e mai letto.

**Fix applicato (2026-07-07, codice+test, NO cleanup DB reale — scelta esplicita utente, vedi sotto):**
1. `semantic/embedder.py`: `NON_PROSE_ORIGINS = ("gdelt", "comtrade")` escluso dalla query `embed_documents` (`WHERE embedded=0 AND origin NOT IN (...)`). `extract.py`/`cluster.py` non hanno bisogno di filtro proprio: entrambi richiedono `embedded=1` a monte, che questi doc non raggiungeranno mai — l'esclusione a un solo punto si propaga a tutta la pipeline.
2. Nuovo modulo `ingest/gdelt_anomaly.py` (+ comando `pathos ingest gdelt-anomalies`, wired in `cycle/orchestrator.py::_phase_ingest` subito dopo `ingest_gdelt`): aggrega `gdelt_events` per giorno+`action_geo_country`+`quad_class` (nuova colonna, migration `ALTER TABLE gdelt_events ADD COLUMN action_geo_country TEXT`, popolata in `gdelt.py::store_rows` da `ActionGeo_CountryCode`), poi riusa `ingest/anomaly.py::find_anomalies` per promuovere deviazioni Goldstein a `events` (`event_type='gdelt_anomaly'`), stesso pattern PortWatch. Dedup by title, no lookahead.
3. **Bug scoperto in `ingest/anomaly.py::find_anomalies` durante i test**: `min_value=0.0` di default è un floor pensato per metriche non-negative (conteggi transiti PortWatch) — applicato a Goldstein (range -10..+10) scartava silenziosamente ogni valore negativo, cioè esattamente quelli destabilizzanti. Fix locale: `gdelt_anomaly.py` chiama `find_anomalies(..., min_value=-10.0)`. Il default della funzione condivisa non è stato toccato (PortWatch/FIRMS ne dipendono) — se altri detector futuri useranno metriche con range negativo, stesso accorgimento necessario.

**NON incluso in questo fix (scope deciso con l'utente, sessione 2026-07-07):** cleanup del DB reale. I 174k `raw_documents` origin=gdelt già `embedded=1`/`ner_done=1` da run precedenti, e le entità/eventi/cluster già derivati da loro, restano nel DB così come sono — il fix impedisce solo che il problema *si ripeta* andando avanti. Se si vuole un DB pulito, serve un comando/script di reset separato (non fatto): azzerare `embedded`/`ner_done`/`dedup_checked` su `origin IN ('gdelt','comtrade')`, e ripulire `entities`/`document_entities`/`entity_links`/eventi da cluster derivati da quei doc (distinguerli dagli eventi creati direttamente da `store_rows`, che restano validi).

**Impatto:** alto — root cause architetturale risolta a livello di codice. 6 nuovi test (`tests/test_gdelt_anomaly.py`) + 2 (`test_semantic.py::test_embed_excludes_gdelt_and_comtrade_origin`, `test_gdelt.py::test_store_rows_action_geo_country_stored`), 432 verdi totali.

**Follow-up nella stessa sessione — bug scoperto lanciando il backfill storico reale:** `pathos ingest gdelt-history --start 2021-01-01` + `pathos ingest gdelt-anomalies --full` sul DB reale (176k→180k doc) hanno prodotto **0 eventi anomalia**. Causa: `gdelt.py::store_rows` usa `INSERT OR IGNORE` su `global_event_id` (chiave primaria) — rilanciare `gdelt-history` su un range già ingerito **non aggiorna** le righe esistenti, quindi la nuova colonna `action_geo_country` restava NULL su 230.941/234.502 righe (98.5%, tutto lo storico pre-fix). Ogni serie (paese+quad_class) aveva perciò 1-2 giorni di dati reali — mai i 10 minimi richiesti da `find_anomalies` per costruire un baseline.

Il country code non era perso: è già incastonato nell'ultimo campo di `events.title` (chiave dedup `Actor1CC|Actor2CC|EventRootCode|SQLDATE|ActionGeoCC`, costruita da sempre in `store_rows`). Fix: nuova funzione `gdelt_anomaly.py::backfill_action_geo_country(conn)` — UPDATE mirato su `gdelt_events.action_geo_country IS NULL`, recupera il valore da `events.title` via join su `event_id`, idempotente (non tocca righe già popolate). Esposta via `pathos ingest gdelt-anomalies --backfill-country` (gira prima del sweep, stesso comando). 4 nuovi test. Sul DB reale: 201.860/234.502 righe ora popolate (il resto ha `ActionGeo_CountryCode` vuoto anche nel CSV originale GDELT — non recuperabile senza ri-scaricare). Risultato dopo fix: **324 serie, 583 eventi anomalia creati**.

**Secondo follow-up — gap più grave del previsto, trovato dal notebook di verifica (`notebooks/study_04_post_fix_verification.ipynb`):** il fix originale assumeva che bloccare `embed` bastasse, perché `extract`/`cluster` richiedono `embedded=1` a monte. Falso per i documenti che erano **già** `embedded=1` prima che il fix esistesse: `extract.py` filtra solo `WHERE embedded=1 AND is_duplicate=0 AND ner_done=0`, nessun filtro `origin`. Sul DB reale, 46.196 doc `origin='gdelt'` erano `embedded=1 AND ner_done=0` — candidati NER validi, **contaminazione attiva** (non solo storica: si aggiungeva ad ogni `pathos extract` successivo, non solo un'eredità congelata nel passato).

Verifica quantitativa: top-20 entità collegate a doc `origin='rss'` → 19/20 nomi propri sensati (Iran, Trump, Russia, NATO...). Top-20 collegate a doc `origin='gdelt'` → 19/20 rumore ALL CAPS (`GDELT` 128k mention, `POLICE`, `PRESIDENT`, `MILITARY`...), invariato rispetto a CP-014 pre-fix. Hairball grafo isolando solo entità rss: 92-93% in componente gigante vs 94.8% baseline mista — miglioramento marginale, perché la contaminazione in corso continuava ad alimentare `entity_links` anche nel sottografo teoricamente "pulito".

**Fix**: stesso pattern di `embedder.py` — `extract.py::extract_entities` ora importa `NON_PROSE_ORIGINS` da `embedder.py` e lo applica alla query candidati NER (`AND (origin IS NULL OR origin NOT IN (...))`). 1 nuovo test (`test_extract.py::test_ner_excludes_gdelt_and_comtrade_origin_even_if_already_embedded`). Non tocca i 128.090 doc gdelt con `ner_done=1` già processati (storico, resta contaminato per la stessa scelta di scope — no cleanup DB reale). Ferma però la crescita: i 46.196 doc `ner_done=0` non verranno più processati da NER futuri.

**Impatto:** alto — senza questo secondo fix, il primo fix (embedder.py) dava una falsa sensazione di sicurezza: i dati NUOVI erano puliti ma la pipeline continuava comunque a inquinare entità ad ogni `extract` per via del backlog storico già `embedded=1`. 437 test verdi totali dopo questo fix.

**Terzo follow-up — cleanup DB reale eseguito (2026-07-09), dopo sincronizzazione con lavoro parallelo (canonicalizzazione entità + CP-015, altra sessione, mergiati su `main`):** `pathos ingest gdelt-reset --yes` lanciato sul DB reale — cancellati tutti i 177.281 `raw_documents` origin=gdelt, 234.502 `gdelt_events`, 118.166 `events` origin=gdelt, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità rimaste orfane, 27.734 `entity_links` coinvolti, 4.836 righe `gdelt_file_log`. Verificato: RSS/Comtrade/PortWatch/USGS/FIRMS/IODA intatti, entità condivise (es. "Iran" citata sia da doc gdelt che rss) sopravvivono con solo la parte gdelt rimossa. `origin=gdelt` ora completamente vuoto in tutte le tabelle derivate — il "NON incluso" della diagnosi originale è ora risolto. Prossimo passo: rilanciare `gdelt-history` da zero con la pipeline pulita (CP-016+CP-015+canonicalizzazione tutti già attivi, niente da smaltire questa volta).

---

## CP-017: copertura fonti prosa (RSS) — collo di bottiglia è la cadenza, non il catalogo

**Contesto:** `pathosphere/ingest/sources_seed.py` ha già 48 feed RSS attivi, copertura quasi completa della wishlist CLAUDE.md (Global Times, TASS, Al Jazeera, Press TV, Anadolu, The Hindu, Folha presenti; Xinhua deliberatamente escluso — commento nel codice: feed RSS congelati al 2018, verificato e abbandonato). Distribuzione per blocco sbilanciata: western 19/48 (40%), china 5, russia 4, arab 4, india 3, africa 7, latam 1, other 5.

`pathos ingest rss` (`max_age_days=2`, no backfill storico per i feed) è già nel ciclo notturno (`cycle/orchestrator.py:120`), ma il ciclo è stato finora lanciato solo a mano, non schedulato. Risultato: 1.939 documenti RSS totali in circa un mese — non per scarsità di fonti ma per esecuzioni sporadiche (ogni run cattura solo le ultime 48h; il volume si accumula solo con run regolari e ripetuti nel tempo).

**Candidati per riequilibrare i blocchi deboli (verificare vivacità feed prima di aggiungere, vedi precedente Xinhua):**
- LatAm (oggi solo Folha): MercoPress, teleSUR English, Buenos Aires Times
- India (3, tutti mainstream): The Wire (indipendente)
- Africa (7, già ok): Mail & Guardian (SA), The East African

**Impatto:** medio — priorità 1 è schedulare `pathos cycle run` (cron/launchd) per accumulo regolare; ampliare/ribilanciare il catalogo è secondario e va ripetuto nel tempo (i feed RSS gratuiti muoiono senza preavviso).

---

## CP-018: canonicalizzazione entità — solo person, location/org restano frammentate — **RISOLTO 2026-07-12**

**Contesto:** sessione 2026-07-12, ispezionando visivamente `study_15_visual_tour.ipynb` (grafo entità), l'utente ha trovato 4 problemi distinti nella qualità delle entità — nessuno coperto da `canonicalize_person_entities()` (che copre solo `entity_type='person'`, vedi commit `510aa1a`).

**1. Tipo sbagliato vince nella risoluzione alias Wikidata — RISOLTO.** `link_wikidata()` risolveva conflitti QID assegnando `canonical_entity_id` a chiunque avesse ottenuto il QID per primo, senza controllare `entity_type`. Verificato: `FRANCE` (company) aveva ottenuto QID `Q142` prima di `France` (location, corretto), propagando il tipo sbagliato a valle. Fix: `link_wikidata` ora interroga Wikidata P31 (`_wikidata_instance_of_hint`, mappa `WIKIDATA_TYPE_HINTS`) quando i due tipi in conflitto divergono, e scambia il canonico verso la riga col tipo corretto invece di "chi arriva prima". Aggiunta `repair_wikidata_type_conflicts()` per correggere conflitti già mal risolti nel DB esistente (rete, opt-in via `pathos extract --repair-wikidata-types`). Verificato sul DB reale: `FRANCE` ora alias di `France` (location, QID Q142 corretto).

**2. `LABEL_MAP` troppo stretto: ORG spaCy → sempre `company` — RISOLTO.** Aggiunto `INTERGOVERNMENTAL_ORGS` (EU, NATO, UN, WHO, IMF, World Bank, WTO, OPEC, G7, G20, ASEAN, African Union, Arab League, BRICS) → `entity_type='organization'` invece di `company`. `backfill_organization_entities()` riclassifica righe esistenti (idempotente, stesso pattern di `backfill_demonym_entities`). Verificato: `EU`/`NATO` ora `organization` con `canonical_name` corretto.

**3. Location/demonimi non canonicalizzati cross-entità — RISOLTO.** Nuova `canonicalize_location_entities()` (stesso pattern non distruttivo di `canonicalize_person_entities`, chiave = `DEMONYM_TO_COUNTRY`/nuovo `LOCATION_ALIAS_TO_COUNTRY`). Verificato sul DB reale: `England`/`British`/`Britain` ora tutte alias di `UK`, `canonical_name="United Kingdom"` su tutte e 4.

**4. Rumore NER puro — RISOLTO.** Nuovo `NOISE_ENTITY_STOPLIST` (video, watch, photo, gallery, live, breaking, update...) escluso **a livello di creazione** in `extract_entities` (non solo skip Wikidata come `GENERIC_ENTITY_STOPLIST`). `purge_noise_entities()` ripulisce righe legacy già in DB. Verificato: `VIDEO` (22 mention) eliminata.

**Bonus — CP-019 trovato durante la verifica di questo fix, vedi sotto.**

**Fix applicati e verificati empiricamente sul DB di produzione** (backup pre-fix: `data/db/pathosphere_backup_20260712_163720_pre_cp018.db`), poi `pathos graph` rieseguito (77516 link scritti, da 83808 pre-canonicalizzazione). 53 nuovi test in `test_extract.py`, 494 totali verdi, ruff pulito.

---

## CP-019: collisione Wikidata su nomi ambigui (acronimi/parole comuni) — **RISOLTO 2026-07-12** (trovato durante verifica CP-018)

**Contesto:** l'utente ha segnalato che i 4 punti di CP-018 erano "esempi, non l'elenco completo" — verificando empiricamente prima di chiudere CP-018 (invece di fidarmi che i 4 punti coprissero tutto), ho trovato un quinto bug reale sul DB di produzione, di natura diversa dai 4 di CP-018.

**Bug trovato:** entità `UK` (location, 58 mention) aveva `canonical_entity_id` che puntava a un'entità `Ukrainian` con `wikidata_qid='Q8798'` — che **non è l'Ucraina**, è la **lingua ucraina** (Wikidata: "Ukrainian", "East Slavic language", codice ISO 639 `uk`). `wbsearchentities("UK")` ha fatto match fuzzy su quel codice ISO, non sul paese Regno Unito. Bug generalizzabile: qualunque demonimo che è anche nome di lingua (french/russian/german/chinese/...) rischia la stessa collisione — non ipotetico, trovato su dati reali.

**Fix (generale, non solo per UK):** nuovo `CURATED_ALIAS_TO_LABEL` (= `DEMONYM_TO_COUNTRY` ∪ `LOCATION_ALIAS_TO_COUNTRY` ∪ `INTERGOVERNMENTAL_ORGS`) — tutti i nomi in queste tabelle curate vengono **esclusi dalla ricerca Wikidata** (stesso meccanismo di `GENERIC_ENTITY_STOPLIST`, ma preservando il `canonical_name` corretto invece di azzerarlo a NULL, dato che qui il valore curato è affidabile). Ripara anche QID sbagliati già assegnati in passato.

**Rischio residuo esplicitamente verificato e mitigato:** audit su parole-paese ambigue in inglese non ancora linkate (`Turkey`/uccello, `Georgia`/stato USA, `Jordan`/persona-fiume, `Chad`/nome proprio, `Guinea`/roditore, `Niger`, `Congo`, `Mali`, `Jersey`/abbigliamento) — nessuna ancora corrotta al momento dell'audit (tutte `wikidata_qid IS NULL`), ma prossime in coda. Aggiunta verifica proattiva (`AMBIGUOUS_ENTITY_NAMES`): per questi nomi, `link_wikidata` interroga P31 **prima di accettare** il match e scarta se il tipo non coincide con `entity_type`, invece di scoprirlo dopo il fatto.

**Nota per il futuro:** questa non è garanzia di completezza — è una lista curata (9 nomi) di collisioni note, non un rilevatore generale. Altre incongruenze non ancora osservate sono probabili (l'utente lo ha esplicitamente segnalato); trattare i controlli odierni come *classi di difesa* generalizzate (stoplist curata, verifica P31 su conflitto, verifica P31 proattiva su ambigui noti), non come lista chiusa di 9+4 nomi risolti.

---

## CP-020: due classi sistemiche aggiuntive (asimmetria demonimo↔paese, aggettivi continentali) — **RISOLTO 2026-07-12**

**Contesto:** dopo aver chiuso CP-018/CP-019, l'utente ha ispezionato di nuovo il grafo e corretto esplicitamente l'inquadramento: *"non sono segnalazioni puntuali, sono classi di errore"* — vedendo ancora `EU`/`European`/`Europe` (3 nodi) e `China`/`Chinese` (2 nodi) separati. Invece di patchare i singoli nomi, ho cercato la causa strutturale.

**Classe A — asimmetria demonimo↔paese in `canonicalize_location_entities`.** `_location_country_key()` risolveva la chiave di gruppo per un'entità demonimo (es. "Chinese" → "China" via `DEMONYM_TO_COUNTRY`), ma **non riconosceva l'entità-paese stessa** ("China") come appartenente allo stesso gruppo, a meno che il suo `canonical_name` non corrispondesse esattamente al valore del dizionario — cosa che spesso non accade perché Wikidata usa il nome ufficiale completo (es. "People's Republic of China" invece di "China"). Risultato verificato: "China" e "Chinese" restavano due nodi separati nel grafo nonostante la mappa demonimo→paese esistesse già. Fix: `_location_country_key` ora riconosce anche il **nome letterale** dell'entità come chiave nota (`_KNOWN_PLACE_VALUES_LOWER`, i valori dei dizionari lowercased), indipendentemente da `canonical_name`. Generalizzabile a qualunque paese nei dizionari esistenti, non solo Cina.

**Classe B — forme aggettivali continentali non coperte.** "European" (aggettivo) non era in nessuna tabella curata → restava `entity_type='other'`, invisibile a `canonicalize_location_entities` (che filtra solo `entity_type='location'`). "Europe" (il continente) aveva **un'altra istanza della stessa classe di bug di CP-019**: `wbsearchentities("Europe")` aveva fatto match fuzzy su "Europe PubMed Central" (un database di letteratura scientifica), non sul continente. Fix: aggiunte `europe`/`european`, `asia`/`asian`, `africa`/`african` a `LOCATION_ALIAS_TO_COUNTRY` (stesso dizionario usato per UK/Britain — nome generico ma meccanismo identico: alias → nome canonico location). Questo li rende automaticamente parte di `CURATED_ALIAS_TO_LABEL` (skip ricerca Wikidata, fix CP-019) **e** disponibili a `backfill_demonym_entities` (generalizzato per iterare anche `LOCATION_ALIAS_TO_COUNTRY`, non solo `DEMONYM_TO_COUNTRY`) per riclassificare righe esistenti mistipizzate.

**Verificato sul DB reale**: `China`/`Chinese` uniti (China canonico, canonical_name corretto "China" non più "People's Republic of China" isolato); `Europe`/`European` uniti (Europe canonico, canonical_name fixato da "Europe PubMed Central" a "Europe"); `EU` resta distinta come `organization` — 3 nodi confusi → 2 nodi corretti e distinti (continente vs organizzazione). Bonus: `Asia`/`Asian` e `Africa`/`African` uniti allo stesso modo (nessuna corruzione Wikidata nota lì, ma stessa asimmetria di Classe A risolta preventivamente).

**Nota per il futuro (ribadita):** l'utente ha ragione — questi sono pattern strutturali, non entità singole. Il fix di Classe A si applica a **tutti** i paesi già in `DEMONYM_TO_COUNTRY`/`LOCATION_ALIAS_TO_COUNTRY` (non solo Cina); il fix di Classe B copre solo 3 continenti curati (Europe/Asia/Africa), non Oceania/Antartide/Americhe (quest'ultima già occupata da "American"→United States, ambiguità pre-esistente non toccata). Restano probabili altre coppie non ancora osservate — stessa avvertenza di CP-019.

---

## CP-021: ordine greedy in story-linking blocca merge validi quando un'entità è quasi-hub — **RISOLTO 2026-07-12**

**Contesto:** ispezionando `study_17` (sezione cluster), l'utente ha notato che il gruppo di cluster "5d" della top-10 include titoli palesemente della stessa macro-storia (trattativa Iran-USA: mediazione Qatar/Pakistan, riapertura Stretto di Hormuz, "sticking points", "deal quasi completo") — 4-5 micro-eventi distinti mai uniti da `pathos story`.

**Verifica**: tutti condividono `Trump` come entità persona canonica. Eseguito `pathos story` di nuovo (27 nuove storie formate altrove, quindi l'algoritmo funziona) — ma il caso Iran-deal specifico (eventi 121960+122131) resta non unito nonostante superi **entrambi** i gate individualmente: similarità embedding diretta 0.847 (soglia 0.82), span temporale combinato 3 giorni (finestra 10). Causa: `Trump` compare in **149 eventi su ~2000** (quasi-hub, non hub totale come nel bug v1 originale già risolto). Il grafo di coppie-candidate diventa enorme (~13700 coppie totali che condividono almeno una persona) e l'algoritmo le processa greedy per gap temporale crescente — un merge con gap più piccolo ma sbagliato, elaborato prima, può allargare un gruppo abbastanza da far fallire il gate complete-linkage quando arriva il turno della coppia corretta. Union-find è irreversibile: un merge subottimale iniziale non si corregge più.

**Scala misurata (con cautela)**: audit isolato (coppia-vs-coppia, non gruppo-vs-gruppo) trova 683 coppie evento che condividono una persona e superano entrambi i gate — di queste solo 298 sono finite nella stessa storia finale, 385 "mancate". **Ma questo numero è sovrastimato**: l'audit isolato replica esattamente il punto cieco (coppia-vs-coppia invece di gruppo-vs-gruppo) che `story.py` stesso è stato costruito per evitare — molte delle 385 sono probabilmente respinte **correttamente** dal vero algoritmo (che valuta l'intero gruppo, non la coppia isolata), verificato campionando: es. "Watch: Why is Trump not at the World Cup?" vs "What Even Is Trump's China Strategy?" condividono Trump e superano la soglia isolata ma sono chiaramene argomenti slegati. L'unico caso **confermato concretamente** come merge valido bloccato dall'ordine è la coppia Iran-deal (121960+122131).

**Perché non bloccante (prima del fix)**: non era una regressione — `story.py` produceva risultati corretti nella stragrande maggioranza dei casi, nessun mega-blob. Una limitazione di qualità nota (sub-ottimalità dell'ordine greedy), non un bug di correttezza.

**Fix applicato**: `sorted_pairs` in `link_related_events` ora ordina per `(gap temporale crescente, similarità decrescente)` invece di solo gap crescente — a parità di gap (frequentissimo con un'entità quasi-hub: centinaia di coppie a gap=0), la coppia con similarità embedding più alta viene processata per prima, invece di lasciare l'ordine a un dettaglio implementativo (ordine di iterazione di un `set` Python). Il resto dell'algoritmo (gate finestra temporale, gate complete-linkage gruppo-vs-gruppo) resta identico — nessuna modifica ai criteri di accettazione, solo all'ordine in cui le coppie candidate vengono provate.

**Verificato empiricamente sul DB reale**: backup pre-fix (`pathosphere_backup_20260712_183828_pre_cp021_reorder.db`), reset completo di `events.story_id` e riesecuzione da zero di `pathos story`. Risultato: 125 storie formate (199 eventi collegati), distribuzione dimensioni sana (2→81 storie, 3→29, ..., max 8, media 2.6) — **nessun mega-blob**, stesso ordine di grandezza di prima del fix. Il caso specifico segnalato (evento 121960 "No final agreement on deal with US–Iran") ora include correttamente 122131 ("US-Iran deal could be sealed within 24 hours") + altri 2 eventi coerenti sulla stessa trattativa — merge che prima non avveniva. Ispezionate a campione altre 2 storie da 6 eventi (funerale Khamenei, dichiarazioni Cremlino su Ucraina) — entrambe internamente coerenti.

**Non completamente risolto**: 122059 (Stretto di Hormuz) e 122072 ("sticking points") restano separati dal gruppo 121960 — non necessariamente un problema: potrebbero essere sotto-angolazioni che non superano la soglia di similarità 0.82 contro l'intero gruppo, comportamento plausibile e sicuro (evita di forzare angolazioni diverse in un blob artificiale, stesso principio conservativo di v3).

**Test**: 1 nuovo (`test_ties_on_time_gap_prefer_higher_similarity_pair`, verifica che a parità di gap temporale vinca la coppia con similarità più alta), 498 totali verdi.

---

## CP-022: eventi RSS non geolocalizzati — nessuno step deriva `location_name` — **RISOLTO 2026-07-14**

**Contesto**: dashboard Streamlit (Fase 4) — utente nota che sulla mappa Cuba/Venezuela mostrano solo
terremoti USGS, mai le notizie politiche/economiche pur presenti nel DB (15+ articoli Cuba, 15+
Venezuela).

**Causa**: `location_name` è scritto solo dagli ingestor geo-nativi (USGS/FIRMS/PortWatch, coordinate
nel dato grezzo). Nessuno step deriva `location_name` per eventi `origin='rss'` dall'entità location
dominante nel cluster — `geocode_events()` (`extract.py:783`) filtra `WHERE location_name IS NOT
NULL`, quindi 0/1996 eventi RSS (e 0/219 IODA) vengono mai geolocalizzati. Non è un problema di
rate-limit/budget Nominatim, è uno step mancante.

**Regola richiesta dall'utente** (non triviale — richiede comprensione del ruolo semantico, non solo
conteggio entità):
- Relazione bilaterale/multilaterale tra grandi potenze (USA-Iran, USA-Israele) → **non
  geolocalizzare**.
- Un solo paese menzionato → geolocalizza lì.
- Attore agisce su bersaglio *tramite* un terzo paese (USA→Cuba via Venezuela) → geolocalizza sul
  **bersaglio**, non su attore né mezzo.

**Validazione fatta** (`notebooks/study_19_rss_event_geolocation.ipynb`, no scrittura DB):
- Euristica su conteggio country-entity (major-power set data-driven, top-8 per n_docs) risolve solo
  **38%** (641/1690) degli eventi RSS con almeno 1 entità location; **59%** (1002) restano ambigui
  (in parte rumore NER, in parte casi genuinamente difficili tipo mediatori Pakistan/Qatar/Svizzera).
- Qwen3 4B locale (Ollama, già cablato in `pathosphere/llm/client.py`) testato su titolo, 2 casi reali
  motivanti l'indagine: **entrambi corretti** (Cuba→Cuba ignorando rumore Venezuela; US-Iran→null).
- **Latenza reale misurata: 90-113s/chiamata** sotto pressione di memoria della sessione di sviluppo
  (Jupyter+Ollama+Claude Code insieme su 8GB, ~4.5GB già wired). Va ri-misurata a macchina scarica
  prima di impegnarsi su un backfill storico (~1000 eventi ambigui × ~100s ≈ 28h in serie all'attuale
  velocità — improponibile interattivo, plausibile solo come batch notturno offline, stesso pattern
  di `pathos loop`).

**Implementato** (`pathosphere/semantic/extract.py`, sessione 2026-07-14, branch `feat/fundamentals-analysis`):

- **Step 1 — `geolocate_rss_events()`**: euristica gratis/istantanea/no-rete, gira sempre in
  `pathos extract` PRIMA di `geocode_events()` (invariata). `MAJOR_POWERS` ricalcolato a runtime
  (top-8 country/location entity per documenti distinti), non lista fissa.
- **Step 2 — `geolocate_ambiguous_events_qwen()`**: fallback Qwen3 4B locale per i casi `ambiguous`,
  **non** nel flusso di default — comando esplicito `pathos extract --geolocate-qwen [--geoloc-limit N]`
  (default 20/run). Riprendibile via nuova colonna `events.geoloc_checked` (migration idempotente):
  un evento è marcato esaminato appena ha una risposta definitiva (location trovata O "nessun
  bersaglio" confermato), mai ririprovato; solo un fallimento di rete/JSON lascia `geoloc_checked=0`
  per retry al batch successivo.

**Latenza ri-misurata a macchina scarica** (nessun Jupyter/IDE aperti insieme, solo Ollama): **46.7s
per chiamata singola** — meglio dei 90-113s originali sotto pressione di memoria, ma ancora lento
(cold-start Qwen3 4B su 8GB M1). Conferma: batch esplicito piccolo, mai sincrono in `pathos extract`
di default.

**Eseguito sul DB reale** (solo Step 1 euristica — Step 2 Qwen NON eseguito sul backlog storico
completo in questa sessione, fuori scope, ~1300 chiamate × 47s ≈ 17h):

| Decisione | N | % |
|---|---|---|
| `located` (`location_name` scritto) | 870 | 32% |
| `ambiguous` (in attesa di `--geolocate-qwen`) | 1324 | 49% |
| `skip_bilateral` (solo grandi potenze, es. USA-Iran) | 74 | 3% |
| `skip_none` (nessuna entità location) | 421 | 16% |

Totale eventi RSS valutati: 2689 (il corpus è cresciuto rispetto ai 1996/1690 del notebook di
validazione — ingest continuato tra le due sessioni). `MAJOR_POWERS` calcolato su questo run: China,
India, Iran, Israel, Japan, Russia, Ukraine, United States.

**Test**: 16 nuovi in `test_extract.py` (535 totali verdi), tutto mockato (nessuna chiamata rete/Ollama
reale in pytest). Ruff pulito sui file toccati (12 violazioni pre-esistenti invariate, non introdotte
da questo fix — F541/F841/E741 sparse in `extract.py`/`cli.py`/`test_extract.py`, fuori scope).

**Valutazione critica (limiti reali, non nascosti)**:
1. **L'euristica dipende dalla qualità del NER a monte** — rumore (nomi propri/date/frammenti taggati
   come location) inquina il conteggio "minor" e può spingere un evento verso `ambiguous` invece che
   `located`, o viceversa assegnare un `location_name` sbagliato se il rumore è l'unica entità location
   di un evento altrimenti a 0 entità reali.
2. **`MAJOR_POWERS` non è stabile nel tempo** — ricalcolato ogni run sul corpus corrente; un paese può
   entrare/uscire dal top-8 man mano che il corpus cresce, il che significa che la classificazione
   `located`/`ambiguous`/`skip_bilateral` di uno stesso evento **può cambiare se rieseguito** in futuro
   (non è un problema di correttezza per un evento già scritto — `location_name` non viene mai
   sovrascritto una volta popolato — ma va tenuto a mente per l'audit storico).
3. **Validazione Qwen resta a 2 campioni reali** (quelli del notebook che hanno motivato l'indagine,
   Cuba e Iran) — non è validazione statistica. Il batch va monitorato a campione quando gira per
   davvero su volumi più grandi, non dato per corretto sulla fiducia del design.
4. **Backfill storico non eseguito**: 1324 eventi `ambiguous` restano senza `location_name` finché
   qualcuno non lancia `pathos extract --geolocate-qwen` ripetutamente (20/run default) — a 46.7s/call
   sono ~17h di chiamate seriali per smaltire tutto il backlog attuale. Pattern consigliato: batch
   notturno via `--geoloc-limit` più alto (es. 200-300) lanciato con `caffeinate -i`, non un singolo
   giro interattivo.
5. **`geocode_events()` invariata** (nessun rischio di regressione lì) — riceve semplicemente più
   `location_name` da geolocalizzare via Nominatim, stesso rate-limit 1 req/s di prima.

**Impatto**: basso/medio — non blocca nulla di esistente (dashboard già gestisce eventi non
geolocalizzati mostrando solo quelli con lat/lon), ma la mappa sottorappresenta sistematicamente
tutte le notizie politiche/economiche a favore dei soli segnali fisici (terremoti/incendi/chokepoint).

---

## CP-031: dashboard pagina Predizioni — KeyError `overall` con predizioni presenti — **RISOLTO 2026-07-17** (branch `fix/cp031-predictions-keyerror`)

**Contesto:** `dashboard/views/predictions.py` leggeva `calib["overall"]["count"]` /
`["mean_brier_score"]` / `["mean_time_adjusted_score"]`, ma `get_calibration()`
(`agent/predictions.py`) non restituisce una chiave `overall`: le metriche aggregate sono
top-level (`total_resolved`, `mean_brier_score`, `mean_time_adjusted_score`). La pagina
funzionava solo nello stato vuoto (guard `open_df.empty and resolved_df.empty` → return
prima del crash): con ANCHE UNA sola predizione nel DB, KeyError immediato. Scoperto
en passant nella sessione scenari 2026-07-16 (la verifica AppTest della Fase 4 era stata
fatta con 0 predizioni), fix rimandato; il DB reale ora ha predizioni → pagina rotta.

**Fix:** chiavi allineate a quelle reali (`total_resolved` come guard, metriche top-level).
Nessuna modifica a `get_calibration()` — il resto della pagina (buckets/accuracy) usava già
le chiavi giuste.

**Verifica:** repro AppTest (scratchpad) su DB temporaneo: pre-fix KeyError riprodotto con
1 predizione aperta; post-fix pagina renderizza senza eccezioni sia con sola predizione
aperta sia con predizione risolta (ramo calibrazione + chart esercitati). Ruff pulito,
498 test verdi invariati (pagina senza test pytest dedicati, come da nota Fase 4).
## CP-023: fondamentali yfinance — degradazione silenziosa e dati non cross-verificati (aperto)

**Contesto:** `pathosphere/market/fundamentals.py` arricchisce le tesi con ratio/Altman Z/Piotroski F
da yfinance. Per design degrada senza mai bloccare `generate_theses` (`None`/campi mancanti + warning
nei log). Due rischi strutturali noti:

1. **Degradazione silenziosa**: se Yahoo rate-limita o i ticker proposti sono non-USA/small-cap
   (statements vuoti — issue yfinance #2584), l'enrichment può restare degradato per giorni e
   l'unico segnale è un warning nei log. Nessun retry/backoff in v1.
2. **Nessun cross-check**: line item Yahoo a volte disallineati di un anno → Z/F calcolati su dati
   sbagliati senza possibilità di accorgersene. SEC EDGAR (cross-check per USA-filer) rimandato a v2.

**Workaround:** `pathos fundamentals <ticker>` per ispezione manuale; campo `data_quality`
(full/partial/minimal/none) salvato in `theses.fundamentals_json` — monitorare la distribuzione dopo
i primi run reali per decidere se EDGAR v2 vale lo sforzo.

**Impatto:** basso — è un livello di contesto, non decisionale; il testo renderizzato dichiara i
caveat. Ma un assessment LLM basato su numeri sbagliati può ancorare male l'umano in approvazione.

---

## CP-024: loop notturno automatico (launchd) non parte mai — permessi macOS (aperto)

**Contesto:** `com.pathosphere.loop` (installato via `scripts/setup_launchd.sh`, vedi CP-017) risulta
caricato (`launchctl list`) ma `LastExitStatus=19968` (78 dopo shift, `EX_CONFIG`), `data/logs/launchd.log`
vuoto. `data/logs/launchd_error.log` pieno di `Operation not permitted` su `.venv/bin/activate`.

**Causa:** non è un bug di codice — macOS (TCC/privacy) blocca l'accesso di processi background
(`launchd`→`bash`) alla cartella `~/Documents` (dove vive il repo) senza permesso esplicito. Il comando
identico funziona perfettamente lanciato a mano da terminale (sessione interattiva con permessi utente).

**Workaround:** nessuno via codice. Richiede azione utente in **System Settings → Privacy & Security →
Full Disk Access** — aggiungere `/bin/bash` (o il processo che esegue il job) alla lista consentita.

**Impatto:** alto per l'automazione — il ciclo notturno documentato come "attivo" in HANDOFF/roadmap
in realtà non ha mai girato da solo; ogni ciclo finora eseguito è stato lanciato a mano
(`caffeinate -i uv run pathos loop ...`). Finché non risolto, l'ingestione/pipeline si ferma se
nessuno lancia il comando manualmente.

**Azione:** nessuna per l'agent — segnalato per azione utente. Dopo la concessione permessi, verificare
con `launchctl kickstart -k gui/$(id -u)/com.pathosphere.loop` e controllare che `launchd.log` si popoli.

---

## CP-025: brief mattutino senza contenuto narrativo nei giorni senza divergenze — **RISOLTO 2026-07-14**

**Contesto**: primo `pathos thesis generate` reale della storia del progetto (2026-07-14). Il brief
generato aveva 0 divergenze, 10 hub entities (solo numeri di grado), 12 anomalie fisiche (terremoti/
IODA) — **zero contenuto narrativo reale**, nonostante **1846 eventi RSS reali** negli ultimi 7 giorni
nel DB (Bulgaria-coalizione Ucraina, UE su pulizia etnica, Al-Shabaab...). Claude, correttamente, si è
rifiutato di fabbricare tesi da segnali così deboli, spiegando il motivo in prosa (vedi CP-026 per la
gestione di quel rifiuto).

**Causa**: `brief.py::_query_divergences` pesca solo da `narrative_divergences` (score > 0.5) — un
segnale specifico e spesso vuoto (749 righe totali nel DB, ma 0 in finestra 7gg quel giorno). Non
esisteva nessuna query di fallback per "eventi RSS recenti in generale", indipendente dal fatto che
sia stata rilevata una divergenza narrativa tra blocchi.

**Fix**: nuova `_query_recent_events()` in `brief.py` — eventi `origin='rss'` recenti (lookback
configurabile), ordinati per copertura fonti (`COUNT(document_id)` via `event_documents`) poi
recency, top 12. Nuova sezione `## RECENT EVENTS` nel prompt, **sempre popolata quando esistono
eventi RSS nella finestra**, indipendente da `narrative_divergences`. `event_count` persistito ora
somma anche questa sezione.

**Verificato sul DB reale**: rilanciato `pathos brief` per il 2026-07-14 — 12 recent events (Hormuz
kinetic escalation, morte Lindsey Graham, verdetto Le Pen...) invece di 0 sezioni narrative. Il
successivo `thesis generate` ha prodotto 7 tesi reali e sensate (vedi CP-026).

**Test**: 8 nuovi in `test_brief.py` (query + integrazione prompt), 546 totali verdi a questo punto
della sessione.

---

## CP-026: `claude -p` eredita CLAUDE.md/hooks del repo — output contaminato + JSON in fence non gestito — **RISOLTO 2026-07-14**

**Contesto**: stesso primo run reale. Il brief generato conteneva testo fuori formato prima E dopo il
Markdown atteso: *"Brief pronto, salvato in scratchpad. Contenuto sotto (Markdown, tono analitico...
non caveman, formato per uso pipeline)"* in testa, e *"File salvato: `scratchpad/brief_2026-07-14.md`.
Dimmi se serve integrarlo nel modulo `brief.py`..."* in coda — il modello si comportava come una
sessione di coding agentica (menzionando file "salvati in scratchpad", chiedendo se integrare nel
codice) invece di una pura completion testuale.

**Causa**: `pathosphere/llm/client.py::_run_claude_subprocess` chiamava `subprocess.run(["claude",
"-p", prompt], ...)` **senza isolarlo dal repo** — il processo `claude -p` lanciato dalla working
directory del progetto carica automaticamente `CLAUDE.md` (incluso il caveman-mode e le istruzioni di
workflow da coding-agent) più hook/skill/plugin del progetto, contaminando ogni completion generata
dalla pipeline (brief, tesi, review fondamentali, debate persona — ogni chiamata `json_mode`/testuale
via backend `claude`).

**Fix 1 — isolamento**: aggiunti `--safe-mode --tools=` alla chiamata subprocess. `--safe-mode`
disabilita CLAUDE.md/hook/skill/plugin per la sessione mantenendo **auth OAuth intatta** (verificato:
nessun `ANTHROPIC_API_KEY` impostato in questo ambiente — il progetto usa credito abbonamento via
login, non API key diretta, per design in CLAUDE.md). **Deliberatamente NON `--bare`**: quel flag
richiede *esplicitamente* `ANTHROPIC_API_KEY`/`apiKeyHelper` e non legge mai OAuth/keychain — avrebbe
rotto l'autenticazione dell'intero progetto. `--tools=` disabilita l'accesso a strumenti file/bash,
garantendo che ogni chiamata resti una pura completion testuale, mai una sessione con effetti
collaterali sul filesystem (verificato: nessun file reale scritto fuori controllo, la menzione
"scratchpad" era contaminazione testuale, non un tool-call effettivo).

**Fix 2 — fence JSON non gestito**: scoperto lanciando `thesis generate` col brief ora pulito — Claude
ha prodotto JSON valido e ben formato ma **avvolto in un fence ` ```json ... ``` `**, nonostante il
system prompt dica esplicitamente "no markdown fences". Ogni chiamante `json_mode=True` (thesis.py×2,
debate.py×4, extract.py×1 per il fallback Qwen geoloc) faceva il proprio `json.loads(raw)` senza
gestire questo caso — con la gestione graceful appena aggiunta per CP-022bis (vedi sopra, thesis.py)
questo veniva silenziosamente scambiato per un "rifiuto" del modello invece che per un parsing
fallito su contenuto valido. Fix centralizzato in `LLMClient.complete()`: nuovo `_strip_json_fence()`
applicato automaticamente a **ogni** risposta quando `json_mode=True`, prima di restituirla al
chiamante — un solo punto di fix per tutti i consumer presenti e futuri, invece di ripetere lo
strip in ognuno.

**Verificato sul DB reale, end-to-end**: rilanciato `pathos brief` (output pulito, parte
direttamente con `# Intelligence Brief`) poi `pathos thesis generate` — **7 tesi reali persistite**
(3 primarie + 4 alternative: BZ=F long/short su escalation Hormuz, FRO long/short su tanker
war-risk, ITA long/short/short su settore difesa), fundamentals review batch completato (1 chiamata
Claude), nessun rifiuto, nessuna contaminazione residua.

**Non esplorato**: se la stessa contaminazione affligge anche il backend `qwen-local` (system prompt
via Ollama non ha accesso a CLAUDE.md per costruzione — improbabile lì, ma non verificato
esplicitamente). Impatto storico sconosciuto: ogni brief/tesi/review generata **prima** di questo fix
(nessuna in produzione — questo era il primo run reale) non esisteva, quindi nessun dato storico da
correggere.

**Test**: 8 nuovi in `test_llm_client.py` (regression guard su flag subprocess + fence-stripping),
554 totali verdi.

---

## CP-029: `pathos thesis debate` — non la concorrenza, è la velocità del modello, variabile e crescente nel tempo — **APERTO, in handoff** (3 run reali falliti, id 1/2/3; timeout 1800s + retry implementati, da validare con run reale)

**Contesto (2026-07-14)**: primo run reale di `pathos thesis debate` (mai lanciato prima d'ora — verificato,
nessuna traccia nei log). Crashato allo Step 1 (research) con `httpx.ReadTimeout` dopo esattamente 120.0s
(20:48:49 → 20:50:49). Ollama era attivo e raggiungibile (`curl localhost:11434/api/tags` OK) — non è
CP-003 (server giù).

**Prima ipotesi (parziale, poi smentita in parte)**: `run_debate()` lanciava le 6 analisi persona via
`asyncio.gather`, tutte e 6 vere richieste HTTP concorrenti allo stesso `qwen3:4b` su Ollama — contraddice
il vincolo hardware esplicito in CLAUDE.md ("un solo modello locale in memoria alla volta, mai due in
parallelo"). Fix applicato su richiesta utente ("mandiamo le richieste a 2 a 2"): nuovo helper
`_gather_in_batches()` (`agent/debate.py`) — batch di `QWEN_BATCH_SIZE=2`, un batch aspetta il precedente
prima di partire. Timeout httpx per-chiamata alzato 120s→300s (`llm/client.py:103`). Test dedicati:
`test_gather_in_batches_caps_concurrency`, `test_gather_in_batches_waits_for_batch_before_next`,
`test_complete_qwen_uses_300s_timeout`.

**Root cause vera, scoperta rilanciando il debate reale dopo il fix**: il fix sopra non basta — secondo run
reale, timeout di nuovo a 300.0s esatti, anche a batch di 2. Misurata poi UNA sola chiamata Qwen isolata
(zero concorrenza) con un prompt di ricerca realistico (brief 5771 char, prompt totale ~6000 char, risposta
strutturata 6 campi incl. narrativa 2 paragrafi): **318.7 secondi**. I numeri di CP-022 (46-113s) erano per
un prompt minuscolo (classificazione location da titolo, output 1 campo) — non rappresentativi del prompt
di ricerca debate, molto più pesante da generare. **Il collo di bottiglia non è la concorrenza, è la
velocità pura di qwen3:4b q4 su M1 8GB CPU-bound per questo tipo/dimensione di prompt.** Batch di 2 o
esecuzione seriale non cambiano il tempo totale in modo determinante: anche seriale, 13 chiamate (6
research + 1 divergence + 6 critique, quest'ultimo con contesto ancora più grande) × ~300-500s stimati ≈
**60-90+ minuti totali** per un intero debate.

**Impatto**: alto per la feature — `pathos thesis debate` (pipeline multi-prospettiva, principio "pluralità
di prospettive" di CLAUDE.md) è tecnicamente funzionante ma **troppo lento per uso interattivo** su questo
hardware; il fast path `pathos thesis generate` (1 sola chiamata Claude, nessun Qwen) resta l'unico percorso
rapido oggi.

**Decisione presa con l'utente**: non è un bug di codice risolvibile — è un limite strutturale
hardware/modello per questo prompt. Scelta: **timeout+documentazione, nessuna riduzione di qualità**
(scartate le opzioni "accorcia prompt"/"meno personas"/"modello più piccolo" — a costo di qualità
dell'analisi, non necessarie per ora).

**Fix finale**: timeout httpx per-chiamata Qwen alzato 300s→**900s** (`llm/client.py:103`, margine ~3x
sopra i 318.7s misurati, per assorbire prompt ancora più grandi negli step successivi — divergence
aggrega 6 analisi, critique include il contesto divergence). Docstring di `pathos thesis debate`
(`cli.py`) aggiornata con avviso esplicito: comando SOLO background/overnight (`caffeinate -i uv run
pathos thesis debate &`), mai interattivo — stesso pattern già adottato per `--geolocate-qwen` (CP-022).
Nessuna modifica a prompt/numero personas/modello — qualità dell'analisi invariata, si accetta il costo
tempo (60-90+ minuti/run) come caratteristica nota del comando, non un difetto da eliminare.

**Verificato**: 582 test verdi (invariato rispetto al fix batching — `test_complete_qwen_uses_900s_timeout`
sostituisce/rinomina la versione 300s, nessun test netto in più). Ruff pulito sui file toccati. Il
fix del batching (`_gather_in_batches`, CP-029 fix 1) resta comunque corretto e testato — necessario ma
non sufficiente da solo, la vera causa era la latenza per-chiamata non la concorrenza.

**Secondo run reale con timeout 900s (2026-07-14 sera, id=3)**: partito 21:26:13. Step 1 research
(batch di 2) completato con successo alle 22:03:20 — **~37 minuti per 6 chiamate**, nessun errore
stavolta (conferma che il batching a 2 + timeout 900s risolve Step 1). Step 2 divergence (1 chiamata)
completato alle 22:13:06 — 9:46 min, sotto soglia. **Step 3 critique è fallito di nuovo**,
`httpx.ReadTimeout` esattamente a 900.0s (22:13:06→22:28:06).

**Osservazione chiave che smentisce l'ipotesi "prompt più grande = più lento"**: il prompt di critique
(`_critique_prompt`, `agent/debate.py:243`) è **più piccolo** del prompt di research — solo i 2 punti di
divergenza (brevi) + la propria narrativa precedente, niente brief intero da 5771 caratteri. Eppure ha
impiegato più di 900s, contro i ~370s/chiamata stimati per il research (più pesante) nello stesso run.
**La latenza non dipende solo dalla dimensione del prompt — cresce con la durata della sessione**,
coerente con la variabilità già documentata in CP-022 (46s vs 90-113s a seconda della pressione di
memoria) ma qui il grado di variazione è molto più ampio (370s → 900s+ nello stesso run, stessa
macchina, ~50 minuti dopo l'inizio). Ipotesi non verificate: throttling termico su M1 sotto carico CPU
sostenuto, degrado memoria/swap accumulato, o interferenza di altri processi attivi nella sessione
(Claude Code stesso, test pytest lanciati in parallelo durante l'attesa).

**Nessun dato sporco**: `debates` id=1,2,3 tutte correttamente `status='failed'`, nessuna tesi/trade
orfana (verificato via query diretta).

**Conclusione onesta**: il fix "timeout+doc" del primo tentativo era prematuro — dichiarato risolto
prima di una validazione end-to-end riuscita, poi smentito dal secondo run reale. **Non richiudere
CP-029 come risolto finché un run completo (tutti e 4 gli step) non arriva a `status='complete'`.**

**Opzione 1 IMPLEMENTATA (2026-07-14 notte, sessione successiva)**: timeout 900s→**1800s** + **retry
automatico 1 volta su `ReadTimeout`** per chiamata (`llm/client.py`, costanti `_QWEN_TIMEOUT_S` /
`_QWEN_READ_TIMEOUT_RETRIES`). Razionale: un timeout può essere un picco transitorio, non un limite
duro — un singolo retry li distingue senza grande complessità; 1800s assorbe i picchi peggiori
osservati (>900s). 3 test dedicati (`test_complete_qwen_uses_1800s_timeout`,
`test_complete_qwen_retries_once_on_read_timeout`,
`test_complete_qwen_raises_after_second_read_timeout`) — 584 test verdi. Verificato prima nel DB:
nessun run nuovo dopo id=3, ultimo status sempre `failed`.

**Opzioni restanti, non ancora fatte**:
2. Investigare la causa della crescita di latenza nel tempo (throttling? altri processi?) —
   rilanciare a macchina totalmente scarica (nessun altro processo, non durante una sessione Claude
   Code attiva) come test di controllo.
3. Accettare il costo e lanciare overnight con margine molto ampio, monitorando `data/logs/` al mattino
   invece di aspettare in sessione.

**Azione**: prossimo run reale lanciato manualmente dall'utente (vedi prompt di ripresa in
`HANDOFF.md`) — CP-029 si chiude SOLO con `debates.status='complete'` verificato nel DB. Peggior caso
teorico con nuovi parametri: 13 chiamate × 1800s × 2 tentativi ≈ 13h — improbabile, ma lanciare
overnight con `caffeinate`.

---

## CP-027: nessuna fonte di dati storici — **PARTE EVENTI RISOLTA 2026-07-14** (branch `feat/historical-events-backfill`), parte prezzi ancora aperta

**Aggiornamento 2026-07-14 (sessione backfill storico)**: implementata la parte 1 (eventi storici).
Decisione chiave: **GDELT scartato come fonte storica** — documenti sintetici da codici CAMEO senza
prosa reale (CP-016), inutilizzabili per mappa e clustering. Scelte 4 fonti aperte, gratuite e
verificabili con testo/coordinate genuini:

1. **UCDP GED** (`ingest/ucdp.py`, `pathos ingest ucdp`) — conflitti armati 1989→, lat/lon precisi.
   CSV zip aperto ~29 MB / 250 MB raw, ~381k righe (l'API REST ora richiede token, il download no).
   Filtro `--min-deaths` default 25 → ~15.8k eventi (≥100 → ~3k). Streaming read, `--csv-path` per
   riusare un download già fatto.
2. **WHO Disease Outbreak News** (`ingest/who_don.py`, `pathos ingest who-don`) — epidemie 1996→,
   OData API aperta, prosa reale in `Overview`, paese dal titolo (separatore en-dash) →
   `location_name`, lat/lon NULL risolti dal geocoder della fase extract. Resume incrementale.
3. **ReliefWeb v2** (`ingest/reliefweb.py`, `pathos ingest reliefweb`) — disastri naturali 1981→
   (UN OCHA). Scoperto durante il probe: v1 dismessa, v2 rifiuta appname non registrati — serve
   `RELIEFWEB_APPNAME` in `.env` (registrazione gratuita, **passo umano ancora da fare**:
   https://apidoc.reliefweb.int/parameters#appname); senza, skip graceful (pattern FIRMS).
4. **Wikidata SPARQL** (`ingest/econ_crises.py`, `pathos ingest econ-crises`) — crisi economiche/
   finanziarie (Q3733076/Q290178/Q176494, P580/P585, paese P17 con coordinate P625); crisi
   multi-paese (>3) salvate come `location_name='global'` senza punto; QID nel summary per
   verificabilità.

Tutte scrivono direttamente in `events` (dedup `(title, first_seen)`, idempotenti) — **niente
`raw_documents`/embedding**: lo storico è statico, serve per mappa e future "situazioni" (Fase 5),
non come input di clustering live. Nessuna modifica schema. 19 test nuovi → 603 verdi, ruff pulito
sui file toccati. Run reale di backfill: lanciato dall'utente da terminale (non ancora eseguito al
momento della scrittura).

**Resta aperto di CP-027**: parte 2 — serie storiche prezzi (`fetch_price` prende solo l'ultimo
close; yfinance `history(period="max")` disponibile, manca persistenza) e calendario economico
(earnings/CPI/NFP) senza candidato gratuito assegnato.

**Contesto originale (2026-07-14)**: due esigenze emerse in discussione con l'utente, entrambe bloccate dalla
stessa lacuna strutturale — il progetto non persiste **nulla di storico** oltre a ciò che ingerisce
giorno per giorno da quando gira. Non un bug, una fonte dati mai costruita.

1. **Eventi geopolitici storici** — necessari per Fase 5 "situazioni" (`docs/roadmap.md`): per
   riconoscere che la guerra Ucraina è iniziata il 2022-02-24, o distinguere Crimea 2014 da Golfo 1
   vs Golfo 2, serve sapere quando sono iniziati — questi eventi non sono nel DB (che parte da quando
   l'ingest ha iniziato a girare, molto dopo).
2. **Serie storiche prezzi/economiche** — necessarie per l'idea (discussa, non approvata per ora)
   di correlazione storica eventi↔movimenti di borsa: `market/prices.py::fetch_price` prende solo
   l'ultimo close (finestra 5 giorni), nessuno storico salvato. Senza serie storica non si può
   chiedere "cosa è successo al prezzo X nei giorni dopo l'evento Y accaduto 3 anni fa".

**Non ancora iniziato — nessuna azione presa.** Fonti candidate da valutare quando si apre sessione
dedicata (non decise, solo annotate):
- Eventi storici: GDELT ha storico dal 1979 (stessa fonte già in uso per l'ingest corrente, query
  su range di date passate); Wikidata (`start_date`/`P580` su entità conflitto) per ancoraggi
  affidabili; ACLED/UCDP (oggi scope-out in roadmap per cadenza settimanale, ma potrebbero avere
  dataset storici scaricabili one-off, cadenza non rilevante per uno storico statico)
- Prezzi storici: `yfinance` stesso supporta `history(period="max")`/range di date esplicite — già
  disponibile nella libreria già in uso, manca solo la persistenza (nuova tabella o Parquet, coerente
  col principio già in CLAUDE.md "raw in Parquet è la fonte di verità ricostruibile")
- Eventi economici: nessuna fonte gratuita ancora scelta — FRED è già previsto in CLAUDE.md come
  fonte futura ("non incluso nell'MVP") e potrebbe coprire in parte, ma un calendario economico
  vero e proprio (earnings, CPI/NFP release date) non ha candidato assegnato

**Impatto**: basso ora — nessun blocco su lavoro corrente, entrambe le feature che ne dipendono
(Fase 5 situazioni, correlazione eventi↔prezzi) sono già esplicitamente rimandate. Serve però essere
tracciato per non perdere il filo quando si riprende in mano una delle due.

---

## CP-028: code review pre-merge (8 angoli + verifica indipendente), 10 bug/gap trovati e risolti — **RISOLTO 2026-07-14**

**Contesto**: prima del merge di `feat/fundamentals-analysis` (13+ commit, fundamentals+CP-008/010/
012/022/025/026+auto-open), l'utente ha chiesto una code review vera invece di fidarsi solo di test+
esecuzione reale. Eseguita review strutturata: 8 angoli di ricerca indipendenti (line-by-line,
removed-behavior, cross-file tracer, reuse, simplification, efficiency, altitude, conventions
CLAUDE.md) su `git diff main...HEAD`, poi verifica 1-voto per ogni candidato a bassa-confidenza
(trovato da un solo angolo). 6 bug confermati, 1 plausibile, 3 di efficienza/manutenzione confermati
per consenso multi-angolo. Tutti fixati su richiesta esplicita dell'utente ("fixerei tutto prima").

**1. Crash TypeError su confidence non numerica** (`agent/thesis.py::_maybe_auto_open`) — il confronto
`confidence < threshold` non validava il tipo prima di confrontare; un `confidence` LLM non numerico
(json_mode è un'istruzione di prompt, non uno schema) sollevava `TypeError` non gestita **dopo** che
le tesi erano già committate, esattamente il crash che il fix parallelo per il rifiuto JSON doveva
evitare. Fix: `isinstance(confidence, (int, float))` prima del confronto.

**2. Il ciclo automatico non usava il fix CP-022** (`cycle/orchestrator.py::_phase_extract`) — non
chiamava mai `geolocate_rss_events()`, solo `pathos extract` manuale ce l'aveva. Fix: aggiunta la
chiamata, stesso ordine del comando CLI (prima di `geocode_events`).

**3. `pathos thesis debate` senza fundamentals/auto-open** (`agent/debate.py::_persist_theses`) —
pipeline alternativa mai aggiornata insieme a `generate_theses`, divergeva in silenzio. Fix: riscritta
per riusare le stesse funzioni private di `thesis.py` (`_fundamentals_doc`, `_run_fundamentals_review`,
`_maybe_auto_open`) invece di reimplementarle — ora `async`, nuovi flag CLI `--no-fundamentals`/
`--no-auto-open`/`--auto-open-threshold` su `pathos thesis debate`, pari a `thesis generate`.

**4. Fence-stripping non gestiva testo dopo la chiusura** (`llm/client.py::_strip_json_fence`) — regex
ancorata a fine-stringa mancava testo LLM dopo ` ``` ` (es. "...```\nHope this helps!"), riproducendo
lo stesso crash che il fix di oggi per CP-026 doveva risolvere. Fix: `re.search` non ancorato invece
di `re.match` ancorato — estrae il primo blocco fenced ovunque si trovi, ignora prosa prima/dopo.

**5. Mismatch alias entità in geoloc RSS** (`semantic/extract.py::_rss_event_countries`) — mancava il
filtro/risoluzione `canonical_entity_id` presente nella funzione gemella `compute_major_powers`.
Verificato con dato reale: entità "turkey" (minuscolo, alias) vs "Turkey" (canonica) — mismatch
case-sensitive che avrebbe fatto sfuggire un major power alla classificazione. Fix: self-join
`JOIN entities canon ON canon.id = COALESCE(en.canonical_entity_id, en.id)`, stesso idiom già
stabilito in `graph.py::build_entity_links`.

**6. Auto-open saltava `validate_ticker`** (plausibile, non confermato al 100% — l'errore finale è
comunque specifico, non generico come temuto) — il path manuale (`thesis approve`) validava il ticker
prima di approvare, l'auto-open no. Fix: risolto insieme al punto 7 (vedi sotto).

**7. `_maybe_auto_open` duplicava la sequenza approve+open del CLI** — stesso identico flusso
(`approve_thesis`→`create_thesis_prediction`→`open_agent_trade`→`link_thesis_prediction_to_trade`)
scritto due volte, una in `cli.py` (due comandi separati) e una in `thesis.py`. Fix combinato con #6:
estratte `approve_thesis_with_prediction()` e `open_trade_and_link()` in `agent/approval.py` — unica
fonte di verità usata sia da `pathos thesis approve`/`pathos trade open` sia da `_maybe_auto_open`,
la prima ora include la validazione ticker che prima solo il path manuale aveva.

**8. `get_connection()` rigirava tutte le migration ad ogni chiamata** — trovato indipendentemente da
2 angoli (efficiency + cross-file): 6 chiamate per `pathos cycle`, una per ogni rerun della dashboard
Streamlit (processo long-lived). Fix: cache per-processo (`_migrated_paths: set[Path]`) — un path già
migrato in questo processo salta il giro di ~20 ALTER/CREATE idempotenti su connessioni successive.
Nessun coordinamento cross-processo necessario (migration list additiva e idempotente).

**9. `compute_major_powers()` ricalcolata due volte per run** — trovato indipendentemente da 3 angoli
(simplification + efficiency + altitude): `pathos extract --geolocate-qwen` chiama sia
`geolocate_rss_events` sia `geolocate_ambiguous_events_qwen`, ciascuna ricalcolava da zero la query
più costosa del modulo. Fix: nuovo parametro opzionale `major_powers` su entrambe le funzioni;
`compute_major_powers` (rinominata da `_compute_major_powers`, ora pubblica) calcolata una volta nel
comando CLI e passata a entrambe.

**10. `event_count` doppio conteggio nel brief** (cosmetico, confermato ma zero impatto funzionale —
solo display in log/dashboard, nessuna logica ne dipende) — un evento presente sia in `divergences`
sia in `recent_events` veniva contato due volte. Fix: dedup per `event_id`/`id` prima di sommare.

**Verificato**: 579 test verdi (era 560 prima della review, +19: +11 dai fix di regressione
(orchestrator geoloc, debate fundamentals/auto-open ×3, fence trailing-prose ×2, alias canonical,
migration cache ×2, major_powers passthrough ×2, event_count dedup) + 8 test dedicati diretti per i
punti 1/6/7 (TypeError guard su confidence stringa, validate_ticker verificato chiamato davvero con
mock yfinance, `approve_thesis_with_prediction`/`open_trade_and_link` testate singolarmente:
successo/ticker invalido/nessun ticker/stato non-pending/nessun portafoglio). Ruff pulito su tutti i
file toccati (14 violazioni pre-esistenti sul resto del tree, invariate, verificate riga per riga
come già presenti su `main` prima di questa sessione).

**Metodo**: 8 subagent paralleli per la ricerca candidati, poi 7 subagent di verifica 1-voto
indipendenti sui candidati a bassa confidenza (trovati da un solo angolo) — i 3 candidati trovati
indipendentemente da 2-3 angoli diversi sono stati trattati come già verificati per consenso, senza
ulteriore verifica dedicata.

## CP-030: `_persist_scenario_set` non transazionale — persistenza parziale possibile — **RISOLTO 2026-07-17** (branch `fix/cp030-transactional-scenario-persist`)

**Fix (come da piano sotto)**: `add_prediction(..., commit: bool = True)` — con `commit=False`
salta il `conn.commit()` interno, il chiamante possiede la transazione. `_persist_scenario_set`
ora: corpo in `_persist_scenario_set_inner` con `add_prediction(commit=False)`, UN commit finale;
wrapper con `except Exception: conn.rollback(); raise` — su fallimento a metà nessuna riga
set/scenario/prediction/watchlist sopravvive, e nessuna riga orfana non committata può essere
trascinata nel commit dell'hotspot successivo sulla stessa connessione. Default `commit=True`
invariato per tutti gli altri chiamanti (cli.py, create_thesis_prediction).

**Test**: +3 — rollback intero set su fallimento al 2° scenario (monkeypatch `add_prediction`,
verifica 5 tabelle a 0 righe); `commit=False` + rollback → 0 righe; default committa (sopravvive
a rollback). Suite verde sui file toccati; 4 rossi in `test_doctor.py` pre-esistenti su main
(interazione merge #17/#18, tracciati a parte).

**Contesto (2026-07-16, branch `feat/conflict-forecasting`)**: `agent/scenarios.py::_persist_scenario_set`
inserisce set → per ogni scenario: riga `scenarios` + `add_prediction()` + watchlist. Ma
`add_prediction()` (predictions.py) fa `conn.commit()` internamente → se un insert successivo fallisce
(sqlite error, disco pieno), il set resta `active` ma incompleto (N scenari invece di 3-4, predictions
orfane già committate). Input LLM già validati/normalizzati a monte (probabilità, scope, domini), quindi
il fallimento richiede un errore infrastrutturale, non un input cattivo.

- **Workaround**: review/resolve gestiscono `prediction_id IS NULL`; un set visibilmente monco si
  chiude a mano (`UPDATE scenario_sets SET status='resolved'` o si risolve normalmente).
- **Fix pulito futuro**: variante `add_prediction(..., commit=False)` + transazione unica nel chiamante.
- **Impatto**: basso (paper predictions, nessun denaro; evento raro).

## CP-031: dashboard pagina Predizioni — KeyError `overall` con predizioni presenti (aperto, pre-esistente)

**Contesto (2026-07-16, osservato durante wiring scenari — NON introdotto da questo branch)**:
`dashboard/views/predictions.py` usa `calib["overall"]["count"]` ma
`agent/predictions.py::get_calibration()` non restituisce alcuna chiave `overall` (le chiavi sono
`total_resolved`, `mean_brier_score`, `mean_time_adjusted_score`, `buckets`, `by_macro_area`,
`by_prediction_type`). Appena `predictions` ha righe (oggi 3), la pagina va in KeyError.

- **Fix banale**: `calib["total_resolved"] > 0` + metriche top-level. Da fare in sessione dashboard
  dedicata (file fuori dallo scope del branch scenari).
- **Nota**: la pagina Scenari nuova legge le probabilità direttamente dalle tabelle, non da
  `get_calibration()` — non è affetta.

# Critical Points — Pathosphere

Punti critici aperti: bug noti, decisioni tecniche rimandati, rischi potenziali.
Aggiornare immediatamente quando emerge un nuovo punto. Rimuovere quando risolto.

---

## CP-001: Claude CLI — prerequisito non verificato a runtime

**Contesto:** backend `claude` chiama `claude -p "PROMPT"` via subprocess. Richiede CLI installata e autenticata sulla macchina.

**Workaround:** documentato come prerequisito. Se necessario, aggiungere `_claude_available()` check in `LLMClient.__init__` con fallback a `qwen-local`.

**Impatto:** tool inutilizzabile senza CLI Claude. Errore chiaro in subprocess se assente.

**Azione:** nessuna modifica necessaria ora. Documentare in README se si distribuisce.

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

---

## CP-004: Predictions v2 — separazione tipi mancante nel modello

**Contesto:** design v2 ha formalizzato scoring e tassonomia ma NON la separazione tra predizioni geopolitiche/politiche/sociali ed economiche. Il campo `prediction_type` non è stato aggiunto allo schema.

**Decisione:** due colonne distinte che coesistono:
- `macro_area TEXT NOT NULL CHECK IN ('world','economic')` — guida workflow e metrica
- `prediction_type TEXT NOT NULL CHECK IN ('geopolitical','political','social','economic')` — granularità per filtri e calibrazione per tipo
Relazione: `world` → prediction_type IN ('geopolitical','political','social'); `economic` → prediction_type='economic'. FK `trade_id` opzionale solo per `macro_area='economic'`.

**Azione:** includere in `feat/predictions-v2`.

---

## CP-005: Predictions v2 — backward compat migration

**Contesto:** nuove colonne (`outcome_eventual`, `outcome_on_time`, `resolved_date`, `origin_scope`, `impact_scope`, `time_adjusted_score`) e nuova tabella `prediction_domains`. Predizioni esistenti avranno queste colonne a NULL.

**Workaround:** migration idempotente in `schema.py` via `_MIGRATIONS`. `get_calibration` deve escludere righe con `time_adjusted_score=NULL` o gestire NULL esplicitamente.

**Impatto:** dati storici pre-v2 esclusi da `time_adjusted_score`; `brier_score` ancora disponibile per confronto.

**Azione:** risolvere nell'implementazione `feat/predictions-v2`.

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

## CP-008: ruff F821 — `sqlite3` usato ma mai importato in moduli ingest

**Contesto:** `ruff check` segnala 9 F821 (`sqlite3` undefined) in `ingest/comtrade.py:186`, `ingest/gdelt.py:229,246,389`, `ingest/physical.py:159,404`, `ingest/portwatch.py:255`, `ingest/rss.py:88`, `ingest/sources_seed.py:405` — probabilmente type hints o except su `sqlite3.X` senza import. Più 37 violazioni minori (F401 import inutilizzati, F541, E741), 33 auto-fixabili con `ruff check --fix`.

**Workaround:** i 403 test passano — i path incriminati o non vengono eseguiti nei test o il nome arriva per vie indirette. Rischio: NameError a runtime su path non coperti.

**Impatto:** potenziale crash negli ingestor su path di errore. Fuori scope per `feat/predictions-v2` (nessuna violazione nel diff v2).

**Azione:** branch dedicato `chore/ruff-cleanup` — aggiungere `import sqlite3` mancanti + `ruff check --fix` per il resto.

---

## CP-009: timing_penalty_alpha — cambio invalida comparabilità storica

**Contesto:** `time_adjusted_score` calcolato a risoluzione con alpha corrente (settings o parametro esplicito di `resolve_prediction`). Alpha NON persistito sulla riga: cambiarlo in `.env` rende i nuovi score incomparabili con quelli già risolti.

**Workaround:** gli input per ricalcolare sono tutti persistiti (`probability`, `brier_score`, `resolved_date`, `horizon_date`) — possibile script di ricalcolo retroattivo con alpha uniforme.

**Impatto:** medie di calibrazione miste se alpha cambia a metà storia. Non cambiare alpha senza ricalcolo.

---

## CP-010: migration v2 girano solo con `pathos db init`

**Contesto:** `get_connection` non esegue `migrate_db`; solo `init_db` (comando `pathos db init`, idempotente). DB pre-v2 + codice v2 senza re-init → `sqlite3.OperationalError: no column named macro_area` sui nuovi path.

**Workaround:** dopo ogni pull con modifiche schema: `uv run pathos db init` (sicuro, idempotente).

**Impatto:** crash CLI su DB non migrato. Documentato in HANDOFF comandi utili.

---

## CP-011: embed processa tutto il raw GDELT senza filtro a monte

**Contesto:** `pathos embed` embedda TUTTI i `raw_documents` con `embedded=0` (`semantic/embedder.py`, query senza filtro rilevanza/età). Backfill GDELT 6 mesi → ~169k doc → 1-3+ ore su M1 CPU. Viola il principio "filtraggio aggressivo a monte": la ridondanza GDELT si paga in ore di embedding invece di essere tagliata prima.

**Workaround:** commit per batch (32) → Ctrl+C sicuro, riprende dai residui `embedded=0`. Backfill grossi: lanciare di notte o in assenza. Progresso visibile solo via `sqlite3 data/pathosphere.db "SELECT sum(embedded=1), count(*) FROM raw_documents;"` (log batch è DEBUG).

**Impatto:** ore di CPU per backfill lunghi; nel ciclo notturno incrementale (1 giorno di doc) impatto trascurabile. Fix futuro: filtro pre-embedding (keyword/QuadClass GDELT, dedup URL aggressiva) o embed limitato a finestra recente + progress log a INFO.

---

## CP-012: dedup — transazione unica, non riprendibile, nessun progresso

**Contesto:** `dedup_documents` (`semantic/dedup.py:70`) avvolge l'intero loop in un solo `with conn:` → una transazione per 169k doc. Ctrl+C o crash = rollback totale, riparte da zero (a differenza di embed, commit per batch). Nessun log di progresso tra inizio e fine; progresso invisibile anche da fuori (update uncommitted). KNN sqlite-vec è brute-force: 169k query × scan 169k vettori ≈ ore su backfill grossi.

**Workaround:** non interrompere; lanciare con `caffeinate -i` e lasciar finire. Vitalità verificabile solo via `ps aux | grep pathos` (CPU ~100%).

**Impatto:** backfill grossi fragili (ore di lavoro persi su interrupt). Fix futuro: commit ogni N doc + tqdm, come embed.

---

## CP-013: stoplist Wikidata curata a mano — nuovi termini generici possono emergere
## CP-012: stoplist Wikidata curata a mano — nuovi termini generici possono emergere

**Contesto:** `GENERIC_ENTITY_STOPLIST` (`semantic/extract.py`, ~110 voci) blocca lookup Wikidata per nomi comuni/ruoli/demonimi ALL CAPS prodotti dal NER su testo GDELT (`CRIMINAL`, `MILITARY`, `MALE`…). Lista statica: termini generici nuovi (altre lingue, plurali mancanti) passano il filtro e consumano budget lookup.

**Workaround:** controllare log `Wikidata linking` a inizio run; se compaiono nomi generici, aggiungerli alla stoplist. Entità già linkate male: azzerare `wikidata_qid`/`canonical_name` a mano nel DB.

**Impatto:** basso — 50 lookups/notte, qualche lookup sprecato al peggio. Fix futuro: euristica strutturale (es. skip mono-parola ALL CAPS con match in wordlist inglese) invece di lista enumerata.

---

## CP-014: entità "GDELT" — leak del prefisso titolo sintetico nel NER

**Contesto:** i titoli sintetici GDELT hanno formato `"GDELT: ACTOR → ACTOR2"` (`ingest/gdelt.py`). `_build_text` (`semantic/extract.py`) concatena title+body senza rimuovere il prefisso → il NER tagga la parola letterale `GDELT` come entità ORG/company su quasi ogni documento origin=gdelt. Scoperto in `notebooks/study_02_extract.ipynb`: **entità col maggior numero di mention in assoluto** — 128.082 documenti (73.5% dei doc origin=gdelt).

**Impatto sul grafo:** in `notebooks/study_03_graph.ipynb`, il nodo `GDELT` ha grado 3.962/89.838 archi (4.4%) — hub artificiale, causa diretta più probabile della componente connessa gigante osservata (9.666/10.192 nodi, 94.8%). Inquina anche budget Wikidata (voce già stoplistata come "generica" solo se aggiunta a mano — non matcha `GENERIC_ENTITY_STOPLIST` attuale).

**Workaround:** nessuno applicato (analisi as-is, nessun fix in questa sessione). Aggiungere `GDELT` a `GENERIC_ENTITY_STOPLIST` è un cerotto sul sintomo Wikidata, non risolve l'inquinamento di `document_entities`/`entity_links`.

**Impatto:** alto — singolo artefatto che spiega la maggior parte dell'hairball nel grafo entità. Fix futuro: strip del prefisso `"GDELT: "` in `_build_text` prima del NER, oppure NER solo sul body per documenti origin=gdelt.

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

## CP-022: eventi RSS non geolocalizzati — nessuno step deriva `location_name` (aperto, in validazione)

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

**Non ancora implementato**: nessuna modifica a `extract.py`. Prossimo passo: `geolocate_rss_events()`
(euristica + fallback batch Qwen offline) chiamata da `pathos extract`, poi `geocode_events()`
esistente invariato. Ollama installato ex-novo in questa sessione (`brew install ollama`, non
presente prima) — verificare se debba restare un servizio permanente (`brew services start ollama`)
o essere avviato solo durante il ciclo notturno.

**Impatto**: basso/medio — non blocca nulla di esistente (dashboard già gestisce eventi non
geolocalizzati mostrando solo quelli con lat/lon), ma la mappa sottorappresenta sistematicamente
tutte le notizie politiche/economiche a favore dei soli segnali fisici (terremoti/incendi/chokepoint).

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

## CP-015: frammenti HTML taggati come entità — body non ripulito da markup prima del NER

**Contesto:** scoperto in `notebooks/study_02_extract.ipynb`: entità con `<`/`>` nel nome (es. `span><strong`, `said.</p`) presenti nel DB, generate dal NER su body RSS non sanitizzato. Compaiono anche tra i nodi ad alto grado nel grafo (`notebooks/study_03_graph.ipynb`).

**Workaround:** nessuno applicato (analisi as-is). Filtro manuale possibile lato query (`WHERE name NOT LIKE '%<%'`) ma non risolve alla fonte.

**Impatto:** medio — rumore silenzioso in `entities`/`entity_links`, non blocca nulla ma inquina classifiche e grafo. Fix futuro: strip HTML/markup dal body (es. con `bleach` o regex) prima di `_build_text` in `semantic/extract.py`, idealmente già a monte in ingest RSS.

---

## CP-016: causa radice — pipeline NLP prosa applicata a documenti sintetici GDELT (98.8% del corpus)

**Contesto:** `pathos ingest gdelt` costruisce documenti sintetici da metadata strutturato CAMEO, non da prosa: `title = f"GDELT: {Actor1Name} → {Actor2Name} [{EventCode}]"` (`ingest/gdelt.py:284`), body analogo (righe 330-332). Quando GDELT non identifica un attore specifico, `Actor1Name`/`Actor2Name` sono **codici di ruolo generici** (`PRESIDENT`, `POLICE`, `MILITARY`, `SCHOOL`…), non nomi propri. Il DB reale conferma lo squilibrio: `raw_documents` per origin — gdelt 174.286 (98.8%), rss 1.939 (1.1%), comtrade 252 (0.1%). Pipeline semantica (`embed`→`extract`→`cluster`→`graph`) tratta tutte le origin allo stesso modo, come se fossero prosa.

**Diagnosi (sessione 2026-07-07, studio qualità in `notebooks/`):** questa è la causa unica che spiega CP-014, CP-015, l'hairball nel grafo (94.8% nodi in 1 componente), la tassonomia entità povera (solo person/company/location/other popolati, mai country/commodity/infrastructure nonostante lo schema li preveda), e cluster di eventi che raggruppano ricorrenze di ruoli generici (es. "Evento 1191", 155 doc, titoli tutti tipo `GDELT: CREDIT UNION → ` `GDELT: JUDGE → `) invece di storie reali. **Non sono bug indipendenti**: sono sintomi dello stesso disallineamento architetturale.

**Il segnale numerico reale di GDELT è inutilizzato:** `gdelt_events` ha campi quantitativi (`goldstein`, `avg_tone`, `quad_class`, `num_mentions`, `num_sources`) — verificato via grep: usati SOLO come filtro a monte in ingest (`--max-goldstein`, `cli.py:177`), mai aggregati/analizzati a valle. Il valore vero di GDELT (intensità conflitto/cooperazione nel tempo/spazio) è scritto e mai letto.

**Fix proposto (non applicato in questa sessione — analisi as-is):**
1. Escludere `origin='gdelt'` (e `'comtrade'`) dalle query candidate in `semantic/embedder.py`, `semantic/extract.py` (NER), `semantic/cluster.py`, `semantic/graph.py` (via `document_entities`) — pipeline NLP ristretta a prosa reale.
2. Riusare `pathosphere/ingest/anomaly.py::find_anomalies` (stesso modulo già usato da PortWatch/FIRMS/IODA, pattern trailing-baseline no-lookahead) per promuovere direttamente `gdelt_events` anomali (goldstein/tone aggregati per giorno+paese+quad_class) a `events`, saltando NER/embed/cluster. Template di riferimento: `ingest/portwatch.py` righe 175-214 (`_detect_and_promote` — query serie storica, `find_anomalies(...)`, poi INSERT su `events` con dedup by title).

**Impatto:** alto — root cause architetturale, non un bug isolato. Branch dedicato consigliato: `fix/gdelt-numeric-split` (o `refactor/gdelt-pipeline`).

---

## CP-017: copertura fonti prosa (RSS) — collo di bottiglia è la cadenza, non il catalogo

**Contesto:** `pathosphere/ingest/sources_seed.py` ha già 48 feed RSS attivi, copertura quasi completa della wishlist CLAUDE.md (Global Times, TASS, Al Jazeera, Press TV, Anadolu, The Hindu, Folha presenti; Xinhua deliberatamente escluso — commento nel codice: feed RSS congelati al 2018, verificato e abbandonato). Distribuzione per blocco sbilanciata: western 19/48 (40%), china 5, russia 4, arab 4, india 3, africa 7, latam 1, other 5.

`pathos ingest rss` (`max_age_days=2`, no backfill storico per i feed) è già nel ciclo notturno (`cycle/orchestrator.py:120`), ma il ciclo è stato finora lanciato solo a mano, non schedulato. Risultato: 1.939 documenti RSS totali in circa un mese — non per scarsità di fonti ma per esecuzioni sporadiche (ogni run cattura solo le ultime 48h; il volume si accumula solo con run regolari e ripetuti nel tempo).

**Candidati per riequilibrare i blocchi deboli (verificare vivacità feed prima di aggiungere, vedi precedente Xinhua):**
- LatAm (oggi solo Folha): MercoPress, teleSUR English, Buenos Aires Times
- India (3, tutti mainstream): The Wire (indipendente)
- Africa (7, già ok): Mail & Guardian (SA), The East African

**Impatto:** medio — priorità 1 è schedulare `pathos cycle run` (cron/launchd) per accumulo regolare; ampliare/ribilanciare il catalogo è secondario e va ripetuto nel tempo (i feed RSS gratuiti muoiono senza preavviso).

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

# Architettura — Pathosphere

Sistema personale di intelligence OSINT con paper trading come metrica di valutazione.  
**Mono-utente. Nessun server. Budget quasi zero.**

---

## Problema che risolve

Le crisi geopolitiche (Taiwan, Hormuz, semiconduttori) impattano i mercati con anticipo di giorni o settimane rispetto al consensus. Pathosphere aggrega fonti aperte da tutti i blocchi geopolitici, estrae la semantica, costruisce scenari e li valuta tramite paper trading virtuale. Se l'agent non batte il random, si sa subito.

---

## Vincoli di progetto

| Vincolo | Scelta |
|---|---|
| MacBook Air M1, 8 GB RAM | SQLite, un modello in RAM alla volta |
| Budget quasi zero | Solo dati gratuiti, LLM locale per lavoro di massa |
| Human-in-the-loop | Agent propone, utente approva — nessuna operazione autonoma |
| No lookahead bias | `price_open` immutabile al momento della decisione |

---

## Componenti principali

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI  (pathos)                        │
│   db · sources · ingest · cycle · config                    │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────────┐
│                   Ciclo notturno                            │
│   INGEST → EMBED → EXTRACT → CLUSTER → BRIEF               │
│   (sequenziale, riprendibile da qualsiasi fase)             │
└──────┬───────────┬───────────┬──────────────────────────────┘
       │           │           │
       ▼           ▼           ▼
  ┌─────────┐ ┌─────────┐ ┌──────────────┐
  │ Ingest  │ │Semantic │ │    Agent     │
  │ GDELT   │ │NER+emb  │ │brief+tesi    │
  │ RSS     │ │cluster  │ │paper trading │
  │ ...     │ │grafo    │ │calibrazione  │
  └────┬────┘ └────┬────┘ └──────┬───────┘
       │           │              │
       └───────────┴──────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │   SQLite + sqlite-vec│
        │   data/db/pathos.db  │
        └──────────────────────┘
                   │
                   ▼
        ┌──────────────────────┐
        │  Parquet (storico)   │
        │  data/parquet/       │
        │  (fonte di verità)   │
        └──────────────────────┘
```

---

## Moduli

### `pathosphere/config.py`
Settings da `.env` via pydantic-settings. Singleton `get_settings()`.  
Campi: `db_path`, `parquet_dir`, `ollama_*`, `embed_model_name`, `log_*`.

### `pathosphere/logging_setup.py`
Loguru con output colorato su stderr + rotazione giornaliera su file.  
Chiamato una volta all'avvio dalla CLI.

### `pathosphere/db/schema.py`
DDL SQLite completo + tabella virtuale sqlite-vec.  
`get_connection(path)` — abilita sqlite_vec, imposta FK e row_factory.  
`init_db(path)` — idempotente (`CREATE TABLE IF NOT EXISTS`).

### `pathosphere/cli.py`
Entry point `pathos` (Click). Gruppi:
- `db init` / `db info`
- `sources list` / `sources seed`
- `ingest gdelt` / `ingest gdelt-history`
- `cycle` / `cycle --dry-run` / `cycle --from-phase`
- `config`

### `pathosphere/cycle/orchestrator.py`
Ciclo notturno sequenziale in 5 fasi: INGEST → EMBED → EXTRACT → CLUSTER → BRIEF.  
`run_cycle(start_from, dry_run)` — riprendibile da qualsiasi fase, atomico per fase.  
Fasi 2-5 stub (`NotImplementedError`), pronte per implementazione.

### `pathosphere/ingest/gdelt.py`
Downloader GDELT 2.0 Events (TSV 61 colonne, file ogni 15 minuti).  
Due modalità:
- **Incrementale**: ultimi N giorni, salta file già scaricati (`gdelt_file_log`)
- **Bootstrap storico**: range date, campionamento configurabile (default 1h)

Filtri: QuadClass, NumMentions, GoldsteinScale, paesi ISO-2.  
Dedup: URL esatto per `raw_documents`, chiave semantica per `events`.  
HTTP: httpx + tenacity (3 retry, backoff esponenziale). Ctrl+C safe.

---

## Strategia LLM ibrida

```
Lavoro di massa (gratis)          Ragionamento pesante (~2-3/giorno)
─────────────────────────         ────────────────────────────────────
Qwen3 4B q4 via Ollama            Claude via Agent SDK (credito abbonamento)
classificazione                   brief mattutino
estrazione strutturata            generazione tesi con catene causali
dedup semantica                   scenari multi-prospettiva (ACH)
NER / entity linking              
```

Entrambi dietro un'unica astrazione OpenAI-compatible.  
Cambiare backend = una riga di config. A/B testing possibile.

---

## Flusso dati

```
Sorgenti esterne          Pipeline interna             Output
─────────────────         ────────────────────         ──────────────────
GDELT 2.0 (ogni 15min)  → download + dedup          → raw_documents
RSS multi-blocco        → NER + geocoding           → entities
PortWatch, Comtrade     → embedding e5-small (384d) → vec_documents
USGS, FIRMS, IODA       → clustering → eventi       → events
yfinance (EOD)          → confronto narrazioni      → narrative_divergences
                        → grafo entità              → entity_links
                        → brief + tesi              → theses
                        → approvazione utente       → trades, predictions
                        → EOD prices yfinance       → portfolios (P&L)
```

---

## Database

**SQLite** in locale (`data/db/pathosphere.db`). Un file, zero processi residenti.  
**sqlite-vec** per nearest-neighbour su embedding (384 dim, multilingual-e5-small).  
**Parquet** per storico >90 giorni — fonte di verità ricostruibile, interrogabile con DuckDB.

Evoluzione pianificata: **Turso/libSQL** — drop-in replacement con replica cloud automatica (backup gratis, quasi zero code change).

Dettaglio tabelle → [schema.md](schema.md).

---

## Valutazione del modello

```
Portafoglio agent     — tesi approvate dall'utente
Portafoglio random    — stesse dimensioni, ticker casuali
Portafoglio benchmark — buy & hold SPY

Se agent ≤ random → nessun segnale predittivo reale
```

Calibrazione Tetlock su predizioni non finanziarie:  
`brier_score = (probabilità - outcome)²` — 0 = perfetto, 1 = peggio del caso.

---

## Pluralità di prospettive

Ogni fonte è etichettata con paese, blocco geopolitico e grado di controllo statale (0-3).  
La divergenza narrativa tra blocchi è essa stessa un segnale: quando CNN e Xinhua raccontano lo stesso evento in modo opposto, qualcosa di rilevante sta accadendo.

Blocchi coperti: western · china · russia · arab · india · latam · africa

---

## Stato implementazione

| Componente | Stato |
|---|---|
| Config, logging, CLI skeleton | ✅ Fase 0 |
| SQLite schema + sqlite-vec | ✅ Fase 0 |
| Ciclo orchestrator (struttura) | ✅ Fase 0 |
| GDELT 2.0 ingestor (incrementale + bootstrap) | ✅ Fase 1 |
| RSS multi-blocco | ⬜ Fase 1 |
| PortWatch, Comtrade, USGS/FIRMS | ⬜ Fase 1 |
| NER + geocoding + Wikidata | ⬜ Fase 2 |
| Embeddings e5-small + clustering → eventi | ⬜ Fase 2 |
| Grafo entità | ⬜ Fase 2 |
| Brief mattutino + generatore tesi | ⬜ Fase 3 |
| Paper trading engine + approvazione | ⬜ Fase 3 |
| Calibrazione Tetlock | ⬜ Fase 3 |
| Dashboard Streamlit | ⬜ Fase 4 |

MVP verticale: filiera semiconduttori (TSMC/ASML/SMIC, chokepoint Taiwan Strait).

---

## Test

```
tests/
  conftest.py          — fixture tmp_db, make_gdelt_row()
  test_db.py           — schema init, tabelle, sqlite-vec, integrità
  test_gdelt.py        — URL gen, parsing, filtraggio, storage, dedup
  test_orchestrator.py — dry_run, from_phase, gestione errori
```

Esecuzione: `uv run pytest` — 66 test, ~0.4s (nessuna chiamata HTTP reale).

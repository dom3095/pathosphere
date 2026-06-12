# Schema del Database — Pathosphere

SQLite + sqlite-vec. File: `data/db/pathosphere.db`.  
Backup = copia del file. Raw storicizzati in Parquet (fonte di verità ricostruibile).

---

## Diagramma ER — Vista completa

```mermaid
erDiagram

    sources {
        INTEGER id PK
        TEXT name "UNIQUE"
        TEXT url
        TEXT country "ISO 3166-1 alpha-2"
        TEXT geopolitical_block "western|china|russia|arab|india|latam|africa|other"
        TEXT orientation "state|independent|opposition"
        INTEGER state_control "0=nessuno  3=totale"
        TEXT language "ISO 639-1"
        INTEGER active "1=attiva"
        TEXT notes
        TEXT created_at "ISO 8601"
    }

    raw_documents {
        INTEGER id PK
        INTEGER source_id FK
        TEXT url "UNIQUE"
        TEXT title
        TEXT body
        TEXT published_at "ISO 8601"
        TEXT fetched_at "ISO 8601"
        TEXT language "ISO 639-1"
        TEXT content_hash "SHA-256 — dedup esatto"
        INTEGER embedded "0=da fare  1=embedding calcolato"
        INTEGER is_duplicate "1=near-duplicate di altro doc"
        INTEGER duplicate_of FK "id del doc canonico"
        INTEGER dedup_checked "1=dedup già processato"
    }

    events {
        INTEGER id PK
        TEXT title
        TEXT summary
        TEXT first_seen "ISO 8601"
        TEXT last_seen "ISO 8601"
        TEXT event_type "conflict|epidemic|trade|infrastructure|political|other"
        INTEGER severity "1-5"
        TEXT location_name "nome human-readable"
        REAL lat
        REAL lon
        TEXT resolved_at "ISO 8601 se chiuso"
        TEXT created_at "ISO 8601"
    }

    event_documents {
        INTEGER event_id FK
        INTEGER document_id FK
    }

    narrative_divergences {
        INTEGER id PK
        INTEGER event_id FK
        TEXT block_a "blocco geopolitico A"
        TEXT block_b "blocco geopolitico B"
        REAL divergence_score "0-1 (0=narrativa identica)"
        TEXT summary "sintesi della divergenza"
        TEXT computed_at "ISO 8601"
    }

    entities {
        INTEGER id PK
        TEXT name "nome originale"
        TEXT canonical_name "forma normalizzata"
        TEXT entity_type "country|company|commodity|infrastructure|person|other"
        TEXT wikidata_qid "es. Q540386 = TSMC"
        TEXT aliases "JSON array nomi alternativi"
        TEXT created_at "ISO 8601"
    }

    entity_links {
        INTEGER id PK
        INTEGER entity_a FK
        INTEGER entity_b FK
        TEXT relation_type "depends_on|supplies|sanctions|ally|adversary|..."
        REAL strength "0-1"
        INTEGER source_event FK
        TEXT valid_from "ISO 8601"
        TEXT valid_to "ISO 8601 — NULL se ancora valida"
        TEXT notes
    }

    watchlist_items {
        INTEGER id PK
        TEXT label "nome breve"
        TEXT description
        TEXT indicator_query "keyword o filtro GDELT"
        TEXT status "active|triggered|expired"
        TEXT triggered_at "ISO 8601"
        TEXT created_at "ISO 8601"
    }

    theses {
        INTEGER id PK
        INTEGER trigger_event FK
        TEXT title
        TEXT causal_chain "testo libero o JSON con catena causale"
        TEXT instrument "ticker o ETF — es. USO, TSM, GLD"
        TEXT direction "long|short|neutral"
        INTEGER horizon_days
        TEXT invalidation "condizione che invalida la tesi"
        REAL confidence "0-1 soggettivo"
        TEXT status "pending|approved|rejected|closed"
        TEXT approved_at "ISO 8601"
        TEXT rejected_at "ISO 8601"
        TEXT rejection_reason
        TEXT sources_json "JSON array di URL"
        TEXT created_at "ISO 8601"
    }

    portfolios {
        INTEGER id PK
        TEXT name "agent|random|benchmark — UNIQUE"
        TEXT portfolio_type
        REAL cash "liquidità virtuale (default 100.000)"
        TEXT created_at "ISO 8601"
    }

    trades {
        INTEGER id PK
        INTEGER portfolio_id FK
        INTEGER thesis_id FK
        TEXT ticker
        TEXT direction "buy|sell"
        REAL quantity
        REAL price_open "prezzo al momento della DECISIONE — no lookahead"
        REAL price_close "prezzo alla chiusura della posizione"
        TEXT opened_at "ISO 8601"
        TEXT closed_at "ISO 8601 — NULL se aperta"
        REAL transaction_cost "commissioni simulate"
        REAL slippage "slippage simulato"
        REAL pnl "calcolato a chiusura"
        TEXT notes
    }

    predictions {
        INTEGER id PK
        INTEGER thesis_id FK
        TEXT description "es. Escalation in X entro 2 settimane"
        REAL probability "0-1"
        TEXT horizon_date "scadenza ISO 8601"
        INTEGER resolved "0=aperta  1=risolta"
        INTEGER outcome "NULL=aperta  1=vero  0=falso"
        TEXT resolved_at "ISO 8601"
        REAL brier_score "(p - o)^2 — calcolato alla risoluzione"
        TEXT created_at "ISO 8601"
    }

    gdelt_file_log {
        INTEGER id PK
        TEXT filename "UNIQUE — es. 20260611153000.export.CSV.zip"
        TEXT url
        TEXT downloaded_at "ISO 8601"
        INTEGER rows_raw "righe nel CSV originale"
        INTEGER rows_stored "righe inserite dopo filtro"
        TEXT status "ok|error|skipped"
    }

    %% ─── Relazioni ────────────────────────────────────
    sources          ||--o{  raw_documents        : "source_id"
    raw_documents    ||--o{  event_documents      : "document_id"
    events           ||--o{  event_documents      : "event_id"
    events           ||--o{  narrative_divergences: "event_id"
    events           ||--o{  theses               : "trigger_event"
    events           ||--o{  entity_links         : "source_event"
    entities         ||--o{  entity_links         : "entity_a"
    entities         ||--o{  entity_links         : "entity_b"
    portfolios       ||--o{  trades               : "portfolio_id"
    theses           ||--o{  trades               : "thesis_id"
    theses           ||--o{  predictions          : "thesis_id"
```

---

## Diagramma ER — Vista semplificata (domini funzionali)

```mermaid
graph TD
    subgraph INGEST["Ingestione"]
        S[sources]
        RD[raw_documents]
        GFL[gdelt_file_log]
        VD[vec_documents<br/><i>virtual — sqlite-vec</i>]
        S --> RD
        RD -.->|dopo embedding| VD
    end

    subgraph SEMANTIC["Semantica"]
        E[events]
        ND[narrative_divergences]
        EN[entities]
        EL[entity_links]
        ED[event_documents]
        RD --> ED
        E --> ED
        E --> ND
        E --> EL
        EN --> EL
    end

    subgraph AGENT["Agent & Valutazione"]
        TH[theses]
        WL[watchlist_items]
        PO[portfolios]
        TR[trades]
        PR[predictions]
        E --> TH
        TH --> TR
        TH --> PR
        PO --> TR
    end

    INGEST --> SEMANTIC
    SEMANTIC --> AGENT
```

---

## Tabelle — Riferimento rapido

| Tabella | Fase | Righe tipiche | Note |
|---|---|---|---|
| `sources` | 0 | ~20 | Seeded una volta, aggiornate raramente |
| `raw_documents` | 1 | migliaia/giorno | `content_hash` previene duplicati esatti |
| `events` | 2 | centinaia/giorno | Aggregano N documenti sullo stesso evento |
| `event_documents` | 2 | join N:M | |
| `narrative_divergences` | 2 | decine/giorno | Solo eventi con ≥2 blocchi coperti |
| `entities` | 2 | crescita lenta | Deduplicate via `wikidata_qid` |
| `entity_links` | 2 | crescita lenta | Grafo relazionale entità |
| `watchlist_items` | 3 | decine | Indicatori osservabili per scenario ACH |
| `theses` | 3 | 2-3/giorno | Approvate manualmente |
| `portfolios` | 3 | 3 fissi | agent, random, benchmark |
| `trades` | 3 | 2-3/giorno | `price_open` immutabile dopo apertura |
| `predictions` | 3 | 2-3/giorno | Risolte vero/falso a scadenza |
| `gdelt_file_log` | 1 | ~96/giorno | Tracking per resume download |
| `vec_documents` | 2 | = embedded docs | Tabella virtuale sqlite-vec |

---

## Vincoli e garanzie di integrità

### Dedup documenti (tre livelli)

```
Livello 1 — Esatto URL:      url UNIQUE in raw_documents
Livello 2 — Esatto contenuto: content_hash SHA-256 UNIQUE in raw_documents
Livello 3 — Semantico KNN:   is_duplicate=1 se cosine >= 0.92 in finestra 72h
                              calcolato da semantic/dedup.py via sqlite-vec KNN
```

### No lookahead bias nel paper trading

```
trades.price_open = prezzo yfinance al momento dell'approvazione della tesi
                  = MAI aggiornato retroattivamente
```

### Gerarchia portafogli di controllo

```
portfolios.name IN ('agent', 'random', 'benchmark')
  agent     — tesi approvate dall'utente
  random    — stesse dimensioni trade, ticker casuali
  benchmark — buy & hold indice (es. SPY)
```

### Brier Score (calibrazione Tetlock)

```
brier_score = (probability - outcome)²
  outcome ∈ {0, 1}
  brier_score ∈ [0, 1]  — 0 = predizione perfetta
```

---

## Estensioni future

| Componente | Note |
|---|---|
| `vec_documents` | ✅ Popolata da `semantic/embedder.py` — multilingual-e5-small 384-dim, vettori unitari, blob `struct.pack("384f")` |
| Parquet raw | Storico >90 giorni archiviato in `data/parquet/`, interrogabile con DuckDB |
| Turso/libSQL | Drop-in replacement per SQLite con replica cloud automatica |

# Pathosphere — Wiki

Sistema personale di intelligence OSINT. Paper trading virtuale come metrica di valutazione.  
**Mono-utente · Dati aperti · Budget quasi zero · Human-in-the-loop.**

---

## Indice

1. [Progetto](#1-progetto)
   - 1.1 [Obiettivo](#11-obiettivo)
   - 1.2 [Principi non negoziabili](#12-principi-non-negoziabili)
   - 1.3 [Vincoli hardware](#13-vincoli-hardware)
2. [Setup](#2-setup)
   - 2.1 [Requisiti](#21-requisiti)
   - 2.2 [Installazione](#22-installazione)
   - 2.3 [Variabili d'ambiente](#23-variabili-dambiente)
3. [Architettura](#3-architettura)
   - 3.1 [Componenti](#31-componenti)
   - 3.2 [Moduli Python](#32-moduli-python)
   - 3.3 [Flusso dati](#33-flusso-dati)
   - 3.4 [Strategia LLM ibrida](#34-strategia-llm-ibrida)
4. [Database](#4-database)
   - 4.1 [Tabelle](#41-tabelle)
   - 4.2 [Dedup e integrità](#42-dedup-e-integrità)
   - 4.3 [sqlite-vec](#43-sqlite-vec)
   - 4.4 [Evoluzione pianificata](#44-evoluzione-pianificata)
5. [Ingestori](#5-ingestori)
   - 5.1 [GDELT 2.0](#51-gdelt-20)
   - 5.2 [RSS multi-blocco](#52-rss-multi-blocco-fase-1)
   - 5.3 [PortWatch / Comtrade / USGS](#53-portwatch--comtrade--usgs-fase-1)
6. [Ciclo notturno](#6-ciclo-notturno)
7. [CLI Reference](#7-cli-reference)
8. [Fonti dati](#8-fonti-dati)
9. [Valutazione del modello](#9-valutazione-del-modello)
10. [Testing](#10-testing)
11. [Roadmap](#11-roadmap)

---

## 1. Progetto

### 1.1 Obiettivo

Le crisi geopolitiche (Taiwan, Hormuz, semiconduttori) impattano i mercati con anticipo di giorni o settimane rispetto al consensus. Pathosphere aggrega fonti aperte da tutti i blocchi geopolitici, estrae la semantica, costruisce scenari con catene causali e li valuta tramite un portafoglio virtuale.

Se l'agent non batte il random (stesso numero di trade, ticker casuali), si sa subito.

### 1.2 Principi non negoziabili

| # | Principio | Implementazione |
|---|---|---|
| 1 | **Budget quasi zero** | Solo dati gratuiti. LLM locale per lavoro di massa, Claude SDK per i 2-3 task di ragionamento al giorno. |
| 2 | **Human-in-the-loop** | Agent propone tesi/trade, utente approva o rifiuta (con motivazione loggata). Nessuna operazione autonoma. |
| 3 | **Pluralità di prospettive** | Fonti da 7 blocchi geopolitici. Ogni fonte etichettata con paese, orientamento, grado di controllo statale. La divergenza tra narrazioni è un segnale. |
| 4 | **LLM vede solo il meglio** | Filtraggio aggressivo a monte (GDELT pre-codificato, keyword, dedup vettoriale). L'LLM processa ~30-50 documenti/giorno, non migliaia. |
| 5 | **No lookahead bias** | `price_open` immutabile al momento della decisione. Costi di transazione e slippage simulati. |

### 1.3 Vincoli hardware

MacBook Air M1, 8 GB RAM (~4-5 GB utilizzabili).

- **Un solo modello in RAM alla volta.** Mai due in parallelo.
- Ciclo notturno **sequenziale e riprendibile**: ogni fase è atomica.
- Throttling termico notturno: accettabile, non critico.

---

## 2. Setup

### 2.1 Requisiti

- macOS M1/M2, Python 3.12+
- [uv](https://astral.sh/uv) — gestore pacchetti e venv
- [Ollama](https://ollama.com) con `qwen3:4b` (per fasi semantiche e brief)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install ollama
ollama pull qwen3:4b
```

### 2.2 Installazione

```bash
uv sync                         # installa dipendenze (incl. sqlite-vec, httpx, pydantic-settings)
cp .env.example .env            # crea configurazione locale
uv run pathos db init           # crea schema SQLite + tabella virtuale sqlite-vec
uv run pathos sources seed      # inserisce 15 fonti predefinite (7 blocchi geopolitici)
```

Verifica:

```bash
uv run pathos db info           # conta righe per tabella
uv run pathos cycle --dry-run   # simula ciclo senza I/O
uv run pytest                   # 66 test, ~0.4s
```

### 2.3 Variabili d'ambiente

Tutte opzionali — i default funzionano out-of-the-box.

| Variabile | Default | Descrizione |
|---|---|---|
| `DB_PATH` | `data/db/pathosphere.db` | Path database SQLite |
| `PARQUET_DIR` | `data/parquet` | Storico raw in Parquet |
| `LOG_DIR` | `data/logs` | Log giornalieri (loguru) |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |
| `OLLAMA_HOST` | `http://localhost:11434` | Endpoint Ollama locale |
| `OLLAMA_MODEL` | `qwen3:4b` | Modello per lavoro di massa |
| `EMBED_MODEL_NAME` | `intfloat/multilingual-e5-small` | Modello embedding (HuggingFace) |

---

## 3. Architettura

→ Documento completo: [architecture.md](architecture.md)

### 3.1 Componenti

```
┌──────────────────────────────────────────────────────────────┐
│                        CLI  (pathos)                         │
│   db · sources · ingest · cycle · config                     │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                    Ciclo notturno                            │
│   INGEST → EMBED → EXTRACT → CLUSTER → BRIEF                │
│   (sequenziale, riprendibile da qualsiasi fase)              │
└──────┬──────────┬───────────┬─────────────────────────────────┘
       │          │           │
       ▼          ▼           ▼
  ┌─────────┐ ┌────────┐ ┌────────────┐
  │ Ingest  │ │Semantic│ │   Agent    │
  │ GDELT   │ │NER+emb │ │brief+tesi  │
  │ RSS     │ │cluster │ │paper trade │
  │ ...     │ │grafo   │ │calibraz.   │
  └────┬────┘ └───┬────┘ └─────┬──────┘
       │          │             │
       └──────────┴─────────────┘
                  │
                  ▼
       ┌────────────────────┐
       │ SQLite + sqlite-vec│
       │ data/db/pathos.db  │
       └────────────────────┘
                  │
                  ▼
       ┌────────────────────┐
       │  Parquet (storico) │
       │  data/parquet/     │
       └────────────────────┘
```

### 3.2 Moduli Python

```
pathosphere/
├── config.py           Settings da .env (pydantic-settings). Singleton get_settings().
├── logging_setup.py    Loguru: stderr colorato + rotazione giornaliera su file.
├── db/
│   └── schema.py       DDL SQLite + sqlite-vec. get_connection() + init_db().
├── cli.py              Entry point `pathos` (Click). Gruppi: db, sources, ingest, cycle, config.
├── cycle/
│   └── orchestrator.py 5 fasi sequenziali riprendibili. Fasi 2-5: stub NotImplementedError.
├── ingest/
│   └── gdelt.py        Downloader GDELT 2.0. Incrementale + bootstrap storico.
├── semantic/           (Fase 2) NER, embedding, clustering — TODO
└── agent/              (Fase 3) brief, tesi, paper trading — TODO
```

### 3.3 Flusso dati

```
Sorgenti esterne          Pipeline interna              Output tabelle
─────────────────         ─────────────────────         ─────────────────────
GDELT (ogni 15min)    →   download + dedup          →   raw_documents
RSS multi-blocco      →   NER + geocoding           →   entities
PortWatch, Comtrade   →   embedding e5-small (384d) →   vec_documents
USGS, FIRMS, IODA     →   clustering → eventi       →   events
yfinance (EOD)        →   confronto narrazioni      →   narrative_divergences
                      →   grafo entità              →   entity_links
                      →   brief + tesi              →   theses
                      →   approvazione utente       →   trades, predictions
                      →   EOD prices yfinance       →   portfolios (P&L)
```

### 3.4 Strategia LLM ibrida

| | Locale (Qwen3 4B q4 via Ollama) | Cloud (Claude via Agent SDK) |
|---|---|---|
| **Costo** | Gratis, illimitato | Credito mensile abbonamento (~2-3 task/giorno) |
| **Uso** | Lavoro di massa: classificazione, estrazione strutturata, NER, dedup semantica | Ragionamento pesante: brief mattutino, tesi con catene causali, scenari ACH multi-prospettiva |
| **Quando** | Notte, ogni documento | Mattina, 1 volta al giorno |

Entrambi dietro un'unica astrazione OpenAI-compatible. Cambiare backend = una riga di config.  
A/B testing possibile: stesso giorno, tesi da Qwen3 4B vs Claude, paper trading misura la differenza.

---

## 4. Database

→ Schema completo con ER Mermaid: [schema.md](schema.md)  
→ Query annotate: [../useful_queries.sql](../useful_queries.sql)

### 4.1 Tabelle

| Tabella | Righe tipiche | Scopo |
|---|---|---|
| `sources` | ~15-50 | Catalogo fonti (paese, blocco, controllo statale) |
| `raw_documents` | 10k-500k | Documenti grezzi (URL, titolo, hash dedup) |
| `events` | 1k-50k | Eventi aggregati da cluster di articoli |
| `event_documents` | N:M | Join eventi ↔ documenti |
| `narrative_divergences` | 100-5k | Divergenza narrativa per blocco geopolitico |
| `entities` | 500-10k | Paesi, aziende, commodity, infrastrutture |
| `entity_links` | 1k-50k | Grafo relazioni (depends_on, supplies, sanctions…) |
| `watchlist_items` | 10-200 | Indicatori osservabili per scenario (ACH) |
| `theses` | 10-500 | Tesi con catena causale, strumento, invalidazione |
| `trades` | 50-2k | Paper trading (prezzo registrato alla DECISIONE) |
| `portfolios` | 3 | agent · random · benchmark |
| `predictions` | 20-500 | Anticipazioni non finanziarie (calibrazione Tetlock) |
| `gdelt_file_log` | 1k-50k | Tracking file GDELT scaricati (dedup + ripresa) |
| `vec_documents` | uguale a raw_documents | Tabella virtuale sqlite-vec (embedding 384d) |

### 4.2 Dedup e integrità

Due livelli:

- **Esatto** (`raw_documents`): `url UNIQUE` + `content_hash UNIQUE` (SHA-256 del body).
- **Semantico** (`events`): chiave composita `actor1+actor2+event_root+date+geo`. Stesso evento da fonti diverse → 1 evento, N documenti.

Constraint: `PRAGMA foreign_keys = ON` impostato su ogni connessione (pragma per-sessione, non persiste tra connessioni).

### 4.3 sqlite-vec

Tabella virtuale `vec_documents` con embedding FLOAT[384] (multilingual-e5-small output dim).

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents
USING vec0(
    document_id INTEGER PRIMARY KEY,
    embedding   FLOAT[384]
);
```

Query nearest-neighbour:

```sql
SELECT d.url, d.title, v.distance
FROM vec_documents v
JOIN raw_documents d ON d.id = v.document_id
WHERE v.embedding MATCH (SELECT embedding FROM vec_documents WHERE document_id = ?)
  AND k = 5;
```

### 4.4 Evoluzione pianificata

```
Oggi:    SQLite locale (data/db/pathosphere.db)
         → zero processi residenti, backup = copia file

Futuro:  Turso/libSQL con embedded replica
         → scritture locali a velocità SQLite
         → replica cloud automatica = backup gratis fuori macchina
         → quasi zero code change (libSQL è fork di SQLite)
```

**Raw in Parquet = fonte di verità ricostruibile.** Il DB può sempre essere rigenerato dai Parquet. Protezione contro sparizione free tier (caso PlanetScale 2024).

---

## 5. Ingestori

### 5.1 GDELT 2.0

**Stato: ✅ Implementato**

GDELT Events pubblica file TSV di 61 colonne ogni 15 minuti, estratti da migliaia di testate mondiali in 100+ lingue.

**Modalità:**

| Modalità | Comando | Uso |
|---|---|---|
| Incrementale | `pathos ingest gdelt [--days N]` | Ciclo notturno. Salta file già scaricati. |
| Bootstrap storico | `pathos ingest gdelt-history --start YYYY-MM-DD` | Operazione una-tantum. Ripartibile con Ctrl+C. |

**Filtri applicati prima dello storage:**

| Filtro | Parametro | Default |
|---|---|---|
| Tipo evento | `--quad` | `conflict` (QuadClass 3-4) |
| Menzioni minime | `--min-mentions` | `10` |
| Scala Goldstein | (hardcoded) | nessuno (configurable in futuro) |
| Paesi (ISO-2) | `--countries` | tutti |

**Colonne GDELT chiave:**

| Colonna | Tipo | Significato |
|---|---|---|
| `QuadClass` | INT | 1=Coop verbale, 2=Coop materiale, 3=Conflitto verbale, 4=Conflitto materiale |
| `GoldsteinScale` | FLOAT | -10 (destabilizzante) → +10 (stabilizzante) |
| `NumMentions` | INT | Numero di articoli che citano l'evento |
| `Actor1CountryCode` | TEXT | ISO-2 attore principale |
| `Actor2CountryCode` | TEXT | ISO-2 attore secondario |
| `EventRootCode` | TEXT | CAMEO root code (es. "19" = fight) |
| `ActionGeo_*` | TEXT/REAL | Luogo dell'azione (nome, lat, lon, paese) |
| `SOURCEURL` | TEXT | URL articolo originale (chiave dedup) |

**HTTP:** httpx + tenacity (3 retry, backoff esponenziale). Ctrl+C safe.

**Esempi:**

```bash
uv run pathos ingest gdelt                                      # ieri, conflitti, min 10 menzioni
uv run pathos ingest gdelt --days 3 --countries CN,TW,US        # 3 giorni, filtra per paese
uv run pathos ingest gdelt --quad all --min-mentions 5          # tutti i tipi evento
uv run pathos ingest gdelt --max-files 5                        # test: solo 5 file

uv run pathos ingest gdelt-history --start 2024-01-01           # ~0.8 notti
uv run pathos ingest gdelt-history --start 2021-01-01 \
    --sample-hours 2                                            # più veloce, 1 file ogni 2h
```

### 5.2 RSS multi-blocco _(Fase 1)_

**Stato: ⬜ Non implementato**

Un feed per blocco geopolitico. Ogni articolo → `raw_documents` con `source_id` che porta il blocco.

Fonti pianificate:

| Blocco | Fonte | Lingua | Controllo statale |
|---|---|---|---|
| western | Reuters, BBC | EN | 0 |
| china | Xinhua, Global Times | EN/ZH | 3 |
| russia | TASS, RT | EN/RU | 3 |
| arab | Al Jazeera, Anadolu | EN/AR | 1-2 |
| india | The Hindu | EN | 0 |
| latam | Folha de São Paulo | PT | 0 |
| africa | AllAfrica | EN | 0-1 |

### 5.3 PortWatch / Comtrade / USGS _(Fase 1)_

**Stato: ⬜ Non implementato**

| Fonte | Dati | Chokepoint/tema |
|---|---|---|
| IMF PortWatch | Traffico marittimo | Suez, Hormuz, Panama, Malacca |
| UN Comtrade | Flussi commerciali | Semiconduttori HS 8541/8542, macchinari 8486 |
| USGS | Terremoti | Infrastrutture fisiche |
| NASA FIRMS | Incendi | Conflitti, infrastrutture |
| IODA / Cloudflare Radar | Blackout internet | Censura, conflitti |
| yfinance | Prezzi EOD | Valutazione paper trading |

---

## 6. Ciclo notturno

**File:** `pathosphere/cycle/orchestrator.py`

Cinque fasi sequenziali, riprendibili da qualsiasi punto. Ogni fase è atomica: se fallisce, il ciclo si ferma e salva l'errore in `CycleState`.

```
INGEST → EMBED → EXTRACT → CLUSTER → BRIEF
  ✅        ⬜       ⬜        ⬜       ⬜
```

| Fase | Funzione | Stato | Descrizione |
|---|---|---|---|
| `INGEST` | `_phase_ingest` | ✅ | Scarica GDELT (+ futuro RSS, PortWatch) |
| `EMBED` | `_phase_embed` | ⬜ stub | Calcola embedding e5-small su `raw_documents.embedded=0` |
| `EXTRACT` | `_phase_extract` | ⬜ stub | NER, geocoding, entity linking (Qwen3 4B) |
| `CLUSTER` | `_phase_cluster` | ⬜ stub | Clustering documenti → eventi, confronto narrazioni |
| `BRIEF` | `_phase_brief` | ⬜ stub | Genera brief + tesi (Claude SDK) |

**Comandi:**

```bash
uv run pathos cycle                         # ciclo completo
uv run pathos cycle --dry-run               # simula senza I/O
uv run pathos cycle --from-phase embed      # riprendi da EMBED (salta INGEST)
uv run pathos cycle --from-phase brief      # solo brief mattutino
```

---

## 7. CLI Reference

Entry point: `uv run pathos`

```
pathos
├── db
│   ├── init            Crea/aggiorna schema SQLite + sqlite-vec
│   └── info            Mostra conteggi per tabella
├── sources
│   ├── list            Lista fonti configurate
│   └── seed            Inserisce le 15 fonti predefinite
├── ingest
│   ├── gdelt           Ciclo incrementale (ultimi N giorni)
│   └── gdelt-history   Bootstrap storico (range date)
├── cycle               Esegui ciclo notturno
│   ├── --dry-run       Simula tutte le fasi senza I/O
│   └── --from-phase    Riprendi da fase specifica (ingest|embed|extract|cluster|brief)
└── config              Mostra configurazione attiva (.env + defaults)
```

---

## 8. Fonti dati

### Blocchi geopolitici

| Blocco | Codice DB | Esempi |
|---|---|---|
| Occidentale | `western` | Reuters, BBC, AP, NYT, Le Monde |
| Cina | `china` | Xinhua, Global Times, CGTN |
| Russia | `russia` | TASS, RT, Kommersant |
| Mondo arabo | `arab` | Al Jazeera, Anadolu, Press TV |
| India | `india` | The Hindu, NDTV, Times of India |
| America Latina | `latam` | Folha de São Paulo, Infobae, La Jornada |
| Africa | `africa` | AllAfrica, Daily Maverick |

### Segnali fisici (Fase 1)

| Fonte | Tipo | Frequenza |
|---|---|---|
| GDELT 2.0 | Conflitti/politica (multilingua) | 15 min |
| ACLED, UCDP | Conflitti armati | Settimanale |
| WHO DON, ProMED | Epidemie | Quotidiana |
| IMF PortWatch | Traffico chokepoint marittimo | Quotidiana |
| UN Comtrade | Flussi commerciali (HS code) | Mensile |
| USGS | Terremoti | Realtime |
| NASA FIRMS | Incendi | 3h |
| IODA | Blackout internet | Realtime |
| yfinance | Prezzi EOD | Giornaliero |
| FRED | Macro (tassi, CPI…) | Varia |

### Divergenza narrativa come segnale

Ogni `raw_document` porta il `source_id` → `geopolitical_block`. Quando CNN e Xinhua raccontano lo stesso evento in modo opposto, la `narrative_divergences.divergence_score` sale → segnale da analizzare.

---

## 9. Valutazione del modello

### Portafogli di controllo

```
Portafoglio agent     — tesi approvate dall'utente
Portafoglio random    — stesse dimensioni trade, ticker casuali
Portafoglio benchmark — buy & hold SPY

Se agent ≤ random → nessun segnale predittivo reale
```

### No lookahead bias

`trades.price_open` = prezzo al momento dell'APPROVAZIONE della tesi. Immutabile. Costi di transazione e slippage simulati a parte.

### Calibrazione Tetlock

Tabella `predictions` per anticipazioni non finanziarie:

```
"Escalation in X entro 2 settimane: 60%"
→ risolve vero/falso a scadenza
→ brier_score = (0.6 - 1)² = 0.16
```

`brier_score` 0 = perfetto, 1 = peggio del caso. Aggregato per bucket di probabilità → curva di calibrazione.

### Ogni tesi registra

- Evento scatenante (con fonti)
- Catena causale (testo / JSON)
- Strumento finanziario
- Orizzonte temporale
- Condizione di invalidazione
- Confidence soggettiva (0-1)

---

## 10. Testing

```
tests/
├── conftest.py          Fixture tmp_db (SQLite in-memory), make_gdelt_row()
├── test_db.py           Schema init, tabelle, sqlite-vec, integrità FK
├── test_gdelt.py        URL gen, parsing, filtraggio, storage, dedup
└── test_orchestrator.py dry_run, from_phase, gestione errori
```

**Esecuzione:**

```bash
uv run pytest            # 66 test, ~0.4s, zero chiamate HTTP reali
uv run pytest -v         # output verboso
uv run pytest tests/test_gdelt.py   # solo GDELT
```

**Filosofia:** nessuna chiamata HTTP reale nei test. Le funzioni sotto `ingest_gdelt` (parsing, filtraggio, storage) sono testabili in isolamento. `tmp_db` fixture crea un DB SQLite temporaneo per ogni test.

**Fixture riutilizzabili:**

```python
# conftest.py
tmp_db      → sqlite3.Connection con schema completo, FK ON, WAL
make_gdelt_row(**overrides)  → dict con tutti i 61 campi GDELT (default: CN-TW conflict)
```

---

## 11. Roadmap

| Fase | Componente | Stato |
|---|---|---|
| **0** | Config, logging, CLI skeleton | ✅ |
| **0** | SQLite schema + sqlite-vec | ✅ |
| **0** | Ciclo orchestrator (struttura) | ✅ |
| **1** | GDELT 2.0 ingestor (incrementale + bootstrap) | ✅ |
| **1** | RSS multi-blocco (7 blocchi geopolitici) | ⬜ |
| **1** | PortWatch + Comtrade semiconduttori | ⬜ |
| **1** | USGS + NASA FIRMS + IODA | ⬜ |
| **1** | Storicizzazione Parquet | ⬜ |
| **2** | NER + geocoding (spaCy + Nominatim) | ⬜ |
| **2** | Wikidata entity linking | ⬜ |
| **2** | Embedding e5-small + clustering → eventi | ⬜ |
| **2** | Grafo entità | ⬜ |
| **3** | Brief mattutino (Claude SDK) | ⬜ |
| **3** | Generatore tesi con catene causali | ⬜ |
| **3** | Paper trading engine + approvazione CLI | ⬜ |
| **3** | Portafogli di controllo + calibrazione Tetlock | ⬜ |
| **4** | Dashboard Streamlit minimale | ⬜ |

**MVP verticale:** filiera semiconduttori — TSMC/ASML/SMIC, chokepoint Taiwan Strait. Pochi attori, geopolitica intensa, segnali chiari.

---

*Documenti correlati: [architecture.md](architecture.md) · [schema.md](schema.md) · [../useful_queries.sql](../useful_queries.sql)*

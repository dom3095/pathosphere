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
   - 5.2 [RSS multi-blocco](#52-rss-multi-blocco)
   - 5.3 [PortWatch / Comtrade / USGS](#53-portwatch--comtrade--usgs)
6. [Pipeline semantica (Fase 2)](#6-pipeline-semantica-fase-2)
   - 6.1 [Embedding](#61-embedding)
   - 6.2 [Dedup semantica](#62-dedup-semantica)
   - 6.3 [Clustering → eventi](#63-clustering--eventi)
   - 6.4 [NER + geocoding + Wikidata](#64-ner--geocoding--wikidata)
7. [Ciclo notturno](#7-ciclo-notturno)
8. [CLI Reference](#8-cli-reference)
9. [Fonti dati](#9-fonti-dati)
10. [Valutazione del modello](#10-valutazione-del-modello)
11. [Testing](#11-testing)
12. [Roadmap](#12-roadmap)

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
uv run python -m spacy download xx_ent_wiki_sm    # NER multilingua (~30 MB, una-tantum)
```

### 2.2 Installazione

```bash
uv sync                         # installa dipendenze (incl. sqlite-vec, httpx, feedparser, sentence-transformers)
cp .env.example .env            # crea configurazione locale
uv run pathos db init           # crea schema SQLite + tabella virtuale sqlite-vec
uv run pathos sources seed      # inserisce 52 fonti (48 attive) predefinite (7 blocchi geopolitici)
```

Verifica:

```bash
uv run pathos db info           # conta righe per tabella
uv run pathos cycle --dry-run   # simula ciclo senza I/O
uv run pytest                   # 150 test, ~8s
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
│   db · sources · ingest · embed · cycle · config             │
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
  │ GDELT   │ │embed   │ │brief+tesi  │
  │ RSS     │ │dedup   │ │paper trade │
  │ ...     │ │cluster │ │calibraz.   │
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
│   └── schema.py       DDL SQLite + sqlite-vec. get_connection() + init_db() + migrate_db().
├── cli.py              Entry point `pathos` (Click). Gruppi: db, sources, ingest, embed, cycle, config.
├── cycle/
│   └── orchestrator.py 5 fasi sequenziali riprendibili. EMBED+CLUSTER: ✅. EXTRACT+BRIEF: stub.
├── ingest/
│   ├── gdelt.py        Downloader GDELT 2.0. Incrementale + bootstrap storico.
│   ├── rss.py          Ingestor RSS multi-blocco. 52 fonti (48 attive), 7 blocchi geopolitici.
│   └── sources_seed.py Catalogo fonti: lista completa + seed_sources(conn).
├── semantic/
│   ├── embedder.py     Batch embedding multilingual-e5-small → vec_documents.
│   ├── dedup.py        KNN dedup semantica via sqlite-vec (cosine ≥ 0.92).
│   ├── cluster.py      Union-find clustering → events + event_documents.
│   └── extract.py      NER (spaCy xx_ent_wiki_sm) + geocoding Nominatim + Wikidata QID.
└── agent/              (Fase 3) brief, tesi, paper trading — TODO
```

### 3.3 Flusso dati

```
Sorgenti esterne          Pipeline interna              Output tabelle
─────────────────         ─────────────────────         ─────────────────────
GDELT (ogni 15min)    →   download + dedup esatto   →   raw_documents
RSS multi-blocco      →   embedding e5-small (384d) →   vec_documents
PortWatch, Comtrade   →   dedup semantica KNN       →   raw_documents.is_duplicate
USGS, FIRMS, IODA     →   clustering → eventi       →   events + event_documents
yfinance (EOD)        →   confronto narrazioni      →   narrative_divergences
                      →   NER + grafo entità        →   entities + entity_links
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
| `raw_documents` | 10k-500k | Documenti grezzi (URL, titolo, hash dedup, flag semantici, `origin`) |
| `events` | 1k-50k | Eventi aggregati da cluster di articoli (`origin` = ingestor) |
| `event_documents` | N:M | Join eventi ↔ documenti |
| `gdelt_events` | 1/riga GDELT | Dettaglio numerico per `GlobalEventID` (Goldstein/tone/mentions) |
| `comtrade_flows` | 1/record | Valori numerici flussi commerciali (USD, kg) |
| `chokepoint_metrics` | 1/(chokepoint, giorno) | Timeseries transiti PortWatch (anomalie z-score → `events`) |
| `fire_metrics` | 1/(area, giorno) | Timeseries rilevazioni FIRMS (surge z-score → `events`) |
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

Tre livelli:

- **Esatto URL** (`raw_documents`): `url UNIQUE` — duplica bloccato in insert.
- **Esatto contenuto** (`raw_documents`): `content_hash UNIQUE` (SHA-256 del body) — stesso articolo da URL diversi bloccato.
- **Semantico** (`raw_documents`): colonne `is_duplicate`, `duplicate_of`, `dedup_checked` — calcolato dalla fase EMBED tramite KNN su `vec_documents` (cosine ≥ 0.92 in finestra 72h).

Colonne semantiche su `raw_documents`:

| Colonna | Default | Significato |
|---|---|---|
| `embedded` | 0 | 0=da processare, 1=embedding calcolato |
| `is_duplicate` | 0 | 1=near-duplicate di un altro documento |
| `duplicate_of` | NULL | FK al documento canonico |
| `dedup_checked` | 0 | 1=fase dedup ha già processato questo doc |

`migrate_db(conn)` aggiunge queste colonne ai DB esistenti in modo idempotente (safe su ogni `db init`).

Constraint: `PRAGMA foreign_keys = ON` impostato su ogni connessione.

### 4.3 sqlite-vec

Tabella virtuale `vec_documents` con embedding FLOAT[384] (multilingual-e5-small output dim).

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents
USING vec0(
    document_id INTEGER PRIMARY KEY,
    embedding   FLOAT[384]
);
```

Query nearest-neighbour (usata da `dedup.py` e `cluster.py`):

```sql
SELECT document_id, distance
FROM vec_documents
WHERE embedding MATCH ?   -- blob: struct.pack("384f", *unit_vector)
  AND k = 20
ORDER BY distance;
```

Distanza L2 su vettori normalizzati ≈ distanza coseno: `L2 = sqrt(2*(1-cos_sim))`.  
Soglie: dedup `cos≥0.92` → `L2<0.4` · cluster `cos≥0.85` → `L2<0.55`.

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

**Data dell'evento:** si usa **DATEADDED** (quando GDELT ha osservato l'evento)
come data canonica → `published_at` / `first_seen`. `SQLDATE` è inaffidabile
(bug noti di anno: rollover −100 e off-by-1yr) e resta solo come fallback. Vedi
[data-semantics.md](data-semantics.md).

**Dettaglio numerico:** ogni riga GDELT (`GlobalEventID`) è salvata in
**`gdelt_events`** con i segnali numerici per-riga (`goldstein`, `avg_tone`,
`quad_class`, `num_mentions`/`sources`/`articles`, `event_code`, `date_added`),
legata al cluster `events` e al documento. `raw_documents.origin` / `events.origin`
= `gdelt`.

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

### 5.2 RSS multi-blocco

**Stato: ✅ Implementato** — `pathosphere/ingest/rss.py`

Fetches RSS feeds da tutte le fonti attive in `sources`. Ogni articolo → `raw_documents` con `source_id` → blocco geopolitico. Dedup su `url UNIQUE` + `content_hash UNIQUE` (SHA-256 del body).

**Comando:**

```bash
uv run pathos ingest rss                         # tutte le fonti attive, ultimi 2 giorni
uv run pathos ingest rss --max-age-days 7        # ultimi 7 giorni
uv run pathos ingest rss --source-ids 1,2,3      # solo queste sorgenti
```

**48 fonti attive, 7 blocchi geopolitici** (aggiornato 2026-06-15):

| Blocco | Fonti attive | Lingue |
|---|---|---|
| `western` | ANSA, BBC, France 24, DW, MarketWatch, FT, Nikkei Asia, Straits Times, Haaretz, OilPrice, Defense News, Taipei Times, DIGITIMES, HK Free Press, The Diplomat, ChinaFile, **MERICS**, **Taiwan MOFA** | en/it |
| `china` | Global Times (state, bassa freq), SCMP (All News), **SCMP China** (section), China Digital Times, TechNode | en |
| `russia` | TASS, RT (via Tor), The Moscow Times, Russia in Global Affairs | en |
| `arab` | Al Jazeera, Anadolu, Press TV, Arab News | en |
| `india` | The Hindu, NDTV, Scroll.in | en |
| `latam` | Folha de S.Paulo | pt |
| `africa` | AllAfrica, Daily Maverick, RFI Afrique, Jeune Afrique, Premium Times, La Nation Djibouti, Somaliland Sun, Somaliland Standard | en/fr |
| `other` | Dawn, Geo News (PK); EVN Report (AM); Trend, AzerNews (AZ) | en |

Aggiunte 2026-06-15: **MERICS** (DE, istituto europeo ricerca China, live); **Taiwan MOFA** (TW, segnali diplomatici cross-strait, live); **SCMP China** (sezione China Politics/Diplomacy, integra feed All News). SCMP aggiornato da `/5` (World) a `/91` (All News, volume ×4).

**Principio:** divergenza narrativa tra blocchi = segnale analitico. Stessa notizia da TASS e The Moscow Times con frame opposti → `narrative_divergences.divergence_score` alto → input per tesi.

**Fonti disabilitate** (`active=0`, conservate nel seed):
- *Nessun RSS pubblico*: AP, AFP, DPA, APO Group, China Daily (feed congelato al 2017-12-12).
- *Feed morto/bloccato* (commentate in `sources_seed.py`): Reuters (DNS), EFE (500), Kyodo (404), ANI (404), Focus Taiwan (404), The East African (403), Armenpress (403 Cloudflare), Xinhua (congelato 2018), Sixth Tone (404), Caixin (403).
- *Bassa frequenza ma live*: Global Times `outbrain.xml` (~1 art/mese, unico feed funzionante).

**HTTP / anti-blocco:** httpx con header browser completi (UA + `Accept-Language` + `Sec-Fetch-*` + `Upgrade-Insecure-Requests`) — necessari oltre i bot-check stile Cloudflare (es. Arab News). Timeout 20s, follow_redirects. Parsing: feedparser 6.x. Errori per singola fonte non bloccanti.

**Fonti geo-bloccate via Tor:** `TOR_SOURCES` in `rss.py` (oggi `{"RT"}`, sanzionata UE → connessione rifiutata diretta). [`tor_proxy.py`](../pathosphere/ingest/tor_proxy.py) riusa un proxy Tor attivo (Tor Browser 9150 / daemon 9050) o avvia un **daemon `tor` effimero** (bootstrap → fetch → stop). Config: `tor_socks_proxy`. Se Tor non è disponibile, quelle fonti vengono saltate senza bloccare le altre. Richiede il binario `tor` (`brew install tor`) per il daemon.

### 5.3 PortWatch / Comtrade / USGS / FIRMS

**Stato: ✅ Implementati** — `ingest/portwatch.py`, `ingest/comtrade.py`, `ingest/physical.py`

| Fonte | Dati | Tabelle | Storico | Incrementale (da ultimo) |
|---|---|---|---|---|
| IMF PortWatch | Transiti chokepoint | `chokepoint_metrics` + `events` (anomalie z-score) | `--full` (~2019→oggi, paginato) | default `--days 90` (overlap + upsert idempotente) |
| UN Comtrade | Flussi HS 8541/8542/8486 | `raw_documents` (doc sintetico) + `comtrade_flows` | `--start YYYYMM` (backoff su 429) | default 3 mesi recenti (~2 mesi lag) |
| USGS | Terremoti significativi | `events` (`origin=usgs`, `hazard`) | `--start YYYY-MM-DD [--end]` | riprende da `max(first_seen)` USGS; fallback `--days` |
| NASA FIRMS | Incendi attivi | `fire_metrics` + `events` (surge z-score) | `--start YYYY-MM-DD` (auto source `VIIRS_NOAA20_SP`, finestre ≤5gg) | riprende da `max(date)` per area; fallback `--days` |

Tutti gli ingestor valorizzano `origin`. **Pattern satellite numerico**: PortWatch
e FIRMS tengono la timeseries giornaliera fuori dalla vista dell'LLM
(`chokepoint_metrics` / `fire_metrics`) e promuovono a `events` solo le **anomalie
z-score** vs baseline trailing (punto escluso → no lookahead). Detector condiviso
in `ingest/anomaly.py`: nel ciclo incrementale valuta solo l'ultimo giorno; nel
backfill (`portwatch --full`, `firms --start`) **scorre tutta la timeseries** e
recupera anche le anomalie storiche nel mezzo del range (non solo l'ultima). Comtrade salva i
valori numerici dei flussi (USD, kg) in `comtrade_flows`, oltre al doc sintetico.

**Ogni fonte ha due modalità** — bootstrap storico (post-2018 dove la fonte lo
consente) + incrementale "dall'ultimo rilevamento". Eccezione: **RSS** è solo
incrementale (i feed espongono solo articoli recenti; nessuno storico possibile).

**FIRMS — dettaglio sorgenti.** Default NRT: `VIIRS_NOAA20_NRT` (NOAA-20/JPSS-1,
satellite operativo primario); archivio standard: `VIIRS_NOAA20_SP` (dal 2018+) e
`MODIS_SP` (dal 2000). Con `--start` la CLI passa automaticamente a `VIIRS_NOAA20_SP`;
se SP restituisce 400 (dati non ancora archiviati per date recenti) scatta il
fallback NRT automatico. L'API area FIRMS limita ogni richiesta a ≤5 giorni → il
backfill itera finestre da 5gg per area. Nota: Bering Strait e Kerch Strait (area
prevalentemente acquatica/artica) possono risultare senza dati fire (0 rilevazioni =
corretto). L'anomalia richiede ≥11 punti di baseline e un floor assoluto
(`--min-detections`, default 50) per non scattare su baseline quasi vuote.

**Ancora da agganciare:** IODA / Cloudflare Radar (blackout internet), yfinance
(prezzi EOD per il paper trading).

---

## 6. Pipeline semantica (Fase 2)

**Stato: ✅ Implementato** — `pathosphere/semantic/`

La pipeline semantica trasforma `raw_documents` in segnali strutturati eliminando il rumore prima che l'LLM lo veda. Tre step in sequenza, lanciabili con un unico comando:

```bash
uv run pathos embed                          # embed + dedup + cluster
uv run pathos embed --batch-size 16          # batch più piccoli (meno RAM)
uv run pathos embed --skip-dedup             # solo embedding, no dedup
uv run pathos embed --skip-cluster           # embedding + dedup, no clustering
```

### 6.1 Embedding

**File:** `pathosphere/semantic/embedder.py`

Modello: **`intfloat/multilingual-e5-small`** (~500 MB, HuggingFace/sentence-transformers).  
Output: vettori 384-dim normalizzati a norma unitaria.

| Parametro | Valore |
|---|---|
| Dimensione embedding | 384 float32 |
| Batch size default | 32 documenti |
| Prefisso testo | `"passage: "` (convenzione intfloat/e5) |
| Troncamento | 1024 caratteri prima del tokenizer |
| Storage | blob `struct.pack("384f", ...)` in `vec_documents` |

Processo per ogni batch:
1. Legge `raw_documents WHERE embedded = 0`
2. Costruisce `"passage: " + title + " " + body[:1024]`
3. Encode con `normalize_embeddings=True` → vettori unitari
4. `INSERT OR REPLACE INTO vec_documents`
5. `UPDATE raw_documents SET embedded = 1`

Documenti senza title e body: `embedded=1` (nessun vettore inserito), contati in `docs_skipped`.

Vincolo RAM: un solo modello in memoria alla volta. Il modello viene caricato una volta e usato per tutti i batch del ciclo.

### 6.2 Dedup semantica

**File:** `pathosphere/semantic/dedup.py`

Marca near-duplicati (stesso articolo ripreso da più fonti) prima che arrivino al clustering.

| Parametro | Default | Note |
|---|---|---|
| Soglia coseno | 0.92 | `L2 < sqrt(2*0.08) ≈ 0.4` su vettori unitari |
| Finestra temporale | 72h | Confronto solo tra articoli vicini nel tempo |
| K nearest neighbours | 20 | Query sqlite-vec KNN per doc |

Algoritmo (ordine cronologico ASC → il documento più vecchio è canonico):
1. Per ogni `embedded=1, is_duplicate=0, dedup_checked=0` (ordinati per `published_at ASC`)
2. KNN query su `vec_documents`
3. Se un neighbour ha `id < corrente` e `distance < soglia` e `published_at` nella finestra: `is_duplicate=1, duplicate_of=<nb_id>`
4. `dedup_checked=1` in ogni caso

Risultato: `is_duplicate=0` = documento canonico da mostrare all'LLM.

### 6.3 Clustering → eventi

**File:** `pathosphere/semantic/cluster.py`

Raggruppa articoli canonici (non-duplicati) che parlano dello stesso evento in record `events`.

| Parametro | Default | Note |
|---|---|---|
| Soglia coseno | 0.85 | Soglia alta — separa storie distinte (0.75 causava chain-collapse) |
| Max cluster size | 30 | Tetto hard su union-find — previene chaining runaway |
| Finestra temporale | 72h | Solo articoli recenti (COALESCE published_at, fetched_at) |
| K nearest neighbours | 20 | Query KNN per candidato |

Algoritmo union-find con size-cap:
1. Candidati: `embedded=1, is_duplicate=0`, non già in `event_documents`, pubblicati nelle ultime 72h
2. Per ogni candidato: KNN → union se `distance < soglia` E cluster risultante `≤ max_cluster_size`
3. Componenti connesse → un record `events` per componente (`origin` = blocco maggioritario)
4. Titolo evento: primo documento (più vecchio) con titolo non-NULL
5. `INSERT OR IGNORE INTO event_documents` per ogni doc nel cluster

Risultato campione (2026-06-15, 800 doc RSS 72h): 329 eventi, di cui 268 singleton + 10 cappati a 30 (storie più coperte). Cluster top con copertura multi-blocco: Taiwan/defense (4 blocchi), Iran drones (6 blocchi), Russia oil ban (4 blocchi).

**Nota GDELT**: GDELT escluso dall'embedding (opzione a) — `UPDATE raw_documents SET embedded=1 WHERE origin='gdelt'` prima di `pathos embed`. Body vuoto → titolo sintetico non utile per clustering semantico. Se in futuro si arricchisce via GKG, resettare `embedded=0` sui doc GDELT e ri-eseguire.

### 6.4 NER + geocoding + Wikidata

**File:** `pathosphere/semantic/extract.py`  
**Comando:** `uv run pathos extract`

Tre step indipendenti e riprendibili:

| Step | Funzione | Output tabelle |
|---|---|---|
| NER (spaCy `xx_ent_wiki_sm`) | `extract_entities` | `entities` + `document_entities` |
| Geocoding (Nominatim) | `geocode_events` | `events.lat/lon` + `geocode_cache` |
| Wikidata QID | `link_wikidata` | `entities.wikidata_qid` + `canonical_name` |

**Parametri:**

| Flag | Default | Note |
|---|---|---|
| `--limit` | nessuno | Max doc per NER (utile per test) |
| `--max-lookups` | 50 | Budget lookup Nominatim + Wikidata per run |
| `--skip-geocode` | off | Salta Nominatim (solo NER + Wikidata) |
| `--skip-wikidata` | off | Salta Wikidata (solo NER + geocoding) |

**NER:** modello `xx_ent_wiki_sm` (~30 MB), multilingua. Label map: `PER→person`, `ORG→company`, `LOC→location`, `MISC→other`. Ogni doc viene troncato a 2000 caratteri (title + body head). Flag `ner_done=1` segna i doc già processati → riprendibile.

**Geocoding:** Nominatim lookup per eventi con `location_name` non nullo e `lat IS NULL`. Rate: 1 req/s (usage policy). Cache in `geocode_cache` (include misses → no rilookup).

**Wikidata:** `wbsearchentities` API per entità ordinate per `mentions DESC` (priorità alle più citate). Conflict on `UNIQUE(wikidata_qid)` gestito: marca `wikidata_checked=1` senza sovrascrivere (merge futura work).

**Prerequisito una-tantum:**

```bash
uv run python -m spacy download xx_ent_wiki_sm
```

---

## 7. Ciclo notturno

**File:** `pathosphere/cycle/orchestrator.py`

Cinque fasi sequenziali, riprendibili da qualsiasi punto. Ogni fase è atomica: se fallisce, il ciclo si ferma e salva l'errore in `CycleState`.

```
INGEST → EMBED → EXTRACT → CLUSTER → BRIEF
  ✅       ✅        ✅         ✅        ⬜
```

| Fase | Funzione | Stato | Descrizione |
|---|---|---|---|
| `INGEST` | `_phase_ingest` | ✅ | Scarica GDELT + RSS 52 fonti (48 attive) |
| `EMBED` | `_phase_embed` | ✅ | Embedding e5-small + dedup semantica KNN |
| `EXTRACT` | `_phase_extract` | ✅ | NER (spaCy) + geocoding Nominatim + Wikidata QID |
| `CLUSTER` | `_phase_cluster` | ✅ | Union-find clustering → eventi |
| `BRIEF` | `_phase_brief` | ⬜ stub | Genera brief + tesi (Claude SDK) |

**Comandi:**

```bash
uv run pathos cycle                         # ciclo completo
uv run pathos cycle --dry-run               # simula senza I/O
uv run pathos cycle --from-phase embed      # riprendi da EMBED (salta INGEST)
uv run pathos cycle --from-phase cluster    # riprendi da CLUSTER
uv run pathos cycle --from-phase brief      # solo brief mattutino
```

---

## 8. CLI Reference

Entry point: `uv run pathos`

```
pathos
├── db
│   ├── init            Crea/aggiorna schema SQLite + sqlite-vec + migrate_db
│   └── info            Mostra conteggi per tabella
├── sources
│   ├── list            Lista fonti configurate
│   └── seed            Inserisce le 52 fonti (48 attive) predefinite (7 blocchi)
├── ingest
│   ├── gdelt           Ciclo incrementale GDELT (ultimi N giorni)
│   │   ├── --days          Giorni back [default: 1]
│   │   ├── --quad          conflict|all [default: conflict]
│   │   ├── --min-mentions  Threshold NumMentions [default: 10]
│   │   └── --countries     ISO-2 filtro paesi (es. CN,TW,US)
│   ├── gdelt-history   Bootstrap storico GDELT (range date, resumable)
│   │   ├── --start         YYYY-MM-DD (obbligatorio)
│   │   ├── --end           YYYY-MM-DD [default: ieri]
│   │   └── --sample-hours  1 file ogni N ore [default: 1]
│   ├── rss             Fetch RSS da tutte le fonti attive
│   │   ├── --max-age-days  Salta articoli più vecchi di N giorni [default: 2]
│   │   └── --source-ids    Comma-separated IDs sorgente
│   ├── portwatch       Transiti chokepoint IMF PortWatch → chokepoint_metrics + eventi anomalia
│   │   ├── --days          Record per chokepoint [default: 90]
│   │   ├── --full          Backfill completo ~2019→oggi (paginato)
│   │   ├── --baseline-days Finestra baseline anomalia [default: 30]
│   │   └── --z-threshold   Soglia z-score [default: 2.0]
│   ├── comtrade        Flussi commerciali HS 8541/8542/8486 → comtrade_flows
│   │   ├── --start         Backfill da YYYYMM
│   │   ├── --end           Fine backfill YYYYMM
│   │   └── --delay         Secondi tra chiamate [default: 6; alzare se 429]
│   ├── usgs            Terremoti significativi USGS → events (hazard)
│   │   ├── --start         Backfill da YYYY-MM-DD
│   │   ├── --end           Fine backfill
│   │   └── --min-magnitude [default: 5.0]
│   └── firms           Rilevazioni fire NASA FIRMS → fire_metrics + eventi surge (richiede FIRMS_MAP_KEY)
│       ├── --start         Backfill da YYYY-MM-DD (auto source VIIRS_NOAA20_SP)
│       ├── --end           Fine backfill
│       ├── --baseline-days Finestra baseline [default: 30]
│       ├── --z-threshold   Soglia z-score [default: 2.0]
│       └── --min-detections Floor rilevazioni per anomalia [default: 50]
├── embed               Embedding + dedup semantica + clustering → eventi
│   ├── --batch-size    Doc per chiamata encode() [default: 32]
│   ├── --skip-dedup    Solo embedding, no dedup
│   └── --skip-cluster  Embedding + dedup, no clustering
├── extract             NER + geocoding + Wikidata entity linking
│   ├── --limit         Max doc su cui girare NER
│   ├── --max-lookups   Budget lookup geocoding + Wikidata [default: 50]
│   ├── --skip-geocode  Salta Nominatim
│   └── --skip-wikidata Salta Wikidata
├── cycle               Esegui ciclo notturno (INGEST→EMBED→EXTRACT→CLUSTER→BRIEF)
│   ├── --dry-run       Simula tutte le fasi senza I/O
│   └── --from-phase    Riprendi da fase specifica (ingest|embed|extract|cluster|brief)
└── config              Mostra configurazione attiva (.env + defaults)
```

---

## 9. Fonti dati

### Blocchi geopolitici

| Blocco | Codice DB | Fonti attive |
|---|---|---|
| Occidentale | `western` | ANSA, BBC, France 24, DW, MarketWatch, FT, Nikkei Asia, Straits Times, Haaretz, OilPrice, Defense News, Taipei Times, DIGITIMES, HK Free Press, The Diplomat, ChinaFile, MERICS, Taiwan MOFA |
| Cina | `china` | Global Times (bassa freq), SCMP (All News + China section), China Digital Times, TechNode |
| Russia | `russia` | TASS, RT (via Tor), The Moscow Times, Russia in Global Affairs |
| Mondo arabo | `arab` | Al Jazeera, Anadolu Agency, Press TV, Arab News |
| India | `india` | The Hindu, NDTV, Scroll.in |
| America Latina | `latam` | Folha de S.Paulo |
| Africa | `africa` | AllAfrica, Daily Maverick, RFI Afrique, Jeune Afrique, Premium Times, La Nation Djibouti, Somaliland Sun, Somaliland Standard |
| Altro | `other` | Dawn, Geo News (PK); EVN Report (AM); Trend, AzerNews (AZ) |

### Segnali fisici

| Fonte | Tipo | Frequenza | Stato |
|---|---|---|---|
| GDELT 2.0 | Conflitti/politica (multilingua) | 15 min | ✅ |
| RSS multi-blocco | Notizie 7 blocchi geopolitici | Asincrono | ✅ |
| ACLED, UCDP | Conflitti armati | Settimanale | ⬜ |
| WHO DON, ProMED | Epidemie | Quotidiana | ⬜ |
| IMF PortWatch | Traffico chokepoint marittimo | Quotidiana | ✅ |
| UN Comtrade | Flussi commerciali (HS code) | Mensile | ✅ |
| USGS | Terremoti | Realtime | ✅ |
| NASA FIRMS | Incendi | 3h | ✅ |
| IODA | Blackout internet | Realtime | ⬜ |
| yfinance | Prezzi EOD | Giornaliero | ⬜ |
| FRED | Macro (tassi, CPI…) | Varia | ⬜ |

### Divergenza narrativa come segnale

Ogni `raw_document` porta il `source_id` → `geopolitical_block`. Quando CNN e Xinhua raccontano lo stesso evento in modo opposto, la `narrative_divergences.divergence_score` sale → segnale da analizzare.

---

## 10. Valutazione del modello

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

## 11. Testing

```
tests/
├── conftest.py          Fixture tmp_db (SQLite in-memory), make_gdelt_row()
├── test_db.py           Schema init, tabelle, sqlite-vec, integrità FK
├── test_gdelt.py        URL gen, parsing, filtraggio, storage, dedup
├── test_orchestrator.py dry_run, from_phase, gestione errori
├── test_semantic.py     embed, dedup semantica, clustering (MockModel, no download)
├── test_portwatch.py    PortWatch fetch, upsert, anomalie z-score
├── test_physical.py     USGS quake parse/store; FIRMS window logic, metrics, anomalie
└── test_anomaly.py      find_anomalies: surge/drop/both, whole_history, min_value (8 test)
```

**Esecuzione:**

```bash
uv run pytest            # 150 test, ~8s, zero chiamate HTTP/modello reali
uv run pytest -v         # output verboso
uv run pytest tests/test_semantic.py   # solo pipeline semantica
```

**Filosofia:** nessuna chiamata HTTP reale, nessun download di modello nei test. `MockModel` restituisce vettori unitari deterministici (seed da hash del testo). `tmp_db` fixture crea un DB SQLite temporaneo per ogni test.

**Fixture riutilizzabili:**

```python
# conftest.py
tmp_db      → sqlite3.Connection con schema completo, FK ON, WAL
make_gdelt_row(**overrides)  → dict con tutti i 61 campi GDELT (default: CN-TW conflict)

# test_semantic.py (helper locali)
MockModel            → encode() deterministico, no sentence-transformers download
_insert_doc(conn)    → inserisce raw_document e ritorna id
_insert_vec(conn, doc_id, vec)  → inserisce blob in vec_documents, setta embedded=1
_unit_vec(seed)      → genera vettore unitario riproducibile
```

---

## 12. Roadmap

| Fase | Componente | Stato |
|---|---|---|
| **0** | Config, logging, CLI skeleton | ✅ |
| **0** | SQLite schema + sqlite-vec | ✅ |
| **0** | Ciclo orchestrator (struttura) | ✅ |
| **1** | GDELT 2.0 ingestor (incrementale + bootstrap) | ✅ |
| **1** | RSS multi-blocco (52 fonti (48 attive), 7 blocchi, 6 lingue) | ✅ |
| **1** | PortWatch + Comtrade semiconduttori | ✅ |
| **1** | USGS + NASA FIRMS | ✅ |
| **1** | IODA / Cloudflare Radar (blackout internet) | ⬜ |
| **1** | Storicizzazione Parquet | ⬜ |
| **2** | Embedding e5-small + dedup semantica KNN | ✅ |
| **2** | Clustering articoli → eventi | ✅ |
| **2** | NER + geocoding (spaCy + Nominatim) | ✅ |
| **2** | Wikidata entity linking | ✅ |
| **2** | Grafo entità | ⬜ |
| **3** | Brief mattutino (Claude SDK) | ⬜ |
| **3** | Generatore tesi con catene causali | ⬜ |
| **3** | Paper trading engine + approvazione CLI | ⬜ |
| **3** | Portafogli di controllo + calibrazione Tetlock | ⬜ |
| **4** | Dashboard Streamlit minimale | ⬜ |

**MVP verticale:** filiera semiconduttori — TSMC/ASML/SMIC, chokepoint Taiwan Strait. Pochi attori, geopolitica intensa, segnali chiari.

---

*Documenti correlati: [architecture.md](architecture.md) · [schema.md](schema.md) · [decisions.md](decisions.md) · [../useful_queries.sql](../useful_queries.sql)*

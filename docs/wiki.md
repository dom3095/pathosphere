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
8. [Agent e valutazione (Fase 3)](#8-agent-e-valutazione-fase-3)
   - 8b. [Dashboard Streamlit (Fase 4)](#8b-dashboard-streamlit-fase-4)
   - 8c. [Doctor — health check operativo](#8c-doctor--health-check-operativo)
9. [CLI Reference](#9-cli-reference)
10. [Fonti dati](#10-fonti-dati)
11. [Valutazione del modello](#11-valutazione-del-modello)
12. [Testing](#12-testing)
13. [Roadmap](#13-roadmap)

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
│   INGEST → EMBED → EXTRACT → CLUSTER → GRAPH → BRIEF        │
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
│   └── orchestrator.py 6 fasi sequenziali riprendibili (INGEST→EMBED→EXTRACT→CLUSTER→GRAPH→BRIEF).
├── ingest/
│   ├── gdelt.py        Downloader GDELT 2.0. Incrementale + bootstrap storico.
│   ├── rss.py          Ingestor RSS multi-blocco. 52 fonti (48 attive), 7 blocchi geopolitici.
│   ├── portwatch.py    IMF PortWatch: transiti chokepoint → anomalie z-score.
│   ├── comtrade.py     UN Comtrade: flussi HS 8541/8542/8486 → comtrade_flows.
│   ├── physical.py     USGS terremoti + NASA FIRMS incendi → events.
│   ├── ioda.py         IODA blackout internet (BGP, 24 paesi) → internet_metrics + events.
│   ├── anomaly.py      Detector z-score condiviso (surge/drop/both, no lookahead).
│   └── sources_seed.py Catalogo fonti: lista completa + seed_sources(conn).
├── export/
│   └── parquet.py      Export Parquet partizionato (dated/undated). Fonte di verità ricostruibile.
├── semantic/
│   ├── embedder.py     Batch embedding multilingual-e5-small → vec_documents.
│   ├── dedup.py        KNN dedup semantica via sqlite-vec (cosine ≥ 0.92).
│   ├── cluster.py      Union-find clustering → events + event_documents.
│   ├── extract.py      NER (spaCy xx_ent_wiki_sm) + geocoding Nominatim + Wikidata QID.
│   └── graph.py        Grafo co-occorrenze → entity_links; divergenza narrativa → narrative_divergences.
├── llm/
│   └── client.py       LLMClient: Claude SDK + Qwen-local (Ollama). OpenAI-compatible. ✅
├── agent/
│   ├── brief.py        Brief mattutino: divergenze + anomalie → Claude → briefs. ✅
│   ├── thesis.py       Generatore tesi fast path (1 Claude call). ✅
│   ├── debate.py       Pipeline debate 4-step (Qwen×13 + Claude×1). ✅
│   └── approval.py     Approvazione/rifiuto tesi: list, show, approve, reject. ✅
└── market/
    └── prices.py       fetch_price(ticker) via yfinance EOD. ✅
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
| `gdelt_events` | 1/riga GDELT | Dettaglio numerico per `GlobalEventID` (Goldstein/tone/mentions), aggregato → anomalie `events` (CP-016) |
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
| `internet_metrics` | 1/(paese, giorno) | Segnale BGP/active giornaliero per 24 paesi (IODA; drop → `events`) |
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
`quad_class`, `num_mentions`/`sources`/`articles`, `event_code`, `date_added`,
`action_geo_country`), legata al cluster `events` e al documento. `raw_documents.origin`
/ `events.origin` = `gdelt`.

**Percorso numerico anomalie (CP-016)** — `pathosphere/ingest/gdelt_anomaly.py`,
comando `pathos ingest gdelt-anomalies`: aggrega `gdelt_events` per
giorno+paese (`action_geo_country`)+`quad_class` (media Goldstein/tone, conteggio
righe), poi riusa il rilevatore trailing-baseline condiviso (`ingest/anomaly.py`,
stesso usato da PortWatch/FIRMS/IODA, no lookahead) per promuovere deviazioni
|z| ≥ soglia direttamente a `events` (`event_type='gdelt_anomaly'`, dedup by
title). Bypassa NER/embed/cluster — il segnale quantitativo di GDELT (prima
scritto e mai letto) ora produce eventi propri invece di passare per la
pipeline NLP pensata per prosa reale (vedi nota §6.3). Nel ciclo notturno gira
subito dopo `ingest gdelt` (`cycle/orchestrator.py::_phase_ingest`).

```bash
uv run pathos ingest gdelt-anomalies                             # ultimo giorno per serie, baseline 30gg
uv run pathos ingest gdelt-anomalies --full                      # sweep intera storia (dopo gdelt-history)
uv run pathos ingest gdelt-anomalies --z-threshold 2.5 --min-events-per-day 5
uv run pathos ingest gdelt-anomalies --backfill-country --full   # dopo un gdelt-history su range già ingerito, vedi nota sotto
```

**Nota `--backfill-country`:** `gdelt.py::store_rows` fa `INSERT OR IGNORE` su `global_event_id` — rilanciare `gdelt-history` su un range di date già scaricato **non aggiorna** le righe `gdelt_events` esistenti. Se `action_geo_country` è stata aggiunta dopo che quel range era già in DB (caso reale del 2026-07-07: 230k/234k righe storiche con la colonna NULL), il sweep anomalie non ha abbastanza giorni per serie e produce 0 eventi in silenzio. `--backfill-country` recupera il country code dall'ultimo campo di `events.title` (chiave dedup `Actor1CC|Actor2CC|EventRootCode|SQLDATE|ActionGeoCC`, sempre stata lì) prima di far girare il sweep — idempotente, va eseguito una volta dopo qualunque `gdelt-history` su storico pre-esistente.

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

**yfinance** (prezzi EOD): agganciato in Fase 3 — `market/prices.py` + paper trading EOD (3e).

### 5.4 IODA (blackout internet)

**Stato: ✅ Implementato** — `ingest/ioda.py`

IODA (Internet Outage Detection and Analysis, Georgia Tech) rileva blackout internet via segnale BGP (visibilità prefissi di routing) e probing attivo ICMP. Nessuna chiave API richiesta.

**24 paesi monitorati:** Afghanistan, Azerbaijan, Bangladesh, Belarus, China, Cuba, Ethiopia, Iraq, Iran, Kazakhstan, Libya, Myanmar, Nigeria, Pakistan, Palestine, Russia, Sudan, Syria, Tajikistan, Ukraine, Uzbekistan, Venezuela, Vietnam, Yemen.

**Flusso:**
1. Fetch segnale BGP giornaliero per ogni paese (timeseries 5-min → media giornaliera)
2. Upsert in `internet_metrics(country_code, date, signal_bgp)`
3. Rileva drop anomali (`direction="drop"`) vs baseline 30 giorni — z-score ≥ 2.5
4. Promuove anomalie a `events(event_type='infrastructure', origin='ioda')`

**Parametri:**

| Flag | Default | Note |
|---|---|---|
| `--days` | 1 | Giorni recenti (incrementale) |
| `--start / --end` | — | Bootstrap storico (date fisse) |
| `--countries` | tutti i 24 | Sottoinsieme ISO-2 (es. `CN,RU`) |
| `--baseline-days` | 30 | Finestra baseline z-score |
| `--z-threshold` | 2.5 | Più stretto di PortWatch/FIRMS (blackout rari) |
| `--datasource` | `bgp` | `bgp` o `active` |

**Esempi:**

```bash
uv run pathos ingest ioda                          # ieri, tutti i 24 paesi
uv run pathos ingest ioda --days 7                 # ultima settimana
uv run pathos ingest ioda --countries CN,RU,IR     # solo questi tre
uv run pathos ingest ioda --start 2026-01-01       # bootstrap storico
```

**Incrementale:** per ogni paese riprende dall'ultima data in `internet_metrics`. Se nessun dato, recupera `days + baseline_days - 1` giorni per costruire subito una baseline significativa.

**Rate limit:** 1 req/s (cortesia verso l'API pubblica). Errori per singolo paese non bloccanti → `IODAResult.errors`.

**API (2026-07):** endpoint corretto `https://api.ioda.inetintel.cc.gatech.edu/v2` (il vecchio host `ioda.inetintel.cc.gatech.edu/api/v2` risponde HTML SPA con 200). Query singola limitata a <100 giorni → range lunghi spezzati automaticamente in chunk da 90 giorni (`IODA_MAX_CHUNK_DAYS`). Risposta reale annidata `{"data": [[{...}]]}` — gestita insieme alle shape `{"data": {"signals": [...]}}` e `{"data": [...]}`.

### 5.5 Export Parquet

**Stato: ✅ Implementato** — `export/parquet.py`

Esporta le tabelle principali in formato Parquet partizionato per data. I raw in Parquet sono la **fonte di verità ricostruibile**: se il DB sparisce, si rigenera dai Parquet.

**Tabelle esportate:**
- **Dated** (`raw_documents`, `events`): partizionato `table/year=YYYY/month=MM/data.parquet`
- **Undated** (righe con `published_at/first_seen = NULL`): `table/undated/data.parquet`
- **Non-dated** (`entities`, `entity_links`): `table/data.parquet`

**Compressione:** Snappy (default pyarrow). Idempotente: sovrascrive le partizioni esistenti.

```bash
uv run pathos export parquet                       # tutte le tabelle → data/parquet/
uv run pathos export parquet --tables raw_documents,events
uv run pathos export parquet --out-dir /mnt/backup/parquet
```

**DuckDB query diretta (senza SQLite):**
```sql
SELECT * FROM 'data/parquet/raw_documents/**/*.parquet'
WHERE origin = 'gdelt' AND published_at > '2026-01-01';
```

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

**Nota GDELT (CP-016, risolto 2026-07-07)**: `origin IN ('gdelt','comtrade')` esclusi a monte in `semantic/embedder.py` (`NON_PROSE_ORIGINS`) — mai selezionati dalla query `embed_documents`, quindi restano `embedded=0` per sempre e non entrano mai in extract/cluster/graph (tutti richiedono `embedded=1`). Non più il workaround manuale precedente (`UPDATE ... SET embedded=1`, che falsificava il flag). GDELT ha ora un percorso numerico proprio, vedi §5.1.

### 6.5 Grafo entità + Divergenza narrativa

**File:** `pathosphere/semantic/graph.py`  
**Comando:** `uv run pathos graph`

Due step indipendenti e riprendibili:

| Step | Funzione | Output tabelle |
|---|---|---|
| Grafo co-occorrenze | `build_entity_links` | `entity_links` |
| Divergenza narrativa | `compute_narrative_divergences` | `narrative_divergences` |

**`build_entity_links`** — popola `entity_links` da co-occorrenze di entità all'interno degli stessi eventi:
- Query SQL unica (no loop Python): `JOIN event_documents × document_entities` per coppia `(entity_a < entity_b)`
- Conta quanti eventi distinti condividono la coppia → `strength = min(1.0, count / 10.0)`
- `relation_type = 'co-occurs'` (tipi semantici come `sanctions`, `supplies` spettano a Fase 3/LLM)
- Idempotente: `DELETE WHERE relation_type='co-occurs'` prima del re-insert

**`compute_narrative_divergences`** — per ogni evento con ≥ 2 blocchi geopolitici:
1. Raccoglie embeddings dei doc per blocco (via `event_documents → raw_documents → sources`)
2. Calcola centroide per blocco → L2-normalizza
3. `divergence_score = max(0, 1 - cos_sim)` — 0 = narrazioni identiche, 1 = opposte
4. Inserisce una riga per ogni coppia `(block_a < block_b)` in `narrative_divergences`
5. `summary = NULL` (Fase 3: LLM riempirà con testo esplicativo)

**Parametri:**

| Flag | Default | Note |
|---|---|---|
| `--skip-links` | off | Salta grafo co-occorrenze |
| `--skip-divergence` | off | Salta calcolo divergenza |
| `--min-cooccurrences` | 1 | Min eventi condivisi per creare un link |

**Vincoli RAM:** loop per evento. Ogni evento carica al massimo ~30 vettori × 384 × 4B ≈ 46 KB. Safe su M1 8 GB.

**Nota GDELT / source_id:** doc GDELT hanno `source_id=NULL` → esclusi automaticamente dalla divergenza (richiedono `source_id IS NOT NULL` per risalire al blocco). Solo RSS e Comtrade contribuiscono alla divergenza.

---

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

**Wikidata:** `wbsearchentities` API per entità ordinate per `mentions DESC` (priorità alle più citate). Rate: 1 req/s (`WIKIDATA_DELAY_S`), delay rispettato anche su errore. Su HTTP 429 il run si interrompe subito (le entità restanti restano `wikidata_checked=0` → ritentate al ciclo successivo). Stoplist `GENERIC_ENTITY_STOPLIST` (~110 nomi comuni/ruoli/demonimi es. `CRIMINAL`, `MILITARY`, `MALE`): marcati `wikidata_checked=1` senza lookup (e QID legacy sbagliati azzerati), così il budget va a entità vere. Conflict on `UNIQUE(wikidata_qid)` gestito: marca `wikidata_checked=1` senza sovrascrivere (merge futura work).

**Prerequisito una-tantum:**

```bash
uv run python -m spacy download xx_ent_wiki_sm
```

---

## 7. Ciclo notturno e automazione

**File:** `pathosphere/cycle/orchestrator.py`, `pathosphere/cycle/loop.py`, `scripts/setup_launchd.sh`

Sei fasi sequenziali, riprendibili da qualsiasi punto. Ogni fase è atomica e standalone: se fallisce, il ciclo si ferma e salva l'errore in `CycleState` (JSON persistente).

```
INGEST → EMBED → EXTRACT → CLUSTER → GRAPH → BRIEF
  ✅       ✅        ✅         ✅        ✅       ✅
```

| Fase | Funzione | CLI | Descrizione |
|---|---|---|---|
| `INGEST` | `_phase_ingest` | `pathos ingest gdelt/rss/…` | Scarica GDELT (+ anomalie Goldstein CP-016) + RSS 52 fonti (48 attive) + PortWatch/Comtrade/USGS/FIRMS/IODA |
| `EMBED` | `_phase_embed` | `pathos embed` | Embedding e5-small + dedup semantica KNN |
| `EXTRACT` | `_phase_extract` | `pathos extract` | NER (spaCy) + geocoding Nominatim + Wikidata QID |
| `CLUSTER` | `_phase_cluster` | `pathos cluster` | Union-find clustering → eventi |
| `GRAPH` | `_phase_graph` | `pathos graph` | Grafo co-occorrenze → entity_links; divergenza narrativa → narrative_divergences |
| `BRIEF` | `_phase_brief` | `pathos brief` | Genera brief mattutino + tesi (Claude SDK) |

### Comandi ciclo (manuale)

**Pipeline completa (4 fasi semantiche):**
```bash
chmod +x scripts/run_pipeline.sh
./scripts/run_pipeline.sh                   # anomalie → embed → extract → cluster → graph (~1.5h, caffeinate)
```

**Step singoli:**
```bash
uv run pathos ingest gdelt-anomalies --backfill-country --full    # anomalie Goldstein
uv run pathos embed                         # embedding + dedup
uv run pathos cluster                       # clustering → eventi
uv run pathos extract                       # NER + geocoding + Wikidata
uv run pathos graph                         # grafo entità + divergenza narrativa
```

**Ciclo completo notturno (6 fasi):**
```bash
uv run pathos cycle                         # ciclo completo una volta
uv run pathos cycle --dry-run               # simula senza I/O
uv run pathos cycle --from-phase embed      # riprendi da EMBED (salta INGEST)
uv run pathos cycle --from-phase cluster    # riprendi da CLUSTER
uv run pathos cycle --from-phase graph      # solo graph + brief
uv run pathos cycle --from-phase brief      # solo brief mattutino
```

### Loop autonomo (CP-017)

**Loop permanente** (`pathosphere/cycle/loop.py`):
- Legge/scrive stato persistente: `data/cycle_state.json`
- Rilancia il ciclo completo ogni N ore (default 1h tra cicli)
- Retry automatico con backoff esponenziale (max 3 tentativi per fase, poi pausa 5min)
- Resumable da crash: legge `last_phase` dal JSON, riparte da `next_phase_after`
- Graceful shutdown: Ctrl+C salva stato e esci

**Setup launchd (macOS daemon — una volta sola):**
```bash
chmod +x scripts/setup_launchd.sh
./scripts/setup_launchd.sh
# Opzioni:
#   --interval SECONDS    (default 43200 = 12 ore)
#   --uninstall           (disattiva e rimuovi)
# Monitor: tail -f data/logs/launchd.log
```

**Loop manuale (debug/test):**
```bash
chmod +x scripts/run_pipeline.sh
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Monitor: tail -f data/cycle_state.json
```

**Stato persistente** (`data/cycle_state.json`):
```json
{
  "last_phase": "EXTRACT",
  "last_completion": "2026-07-10T15:32:00",
  "error_log": [
    {"timestamp": "2026-07-10T14:00:00", "phase": "EMBED", "error": "OOM", "attempts": 3},
    ...
  ]
}
```

---

## 8. Agent e valutazione (Fase 3)

**Stato: ✅ Completa — 3a/3b/3c/3d/3e/3f ✅**

### 8.1 LLM client — `pathosphere/llm/client.py` ✅

Astrazione OpenAI-compatible. Un backend, due implementazioni:

| Backend | Config | Uso |
|---|---|---|
| `claude` | `REASONING_MODEL=claude` (default) | Brief mattutino, sintesi debate, tesi finali |
| `qwen-local` | `REASONING_MODEL=qwen-local` | Ricerca e critica personas (lavoro di massa) |

```python
client = LLMClient(backend="claude")
result = await client.complete(messages, json_mode=True)
```

Cambiare backend = una riga di config. A/B testing: stesso giorno, tesi da Qwen3 4B vs Claude, il paper trading misura la differenza.

### 8.2 Brief mattutino — `pathosphere/agent/brief.py` ✅

Legge dal DB: divergenze narrative (`divergence_score > 0.5`), entità hub (`entity_links`), anomalie recenti (portwatch/firms/usgs/ioda). 1 chiamata Claude → brief strutturato salvato in `briefs` + file `data/briefs/YYYY-MM-DD.md`.

```bash
uv run pathos brief                        # oggi, tutti i segnali
uv run pathos brief --lookback-days 3      # finestra più stretta
uv run pathos brief --dry-run              # solo conteggi, no LLM
```

### 8.3 Generatore tesi — `pathosphere/agent/thesis.py` + `debate.py` ✅

**Fast path** (`pathos thesis generate`): 1 Claude call → N tesi primarie + alternative. Ogni tesi: `title`, `causal_chain` (JSON), `instrument`, `direction`, `horizon_days`, `confidence`, `invalidation`, `watchlist_items`.

**Debate pipeline** (`pathos thesis debate`): 4 step sequenziali:
1. Research — 6 personas × Qwen (Beijing/Washington/Moscow/Riyadh/Jerusalem/Paris)
2. Divergence detection — Qwen identifica 2-3 disaccordi strutturali
3. Critique — ogni persona risponde ai punti di divergenza (Qwen)
4. Synthesis — Claude genera tesi con `debate_context` (supporters/opponents)

`price_snapshot` = prezzo EOD yfinance al momento della generazione (no-lookahead bias).

### 8.4 Flusso approvazione — `pathosphere/agent/approval.py` ✅

Human-in-the-loop: l'agent propone, l'utente decide.

```bash
uv run pathos thesis list                  # tesi pending (tabella: id/title/inst/dir/price/horizon/conf)
uv run pathos thesis show <id>             # dettaglio: trigger, causal chain, invalidation, debate context
uv run pathos thesis approve <id>          # status → approved | valida ticker yfinance (warn, non blocca)
uv run pathos thesis reject <id> --reason "Invalidation condition met"
```

- `rejection_reason` loggato in `theses` → dataset per analizzare pattern di rifiuto
- Ticker validation: `yfinance.fast_info.last_price` — se assente: warning stampato, approvazione procede
- `list --status all` mostra tutte le tesi indipendentemente dallo status

### 8.5 Paper trading EOD — `pathosphere/market/trading.py` ✅

```bash
uv run pathos portfolio init               # crea agent/random/benchmark + benchmark SPY trade
uv run pathos portfolio status             # P&L realizzato + non realizzato (live prices)
uv run pathos trade open <thesis_id>       # agent + random trade (price_open = live yfinance)
uv run pathos trade close <trade_id>       # chiude, calcola pnl
uv run pathos trade list [--portfolio agent|random|benchmark] [--closed]
```

**Tre portafogli:**
- `agent` — trade da tesi approvate
- `random` — trade di controllo: stesso qty/dir/timing, ticker casuale da pool `[SPY, QQQ, GLD, USO, TLT, EEM, IWM, XLE, XLF, DIA]`
- `benchmark` — buy-and-hold SPY, aperto a `portfolio init`

**Costi simulati:** `transaction_cost = 0.1% per lato`, `slippage = 0.05% per lato`. Entrambi i lati detratti al close nel calcolo del pnl.

**No-lookahead:** `price_open = yfinance fetch al momento di `trade open`` (non il price_snapshot salvato alla generazione della tesi).

### 8.6 Predizioni non finanziarie (v2) — `pathosphere/agent/predictions.py` ✅

**Predictions v2**: due binari (`macro_area`), time-adjusted scoring, calibrazione Tetlock.

**Binari:**
- `world` — geopolitical|political|social; richiede `origin_scope` + `impact_scope` + `domains` (10-tassonomia)
- `economic` — financial; legato a tesi approvata (`thesis_id` obbligatorio); scoring primario è EOD P&L

**Scoring:**
- `brier_score = (probability − outcome_eventual)²` — qualità direzione (0=perfetto)
- `time_adjusted_score = (1 − brier) × max(0, 1 − alpha × |resolved − horizon| giorni)` — penalità timing
- Se `outcome_eventual=false`, `time_adjusted_score=0` (evento non accaduto)
- Dual metric in `get_calibration()`: time-adjusted primaria (operativa), Brier secondaria (Tetlock-compatibile)

**Tabelle correlate:**
- `prediction_domains(prediction_id, domain, is_primary)` — 10 domini (conflitto_armato, tensione_militare, politica_interna, diplomazia, commercio, tecnologia, infrastruttura, finanza, salute, clima_risorse)
- `prediction_revisions(id, prediction_id, probability, rationale, revised_at)` — storia revisioni (superforecaster pattern)
- `theses.prediction_id` — catena world-prediction → thesis → trade (misurabile end-to-end)

**Comandi CLI:**
```
pathos predict add "Desc" \
  --macro-area world \
  --prediction-type geopolitical \
  --probability 0.65 \
  --horizon 2026-07-10 \
  --domain conflitto_armato --domain commercio \
  --primary-domain conflitto_armato \
  --origin-scope regionale \
  --impact-scope globale

pathos predict revise <id> --probability 0.7 [--rationale "new data"]
pathos predict list [--open|--resolved] [--macro-area world|economic] [--prediction-type X] [--domain X]
pathos predict resolve <id> --outcome-eventual true|false --resolved-date 2026-07-10
pathos predict calibration
```

**Nuovo comportamento:**
- `pathos thesis approve <id>` → auto-crea `predictions` con `macro_area=economic` e `prediction_type=economic`
- `pathos trade open <thesis_id>` → link oldest unresolved economic prediction a trade via `link_thesis_prediction_to_trade()`
- Migrazione: `outcome` legacy specchia `outcome_on_time` per retrocompatibilità; pre-v2 righe auto-backfillate come `macro_area='world'` + `prediction_type='geopolitical'`

---

## 8b. Dashboard Streamlit (Fase 4)

`pathos serve [--host localhost] [--port 8501]` — shell-out a `streamlit run pathosphere/dashboard/app.py`.

Struttura: `pathosphere/dashboard/app.py` (entry point, `st.set_page_config` +
navigazione sidebar) + `pathosphere/dashboard/views/*.py`, una funzione
`render(conn: sqlite3.Connection)` per pagina. Non multipage nativo
Streamlit (niente cartella `pages/`) — un `st.sidebar.radio` seleziona la
vista, tutto in un solo processo/URL. `dashboard/db.py::get_connection()`
apre una connessione SQLite fresca a ogni rerun invece di `st.cache_resource`:
gli oggetti `sqlite3.Connection` non sono thread-safe e `cache_resource` è
condiviso tra sessioni/thread Streamlit — aprire un file locale è comunque
economico.

**8 pagine:**
| Pagina | Contenuto | Fonte dati |
|---|---|---|
| Overview | Conteggi tabelle, freschezza dati, stato fasi 0-4 | tutte |
| Mappa | Eventi geolocalizzati (Folium + MarkerCluster), filtro tipo/fonte/giorni | `events` (lat/lon) |
| Narrazioni | Divergenza media per coppia di blocchi + top eventi | `narrative_divergences` |
| Grafo entità | Top-N entità hub per grado + sottografo indotto (layout circolare, no dipendenza layout aggiuntiva) | `entities`, `entity_links` (già risolti via `canonical_entity_id`, vedi `graph.py::build_entity_links`) |
| Tesi | Tab pending/approved/rejected, bottoni Approva/Rifiuta/Apri trade — **stesso comportamento della CLI** (`approve_thesis` + `create_thesis_prediction` su approvazione) | `theses`, `watchlist_items` |
| Portafogli | Curva equity (cash iniziale + P&L realizzato cumulato + punto live), trade aperti | `trades`, `portfolios` (via `market.trading`) |
| Predizioni | Curva di calibrazione Tetlock (accuratezza osservata vs probabilità dichiarata per bucket), liste aperte/risolte | `predictions` (via `agent.predictions.get_calibration`) |
| Brief | Storico brief mattutini, selezione per data | `briefs` |

Le pagine Tesi/Portafogli/Predizioni/Brief mostrano stato vuoto (info box)
finché il ciclo agent (Fase 3) non ha prodotto dati reali — normale finché
`pathos brief`/`pathos thesis generate`/approvazioni non sono state eseguite
almeno una volta sul DB corrente.

**Verifica:** `streamlit.testing.v1.AppTest` — carica `app.py`, simula il
click su ognuna delle 8 voci della sidebar, verifica assenza di eccezioni
contro il DB reale. Nessun test pytest dedicato (interfaccia, non logica —
la logica riusata, `approval.py`/`trading.py`/`predictions.py`, è già
coperta dai 498 test esistenti).

---

## 8c. Doctor — health check operativo

`pathos doctor [--network]` — diagnostica read-only in `pathosphere/doctor.py`.
Nessuna chiamata LLM, nessuna API a pagamento; l'unica rete di default è il
socket Ollama locale (timeout 3s). Exit code: 0 = nessun FAIL (warning
ammessi), 1 = almeno un FAIL — usabile in script (`pathos doctor && pathos cycle`).

**5 aree di check** (ogni riga = ✓ ok / ⚠ warn / ✗ fail / · skip):

| Sezione | Cosa verifica | Perché |
|---|---|---|
| Prerequisites | `claude` su PATH (FAIL se `reasoning_model=claude`), Ollama raggiungibile + modello pullato, spaCy `xx_ent_wiki_sm` installato | CP-001, CP-003: prerequisiti mai verificati a runtime prima |
| Config | Solo **presenza** (mai il valore — regola sicurezza CLAUDE.md) di `FIRMS_MAP_KEY`/`RELIEFWEB_APPNAME`; validità `reasoning_model` | chiavi mancanti = ingestori silenziosamente skippati |
| Freshness | Ultimo dato per fonte ricorrente (rss/gdelt/portwatch/firms/ioda/usgs 48-72h, comtrade 45gg) con hint del comando da lanciare | classe CP-023: degradazione che nessuno nota per giorni |
| Backlog | Doc in attesa di embedding/dedup/NER, eventi RSS senza geoloc (`geoloc_checked=0`), geocoding pendente, entità senza Wikidata — **stesse query dei moduli pipeline** (embedder/dedup/extract), conteggi identici a ciò che la fase processerebbe | pipeline ferma ≠ pipeline vuota |
| Agent | Portafogli inizializzati, tesi pending, trade aperti oltre orizzonte tesi, predizioni aperte oltre `horizon_date`, scenario set attivi oltre orizzonte, età ultimo brief (warn > 3gg) | to-do list operativa del giorno |

`--network` aggiunge un probe yfinance (quote SPY) — opt-in perché tocca la rete.

**Difensivo per costruzione**: ogni query DB cattura `sqlite3.OperationalError`
(tabella/colonna di una migration non ancora applicata → riga `skip`, mai
crash); i campi Settings introdotti da branch non mergiati sono letti con
`hasattr`/`getattr` — il comando funziona identico su un DB/config pre- o
post-merge dei branch in volo (technicals, scenari, backfill storico).

Soglie in `pathosphere/doctor.py`: `BACKLOG_WARN_AT` (dict per check),
`STALE_BRIEF_DAYS=3`, ore di staleness per fonte in `_FRESHNESS_SPECS`.

Test: `tests/test_doctor.py` (36, tutto mockato — nessun lookup PATH reale,
nessun socket, fixture autouse `hermetic`).

---

## 9. CLI Reference

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
│   ├── gdelt-anomalies Aggrega gdelt_events (goldstein/tone) → anomalie in eventi (CP-016)
│   │   ├── --baseline-days      Finestra trailing baseline [default: 30]
│   │   ├── --z-threshold        |z| soglia anomalia [default: 2.0]
│   │   ├── --min-events-per-day Minimo righe grezze per cella paese/giorno [default: 3]
│   │   ├── --full               Sweep intera storia invece di solo l'ultimo giorno
│   │   └── --backfill-country   Ripara action_geo_country su righe pre-migration prima del sweep
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
│   ├── firms           Rilevazioni fire NASA FIRMS → fire_metrics + eventi surge (richiede FIRMS_MAP_KEY)
│   │   ├── --start         Backfill da YYYY-MM-DD (auto source VIIRS_NOAA20_SP)
│   │   ├── --end           Fine backfill
│   │   ├── --baseline-days Finestra baseline [default: 30]
│   │   ├── --z-threshold   Soglia z-score [default: 2.0]
│   │   └── --min-detections Floor rilevazioni per anomalia [default: 50]
│   └── ioda            Blackout internet IODA (BGP, 24 paesi) → internet_metrics + eventi drop
│       ├── --days          Giorni recenti [default: 1]
│       ├── --start / --end Bootstrap storico (date fisse YYYY-MM-DD)
│       ├── --countries     ISO-2 comma-separated [default: tutti 24]
│       ├── --baseline-days Finestra baseline [default: 30]
│       ├── --z-threshold   Soglia z-score [default: 2.5]
│       └── --datasource    bgp | active [default: bgp]
├── export
│   └── parquet         Export tabelle principali → Parquet partizionato
│       ├── --tables        Subset (es. raw_documents,events) [default: tutte]
│       └── --out-dir       Directory output [default: data/parquet]
├── embed               Embedding + dedup semantica → eventi
│   ├── --batch-size    Doc per chiamata encode() [default: 32]
│   ├── --skip-dedup    Solo embedding, no dedup
│   └── --skip-cluster  Embedding + dedup, no clustering (usa pathos cluster separato)
├── cluster             Raggruppa doc deduplicati in eventi (Union-find)
│   └── --time-window-hours  Finestra temporale clustering [default: 72]
├── extract             NER + geocoding + Wikidata entity linking
│   ├── --limit         Max doc su cui girare NER
│   ├── --max-lookups   Budget lookup geocoding + Wikidata [default: 50]
│   ├── --skip-geocode  Salta Nominatim
│   ├── --skip-wikidata Salta Wikidata
│   └── --backfill-demonyms  Reclassifica demoniaci (Israeli→Israel) da other a location (CP-016)
├── graph               Grafo co-occorrenze + divergenza narrativa per blocco
│   ├── --skip-links        Salta build_entity_links
│   ├── --skip-divergence   Salta compute_narrative_divergences
│   └── --min-cooccurrences Min eventi condivisi per creare link [default: 1]
├── cycle               Esegui ciclo notturno (INGEST→EMBED→EXTRACT→CLUSTER→GRAPH→BRIEF)
│   ├── --dry-run       Simula tutte le fasi senza I/O
│   └── --from-phase    Riprendi da fase specifica (ingest|embed|extract|cluster|graph|brief)
├── loop                Ciclo autonomo permanente (CP-017) con stato persistente
│   ├── --max-retries         Tentativi per fase [default: 3]
│   ├── --sleep-hours         Ore tra cicli completi [default: 1.0]
│   └── --state-file          Path JSON stato [default: data/cycle_state.json]
├── brief               Genera brief mattutino intelligenza (Claude SDK)
│   ├── --date          Data ISO brief [default: oggi UTC]
│   ├── --lookback-days Giorni back per divergenze e anomalie [default: 7]
│   ├── --model         claude|qwen-local [default: da .env]
│   └── --dry-run       Mostra solo conteggi segnali, no LLM
├── thesis              Generazione e approvazione tesi
│   ├── generate        Genera N tesi da brief (fast path, 1 Claude call)
│   │   ├── --date      Data brief [default: oggi UTC]
│   │   ├── --n         Numero tesi primarie [default: 3]
│   │   └── --model     claude|qwen-local
│   ├── debate          Genera tesi via debate pipeline (Qwen×13 + Claude×1)
│   │   ├── --date      Data brief
│   │   └── --n         Numero tesi primarie [default: 3]
│   ├── list            Lista tesi filtrate per status
│   │   └── --status    pending|approved|rejected|closed|all [default: pending]
│   ├── show <id>       Dettaglio completo: trigger, causal chain, persona notes, debate context, watchlist
│   ├── approve <id>    Approva tesi pending (valida ticker yfinance, warn non blocca)
│   └── reject <id>     Rifiuta tesi pending con motivazione
│       └── --reason    Motivazione (obbligatoria, loggata in theses.rejection_reason)
├── portfolio           Gestione portafogli virtuali (paper trading)
│   ├── init            Crea agent/random/benchmark ($100k); benchmark apre SPY trade
│   └── status          P&L realizzato + non realizzato per portfolio (fetch prezzi live)
├── trade               Gestione paper trade
│   ├── open <thesis_id>  Apre agent + random trade da tesi approvata (price_open = live)
│   ├── close <trade_id>  Chiude trade: fetch prezzo, calcola pnl, persiste
│   └── list              Lista trade aperti (default) o chiusi (--closed)
│       ├── --portfolio   Filtra per portfolio (agent|random|benchmark)
│       └── --closed      Mostra trade chiusi invece di aperti
├── predict             Predizioni non finanziarie (v2: world/economic, time-adjusted score)
│   ├── add "Desc"        Inserisce predizione (world o economic track)
│   │   ├── --macro-area         world|economic (obbligatorio)
│   │   ├── --prediction-type    geopolitical|political|social|economic (obbligatorio)
│   │   ├── --probability        0.0–1.0 (obbligatorio)
│   │   ├── --horizon            YYYY-MM-DD (obbligatorio)
│   │   ├── --domain             X (ripetibile, ≥1 richiesto) — 10-tassonomia
│   │   ├── --primary-domain     X (default: primo --domain)
│   │   ├── --origin-scope       locale|nazionale|regionale|multilaterale|globale (obbligatorio per world)
│   │   ├── --impact-scope       locale|nazionale|regionale|multilaterale|globale (obbligatorio per world)
│   │   ├── --thesis-id          int (obbligatorio per economic)
│   │   └── --trade-id           int (opzionale, economic only)
│   ├── revise <id>       Aggiorna probabilità + registra revisione (history)
│   │   ├── --probability        0.0–1.0 (obbligatorio)
│   │   └── --rationale          Motivo (opzionale, loggato)
│   ├── list              Lista predizioni (default: tutte)
│   │   ├── --open               Solo aperte (non risolte)
│   │   ├── --resolved           Solo risolte
│   │   ├── --macro-area         Filtra track
│   │   ├── --prediction-type    Filtra tipo
│   │   └── --domain             Filtra per dominio tassonomia
│   ├── resolve <id>      Risolve predizione: time-adjusted + Brier score
│   │   ├── --outcome-eventual   true|false (obbligatorio — event ever happened)
│   │   └── --resolved-date      YYYY-MM-DD (obbligatorio — actual event date or eval date)
│   └── calibration       Dual-metric: time-adjusted score (primaria) + Brier (secondaria)
│       └── breakdown per bucket probabilità, macro_area, prediction_type
├── serve                Avvia dashboard Streamlit (Fase 4, vedi sezione 8b)
│   ├── --host           [default: localhost]
│   └── --port           [default: 8501]
├── doctor              Health check read-only: prerequisiti, config, freshness, backlog, agent (sezione 8c)
│   └── --network        Aggiunge probe yfinance (opt-in, tocca la rete)
└── config              Mostra configurazione attiva (.env + defaults)
```

---

## 10. Fonti dati

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
| IODA | Blackout internet (BGP, 24 paesi) | Realtime | ✅ |
| yfinance | Prezzi EOD | Giornaliero | ⬜ |
| FRED | Macro (tassi, CPI…) | Varia | ⬜ |

### Divergenza narrativa come segnale

Ogni `raw_document` porta il `source_id` → `geopolitical_block`. Quando CNN e Xinhua raccontano lo stesso evento in modo opposto, la `narrative_divergences.divergence_score` sale → segnale da analizzare.

---

## 11. Valutazione del modello

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

## 12. Testing

```
tests/
├── conftest.py          Fixture tmp_db (SQLite in-memory), make_gdelt_row()
├── test_db.py           Schema init, tabelle, sqlite-vec, integrità FK
├── test_gdelt.py        URL gen, parsing, filtraggio, storage, dedup
├── test_orchestrator.py dry_run, from_phase, gestione errori (6 fasi)
├── test_semantic.py     embed, dedup semantica, clustering (MockModel, no download)
├── test_extract.py      NER, geocoding, Wikidata QID linking
├── test_graph.py        build_entity_links, compute_narrative_divergences (10 test)
├── test_portwatch.py    PortWatch fetch, upsert, anomalie z-score
├── test_physical.py     USGS quake parse/store; FIRMS window logic, metrics, anomalie
├── test_anomaly.py      find_anomalies: surge/drop/both, whole_history, min_value (8 test)
├── test_ioda.py         _aggregate_daily, _fetch_signals, ingest_ioda: upsert, outage, dedup, errori, chunking 90gg, shape annidate, non-JSON (15 test)
├── test_parquet.py      export_to_parquet: partizioni, roundtrip, undated, idempotenza (9 test)
├── test_prices.py       fetch_price: EOD, ticker vuoto, history empty, exception (5 test)
├── test_brief.py        generate_brief, _query_*, dry-run (mock LLM)
├── test_thesis.py       generate_theses, _save_thesis, _save_watchlist_items (10 test)
├── test_thesis_approval.py  list_theses, approve/reject, validate_ticker, format_causal_chain (34 test)
└── test_trading.py      init_portfolios, open_trade, open_agent_trade, close_trade,
                         get_portfolio_status, list_open_trades, integration lifecycle (41 test)
```

**Esecuzione:**

```bash
uv run pytest            # 336 test, ~25s, zero chiamate HTTP/modello reali
uv run pytest -v         # output verboso
uv run pytest tests/test_semantic.py      # solo pipeline semantica
uv run pytest tests/test_thesis_approval.py  # solo flusso approvazione
uv run pytest tests/test_trading.py       # solo paper trading
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

## 13. Roadmap

| Fase | Componente | Stato |
|---|---|---|
| **0** | Config, logging, CLI skeleton | ✅ |
| **0** | SQLite schema + sqlite-vec | ✅ |
| **0** | Ciclo orchestrator (struttura) | ✅ |
| **1** | GDELT 2.0 ingestor (incrementale + bootstrap) | ✅ |
| **1** | RSS multi-blocco (52 fonti (48 attive), 7 blocchi, 6 lingue) | ✅ |
| **1** | PortWatch + Comtrade semiconduttori | ✅ |
| **1** | USGS + NASA FIRMS | ✅ |
| **1** | IODA blackout internet (BGP, 24 paesi) | ✅ |
| **1** | Storicizzazione Parquet (export partizionato) | ✅ |
| **2** | Embedding e5-small + dedup semantica KNN | ✅ |
| **2** | Clustering articoli → eventi | ✅ |
| **2** | NER + geocoding (spaCy + Nominatim) | ✅ |
| **2** | Wikidata entity linking | ✅ |
| **2** | Grafo entità (co-occorrenze → `entity_links`) | ✅ |
| **2** | Divergenza narrativa per blocco (→ `narrative_divergences`) | ✅ |
| **3** | LLM client (Claude SDK + Qwen-local) | ✅ |
| **3** | Brief mattutino (Claude SDK) | ✅ |
| **3** | Generatore tesi (fast path + multi-persona debate) | ✅ |
| **3** | Flusso approvazione CLI (list/show/approve/reject) | ✅ |
| **3** | Paper trading EOD + portafogli di controllo | ✅ |
| **3** | Calibrazione Tetlock (predizioni non finanziarie) | ✅ |
| **4** | Dashboard Streamlit minimale (`pathos serve`) | ✅ |

**MVP verticale:** filiera semiconduttori — TSMC/ASML/SMIC, chokepoint Taiwan Strait. Pochi attori, geopolitica intensa, segnali chiari.

→ **[Roadmap dettagliata](roadmap.md)** — task per task, Fase 3 con spec, non-goals.

---

*Documenti correlati: [architecture.md](architecture.md) · [schema.md](schema.md) · [decisions.md](decisions.md) · [roadmap.md](roadmap.md) · [../useful_queries.sql](../useful_queries.sql)*

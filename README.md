# Pathosphere

Sistema personale di intelligence OSINT su eventi critici globali — conflitti, epidemie, infrastrutture, rotte commerciali — con paper trading virtuale come metrica di valutazione del modello.

**Mono-utente · Dati aperti · Budget quasi zero · Human-in-the-loop.**

→ **[Wiki completa](docs/wiki.md)** — architettura, ingestori, CLI reference, testing, roadmap.
→ **[Roadmap](docs/roadmap.md)** — stato per fase, spec Fase 3, non-goals.
→ **[Semantica dei dati](docs/data-semantics.md)** — come leggere il DB senza fraintendere i campi (GDELT vs RSS vs Comtrade). **Leggere prima di interpretare i dati.**
→ **[Caveat embeddings](docs/embeddings-caveat.md)** — perché i vettori GDELT non rappresentano gli articoli. **Leggere prima di girare embed/clustering.**

---

## Setup rapido

```bash
# Prerequisiti
curl -LsSf https://astral.sh/uv/install.sh | sh
brew install ollama && ollama pull qwen3:4b

# Progetto
uv sync
cp .env.example .env
uv run pathos db init
uv run pathos sources seed      # 52 fonti (48 attive), 7 blocchi geopolitici
```

## Comandi principali

```bash
# Database
uv run pathos db init
uv run pathos db info

# Ingestione GDELT
uv run pathos ingest gdelt                          # ieri, conflitti
uv run pathos ingest gdelt --days 3                 # ultimi 3 giorni
uv run pathos ingest gdelt --countries CN,TW,US     # filtra per paese

# Bootstrap storico GDELT (una-tantum, ripartibile)
uv run pathos ingest gdelt-history --start 2024-01-01

# Ingestione RSS (52 fonti, 48 attive, 7 blocchi geopolitici)
# Fonti geo-bloccate (RT) via Tor: riusa proxy attivo o avvia daemon effimero (serve `brew install tor`)
uv run pathos ingest rss                            # tutte le fonti attive, ultimi 2 giorni
uv run pathos ingest rss --max-age-days 7           # ultimi 7 giorni

# Segnali fisici e flussi (Fase 1)
uv run pathos ingest portwatch                      # transiti chokepoint IMF (ultimi 90gg) → anomalie
uv run pathos ingest portwatch --full               # backfill storia completa (~2019→oggi), paginato
uv run pathos ingest comtrade                       # flussi semiconduttori HS 8541/8542/8486 (3 mesi recenti)
uv run pathos ingest comtrade --start 201801        # backfill mensile da YYYYMM → oggi (backoff su 429)
uv run pathos ingest comtrade --start 202401 --end 202403   # slice di prova
uv run pathos ingest comtrade --start 201801 --delay 12     # alza il delay se persiste 429
uv run pathos ingest usgs                            # terremoti USGS (riprende da ultimo)
uv run pathos ingest usgs --start 2018-01-01         # backfill storico terremoti
uv run pathos ingest firms                           # incendi FIRMS (riprende da ultimo per area; richiede FIRMS_MAP_KEY)
uv run pathos ingest firms --start 2018-01-01        # backfill storico (auto source VIIRS_NOAA20_SP, finestre 5gg)

# Pipeline semantica (Fase 2)
uv run pathos embed                                 # embed + dedup semantica + clustering → eventi
uv run pathos extract                                # NER + geocoding + Wikidata entity linking
uv run pathos graph                                 # grafo co-occorrenze + divergenza narrativa
uv run pathos embed --skip-dedup                    # solo embedding

# Ciclo notturno
uv run pathos cycle
uv run pathos cycle --dry-run
uv run pathos cycle --from-phase embed
uv run pathos cycle --from-phase graph              # riprendi da GRAPH

# Export
uv run pathos export parquet                       # backup Parquet partizionato → data/parquet/

# Test
uv run pytest                    # 181 test
```

## Bootstrap storico vs aggiornamento incrementale

Ogni fonte ha **due modalità**: un bootstrap storico una-tantum (post-2018 dove
la fonte lo consente) e un aggiornamento incrementale che riprende **dall'ultimo
rilevamento**. Tutti i comandi sono riprendibili (Ctrl+C → rilancia).

### Bootstrap campione rapido

Per testare la pipeline senza scaricare anni di dati:

```bash
# 1. Schema + fonti
uv run pathos db init && uv run pathos sources seed

# 2. Ingestione (lancia tu dal terminale — serve rete/chiavi)
uv run pathos ingest gdelt --days 2
uv run pathos ingest rss --max-age-days 3
uv run pathos ingest portwatch
uv run pathos ingest comtrade --start 202401 --end 202403
uv run pathos ingest usgs --start 2026-01-01
uv run pathos ingest firms --start 2026-01-01     # richiede FIRMS_MAP_KEY in .env
uv run pathos ingest ioda --days 35               # blackout internet BGP (24 paesi + baseline)

# 3. Pipeline semantica
sqlite3 data/db/pathosphere.db "UPDATE raw_documents SET embedded=1 WHERE origin='gdelt';"
uv run pathos embed          # embed RSS+Comtrade + dedup KNN + clustering → eventi
uv run pathos extract        # NER + geocoding Nominatim + Wikidata QID
uv run pathos graph          # grafo co-occorrenze + divergenza narrativa

# Verifica
uv run pathos db info
```

> Nota spaCy (una-tantum): `uv run python -m spacy download xx_ent_wiki_sm`

### Bootstrap storico completo

Una-tantum, dopo `db init` + `sources seed`:

```bash
uv run pathos ingest gdelt-history --start 2018-01-01    # GDELT (lungo: ~2 notti a sample 1h)
uv run pathos ingest rss --max-age-days 7                # RSS: solo recente (nessuno storico via feed)
uv run pathos ingest portwatch --full                    # transiti chokepoint ~2019→oggi
uv run pathos ingest comtrade --start 201801             # flussi mensili da gen-2018
uv run pathos ingest usgs --start 2018-01-01             # terremoti dal 2018
uv run pathos ingest firms --start 2018-01-01            # incendi dal 2018 (auto VIIRS_NOAA20_SP, finestre 5gg)

# Pipeline semantica (dopo ingest)
sqlite3 data/db/pathosphere.db "UPDATE raw_documents SET embedded=1 WHERE origin='gdelt';"
uv run pathos embed          # embed RSS+Comtrade + dedup + cluster → eventi
uv run pathos extract        # NER + geocoding + Wikidata
uv run pathos graph          # grafo co-occorrenze + divergenza narrativa
```

### Aggiornamento incrementale

Ciclo regolare — riprende da solo dall'ultimo dato:

```bash
uv run pathos ingest gdelt --days 1     # salta i file già nel log (resume)
uv run pathos ingest rss                # ultimi 2 giorni
uv run pathos ingest portwatch          # ultimi 90gg, upsert idempotente (overlap)
uv run pathos ingest comtrade           # 3 mesi recenti (~2 mesi di lag a monte)
uv run pathos ingest usgs               # riprende da max(first_seen) USGS
uv run pathos ingest firms              # riprende da max(date) per ogni area

uv run pathos embed                     # nuovi vettori + dedup + cluster
uv run pathos extract                   # NER sui nuovi doc
uv run pathos graph                     # aggiorna grafo + divergenza
```

Dove "riprende da ultimo": USGS legge l'ultimo terremoto salvato, FIRMS l'ultima
data per area in `fire_metrics`, GDELT salta i file già scaricati
(`gdelt_file_log`). PortWatch ri-scarica gli ultimi 90 giorni con upsert
idempotente (sovrapposizione sicura). **RSS** è l'unica fonte solo-incrementale:
i feed non espongono storico.

## Struttura repo

```
pathosphere/
├── pathosphere/        pacchetto Python
│   ├── cli.py          entry point `pathos`
│   ├── config.py       settings da .env
│   ├── db/schema.py    DDL SQLite + sqlite-vec
│   ├── cycle/          orchestratore ciclo notturno
│   ├── ingest/         ingestori (GDELT, RSS, PortWatch, Comtrade, USGS, FIRMS, IODA ✅)
│   ├── export/         export Parquet partizionato ✅
│   └── semantic/       pipeline semantica (embed ✅; dedup ✅; cluster ✅; extract ✅; graph ✅)
├── tests/              181 test, ~10s
├── docs/
│   ├── wiki.md         documentazione completa
│   ├── architecture.md architettura dettagliata
│   └── schema.md       ER diagram + schema DB
└── useful_queries.sql  20 query annotate
```

## Stato avanzamento

- [x] **Fase 0** — Config, SQLite+sqlite-vec, CLI, logging, ciclo orchestrator
- [x] **Fase 1** — GDELT 2.0 (incrementale + bootstrap storico)
- [x] **Fase 1** — RSS multi-blocco (52 fonti (48 attive), 7 blocchi geopolitici, 6 lingue)
- [x] **Fase 1** — PortWatch (anomalie chokepoint), Comtrade (semiconduttori), USGS, FIRMS
- [x] **Fase 1** — IODA blackout internet (BGP, 24 paesi) + export Parquet partizionato
- [x] **Fase 2** — Embedding multilingual-e5-small + dedup semantica KNN
- [x] **Fase 2** — Clustering articoli → eventi (union-find)
- [x] **Fase 2** — NER, geocoding (Nominatim), Wikidata entity linking
- [x] **Fase 2** — Grafo co-occorrenze (`entity_links`) + divergenza narrativa per blocco (`narrative_divergences`)
- [ ] **Fase 3** — Brief, tesi, paper trading, calibrazione Tetlock
- [ ] **Fase 4** — Dashboard Streamlit

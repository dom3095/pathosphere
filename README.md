# Pathosphere

Sistema personale di intelligence OSINT su eventi critici globali — conflitti, epidemie, infrastrutture, rotte commerciali — con paper trading virtuale come metrica di valutazione del modello.

**Mono-utente · Dati aperti · Budget quasi zero · Human-in-the-loop.**

→ **[Wiki completa](docs/wiki.md)** — architettura, ingestori, CLI reference, testing, roadmap.

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
uv run pathos sources seed      # 49 fonti, 7 blocchi geopolitici
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

# Ingestione RSS (49 fonti, 7 blocchi geopolitici)
uv run pathos ingest rss                            # tutte le fonti attive, ultimi 2 giorni
uv run pathos ingest rss --max-age-days 7           # ultimi 7 giorni

# Segnali fisici e flussi (Fase 1)
uv run pathos ingest portwatch                      # transiti chokepoint IMF → anomalie
uv run pathos ingest comtrade                       # flussi semiconduttori HS 8541/8542/8486
uv run pathos ingest usgs                            # terremoti significativi USGS
uv run pathos ingest firms                           # incendi NASA FIRMS (richiede FIRMS_MAP_KEY)

# Pipeline semantica (Fase 2)
uv run pathos embed                                 # embed + dedup semantica + clustering → eventi
uv run pathos extract                                # NER + geocoding + Wikidata entity linking
uv run pathos embed --skip-dedup                    # solo embedding

# Ciclo notturno
uv run pathos cycle
uv run pathos cycle --dry-run
uv run pathos cycle --from-phase embed

# Test
uv run pytest                    # 137 test
```

## Struttura repo

```
pathosphere/
├── pathosphere/        pacchetto Python
│   ├── cli.py          entry point `pathos`
│   ├── config.py       settings da .env
│   ├── db/schema.py    DDL SQLite + sqlite-vec
│   ├── cycle/          orchestratore ciclo notturno
│   │   ├── ingest/         ingestori (GDELT, RSS, PortWatch, Comtrade, USGS, FIRMS ✅)
│   └── semantic/       pipeline semantica (embed ✅; dedup ✅; cluster ✅; extract NER+geo+Wikidata ✅)
├── tests/              137 test, ~1s
├── docs/
│   ├── wiki.md         documentazione completa
│   ├── architecture.md architettura dettagliata
│   └── schema.md       ER diagram + schema DB
└── useful_queries.sql  20 query annotate
```

## Stato avanzamento

- [x] **Fase 0** — Config, SQLite+sqlite-vec, CLI, logging, ciclo orchestrator
- [x] **Fase 1** — GDELT 2.0 (incrementale + bootstrap storico)
- [x] **Fase 1** — RSS multi-blocco (49 fonti, 7 blocchi geopolitici, 6 lingue)
- [x] **Fase 1** — PortWatch (anomalie chokepoint), Comtrade (semiconduttori), USGS, FIRMS
- [x] **Fase 2** — Embedding multilingual-e5-small + dedup semantica KNN
- [x] **Fase 2** — Clustering articoli → eventi (union-find)
- [x] **Fase 2** — NER, geocoding (Nominatim), Wikidata entity linking
- [ ] **Fase 3** — Brief, tesi, paper trading, calibrazione Tetlock
- [ ] **Fase 4** — Dashboard Streamlit

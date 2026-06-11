# Pathosphere

Sistema personale di intelligence OSINT su eventi critici globali — conflitti, epidemie, infrastrutture, rotte commerciali — con paper trading virtuale come metrica di valutazione del modello.

**Mono-utente. Dati aperti. Budget quasi zero. Human-in-the-loop.**

---

## Requisiti

- macOS (M1/M2), Python 3.12+
- [uv](https://astral.sh/uv) — gestore pacchetti e venv
- [Ollama](https://ollama.com) con modello `qwen3:4b` (per le fasi semantiche e brief)

```bash
# Installa uv (se non presente)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Installa Ollama + modello
brew install ollama
ollama pull qwen3:4b
```

---

## Setup iniziale

```bash
# 1. Installa dipendenze
uv sync

# 2. Crea .env dalla template
cp .env.example .env

# 3. Inizializza il database SQLite
uv run pathos db init

# 4. Popola il catalogo fonti (15 fonti predefinite, 7 blocchi geopolitici)
uv run pathos sources seed
```

---

## Comandi principali

### Database

```bash
uv run pathos db init          # Crea/aggiorna schema SQLite + sqlite-vec
uv run pathos db info          # Mostra conteggi per tabella
```

### Fonti

```bash
uv run pathos sources list     # Lista fonti configurate
uv run pathos sources seed     # Inserisce le 15 fonti predefinite
```

### Ingestione GDELT

GDELT pubblica file CSV ogni 15 minuti con eventi estratti da migliaia di testate mondiali.

```bash
# Ciclo incrementale (ultimi N giorni, salta file già scaricati)
uv run pathos ingest gdelt                          # ultimo giorno
uv run pathos ingest gdelt --days 3                 # ultimi 3 giorni
uv run pathos ingest gdelt --quad all               # tutti i QuadClass (non solo conflitti)
uv run pathos ingest gdelt --min-mentions 20        # filtro più stretto
uv run pathos ingest gdelt --countries CN,TW,US     # filtra per paese (ISO-2)
uv run pathos ingest gdelt --max-files 5            # test: scarica solo 5 file

# Bootstrap storico (operazione una-tantum, ripartibile con Ctrl+C)
uv run pathos ingest gdelt-history --start 2021-01-01          # 5 anni, ~2 notti
uv run pathos ingest gdelt-history --start 2024-01-01          # 1.5 anni, ~0.8 notti
uv run pathos ingest gdelt-history --start 2026-06-04 --end 2026-06-11  # test 1 settimana
uv run pathos ingest gdelt-history --start 2021-01-01 --sample-hours 2  # più veloce, meno file
```

**Parametri gdelt-history:**

| Flag | Default | Note |
|---|---|---|
| `--sample-hours` | `1` | File ogni N ore. `0` = tutti (ogni 15min, ~7 notti per 5 anni) |
| `--min-mentions` | `10` | NumMentions minimo per evento |
| `--quad` | `conflict` | `conflict` = QuadClass 3-4, `all` = 1-4 |
| `--countries` | tutti | ISO-2 separati da virgola |

### Ciclo notturno

```bash
uv run pathos cycle             # Esegui ciclo completo (ingest → brief)
uv run pathos cycle --dry-run   # Simula senza eseguire nulla
uv run pathos cycle --from-phase embed  # Riprendi da una fase specifica
```

### Configurazione

```bash
uv run pathos config            # Mostra configurazione attiva
```

---

## Architettura

```
pathosphere/
├── pathosphere/
│   ├── cli.py              # Entry point CLI (comando: pathos)
│   ├── config.py           # Settings da .env via pydantic-settings
│   ├── logging_setup.py    # Loguru, rotazione giornaliera
│   ├── db/
│   │   └── schema.py       # DDL SQLite + init sqlite-vec
│   ├── cycle/
│   │   └── orchestrator.py # Ciclo notturno sequenziale e ripartibile
│   ├── ingest/
│   │   └── gdelt.py        # Downloader GDELT 2.0 Events
│   ├── semantic/           # Fase 2: NER, embedding, clustering (TODO)
│   └── agent/              # Fase 3: brief, tesi, paper trading (TODO)
├── data/
│   ├── db/                 # SQLite (gitignored)
│   ├── parquet/            # Storico raw in Parquet (gitignored)
│   └── logs/               # Log giornalieri (gitignored)
├── .env.example            # Template variabili d'ambiente
└── claude.md               # Documento di progetto completo
```

### Schema DB (tabelle principali)

| Tabella | Contenuto |
|---|---|
| `sources` | Catalogo fonti (paese, blocco geopolitico, controllo statale) |
| `raw_documents` | Documenti grezzi (URL, titolo, hash per dedup) |
| `events` | Eventi aggregati da cluster di articoli |
| `event_documents` | Join eventi ↔ documenti |
| `narrative_divergences` | Divergenza narrativa per blocco geopolitico |
| `entities` | Entità estratte (paesi, aziende, commodity) |
| `entity_links` | Grafo relazioni tra entità |
| `theses` | Tesi generate dall'agent (con catena causale e strumento) |
| `trades` | Paper trading (prezzo registrato al momento della decisione) |
| `portfolios` | Portafogli virtuali: agent, random, benchmark |
| `predictions` | Anticipazioni non finanziarie con calibrazione Tetlock |
| `gdelt_file_log` | Tracking file GDELT scaricati (per dedup e ripresa) |
| `vec_documents` | Tabella virtuale sqlite-vec per nearest-neighbour su embedding |

---

## Stato avanzamento

- [x] **Fase 0** — Fondamenta: uv, SQLite+sqlite-vec, CLI, logging
- [x] **Fase 1** — Ingestione GDELT 2.0 (ciclo incrementale + bootstrap storico)
- [ ] **Fase 1** — RSS multi-blocco, PortWatch, Comtrade, USGS/FIRMS
- [ ] **Fase 2** — NER + geocoding + Wikidata, embeddings e5-small, clustering → eventi
- [ ] **Fase 3** — Brief mattutino, generatore tesi, paper trading, calibrazione
- [ ] **Fase 4** — Dashboard Streamlit minimale

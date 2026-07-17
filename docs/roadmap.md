# Pathosphere — Roadmap

Stato aggiornato: 2026-07-16.

---

## Panoramica fasi

| Fase | Nome | Stato |
|---|---|---|
| **0** | Fondamenta | ✅ Completa |
| **1** | Ingestione | ✅ Completa |
| **2** | Semantica | ✅ Completa |
| **3** | Agent e valutazione | ✅ Completa (3a–3g) |
| **4** | Interfaccia | ⬜ Futuro |

---

## Fase 0 — Fondamenta ✅

| Task | File | Stato |
|---|---|---|
| Config da `.env` (pydantic-settings) | `pathosphere/config.py` | ✅ |
| Logging (loguru, rotazione giornaliera) | `pathosphere/logging_setup.py` | ✅ |
| Schema SQLite + sqlite-vec + migrate_db | `pathosphere/db/schema.py` | ✅ |
| CLI `pathos` (Click) | `pathosphere/cli.py` | ✅ |
| Ciclo notturno orchestrator (6 fasi) | `pathosphere/cycle/orchestrator.py` | ✅ |
| Health check operativo (`pathos doctor`, wiki §8c) | `pathosphere/doctor.py` | ✅ 2026-07-17 |

---

## Fase 1 — Ingestione ✅

| Task | File | Tabelle | Stato |
|---|---|---|---|
| GDELT 2.0 (incrementale + bootstrap storico) | `ingest/gdelt.py` | `raw_documents`, `gdelt_events`, `events` | ✅ |
| GDELT anomalie numeriche (Goldstein, CP-016) | `ingest/gdelt_anomaly.py` | `gdelt_events` → `events` | ✅ |
| RSS multi-blocco (52 fonti, 7 blocchi) | `ingest/rss.py` | `raw_documents` | ✅ |
| IMF PortWatch (chokepoint, anomalie z-score) | `ingest/portwatch.py` | `chokepoint_metrics`, `events` | ✅ |
| UN Comtrade semiconduttori (HS 8541/8542/8486) | `ingest/comtrade.py` | `raw_documents`, `comtrade_flows` | ✅ |
| USGS terremoti (significativi, incrementale) | `ingest/physical.py` | `events` | ✅ |
| NASA FIRMS incendi (NRT + archivio, anomalie) | `ingest/physical.py` | `fire_metrics`, `events` | ✅ |
| IODA blackout internet (BGP, 24 paesi) | `ingest/ioda.py` | `internet_metrics`, `events` | ✅ |
| Storicizzazione Parquet (export deduplicato) | `export/parquet.py` | — (file system) | ✅ |

**Detector anomalie condiviso** (`ingest/anomaly.py`): z-score vs baseline trailing, `direction=drop/surge/both`, no lookahead.

---

## Fase 2 — Semantica ✅

| Task | File | Tabelle | Stato |
|---|---|---|---|
| Embedding multilingual-e5-small (384d, batch, esclude origin gdelt/comtrade CP-016) | `semantic/embedder.py` | `vec_documents` | ✅ |
| Dedup semantica KNN (cosine ≥ 0.92, 72h) | `semantic/dedup.py` | `raw_documents.is_duplicate` | ✅ |
| Clustering → eventi (union-find, soglia 0.85) | `semantic/cluster.py` | `events`, `event_documents` | ✅ |
| NER multilingua (spaCy `xx_ent_wiki_sm`) | `semantic/extract.py` | `entities`, `document_entities` | ✅ |
| Geolocalizzazione eventi RSS (euristica + fallback Qwen, CP-022) | `semantic/extract.py` | `events.location_name`, `events.geoloc_checked` | ✅ |
| Geocoding (Nominatim, cache) | `semantic/extract.py` | `events.lat/lon`, `geocode_cache` | ✅ |
| Wikidata entity linking (QID, canonical_name) | `semantic/extract.py` | `entities.wikidata_qid` | ✅ |
| Grafo co-occorrenze | `semantic/graph.py` | `entity_links` | ✅ |
| Divergenza narrativa per blocco geopolitico | `semantic/graph.py` | `narrative_divergences` | ✅ |

---

## Fase 3 — Agent e valutazione ✅

### 3a. Astrazione LLM `pathosphere/llm/client.py` ✅

- API OpenAI-compatible — cambiare backend = una riga di config
- **Claude**: Claude Agent SDK (`claude -p`), non API dirette (credito abbonamento)
- **Qwen locale**: Ollama `http://localhost:11434/v1`, modello `qwen3:4b`
- Signature: `async def complete(messages, *, model=None, json_mode=False) -> str`
- Config: `settings.reasoning_model = "claude" | "qwen-local"`

### 3b. Brief mattutino `pathosphere/agent/brief.py` ✅

- Input: divergenze narrative (`divergence_score > 0.5`), entità hub, anomalie recenti (portwatch/firms/usgs/ioda)
- Output: testo strutturato (eventi + divergenze + impatti ipotizzati)
- CLI: `pathos brief [--date] [--lookback-days] [--model] [--dry-run]`
- Salvataggio: `data/briefs/YYYY-MM-DD.md` + tabella `briefs`

### 3c. Generatore tesi `pathosphere/agent/thesis.py` + debate pipeline ✅

- **Fast path** (`pathos thesis generate`): 1 Claude call → N tesi primarie + 1-2 alternative ciascuna
- **Debate pipeline** (`pathos thesis debate`): 6 personas × 3 step Qwen (research → divergence → critique) + 1 Claude synthesis
- `causal_chain` JSON: `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}`
- `price_snapshot` al momento della generazione (no-lookahead)
- `watchlist_items` auto-popolati per ogni tesi
- **Enrichment fondamentali** (`pathosphere/market/fundamentals.py`): ratio yfinance +
  Altman Z (skip finanziari) + Piotroski F per ogni ticker proposto →
  `theses.fundamentals_json` + 1 call LLM batch di review (annotazione, non decisione).
  Degrada senza bloccare; `--no-fundamentals` per saltare. CLI: `pathos fundamentals <ticker>`.
  SEC EDGAR rimandato a v2.
- **Enrichment technicals** (`pathosphere/market/technicals.py`, 2026-07-15): price-action
  yfinance 1y daily per ogni ticker proposto (momentum 1w-1y, volatilità 21gg, RSI-14 Wilder,
  distanze SMA 20/50/200, range 52w, max drawdown, trend volume) → `theses.technicals_json`.
  Copre ETF/future/FX dove i fondamentali degradano a minimal. Review LLM unificata
  ("market review": fundamentals+technicals nello stesso prompt batch — zero call extra);
  assessment in fundamentals_json, fallback technicals_json. Descrittivo, mai segnale.
  `--no-technicals` per saltare. CLI: `pathos technicals <ticker>`. Wiki §8.8.
  Post code-review (stessa sessione): stato enrichment condiviso `_MarketEnrichment`
  (thesis+debate, no closures duplicate), price_snapshot riusa il last close dei
  technicals (1 sola history call/ticker), RSI piatto → None, label "52w"/"1y" onesta
  sotto 240 barre.
- **Auto-open a soglia di confidence** (2026-07-14): tesi con `confidence ≥ 0.6` (configurabile,
  `settings.auto_open_confidence_threshold`) auto-approvate e tradate subito dopo la review
  fondamentali — soldi virtuali, revisione umana dopo invece di gate prima. Sotto soglia: flusso
  manuale invariato. `--no-auto-open` / `--auto-open-threshold N` per override.

### 3d. Flusso approvazione CLI ✅

```
pathos thesis list [--status pending|approved|rejected|closed|all]
pathos thesis show <id>              # trigger, causal chain, invalidation, persona notes, debate context, watchlist
pathos thesis approve <id>           # status → approved, valida ticker yfinance (warn, non blocca)
pathos thesis reject <id> --reason "..."  # status → rejected, rejection_reason loggato
```

- `approved_at` / `rejected_at` in `theses`
- `rejection_reason` persistito → dataset per capire pattern di rifiuto
- Ticker validation: `yfinance.fast_info.last_price` — warn se assente, non blocca mai

### 3e. Paper trading EOD ✅

**`pathosphere/market/trading.py`**

- `init_portfolios(conn)` — crea agent / random / benchmark ($100k); benchmark apre trade SPY. Idempotente.
- `open_agent_trade(conn, thesis_id)` — apre agent + random (stesso qty/dir, ticker casuale riproducibile). `price_open = yfinance live` (no-lookahead).
- `close_trade(conn, trade_id)` — pnl = gross − costi entrambi i lati
- `get_portfolio_status(conn)` — P&L realizzato + non realizzato (fetch prezzi live), return %
- Costanti: `INITIAL_CASH=100k`, `ALLOCATION_PCT=10%`, `TC=0.1%`, `SLIPPAGE=0.05%`
- Pool random: `[SPY, QQQ, GLD, USO, TLT, EEM, IWM, XLE, XLF, DIA]`

CLI: `pathos portfolio init/status` · `pathos trade open/close/list [--closed]`

### 3f. Predizioni non finanziarie (v2) ✅

**`pathosphere/agent/predictions.py`** — Due binari, time-adjusted scoring, storia revisioni.

**Modello:**
- `macro_area='world'` (geopolitical|political|social) → richiede origin_scope + impact_scope + ≥1 domain
- `macro_area='economic'` (economic) → auto-creata su `pathos thesis approve` con probability=thesis.confidence, horizon=today + thesis.horizon_days
- Nuove colonne: outcome_eventual (timing-independent), outcome_on_time (horizon-sensitive), time_horizon_class (breve|medio|lungo), time_adjusted_score (primaria operativa)
- Nuove tabelle: `prediction_domains` (10-tassonomia + is_primary), `prediction_revisions` (storia prob + rationale, Superforecaster pattern)
- Integrazione tesi: theses.prediction_id → economic prediction; trade.prediction_id ← link_thesis_prediction_to_trade()

**Funzioni:**
- `add_prediction()` — valida macro_area/prediction_type coerenza, domains, scopes world-only
- `revise_prediction()` — update prob, log revision con timestamp + rationale
- `resolve_prediction()` — compute brier_score + time_adjusted_score con alpha config
- `list_predictions()` — filter by open/resolved/macro_area/prediction_type/domain
- `get_calibration()` — dual-metric (time_adjusted primary, brier secondary) + 5-bucket + breakdown by_macro_area/by_prediction_type

**CLI v2:**
```
pathos predict add "Desc" --macro-area world --prediction-type geopolitical \
  --probability 0.65 --horizon 2026-07-10 \
  --domain conflitto_armato --domain commercio --primary-domain conflitto_armato \
  --origin-scope regionale --impact-scope globale

pathos predict revise <id> --probability 0.7 --rationale "new intel"
pathos predict list [--open] [--resolved] [--macro-area X] [--prediction-type X] [--domain X]
pathos predict resolve <id> --outcome-eventual true|false --resolved-date 2026-07-10
pathos predict calibration
```

**Migration:** v1 rows backfilled as macro_area='world', prediction_type='geopolitical', outcome_on_time=outcome, outcome_eventual=outcome. Idempotent via `uv run pathos db init`.

- 39 test in `tests/test_predictions.py` (test_revise_prediction, test_get_calibration, etc.)

### 3g. Previsione scenari di conflitto ✅ (2026-07-16, branch `feat/conflict-forecasting`)

**`pathosphere/agent/scenarios.py`** — pipeline analitica strutturata (wiki §8.9):

- **Triage hotspot deterministico** (no LLM): per paese FIPS da `gdelt_events`, finestra 14gg vs
  baseline 90gg — z material conflict, shift quota quad4, deterioramento Goldstein, surge volume.
  Solo selezione dell'attenzione, mai predizione (principio core-agentico-non-quant).
- **Dossier di evidenze congelato** (`dossier_json`, id E1..En citabili): metriche + anomalie GDELT +
  cluster RSS + divergenze narrative + IODA + prior UCDP.
- **Generazione ACH** (1 call Claude/hotspot, default 2/run): 3-4 scenari MECE a 90gg, rating per
  evidenza, probabilità che sommano a 1, indicatori osservabili → `watchlist_items.scenario_id`,
  invalidazione, key assumptions.
- **Scoring via predictions v2**: ogni scenario = 1 prediction world/geopolitical (domini
  conflitto_armato+tensione_militare) → Brier/time-adjusted/calibrazione gratis.
- **Review superforecaster** (1 call/set): indicatori triggerati + metriche fresche → revisioni
  con rationale (`revise_prediction`). Set oltre orizzonte: flag OVERDUE, MAI revisionati.
- **Risoluzione umana**: `pathos scenario resolve <id> --winner X` → winner true, fratelli false.
- Tabelle: `scenario_sets`, `scenarios`, `watchlist_items.scenario_id`. CLI: `pathos scenario
  hotspots|generate|list|show|review|resolve`. Wiring: sezione "ACTIVE CONFLICT SCENARIOS" nel brief,
  pagina "Scenari" in dashboard. NON nel ciclo notturno (task Claude on-demand).
- 19 test (`tests/test_scenarios.py`, LLM mockato). Metodologia: ACLED CAST / VIEWS (triage),
  Heuer ACH + Indicators & Warnings (ragionamento), Tetlock (scoring).

---

## Fase 4 — Interfaccia ✅

| Task | Note |
|---|---|
| Dashboard Streamlit minimale | Mappa eventi (folium), confronto narrazioni, grafo entità, tesi aperte (approva/rifiuta), portafogli, predizioni (calibrazione Tetlock), storico brief |
| `pathos serve [--host] [--port]` | Avvia dashboard su `localhost:8501` (shell-out a `streamlit run`) |

Implementata in `pathosphere/dashboard/` (`app.py` + `views/*.py`, una funzione
`render(conn)` per pagina, navigazione via radio in sidebar — non multipage
nativo Streamlit). Connessione SQLite aperta/chiusa per rerun (non
cache_resource: i connection object sqlite3 non sono thread-safe, e
cache_resource è condiviso tra sessioni/thread). Pagine Tesi/Portafogli/
Predizioni/Brief mostrano stato vuoto finché il ciclo agent (Fase 3) non ha
prodotto dati reali (0 righe al momento della prima verifica). Mappa/
Narrazioni/Grafo entità già popolate con dati reali (8241 eventi, 9142
entità, 749 divergenze). Verificata con `streamlit.testing.v1.AppTest` su
tutte le 8 pagine contro il DB reale (nessuna eccezione).

---

## Fase 5 — Situazioni a lungo termine (pianificata, non iniziata)

**Contesto (2026-07-14)**: il brief oggi lavora su due orizzonti — spot news (7gg, `_query_recent_events`
CP-025) ed eventi fisici/divergenze. Nessuno cattura archi pluriennali (guerra Ucraina 2022-ongoing,
Crimea 2014, ecc.) — una finestra a giorni fissi, per quanto allargata, tratta un conflitto lungo come
la sua ultima settimana ("l'unghia del leone").

**Design proposto** (discusso con l'utente, non ancora costruito):
- Nuova tabella `situations` (label, `started_at`, `ended_at` NULL=ongoing, summary aggiornato
  incrementalmente) + `situation_events` (link a eventi/`story_id`)
- **Nessun backfill storico automatico** — il registro parte vuoto da quando il modulo viene
  costruito, non pretende di sapere cos'è "la guerra di Crimea 2014" senza che qualcuno/qualcosa
  gliel'abbia detto esplicitamente
- Confini **semantici, non a giorni fissi**: al momento del brief, Claude giudica se una storia
  recente appartiene a una situazione già tracciata (gli si passa label+summary brevi) o ne apre una
  nuova — proposta LLM validabile/correggibile a mano, non merge cieco automatico (stessa cautela
  imparata da CP-011/018/019/020/021: aggregazione automatica su similarità/entità condivise ha
  già causato chain-collapse tre volte in questo progetto)
- Brief risultante: sezione "SITUAZIONI IN CORSO" con arco temporale reale + ultimo sviluppo

**Perché rimandato**: nuovo modulo (schema + logica + comando CLI), non un tweak — merita sessione
dedicata con validazione preventiva (stesso pattern notebook-prima-del-codice usato per CP-022), non
va infilato di corsa. Vedi anche la feature auto-open (Fase 3, sotto) per il contesto della
discussione che ha originato questa idea.

**Dipendenza — parte RISOLTA 2026-07-14** (branch `feat/historical-events-backfill`): la fase
presuppone di sapere QUANDO sono iniziate le situazioni note (Ucraina 2022-02-24, Crimea 2014...).
Backfill eventi storici ora disponibile — `pathos ingest ucdp|who-don|reliefweb|econ-crises` (conflitti
1989→, epidemie 1996→, disastri 1981→, crisi economiche) — vedi wiki.md §5.5. Resta aperta solo la
parte 2 di **CP-027**: serie storiche prezzi/economiche (correlazione eventi↔borsa), mai persistite.

---

## MVP verticale

**Filiera semiconduttori** — TSMC / ASML / SMIC, Taiwan Strait.

- Pochi attori con relazioni ben definite → grafo entità denso
- Geopolitica intensa → divergenza narrativa alta
- Chokepoint chiari (Taiwan Strait, Suez per spedizioni Asia-Europa)
- Segnali osservabili già nel DB: Comtrade HS 8541/8542, PortWatch, IODA CN/TW

**Indicatori watchlist da monitorare:**
- Variazione transiti Taiwan Strait (PortWatch)
- Menzioni "TSMC" / "export controls" in divergenza western vs china > 0.6
- Anomalie fire_metrics area Strait of Malacca
- IODA: drop segnale BGP CN o TW

---

## Non inclusi (scope out)

| Fonte | Motivo |
|---|---|
| ACLED | Frequenza settimanale, parzialmente coperto da GDELT — UCDP invece incluso come backfill storico one-off (§5.5) |
| ProMED | Nessuna crisi sanitaria in corso nel MVP — WHO DON invece incluso come backfill storico (§5.5) |
| Cloudflare Radar | IODA già copre il segnale di blackout internet |
| FRED (macro) | Utile per contesto, non critico per MVP semiconduttori |
| OpenSky (traffico aereo) | Fuori scope per ora |

---

*Vedi anche: [wiki.md](wiki.md) · [schema.md](schema.md) · [architecture.md](architecture.md)*

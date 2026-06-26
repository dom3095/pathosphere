# Pathosphere — Roadmap

Stato aggiornato: 2026-06-26.

---

## Panoramica fasi

| Fase | Nome | Stato |
|---|---|---|
| **0** | Fondamenta | ✅ Completa |
| **1** | Ingestione | ✅ Completa |
| **2** | Semantica | ✅ Completa |
| **3** | Agent e valutazione | 🔄 In corso (3d/6 ✅) |
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

---

## Fase 1 — Ingestione ✅

| Task | File | Tabelle | Stato |
|---|---|---|---|
| GDELT 2.0 (incrementale + bootstrap storico) | `ingest/gdelt.py` | `raw_documents`, `gdelt_events`, `events` | ✅ |
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
| Embedding multilingual-e5-small (384d, batch) | `semantic/embedder.py` | `vec_documents` | ✅ |
| Dedup semantica KNN (cosine ≥ 0.92, 72h) | `semantic/dedup.py` | `raw_documents.is_duplicate` | ✅ |
| Clustering → eventi (union-find, soglia 0.85) | `semantic/cluster.py` | `events`, `event_documents` | ✅ |
| NER multilingua (spaCy `xx_ent_wiki_sm`) | `semantic/extract.py` | `entities`, `document_entities` | ✅ |
| Geocoding (Nominatim, cache) | `semantic/extract.py` | `events.lat/lon`, `geocode_cache` | ✅ |
| Wikidata entity linking (QID, canonical_name) | `semantic/extract.py` | `entities.wikidata_qid` | ✅ |
| Grafo co-occorrenze | `semantic/graph.py` | `entity_links` | ✅ |
| Divergenza narrativa per blocco geopolitico | `semantic/graph.py` | `narrative_divergences` | ✅ |

---

## Fase 3 — Agent e valutazione 🔄

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

### 3e. Paper trading EOD

- **yfinance**: non ancora agganciato — prerequisito critico per questo step
- `price_open` = prezzo al momento dell'APPROVAZIONE (no-lookahead)
- Costi di transazione + slippage simulati
- Tabelle `trades`, `portfolios` già in schema
- Tre portafogli: agent / random (stesso N trade, ticker casuali) / buy&hold SPY
- CLI: `pathos portfolio status`, `pathos trade close <id>`

### 3f. Predizioni non finanziarie

- Tabella `predictions` già in schema
- Workflow: `pathos predict add "Escalation in X entro 2 settimane" --probability 0.6 --horizon 2026-07-05`
- Risoluzione: `pathos predict resolve <id> --outcome true|false`
- Metrica: Brier score per calibrazione Tetlock

---

## Fase 4 — Interfaccia ⬜

| Task | Note |
|---|---|
| Dashboard Streamlit minimale | Mappa eventi (folium), confronto narrazioni, portafogli, tesi aperte, storico brief |
| `pathos serve` | Avvia dashboard su `localhost:8501` |

Dipende da Fase 3 completata (senza dati reali di trading/tesi, dashboard vuota).

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
| ACLED, UCDP | Frequenza settimanale, parzialmente coperto da GDELT |
| WHO DON, ProMED | Nessuna crisi sanitaria in corso nel MVP |
| Cloudflare Radar | IODA già copre il segnale di blackout internet |
| FRED (macro) | Utile per contesto, non critico per MVP semiconduttori |
| OpenSky (traffico aereo) | Fuori scope per ora |

---

*Vedi anche: [wiki.md](wiki.md) · [schema.md](schema.md) · [architecture.md](architecture.md)*

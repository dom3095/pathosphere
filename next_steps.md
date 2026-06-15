# Next steps

Aggiornato 2026-06-16.

## 🤝 Handoff — leggere per primo

- **Branch**: `feat/numeric-detail-tables-rss-tor`, commit pushati fino alla sessione precedente. Il lavoro di questa sessione (grafo + divergenza + doc) è **in locale non committato** — l'utente dice quando committare. `git status` per il diff.
- **Test**: 160 verdi (`uv run pytest`, ~10s). Eseguirli dopo ogni modifica.
- **DB attuale** `data/db/pathosphere.db` (campione fresco 2026-06-15): 6,967 doc (gdelt 5742, rss 1081, comtrade 144), 4,095 eventi, 2,520 chokepoint_metrics, 1,707 fire_metrics, 1,225 vec_documents. `entity_links` e `narrative_divergences` **vuote** — sono le prime due tabelle da popolare con `pathos graph` (lo lancia l'utente).
- **Drop+rebuild** eseguito 2026-06-15 — campione pulito disponibile.
- **Sicurezza**: mai leggere `.env`/secrets (CLAUDE.md + deny in `.claude/settings.json`). Per FIRMS controllare solo `bool(settings.firms_map_key)`.
- **Gli ingest li lancia l'utente** dal terminale (rete + chiavi), non l'agent.

### Comandi per ricostruire un campione (li lancia l'utente)

```bash
uv run pathos db init && uv run pathos sources seed
uv run pathos ingest gdelt --days 2
uv run pathos ingest rss --max-age-days 3
uv run pathos ingest portwatch
uv run pathos ingest comtrade --start 202401 --end 202403
uv run pathos ingest usgs --start 2026-01-01
uv run pathos ingest firms --start 2026-01-01

sqlite3 data/db/pathosphere.db "UPDATE raw_documents SET embedded=1 WHERE origin='gdelt';"
uv run pathos embed
uv run pathos extract
uv run pathos graph
```

---

## ✅ Fatto in questa sessione (2026-06-16)

- **`semantic/graph.py`** — due funzioni nuove:
  - `build_entity_links(conn, min_cooccurrences=1)` → `entity_links` (co-occorrenze, `relation_type='co-occurs'`, strength 0-1); query SQL unica, idempotente
  - `compute_narrative_divergences(conn)` → `narrative_divergences` (score coseno per coppia blocco, `summary=NULL` per ora); loop per-evento, idempotente
  - `deserialize(blob)` helper per leggere embedding da `vec_documents`
- **CLI**: `pathos graph` (con `--skip-links`, `--skip-divergence`, `--min-cooccurrences`)
- **Orchestratore**: 6° fase `GRAPH` tra CLUSTER e BRIEF; `pathos cycle --from-phase graph` funziona
- **Test**: `tests/test_graph.py` — 10 test (5 links + 5 divergenza, tutti verdi); `tests/test_orchestrator.py` aggiornato (5→6 fasi)
- **Docs**: `wiki.md`, `README.md`, `next_steps.md` aggiornati — Fase 2 marcata completa

---

## ✅ Sessione precedente (2026-06-15)

- **Schema**: colonne `origin`, `gdelt_events`, `comtrade_flows`, `fire_metrics`, fix ordine migrazioni
- **GDELT**, **Comtrade**, **PortWatch**, **FIRMS**, **USGS**: bootstrap + incrementale + anomalie
- **RSS**: Tor per RT, header browser, 8 fonti nuove (SCMP All News, SCMP China, MERICS, Taiwan MOFA)
- **Fase 2 — Pipeline semantica**: embedder, dedup, cluster (threshold 0.85, cap 30), NER+geocoding+Wikidata

---

## ✅ Fase 2 — COMPLETATA (2026-06-16)

| Step | Modulo | Output tabelle |
|---|---|---|
| Embedding e5-small 384d | `semantic/embedder.py` | `vec_documents` |
| Dedup semantica KNN ≥0.92 | `semantic/dedup.py` | `raw_documents.is_duplicate` |
| Clustering → eventi RSS | `semantic/cluster.py` | `events`, `event_documents` |
| NER + geocoding + Wikidata | `semantic/extract.py` | `entities`, `document_entities`, `geocode_cache`, `events.lat/lon` |
| Grafo co-occorrenze | `semantic/graph.py` | `entity_links` |
| Divergenza narrativa | `semantic/graph.py` | `narrative_divergences` |

---

## ▶ Fase 3 — Agent e valutazione

Prossimo blocco di lavoro. Nessun codice ancora presente in `pathosphere/agent/`.

### 3a. Astrazione LLM

File da creare: `pathosphere/llm/client.py`

```python
# API OpenAI-compatible — cambiare backend = una riga di config
# reasoning_model: "claude" | "qwen-local"
async def complete(messages, model=None, json_mode=False) -> str: ...
```

- **Claude**: Claude Agent SDK (`claude -p`), non chiamate API dirette
- **Qwen locale**: Ollama endpoint `http://localhost:11434/v1`, modello `qwen3:4b`
- Config: `settings.reasoning_model` (già in config.py come placeholder?)

### 3b. Brief mattutino

File: `pathosphere/agent/brief.py`  
Funzione: `generate_brief(conn, llm_client) → str`

Input (già disponibili):
- Cluster con divergenza alta (`narrative_divergences.divergence_score > 0.5`)
- Entità più connesse nel grafo (`entity_links` per gradi)
- Anomalie recenti (eventi `origin` in `portwatch`/`firms`/`usgs`)

Output: testo strutturato → loggato + salvato (tabella `briefs`? o file?)

### 3c. Generatore tesi

File: `pathosphere/agent/thesis.py`  
Funzione: `generate_thesis(conn, llm_client, event_ids) → Thesis`

Ogni tesi registra (tabella `theses` già in schema):
- `event_id` scatenante
- `causal_chain` (JSON: A→B→C)
- `instrument` (es. "TSM", "FXI", "UGA")
- `horizon_days`
- `invalidation_condition`
- `confidence` 0-1
- `approved_at` (NULL finché l'utente non approva)

### 3d. Flusso approvazione CLI

Comando: `pathos approve` o `pathos thesis list/approve/reject`

```bash
uv run pathos thesis list           # tesi in attesa di approvazione
uv run pathos thesis approve <id>   # approva → logga prezzo apertura yfinance
uv run pathos thesis reject <id> --reason "..."   # rifiuta con motivazione
```

### 3e. Paper trading EOD

- `yfinance`: non ancora agganciato — prerequisito critico
- Tabelle `trades`, `portfolios` già in schema
- No-lookahead: `price_open` = prezzo al momento dell'APPROVAZIONE
- Portfolio di controllo: agent / random (stesso numero trade, ticker casuali) / buy&hold SPY

### 3f. Predizioni non finanziarie

Tabella `predictions` già in schema:
- `statement`: "Escalation in X entro 2 settimane"
- `probability`: 0-1
- `resolves_at`: data scadenza
- `outcome`: NULL → True/False a scadenza
- Metrica: Brier score per calibrazione Tetlock

---

## Housekeeping / pendenti

- **Push branch + PR** quando pronto
- **Backup `data/db/pathosphere.db.bak-20260614`** (2.5G) = unica copia degli 8 anni GDELT. Tenere.
- **yfinance**: non ancora agganciato — prerequisito per il paper trading
- **`entity_links` e `narrative_divergences`** vuote nel campione — richiedono `pathos graph` (l'utente)
- **IODA / Cloudflare Radar**: blackout internet — mai implementato, Fase 1 residuo
- **Storicizzazione Parquet**: mai implementato, Fase 1 residuo
- **GKG enrichment**: opzionale per abilitare ricerca semantica su GDELT
- **`summary` in `narrative_divergences`**: NULL ora, riempire con LLM in Fase 3
- **`relation_type` in `entity_links`**: solo `co-occurs` ora; tipi semantici (`sanctions`, `supplies`…) via LLM in Fase 3

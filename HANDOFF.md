# Handoff Document — Pathosphere

*Aggiornato: 2026-06-26, fine sessione 3f*

## Stato al momento del handoff

**Branch:** `feat/fase-3d-approval` — pronto per PR su main  
**Test:** 375 verdi  
**Fase 3:** COMPLETA (3a/3b/3c/3d/3e/3f ✅)

---

## Cosa è stato fatto in questa sessione

### 3f — Predizioni non finanziarie (calibrazione Tetlock)

**`pathosphere/agent/predictions.py`** — modulo nuovo
- `add_prediction(conn, description, probability, horizon_date, thesis_id=None)` — valida 0≤p≤1, data ISO YYYY-MM-DD; ValueError con messaggio chiaro se fuori range
- `list_predictions(conn, only_open, only_resolved)` — ordinate per horizon_date ASC, id ASC
- `get_prediction(conn, prediction_id)` — riga singola o None
- `resolve_prediction(conn, prediction_id, outcome: bool)` — `brier_score = (probability - outcome)²`; ValueError se non trovata o già risolta
- `get_calibration(conn)` — Brier medio + 5 bucket (0-20%, 20-40%, 40-60%, 60-80%, 80-100%) con count / mean_brier / accuracy

**`pathosphere/cli.py`** — gruppo `predict` aggiunto:
- `pathos predict add "Descrizione" --probability 0.65 --horizon 2026-07-10 [--thesis-id <id>]`
- `pathos predict list [--open] [--resolved]`
- `pathos predict resolve <id> --outcome true|false`
- `pathos predict calibration`

**`tests/test_predictions.py`** — 39 test:
- add_prediction: valid, probability_too_low/too_high, probability_zero/one (edge), invalid_date, empty/whitespace description, with/without thesis_id, persist
- get_prediction: found, not_found
- list_predictions: empty, all, only_open, only_resolved, order_by_horizon, no_flags
- resolve_prediction: true/false, brier_perfect_true/false, worst_case, persisted, not_found, already_resolved
- calibration: empty, total_resolved, mean_brier, bucket_labels, bucket_counts, accuracy, prob_1_in_last_bucket, open_excluded, five_buckets_always
- integration: full_lifecycle (add → list open → resolve → list resolved → calibration)

---

## Stato esatto al cut-off

**Fase 3:** COMPLETA  
**Branch:** `feat/fase-3d-approval` (tutto il lavoro 3d→3f è su questo branch)  
**Test:** 375/375 verdi

---

## Riepilogo completo Fase 3

### 3a — LLM client
`pathosphere/llm/client.py` — Claude SDK + Qwen-local, stessa API OpenAI-compatible

### 3b — Brief mattutino
`pathosphere/agent/brief.py` — divergenze, hub entities, anomalie → Claude → testo strutturato + salvataggio `briefs`

### 3c — Generatore tesi + debate pipeline
`pathosphere/agent/thesis.py` — fast path 1 Claude call  
`pathosphere/agent/debate.py` — 6 personas × 3 step Qwen + 1 Claude synthesis  
`price_snapshot` no-lookahead, `watchlist_items` auto-popolati

### 3d — Flusso approvazione CLI
`pathosphere/agent/approval.py` — list/get/validate_ticker/approve/reject  
CLI: `pathos thesis list/show/approve/reject`

### 3e — Paper trading EOD
`pathosphere/market/trading.py` — portfolios (agent/random/benchmark), open_trade, open_agent_trade, close_trade, get_portfolio_status  
CLI: `pathos portfolio init/status`, `pathos trade open/close/list`

### 3f — Predizioni non finanziarie
`pathosphere/agent/predictions.py` — add/list/resolve/calibration  
CLI: `pathos predict add/list/resolve/calibration`

---

## Prossima azione: PR + Fase 4

**PR suggerito:**
```bash
gh pr create \
  --title "feat(3d–3f): approval flow + paper trading + Tetlock predictions" \
  --body "..."
```

**Fase 4 — Dashboard Streamlit:**
- Mappa eventi (folium)
- Confronto narrazioni per blocco
- Portafogli: curva equity, P&L per trade
- Tesi aperte + status approvazione
- Storico brief
- Calibrazione Tetlock (grafico bucket vs accuracy)

---

## Punti critici aperti

- **Ticker validation:** LLM produce ticker US-centrici e a volte inesistenti. Validazione fatta in `approve` (warn, non blocca). Correggere manualmente nel DB prima di `trade open`.
- **Qwen locale:** debate pipeline richiede Ollama attivo (`ollama serve`). ConnectError con messaggio chiaro se non disponibile.
- **`causal_chain` JSON schema:** `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}` — non rompere la struttura.
- **`theses` con `debate_id=NULL`:** generate via fast path. `list` mostra entrambe le tipologie.

---

## Comandi utili

```bash
uv run pytest                              # 375 test
uv run pathos thesis list                  # tesi pending
uv run pathos thesis show <id>             # dettaglio tesi
uv run pathos thesis approve <id>          # approva
uv run pathos thesis reject <id> --reason "..."
uv run pathos portfolio init               # crea portfolios + benchmark SPY
uv run pathos portfolio status             # P&L per portfolio (live prices)
uv run pathos trade open <thesis_id>       # apre agent + random trade
uv run pathos trade close <trade_id>       # chiude trade
uv run pathos trade list                   # trade aperti
uv run pathos predict add "Desc" --probability 0.65 --horizon 2026-07-10
uv run pathos predict list                 # tutte
uv run pathos predict resolve <id> --outcome true
uv run pathos predict calibration          # Brier score aggregato
git log --oneline origin/main..HEAD        # commit sul branch
```

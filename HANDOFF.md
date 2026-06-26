# Handoff Document — Pathosphere

*Aggiornato: 2026-06-26, fine sessione 3d*

## Stato al momento del handoff

**Branch:** `feat/fase-3d-approval` — pronto per PR su main  
**Test:** 295 verdi  
**Subtask completati questa sessione:** 3d (flusso approvazione tesi)

---

## Cosa è stato fatto in questa sessione

### 3d — Flusso approvazione tesi

**`pathosphere/agent/approval.py`** — modulo nuovo
- `list_theses(conn, status)` — tesi filtrate per status, ordinate per id DESC
- `get_thesis(conn, id)` — singola tesi o None
- `get_watchlist_items(conn, thesis_id)` — watchlist collegata
- `validate_ticker(ticker)` — `yfinance.Ticker.fast_info.last_price`; warn, non blocca; never raises
- `approve_thesis(conn, id)` — status → `approved`, `approved_at = now UTC`; ValueError se non pending
- `reject_thesis(conn, id, reason)` — status → `rejected`, `rejected_at`, `rejection_reason`; ValueError se reason vuoto o non pending
- `format_causal_chain(raw)` — parse JSON o fallback `{"raw": ...}`

**`pathosphere/cli.py`** — 4 comandi aggiunti al gruppo `thesis`:
- `pathos thesis list [--status pending|approved|rejected|closed|all]`
- `pathos thesis show <id>` — dettaglio completo: trigger, causal chain, invalidation, persona notes, debate context, watchlist
- `pathos thesis approve <id>` — validation ticker + approvazione
- `pathos thesis reject <id> --reason "..."` — rifiuto con motivazione loggata

**`tests/test_thesis_approval.py`** — 34 test:
- list/filter/ordering, FK fixtures per `debate_id`
- validate_ticker: valid, unknown, zero price, exception, empty
- approve: status, persist, not-found, already-approved, rejected-raises
- reject: status, persist, not-found, empty/whitespace reason, already-approved
- format_causal_chain: valid JSON, invalid JSON, empty, None
- integration: full approval/rejection flow, cannot approve after reject, list includes fast+debate

---

## Stato esatto al cut-off

**Subtask corrente:** 3e  
**Branch attivo:** `feat/fase-3d-approval`  
**Test:** 295/295  

---

## Prossima azione: PR per 3d poi iniziare 3e

**PR 3d:**
```bash
gh pr create --title "feat(3d): thesis approval flow — list/show/approve/reject" --body "..."
```

**3e — Paper trading EOD** (nuovo branch dopo merge):

1. `pathos portfolio status` — mostra i 3 portafogli (agent/random/benchmark) con P&L
2. `pathos portfolio init` — crea i 3 portafogli se non esistono ($100k virtuale ciascuno)
3. `pathos trade open <thesis_id>` — apre trade da tesi approvata: `price_open = yfinance EOD`
4. `pathos trade close <trade_id>` — chiude trade: `price_close = yfinance EOD`, calcola `pnl`
5. EOD update notturno: `pathos portfolio update` — aggiorna i prezzi correnti di tutti i trade aperti
6. Trade random: stessa dimensione/direzione, ticker casuale da un pool predefinito (SPY, QQQ, GLD, USO, TLT...)
7. Benchmark: buy & hold SPY, aggiornato a ogni EOD

Vincoli:
- `price_open` = prezzo al momento dell'approvazione (già in `theses.price_snapshot`) o fetch live a `trade open`
- Costi transazione simulati: 0.1% del valore (config)
- Slippage simulato: 0.05% (config)
- Tabelle già in schema: `trades`, `portfolios`

---

## Punti critici aperti

- **Ticker validation:** LLM produce ticker US-centrici e a volte inesistenti. Validazione fatta in `approve` (warn, non blocca). Il ticker si può correggere manualmente nel DB prima di aprire il trade.
- **Qwen locale:** debate pipeline richiede Ollama attivo (`ollama serve`). ConnectError con messaggio chiaro se non disponibile.
- **`causal_chain` JSON schema:** `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}` — non rompere la struttura.
- **`theses` con `debate_id=NULL`:** generate via fast path. `list` mostra entrambe le tipologie.

---

## Comandi utili

```bash
uv run pytest                              # 295 test
uv run pathos thesis list                  # tesi pending
uv run pathos thesis show <id>             # dettaglio tesi
uv run pathos thesis approve <id>          # approva
uv run pathos thesis reject <id> --reason "..." # rifiuta
git log --oneline origin/main..HEAD        # commit sul branch
```

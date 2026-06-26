# Handoff Document — Pathosphere

*Aggiornato: 2026-06-26, fine sessione 3d+3e*

## Stato al momento del handoff

**Branch:** `feat/fase-3d-approval` — pronto per PR su main  
**Test:** 336 verdi  
**Subtask completati questa sessione:** 3d (flusso approvazione tesi) + 3e (paper trading EOD)

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

## Cosa è stato fatto (3e)

### 3e — Paper trading EOD

**`pathosphere/market/trading.py`** — modulo nuovo
- `init_portfolios(conn)` — crea agent/random/benchmark ($100k); benchmark apre trade SPY al prezzo corrente. Idempotente.
- `open_trade(conn, portfolio_id, ticker, direction, qty, price_open, ...)` — inserisce trade, calcola costi
- `open_agent_trade(conn, thesis_id)` — apre trade agent + random (stesso qty/direzione, ticker casuale riproducibile). `price_open = yfinance fetch live` (no-lookahead). ValueError se non approvata o prezzo non disponibile.
- `close_trade(conn, trade_id)` — fetch prezzo corrente, calcola pnl (gross - costi entrambi i lati), persiste
- `get_portfolio_status(conn)` — calcola P&L realizzato + non realizzato (fetch prezzi live), return %
- `list_open_trades(conn, portfolio_name=None)` — lista trade aperti, opzionale filtro per portfolio

Costanti: `INITIAL_CASH=100k`, `ALLOCATION_PCT=10%`, `TRANSACTION_COST_PCT=0.1%`, `SLIPPAGE_PCT=0.05%`, `RANDOM_TICKER_POOL=[SPY, QQQ, GLD, USO, TLT, EEM, IWM, XLE, XLF, DIA]`

**CLI:**
- `pathos portfolio init` — crea portfolios + benchmark SPY trade
- `pathos portfolio status` — tabella P&L per portfolio (prezzi live)
- `pathos trade open <thesis_id>` — apre agent + random trade
- `pathos trade close <trade_id>` — chiude trade con P&L
- `pathos trade list [--portfolio agent|random|benchmark] [--closed]`

**`tests/test_trading.py`** — 41 test: pure helpers, init (idempotent, SPY unavailable), open_trade, open_agent_trade (full flow, short→sell, tutti gli errori), close_trade (long/short profit/loss, persist, already closed), get_portfolio_status (empty, open/closed, return_pct, isolation), list_open_trades, integration lifecycle

---

## Prossima azione: PR per 3d+3e poi iniziare 3f

**PR:**
```bash
gh pr create --title "feat(3d+3e): thesis approval flow + paper trading engine" --body "..."
```

**3f — Predizioni non finanziarie** (nuovo branch dopo merge):

1. `pathos predict add "Descrizione" --probability 0.65 --horizon 2026-07-10` — inserisce in `predictions`
2. `pathos predict list [--open|--resolved]` — lista predizioni con scadenza e probabilità
3. `pathos predict resolve <id> --outcome true|false` — risolve, calcola `brier_score`
4. `pathos predict calibration` — Brier score aggregato per bucket (calibrazione Tetlock)

Schema già presente: `predictions(thesis_id, description, probability, horizon_date, resolved, outcome, brier_score)`

---

## Punti critici aperti

- **Ticker validation:** LLM produce ticker US-centrici e a volte inesistenti. Validazione fatta in `approve` (warn, non blocca). Il ticker si può correggere manualmente nel DB prima di aprire il trade.
- **Qwen locale:** debate pipeline richiede Ollama attivo (`ollama serve`). ConnectError con messaggio chiaro se non disponibile.
- **`causal_chain` JSON schema:** `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}` — non rompere la struttura.
- **`theses` con `debate_id=NULL`:** generate via fast path. `list` mostra entrambe le tipologie.

---

## Comandi utili

```bash
uv run pytest                              # 336 test
uv run pathos thesis list                  # tesi pending
uv run pathos thesis show <id>             # dettaglio tesi
uv run pathos thesis approve <id>          # approva
uv run pathos thesis reject <id> --reason "..." # rifiuta
uv run pathos portfolio init               # crea portfolios + benchmark SPY
uv run pathos portfolio status             # P&L per portfolio (live prices)
uv run pathos trade open <thesis_id>       # apre agent + random trade
uv run pathos trade close <trade_id>       # chiude trade
uv run pathos trade list                   # trade aperti
git log --oneline origin/main..HEAD        # commit sul branch
```

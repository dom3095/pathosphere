# Handoff Document — Pathosphere

*Aggiornato: 2026-06-23, fine sessione 3c*

## Stato al momento del handoff

**Branch:** `feat/fase-3-agent`  
**Test:** 261 verdi  
**Subtask completati questa sessione:** 3c (thesis generator + debate pipeline)

## Cosa è stato fatto

### 3c — Generazione tesi

**`pathosphere/market/prices.py`**
- `fetch_price(ticker)` via yfinance — restituisce ultimo close, None su failure
- Nessun lookahead: lo snapshot viene preso al momento della generazione, prima dell'approvazione

**`pathosphere/agent/thesis.py`**
- `generate_theses()` — fast path: 1 call Claude, genera N tesi dal brief
- Tesi salvate con `price_snapshot`, `watchlist_items` auto-popolati

**`pathosphere/agent/debate.py`**
- Pipeline 4 step:
  1. Research (Qwen x6 parallelo) — ogni persona legge il brief dal proprio angolo
  2. Divergence detection (Qwen x1) — identifica 2-3 disaccordi strutturali
  3. Critique (Qwen x6 parallelo) — ogni persona risponde ai punti di divergenza
  4. Synthesis (Claude x1) — genera tesi informate dal dibattito con `debate_context`
- Personas: Beijing · Washington · Moscow · Riyadh · Jerusalem · Paris
- Tutto persistito in `debates` + `persona_analyses`

**Schema aggiunto via `_MIGRATIONS`:**
- `theses.price_snapshot REAL`
- `theses.debate_id INTEGER`
- `watchlist_items.thesis_id INTEGER`
- Tabelle: `debates`, `persona_analyses`

**CLI:**
- `pathos thesis generate [--date] [--n] [--model]`
- `pathos thesis debate [--date] [--n]`

## Stato esatto al cut-off

**Subtask corrente:** 3d  
**File toccati:** schema.py, prices.py, thesis.py, debate.py, cli.py, test_*.py  
**Test:** 261/261  
**Commit:** `4bcfb29` (schema+prices) · `d641ceb` (thesis+debate)

## Prossima azione esatta

Implementare 3d — flusso approvazione:

1. `pathos thesis list` — tabella tesi pending, mostra titolo/strumento/direzione/prezzo/orizzonte
2. `pathos thesis approve <id>` — status → 'approved', logga approved_at
3. `pathos thesis reject <id> --reason "..."` — status → 'rejected', logga rejection_reason
4. Validazione ticker in `approve`: `yfinance.Ticker(ticker).fast_info` — se vuoto → warn ma non blocca

Poi 3e (paper trading EOD) e 3f (Tetlock predictions).

## Punti critici aperti

- **Ticker validation:** LLM può produrre ticker inesistenti. Validare in `approve`, non in generazione.
- **Qwen locale:** debate pipeline richiede Ollama attivo (`ollama serve`). Se non attivo, fallisce con ConnectError chiaro.
- **`causal_chain` JSON schema:** `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}` — non rompere in 3d.

## Comando per riprendere

```
/loop
```
Leggi LOOP_STATE.md + questa sezione, riprendi da 3d.

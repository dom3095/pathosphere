# Loop State — Pathosphere Autonomous Dev

## Fase corrente: 3 — Agent e valutazione

| Subtask | Descrizione | Stato |
|---|---|---|
| 3a | LLM client (`pathosphere/llm/client.py`) | ✅ DONE |
| 3b | Brief mattutino (`pathosphere/agent/brief.py`) | ✅ DONE |
| 3c | Generatore tesi + debate pipeline | ✅ DONE |
| 3d | Flusso approvazione CLI | ✅ DONE |
| 3e | Paper trading EOD + yfinance | ⬜ TODO |
| 3f | Predizioni non finanziarie | ⬜ TODO |

## Subtask corrente: 3e

## Ultima azione completata
3d completo: approval.py (list_theses, get_thesis, get_watchlist_items,
validate_ticker, approve_thesis, reject_thesis, format_causal_chain).
CLI: `pathos thesis list/show/approve/reject`.
34 test nuovi. 295 test verdi totali. Commit su feat/fase-3d-approval.

## Prossima azione: 3e — paper trading EOD
- `pathos portfolio init` — crea agent/random/benchmark ($100k virtuale)
- `pathos portfolio status` — P&L per portafoglio
- `pathos trade open <thesis_id>` — apre trade da tesi approvata (price_open = yfinance EOD)
- `pathos trade close <trade_id>` — chiude trade, calcola pnl
- `pathos portfolio update` — EOD update prezzi trade aperti
- Portafoglio random: stesso N trade, ticker casuali da pool (SPY/QQQ/GLD/USO/TLT)
- Benchmark: buy & hold SPY

## Note tecniche
- Test suite: `uv run pytest tests/ -x -q`
- Linting: `uv run ruff check pathosphere/`
- Tabelle già in schema: `trades`, `portfolios`
- `price_open` da `theses.price_snapshot` (già fetch al momento generazione) o live fetch
- Costi transazione: 0.1% valore (config); slippage: 0.05% (config)
- causal_chain JSON: {"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}

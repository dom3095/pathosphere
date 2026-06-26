# Loop State — Pathosphere Autonomous Dev

## Fase corrente: 3 — Agent e valutazione

| Subtask | Descrizione | Stato |
|---|---|---|
| 3a | LLM client (`pathosphere/llm/client.py`) | ✅ DONE |
| 3b | Brief mattutino (`pathosphere/agent/brief.py`) | ✅ DONE |
| 3c | Generatore tesi + debate pipeline | ✅ DONE |
| 3d | Flusso approvazione CLI | ✅ DONE |
| 3e | Paper trading EOD + yfinance | ✅ DONE |
| 3f | Predizioni non finanziarie | ⬜ TODO |

## Subtask corrente: 3f

## Ultima azione completata
3e completo: trading.py (init_portfolios, open_trade, open_agent_trade,
close_trade, get_portfolio_status, list_open_trades).
CLI: `pathos portfolio init/status`, `pathos trade open/close/list`.
41 test nuovi. 336 test verdi totali. Commit su feat/fase-3d-approval.

## Prossima azione: 3f — predizioni non finanziarie (calibrazione Tetlock)
- `pathos predict add "Descrizione" --probability 0.65 --horizon 2026-07-10`
- `pathos predict list [--open|--resolved]`
- `pathos predict resolve <id> --outcome true|false` — brier_score = (p - outcome)²
- `pathos predict calibration` — Brier score aggregato per bucket
- Schema già presente: `predictions(thesis_id, description, probability, horizon_date, resolved, outcome, brier_score)`

## Note tecniche
- Test suite: `uv run pytest tests/ -x -q`
- Linting: `uv run ruff check pathosphere/`
- Tabelle già in schema: `trades`, `portfolios`, `predictions`
- trading.py: INITIAL_CASH=100k, ALLOCATION_PCT=10%, tc=0.1%, slippage=0.05%
- causal_chain JSON: {"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}

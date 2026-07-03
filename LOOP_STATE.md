# Loop State — Pathosphere Autonomous Dev

## Fase corrente: Predictions v2 — Implementazione

| Subtask | Descrizione | Stato |
|---|---|---|
| Design | Modello concettuale predictions v2 | ✅ DONE |
| Schema | `predictions` nuove colonne + `prediction_domains` + `prediction_revisions` + `theses.prediction_id` + backfill | ✅ DONE |
| Config | `timing_penalty_alpha` in config.py | ✅ DONE |
| Code | `predictions.py` — add/revise/resolve/calibration + create_thesis_prediction + link_thesis_prediction_to_trade | ✅ DONE |
| CLI | `pathos predict` nuovi flag + `revise` + `resolve` v2 + filtri list + `thesis approve` auto-create + `trade open` link | ✅ DONE |
| Test | 80 test predictions (416 totali verdi) | ✅ DONE |
| Review | code review 8 angoli → 10 finding, 9 fixati, 1 documentato (CP-010) | ✅ DONE |
| Docs | `wiki.md` §8.6 + `schema.md` + `roadmap.md` + `overview_per_amico.md` | 🔄 IN CORSO |
| PR | commit + push + PR conventional commits | ⬜ TODO |

## Fase successiva: 4 — Dashboard Streamlit

## Ultima azione completata
Review multi-agente su diff v2: fixati calibration accuracy vs brier mismatch,
backfill outcome_eventual, auto-create protetta, link trade targettizzato,
business logic spostata in domain layer, IntegrityError gestita, UTC coerente,
alpha parametrico, click.Choice da costanti. 416 test verdi.

## Prossima azione: completare docs → commit → PR

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (416 verdi)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- Scoring: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge

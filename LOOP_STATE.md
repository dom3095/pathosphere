# Loop State — Pathosphere Autonomous Dev

## Fase corrente: Predictions v2 — MERGIATO

| Subtask | Stato |
|---|---|
| Implementazione completa | ✅ DONE |
| Docs aggiornate | ✅ DONE (wiki §8.6, schema, roadmap, overview) |
| Test: 419 verdi | ✅ DONE |
| Merge su main | ✅ DONE (2026-07-05) |

## Fase successiva: 4 — Dashboard Streamlit

## Ultima azione completata
Fix Wikidata linking (2026-07-07, branch fix/wikidata-linking, pushato, PR da creare — gh non autenticato): rate limit rispettato, abort su 429, stoplist generici + strip QID legacy. 423 test verdi. Avviato studio qualità embed/extract/graph su branch docs/quality-study-notebooks — solo ricognizione, notebook non ancora creati. Dettagli e vincoli utente in HANDOFF.md (sezione PROSSIMA AZIONE).

## Prossima azione: notebook studio qualità (HANDOFF.md § PROSSIMA AZIONE) → PR fix Wikidata → Fase 4 Dashboard

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (419 verdi)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- Scoring: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge

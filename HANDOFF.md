# Handoff Document — Pathosphere

*Aggiornato: 2026-07-03, sessione implementazione Predictions v2*

## Stato al momento del handoff

**Branch:** `feat/predictions-v2` — implementazione completa, review fatta, fix applicati
**Test:** 416 verdi (80 in test_predictions.py)
**Manca:** completamento docs (agent in corso) → commit → push → PR

---

## Cosa è stato fatto in questa sessione

### Predictions v2 — implementazione completa

**Schema** (`pathosphere/db/schema.py`, migration idempotenti in `_MIGRATIONS`):
- 10 colonne nuove su `predictions`: `macro_area` (NOT NULL DEFAULT 'world'), `prediction_type` (NOT NULL DEFAULT 'geopolitical'), `outcome_eventual`, `outcome_on_time`, `resolved_date`, `time_adjusted_score`, `origin_scope`, `impact_scope`, `time_horizon_class`, `trade_id`
- Backfill legacy: `outcome_on_time = outcome` E `outcome_eventual = outcome` (guardie IS NULL, idempotenti)
- Tabelle nuove: `prediction_domains(prediction_id, domain, is_primary)`, `prediction_revisions(id, prediction_id, probability, rationale, revised_at)`
- `theses.prediction_id` FK opzionale (catena predizione world → tesi)

**Config:** `timing_penalty_alpha: float = 0.001`

**`pathosphere/agent/predictions.py`** (riscritto):
- Costanti esportate: `VALID_MACRO_AREAS`, `VALID_PREDICTION_TYPES`, `TYPES_BY_MACRO_AREA`, `VALID_DOMAINS` (10), `VALID_SCOPES` (5)
- `add_prediction(...)` — valida coerenza macro_area/type, world richiede scope+domini, economic richiede thesis_id; inserisce prediction_domains; time_horizon_class derivato (breve ≤30gg, medio ≤180gg, lungo; UTC)
- `revise_prediction(id, probability, rationale)` — logga in prediction_revisions
- `resolve_prediction(id, outcome_eventual, resolved_date, alpha=None)` — brier su outcome_eventual; outcome_on_time derivato; legacy `outcome` specchia on_time; time_adjusted_score = 0 se mai accaduto, altrimenti (1−brier)×max(0, 1−alpha×|delta gg|)
- `get_calibration()` — dual metric, bucket con accuracy su outcome_eventual (fallback legacy), per-bucket mean_time_adjusted_score, breakdown by_macro_area/by_prediction_type
- `create_thesis_prediction(conn, thesis)` — auto-predizione economic per tesi approvata; clampa confidence a [0,1], default p=0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade(conn, thesis_id, trade_id)` — aggancia SOLO la più vecchia predizione economic aperta e non collegata

**CLI** (`pathosphere/cli.py`):
- `predict add` — flag v2 completi, click.Choice da costanti (inclusi --domain)
- `predict revise <id> --probability --rationale` — NUOVO
- `predict resolve <id> --outcome-eventual true|false --resolved-date YYYY-MM-DD`
- `predict list` — filtri --macro-area/--prediction-type/--domain; colonna Out con fallback legacy
- `predict calibration` — dual metric + breakdown per area e tipo
- `thesis approve` — auto-crea predizione economic (protetta: fallimento non maschera approvazione)
- `trade open` — aggancia predizione via domain function
- Gestione `sqlite3.IntegrityError` su FK inesistenti

### Review (8 angoli multi-agente) — 10 finding, 9 fixati

Fix principali: calibration accuracy usava `outcome` mentre brier usava `outcome_eventual` (metriche contraddittorie); backfill mancante di outcome_eventual (righe legacy mostravano '—'); auto-create non protetta dopo commit approvazione; UPDATE unbounded in trade open; business logic spostata da CLI a domain layer; timezone UTC coerente; alpha parametrico.

Non fixato (documentato): CP-010 — migration girano solo con `pathos db init`.

### Nuovi punti critici
- **CP-007**: headroom (compressione token) — opzione futura se credito Claude stretto
- **CP-008**: ruff F821 `sqlite3` undefined in 9 punti moduli ingest (pre-esistente, branch dedicato)
- **CP-009**: cambio timing_penalty_alpha invalida comparabilità score storici
- **CP-010**: dopo pull con modifiche schema serve `uv run pathos db init`

---

## Stato esatto al cut-off

- Codice + test: **COMPLETI**, 416 verdi
- Docs (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md): agent haiku in aggiornamento
- LOOP_STATE.md, CRITICAL_POINTS.md: aggiornati
- **Nessun commit ancora fatto** sul branch

---

## Prossima azione raccomandata

1. Verificare docs aggiornate dall'agent
2. Commit (Conventional Commits) + push + PR:
```
feat(predictions): v2 — macro_area/type separation, time-adjusted scoring,
multi-domain taxonomy, revision history, geopolitical→thesis→economic chain
```
3. Dopo merge: **Fase 4 — Dashboard Streamlit**

---

## Comandi utili

```bash
# Stato
uv run pytest tests/ -q                    # 416 verdi
uv run pathos db init                      # OBBLIGATORIO dopo pull con modifiche schema

# Predictions v2
uv run pathos predict add "Desc" --macro-area world --prediction-type geopolitical \
  --probability 0.65 --horizon 2026-08-10 --domain conflitto_armato \
  --origin-scope regionale --impact-scope globale
uv run pathos predict revise <id> --probability 0.7 --rationale "..."
uv run pathos predict resolve <id> --outcome-eventual true --resolved-date 2026-08-05
uv run pathos predict list --open --macro-area world --domain commercio
uv run pathos predict calibration

# Thesis / trading (v2: approve auto-crea predizione economic, trade open la aggancia)
uv run pathos thesis list
uv run pathos thesis approve <id>
uv run pathos thesis reject <id> --reason "..."
uv run pathos trade open <thesis_id>
uv run pathos portfolio status
```

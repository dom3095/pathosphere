# Handoff Document — Pathosphere

*Aggiornato: 2026-06-23, fine sessione 3c*

## Stato al momento del handoff

**Branch:** `feat/fase-3-agent` — pronto per PR, messaggio scritto  
**Test:** 261 verdi  
**Subtask completati questa sessione:** 3c (thesis generator + debate pipeline)

---

## Cosa è stato fatto in questa sessione

### Fix git
- 3 commit di una sessione precedente erano finiti su `origin/main` per errore (loop autonomo)
- Spostati sul branch corretto (`feat/fase-3-agent`) con i SHA originali
- `origin/main` resettato a `a482b56`
- Branch `feat/numeric-detail-tables-rss-tor` eliminato (già mergiato in main)

### 3c — Generazione tesi

**`pathosphere/market/prices.py`**
- `fetch_price(ticker)` via yfinance — restituisce ultimo close EOD, None su failure
- Snapshot al momento della generazione (no-lookahead bias)

**`pathosphere/agent/thesis.py`**
- `generate_theses(conn, llm_client, *, brief_date, n)` — fast path: 1 call Claude
- Tesi salvate con `price_snapshot`, `watchlist_items` auto-popolati, `debate_id=None`

**`pathosphere/agent/debate.py`**
- Pipeline 4 step:
  1. Research (Qwen ×6 parallelo) — ogni persona legge il brief dal proprio angolo
  2. Divergence detection (Qwen ×1) — identifica 2-3 disaccordi strutturali
  3. Critique (Qwen ×6 parallelo) — ogni persona risponde ai punti di divergenza
  4. Synthesis (Claude ×1) — genera tesi con `debate_context` (chi supporta/oppone)
- Personas: Beijing · Washington · Moscow · Riyadh · Jerusalem · Paris
- Tutto persistito in `debates` + `persona_analyses`

**Schema aggiunto via `_MIGRATIONS` (idempotente):**
- `theses.price_snapshot REAL`
- `theses.debate_id INTEGER REFERENCES debates(id)`
- `watchlist_items.thesis_id INTEGER REFERENCES theses(id)`
- Tabelle: `debates`, `persona_analyses`

**CLI:**
- `pathos thesis generate [--date] [--n] [--model]`
- `pathos thesis debate [--date] [--n]`

**Commit sul branch:**
- `99ec2c2` feat(3a): LLM client
- `59687f1` feat(3b): morning brief
- `db9afa1` chore: LOOP_STATE 3b
- `4bcfb29` feat(3c): price snapshot + schema
- `d641ceb` feat(3c): thesis + debate pipeline
- `fc66eda` chore: tracking files 3c

---

## Stato esatto al cut-off

**Subtask corrente:** 3d  
**Branch:** `feat/fase-3-agent` — da fare PR su main prima di iniziare 3d  
**Test:** 261/261  

---

## Prossima azione: aprire PR poi iniziare 3d

**PR:** messaggio già scritto. Aprire con:
```bash
gh pr create --title "Fase 3 (3a/3b/3c): LLM client, morning brief, thesis generation + multi-persona debate" --body "..."
```

**3d — flusso approvazione tesi** (nuovo branch dopo merge):

1. `pathos thesis list` — tabella tesi pending: titolo / strumento / direzione / prezzo snapshot / orizzonte / confidence
2. `pathos thesis show <id>` — dettaglio completo: causal chain, invalidazione, debate context, watchlist items
3. `pathos thesis approve <id>` — status → `approved`, logga `approved_at`
4. `pathos thesis reject <id> --reason "..."` — status → `rejected`, logga `rejection_reason`
5. Validazione ticker in `approve`: `yfinance.Ticker(ticker).fast_info` — warn se vuoto, non blocca

---

## Punti critici aperti

- **Ticker validation:** LLM produce ticker US-centrici e a volte inesistenti. Validare in `approve`, non in generazione. Non bloccare — l'utente può correggere il ticker prima di approvare.
- **Qwen locale:** debate pipeline richiede Ollama attivo (`ollama serve`). ConnectError con messaggio chiaro se non disponibile.
- **`causal_chain` JSON schema:** `{"steps": [...], "trigger_summary": "...", "persona_notes": {}, "debate_context": {...}}` — non rompere la struttura in 3d.
- **`theses` con `debate_id=NULL`:** generate via fast path. `list` deve mostrare entrambe le tipologie.

---

## Comandi utili

```bash
uv run pytest                              # 261 test
uv run pathos brief                        # genera brief
uv run pathos thesis generate              # fast path
uv run pathos thesis debate                # pipeline debate (Ollama richiesto)
git log --oneline origin/main..HEAD        # commit sul branch
```

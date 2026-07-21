# Loop State — Pathosphere Autonomous Dev

## Fase corrente: primo esercizio reale end-to-end — 2026-07-19/20

**Stato**: su richiesta utente, lanciati i 5 punti del "resta a te" (scenario generate, thesis debate,
geoloc Qwen, backfill storico, CP-024) sotto `caffeinate`, sul branch `fix/cp023-yfinance-retry`
(nessun nuovo branch, come richiesto). Dettaglio completo in `HANDOFF.md`.

**Esiti**: CP-029 chiuso (2 run completi confermati, id=4+id=5); 1 bug fixato (`cli.py`, traceback
pulito su precondizioni ValueError in thesis generate/debate, commit `6ab76f4`); scenari generati per
la prima volta con Claude vero (Bahrain, Vietnam — qualità alta); backfill storico incrementale
(+1 WHO DON); geoloc Qwen batch in corso (ritmo confermato ~90s/evento, solo overnight);
CP-024 riconfermato bloccato (azione utente).

**Aggiornamento 2026-07-21**: CP-032 (JSON strutturato Qwen) chiuso con conferma dal vivo —
batch geoloc riavviato apposta col fix, 200/200 chiamate, zero schema-rejection, errore
0.5% (era ~1/10-15 pre-fix). Backlog geoloc sceso da 2181 a **1336** (`pathos doctor` verificato).

**Aggiornamento 2026-07-21/22 — CP-033 (2 round `/code-review` sui fix)**: primo round sul branch
intero (9 finding, tutti applicati: BriefNotFoundError dedicata, retry statement indipendenti,
doctor query allargata, tenacity, capability cache Qwen). Secondo round SUI FIX del primo (per
verificare che i fix stessi non avessero introdotto bug) — trovato e fixato 1 bug reale (guardia
warning morta, duplicati contraddittori in fundamentals.py), documentato 1 tradeoff non menzionato
(latenza 3x), fixato 1 resource-leak cosmetico (`conn.close()` in `finally`), aggiornato wiki.md
stale. Aperto **CP-034** (fuori scope, `scenarios.py` non toccato da questo branch): stesso
bug-pattern trovato per analogia in `scenario_review` — sessione dedicata futura.

**Prossima azione (utente)**: review/merge PR #24 (ora comprende: CP-023 retry yfinance, igiene
ruff/doc, fix cli.py precondizioni, CP-032 structured output). Poi: altri batch geoloc per
smaltire il resto del backlog, registrare `RELIEFWEB_APPNAME`, concedere Full Disk Access per
CP-024, continuare a lanciare `thesis debate`/`scenario generate` periodicamente per accumulare
dati di calibrazione (Brier/time-adjusted) — l'unica metrica che conta.

---

## Fase precedente: chiusura ciclo PR notturne + igiene repo — 2026-07-19

**Stato**: #20/#21/#22 mergiate su main. #23 chiusa senza merge → riaperta come
**PR #24** (`fix/cp023-yfinance-retry`, stesso branch, main mergiata dentro, 697 verdi).
Sulla stessa PR #24 aggiunta igiene repo:
- **Ruff 170→0**: config `[tool.ruff]` in pyproject (exclude `notebooks/` = record storici,
  `.agents/` = script skill vendored); 33 auto-fix su prodotto+test; 11 fix manuali
  (F841 variabili morte in cli.py/loop.py/test, E741 rename `l`→`lbl`). Zero errori.
- **Doc sync**: roadmap (Fase 4 ✅ in tabella, riga Fase 5), HANDOFF header, questo file,
  CLAUDE.md stato attuale.

**Prossima azione (utente)**: review/merge PR #24; poi esercizio reale del sistema —
`pathos scenario generate` (primo run vero), `caffeinate -i uv run pathos thesis debate`
a macchina scarica (CP-029), batch geoloc Qwen (~1324 residui), backfill storico
(serve `RELIEFWEB_APPNAME`), fix launchd CP-024 (manuale macOS).

---

## Fase precedente: manutenzione post-merge — sessione notturna autonoma 2026-07-17

**Stato**: tutte le PR mergiate (#14→#19), main allineata, zero PR aperte. Fasi 0-4
complete + scenari conflitto + technicals + doctor + backfill storico in main.

**Task notte 2026-07-17 — TUTTI COMPLETATI** (branch separati da main, zero merge):
1. ✅ PR #20 `chore/docs-sync-post-merge` — riallineamento docs
2. ✅ PR #21 `fix/cp030-transactional-scenario-persist` — CP-030 risolto (+3 test)
3. ✅ PR #22 `fix/doctor-tests-post-merge` — 4 test rossi di main fixati (686 verdi)
4. ✅ PR #23 `fix/cp023-yfinance-retry` — CP-023 parte 1 (stacked su #22, +8 test, 694 verdi)

**Prossima azione (utente)**: review/merge PR notte in ordine #20→#21→#22→#23; poi primo
`pathos scenario generate` reale; run `thesis debate` a macchina scarica (CP-029);
batch geoloc Qwen.

---

## Fase precedente: previsione scenari di conflitto (3g) — branch `feat/conflict-forecasting` (MERGIATA, PR #17)

**2026-07-16 — modulo scenari implementato, wired, testato, reviewed:**

- `agent/scenarios.py` (~900 righe) — pipeline completa: `compute_hotspots` (triage
  deterministico gdelt_events, finestra 14gg vs baseline 90gg, z material conflict + shift quad4 +
  Goldstein delta + volume surge, score SOLO ranking) → `build_dossier` (evidenze E1..En congelate:
  metriche, anomalie gdelt, RSS, divergenze, IODA, UCDP prior) → `generate_scenarios` (1 call
  Claude/hotspot, default 2: 3-4 scenari MECE ACH, probabilità→1, indicatori→watchlist) →
  `review_scenarios` (trigger indicatori + metriche fresche → `revise_prediction` con rationale;
  set overdue MAI revisionati) → `resolve_scenario_set` (winner umano → scoring predictions v2).
- Migrazioni: `scenario_sets`, `scenarios`, `watchlist_items.scenario_id` (+3 indici)
- Config: `scenario_horizon_days=90`, `scenario_max_hotspots=2`
- Wiring: CLI gruppo `pathos scenario` (hotspots/generate/list/show/review/resolve); brief con
  sezione "ACTIVE CONFLICT SCENARIOS"; dashboard pagina "Scenari" (9ª vista)
- Test: +19 (`tests/test_scenarios.py`) → **650 verdi**; ruff pulito su tutti i file nuovi
- Code review inline (8 finder subagent morti per limite sessione, come sessione precedente):
  4 finding — 3 fixati (revisione post-orizzonte bloccata, confronto date normalizzato con `date()`,
  `--country` uppercased), 1 documentato (CP-030 persistenza parziale). Scoperto e annotato anche
  CP-031 (pagina Predizioni dashboard: KeyError `overall`, pre-esistente, NON toccato).
- Smoke reale via subagent: hotspots su DB reale ok, help ok, migrazione ok, view import ok.

**2026-07-17 — allineamento branch**: mergiato `origin/feat/stock-technicals` (che include
`origin/main` con backfill #15) in `feat/conflict-forecasting`. PR #16 (technicals) in CI,
merge su main da PR. Questo branch va in PR dopo #16.

**Prossima azione**: push + PR branch `feat/conflict-forecasting`; primo
`pathos scenario generate` reale (lanciato dall'utente) per validare il prompt su Claude vero.

---

## Fase precedente: enrichment technicals (analisi finanziaria price-action) — branch `feat/stock-technicals`

**2026-07-15 — Risoluzione merge conflicts (3 file):**
- CRITICAL_POINTS.md: CP-027 combine eventi storico + nota prezzi
- HANDOFF.md: ridotto a sommario, dettagli in sezioni successive
- LOOP_STATE.md: merge in corso

**Branch e PRs**:
1. `feat/fundamentals-analysis` (PR #14): fundamentals layer, CP-008/010/012, CP-022 geoloc RSS, CP-025/026, CP-028 review, auto-open soglia, test 584 verdi — **IN MAIN, MERGIATA**
2. `feat/historical-events-backfill` (creato da feat/fundamentals-analysis): 4 ingestori storici, CP-027 parte 1, 603 verdi — **DA MERGGIARE**
3. `feat/stock-technicals` (creato da feat/historical-events-backfill): technicals analysis, PR #16, 631 verdi — **DA MERGGIARE**
4. CP-029 (CP-029 timeout 1800s + retry): 584→631 verdi in main, attende run reale utente

**Prossima azione**: completare merge → push → verificare stati branch/PR.

**2026-07-14 notte (2ª sessione) — Implementata opzione 1 di CP-029:**

Verificato nel DB: nessun run nuovo dopo id=3 (ultimo sempre `failed`) — l'utente non ha ancora
rilanciato. Implementata opzione 1 di CP-029 in `llm/client.py`:
- Timeout per-chiamata Qwen **900s → 1800s** (`_QWEN_TIMEOUT_S`) — assorbe i picchi >900s osservati.
- **1 retry automatico su `ReadTimeout`** (`_QWEN_READ_TIMEOUT_RETRIES=1`) — distingue picco
  transitorio da limite duro; al secondo timeout consecutivo l'eccezione propaga.
- 3 test dedicati: valore 1800s, retry-poi-successo (2 POST, risposta del 2°), doppio timeout propaga
  (esattamente 2 POST, no loop infinito). **584 test verdi** (582+2). Ruff pulito.

Doc aggiornate: `CRITICAL_POINTS.md` (CP-029: opzione 1 fatta, restano opzioni 2-3), `HANDOFF.md`
(prompt di ripresa riscritto), `docs/wiki.md` §8.3 (nota timeout/retry).

**Prossima azione**: run reale lanciato DALL'UTENTE a macchina scarica (comando nel prompt di ripresa
in `HANDOFF.md`). CP-029 si chiude solo con `debates.status='complete'` verificato nel DB.

---

## Fase precedente: CP-029 — 2 run reali falliti, in handoff (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 notte — Secondo run reale con timeout 900s, fallito di nuovo (id=3):**

Dopo il fix "timeout 900s + doc" (sotto), rilanciato `pathos thesis debate` per validare davvero
(l'utente ha chiesto esplicitamente "dobbiamo aspettare che finisca correttamente, no graceful fail").
Partito 21:26:13. Step 1 research (batch 2) **riuscito** alle 22:03:20 (~37 min, 6 chiamate — batching
+ 900s regge qui). Step 2 divergence **riuscito** alle 22:13:06 (9:46 min). **Step 3 critique fallito**
di nuovo, `ReadTimeout` esattamente a 900.0s.

Scoperta che smentisce l'ipotesi precedente: il prompt di critique è **più piccolo** di quello di
research (solo 2 divergenze brevi + narrativa propria, niente brief intero) — eppure più lento. La
latenza non dipende solo dalla dimensione del prompt, **cresce con la durata della sessione** (~370s
stimati a inizio run → 900s+ dopo ~50 minuti). Causa non verificata: throttling termico M1, degrado
memoria, o interferenza di altri processi attivi (inclusa questa stessa sessione Claude Code). Nessun
dato sporco (debate id=1,2,3 tutte `status='failed'` pulite).

**Decisione utente**: non insistere oltre stasera. "Committa, prepara l'handoff, scrivi il prompt per
il tuo collega e lancio io" — codice attuale (batching+900s) committato così com'è, CP-029 lasciato
esplicitamente **aperto** (non risolto — la dichiarazione di "risolto" del fix precedente era prematura,
corretta in `CRITICAL_POINTS.md`). Prossimo tentativo lanciato dall'utente stesso, non dall'agent.

**Dettaglio + opzioni per il prossimo tentativo**: CP-029 in `CRITICAL_POINTS.md`. **Prompt di ripresa
per la prossima sessione**: vedi `HANDOFF.md`, sezione in cima.

---

## Fase precedente: `pathos thesis debate` — primo tentativo di validazione, fix timeout+batching (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 sera — Primo run reale di `pathos thesis debate` (mai lanciato prima):**

Utente ha chiesto "lancialo" per verificare se il debate funziona davvero (non solo test mockati).
Crash reale al primo tentativo: `httpx.ReadTimeout` a 120.0s sullo Step 1 (research, 6 chiamate Qwen
parallele contro un solo Ollama locale — viola il vincolo hardware CLAUDE.md "un modello alla volta").
Registrato CP-029, nessun dato sporco creato (debate row marcata `failed` correttamente).

Utente ha chiesto di mandare le chiamate **a 2 a 2** (batch, non tutte parallele). Implementato
`_gather_in_batches()` in `agent/debate.py` (`QWEN_BATCH_SIZE=2`), timeout httpx 120s→300s. Rilanciato
per verificare — **timeout di nuovo, stavolta a 300.0s esatti**. Misurata poi una singola chiamata
Qwen isolata (zero concorrenza) con un prompt di ricerca realistico: **318.7 secondi**. Causa vera: non
la concorrenza, la velocità pura di qwen3:4b q4 su M1 8GB per un prompt di questa dimensione (i 46-113s
di CP-022 erano per un prompt minuscolo di classificazione, non rappresentativi).

**Decisione presa con l'utente** (via AskUserQuestion): timeout+documentazione, nessuna riduzione di
qualità (scartate le opzioni "prompt più corti"/"meno personas"/"modello più piccolo"). Fix finale:
timeout 300s→**900s** (margine ~3x sopra i 318.7s misurati), docstring `pathos thesis debate` (`cli.py`)
aggiornata con avviso esplicito "SOLO background/overnight, mai interattivo" + esempio
`caffeinate -i uv run pathos thesis debate &` — stesso pattern già usato per `--geolocate-qwen`.

**Test**: 582 verdi (invariato — nuovi test batching `test_gather_in_batches_caps_concurrency`,
`test_gather_in_batches_waits_for_batch_before_next`, timeout rinominato
`test_complete_qwen_uses_900s_timeout`). Ruff pulito, 7 violazioni pre-esistenti invariate.

**Non testato end-to-end con timeout 900s** — nessun terzo run reale lanciato (costerebbe 60-90+
minuti). Fix verificato per costruzione (timeout matematicamente sopra la latenza misurata) + unit test
sul valore passato a `httpx.AsyncClient`, non da un run reale completo. Prossimo run reale (lanciato
dall'utente, in background) è la prima validazione end-to-end vera.

**Dettaglio**: CP-029 in `CRITICAL_POINTS.md`.

**Prossimo**: se l'utente vuole, lanciare `pathos thesis debate` in background per la prima
validazione end-to-end completa con timeout 900s. Altrimenti pronta per merge insieme al resto —
nessun altro lavoro di codice noto in sospeso.

---

## Fase precedente: code review pre-merge completata, 10 bug/gap fixati (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 sera — Code review strutturata pre-merge (CP-028):**

Utente ha chiesto "hai fatto code review? ci sono bug committati?" prima di mergiare PR #14. Fatta
review con skill `/code-review --level high`: 8 angoli paralleli + verifica 1-voto sui candidati
deboli. **10 problemi reali trovati** (6 confermati, 1 plausibile, 3 di efficienza per consenso
multi-angolo), tutti fixati su richiesta esplicita ("fixerei tutto prima").

Più gravi: crash TypeError post-commit in `_maybe_auto_open` (confidence non validata), ciclo
automatico che non usava il fix CP-022 di oggi (`orchestrator.py` non chiamava mai
`geolocate_rss_events`). Altri: `thesis debate` senza fundamentals/auto-open (ora riusa le funzioni
di `thesis.py`), fence-stripping che non gestiva testo dopo la chiusura, mismatch alias entità in
geoloc (verificato su dato reale "turkey"/"Turkey"), duplicazione approve+open CLI vs auto-open
(estratte funzioni condivise in `agent/approval.py`, sistema anche gap validazione ticker), 2 fix
efficienza (cache migration per-processo, major_powers calcolata una volta invece di due),
doppio conteggio eventi nel brief (cosmetico).

**Test**: 579 verdi (era 560, +19: +11 sui fix di regressione, +8 test dedicati diretti per i fix
#1/#6/#7 — TypeError guard, validate_ticker chiamato davvero, approve_thesis_with_prediction/
open_trade_and_link testate singolarmente). Ruff pulito, 14 violazioni pre-esistenti invariate.

**Dettaglio**: CP-028 in `CRITICAL_POINTS.md`, sezione dedicata in `HANDOFF.md`.

**Prossimo**: pronta per merge — nessun lavoro di codice noto in sospeso. Aspetta review umana su
PR #14 (branch protection) + CI check `test` (ultimo controllo era `pending`, ricontrollare).

---

## Fase precedente: auto-open a soglia di confidence implementato (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 — Discussione utente su scope notizie + cadenza + autonomia → feature auto-open:**

Utente ha chiesto scope notizie passate all'LLM + sollevato 2 punti: (1) cadenza tesi dovrebbe essere
settimanale, non giornaliera — confermato: già così di fatto (thesis generate mai nel loop
automatico, solo manuale); ma 7gg di lookback nel brief è troppo poco per conflitti pluriennali
("l'unghia del leone") — **discusso design "situazioni" a orizzonte semantico, non a giorni fissi,
rimandato a sessione dedicata** (Fase 5 in roadmap.md, non iniziata, richiede cautela per rischio
chain-collapse già visto 3 volte nel progetto). (2) "tesi aperte in autonomia, poi rifinite" —
contraddiceva la regola scritta in CLAUDE.md ("nessuna operazione autonoma") — chiarito: soglia di
confidence (0.6) decide auto-open vs pending manuale.

**Implementato**: `pathosphere/agent/thesis.py` — `_maybe_auto_open()` replica esattamente la
sequenza manuale (`approve_thesis` → `create_thesis_prediction` → `open_agent_trade` →
`link_thesis_prediction_to_trade`), eseguita **dopo** la review fondamentali (una tesi contraddetta
dai fondamentali beneficia comunque di quel contesto prima di aprire). Soglia in
`config.py::auto_open_confidence_threshold` (default 0.6, calibrato sui dati reali di oggi). CLI:
`--no-auto-open`, `--auto-open-threshold N`. Degrado: approvazione riuscita + apertura trade fallita
→ resta `approved` (non torna `pending`), completabile dopo con `pathos trade open <id>`.

**CLAUDE.md aggiornato** (principio 2, "Human-in-the-loop, con auto-open a soglia") — la regola
scritta ora riflette il comportamento reale, non più "nessuna operazione autonoma" senza eccezioni.

**Test**: 560 verdi (era 554, +6: `_maybe_auto_open` successo/fallimento/soglia + integrazione
`generate_theses`). Ruff pulito (baseline 8 pre-esistenti invariate).

**Prossimo**: prossimo `pathos thesis generate` reale eserciterà la feature per davvero (oggi le 7
tesi esistenti restano tutte `pending`, generate prima di questo fix). Fase 5 "situazioni" quando si
apre una sessione dedicata.

---

## Fase precedente: primo ciclo reale completato — CP-025/CP-026 trovati e risolti (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 — Primo `pathos brief` → `pathos thesis generate` reale della storia del progetto:**

`pathos portfolio init` (3 portafogli, benchmark SPY aperto) → `pathos brief` → `pathos thesis
generate`. Il primo tentativo ha esposto 2 bug reali mai visti prima (nessun run reale era mai stato
fatto):

- **CP-025**: brief senza contenuto narrativo nei giorni a 0 `narrative_divergences` (il caso comune)
  — nessuna query di fallback per eventi RSS recenti in generale. Fix: `_query_recent_events()` in
  `brief.py`, sezione sempre popolata indipendente dalle divergenze.
- **CP-026**: `claude -p` (subprocess in `llm/client.py`) ereditava CLAUDE.md/hook del repo,
  contaminando l'output con meta-commentario da coding-agent ("salvato in scratchpad", "vuoi che lo
  integri in brief.py?"). Fix: `--safe-mode --tools=` (isola dal repo, preserva auth OAuth — NON
  `--bare`, che romperebbe l'auth). Trovato anche un secondo problema nello stesso giro: JSON valido
  ma avvolto in fence ` ```json ` non gestito da nessun chiamante `json_mode=True` — fix centralizzato
  (`_strip_json_fence()` in `complete()`).

**Risultato dopo i fix**: brief pulito (parte da `# Intelligence Brief`, 12 recent events reali su
Hormuz/Graham/Le Pen), **7 tesi reali persistite** (BZ=F, FRO, ITA — 3 primarie + 4 alternative),
fundamentals review batch completato, nessun rifiuto, nessuna contaminazione.

Test: 554 verdi (era 546, +8 llm_client +8 brief). `.gitignore` corretto (`data/briefs/` mancava,
ora ignorato come db/parquet/logs).

**Prossimo**: `pathos thesis approve <id>` su una delle 7 (verifica auto-creazione predizione
economic, CP-004/005) → `pathos trade open <id>` (verifica apertura trade reale, primo dato vero per
CP-023). Oppure aspettare merge PR #14 prima di continuare ad accumulare commit sullo stesso branch.

---

## Fase precedente: CP-022 geoloc RSS risolto (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-14 — CP-022 implementato (euristica + fallback Qwen), eseguito sul DB reale (solo Step 1):**

`geolocate_rss_events()` (euristica gratis, sempre attiva in `pathos extract` prima di
`geocode_events`) + `geolocate_ambiguous_events_qwen()` (fallback Qwen3 4B, opt-in esplicito
`--geolocate-qwen --geoloc-limit N`, riprendibile via nuova colonna `events.geoloc_checked`).
Latenza Qwen ri-misurata a macchina scarica: 46.7s/call (era 90-113s nel notebook sotto stress).

Eseguito Step 1 sul DB reale: 2689 eventi RSS valutati → 870 `located` (32%), 1324 `ambiguous`
(49%, in attesa di `--geolocate-qwen`), 74 `skip_bilateral`, 421 `skip_none`. Step 2 (Qwen) NON
eseguito sul backlog storico completo (fuori scope questa sessione, ~17h di chiamate seriali) —
prossimo passo consigliato: batch notturno `caffeinate -i uv run pathos extract --geolocate-qwen
--geoloc-limit 200` ripetuto finché `ambiguous` non scende a 0.

Test: 535 verdi (era 519, +16). Ruff pulito sui file toccati (12 violazioni pre-esistenti
invariate). CP-022 marcato RISOLTO in `CRITICAL_POINTS.md` con valutazione critica a 5 punti
(dipendenza da qualità NER, instabilità `MAJOR_POWERS` nel tempo, validazione Qwen a 2 campioni,
backfill storico non completato, `geocode_events()` invariata).

Nota di processo: implementazione fatta da subagent in background, interrotto 2 volte da errori
infra (connessione caduta, poi stallo 600s) — non errori logici. Codice/test recuperati intatti
dal working tree entrambe le volte (mai persi), completamento doc+commit+push fatto a mano.

**Prossimo**: batch Qwen storico (~1324 eventi ambigui) quando comodo, non urgente (mappa dashboard
già migliorata da 870 nuovi eventi geolocalizzati). Oppure: merge PR #14 (fundamentals + CP-008/010/
012/022 tutti insieme) → primo `thesis generate` reale.

---

## Fase precedente: CP-008/CP-010/CP-012 risolti (branch `feat/fundamentals-analysis`, PR #14)

**2026-07-13 ~ notte — 3 critical point chiusi (indipendenti, 1 giro):**

- **CP-008**: `import sqlite3` mancante in 6 moduli ingest (comtrade/gdelt/physical/portwatch/rss/sources_seed) → `ruff check --select F821` 0 errori.
- **CP-010**: `get_connection` ora chiama `migrate_db(conn)` — DB pulled con schema vecchio non crasha più senza `pathos db init` esplicito. 1 test nuovo.
- **CP-012**: `dedup_documents` commit per batch (BATCH_SIZE=32, stesso pattern di `embedder.py`) invece di una transazione unica sull'intero backfill — resiliente a Ctrl+C/crash, log progresso INFO per batch. 1 test nuovo (batch parziale committato sopravvive a eccezione su batch successivo).

Test: 519 verdi (era 517 in `feat/fundamentals-analysis`, +2). Ruff pulito su tutti i file toccati (2 F401 pre-esistenti in `gdelt.py`, non introdotti, fuori scope). 3 commit separati, push su `feat/fundamentals-analysis` (PR #14 si aggiorna da sola).

**Prossimo**: nessuna azione specifica — CP-008/010/012 chiusi. Riprendere da dove lasciato prima (merge PR fondamentali → primo `thesis generate` reale, o CP-022 geoloc RSS).

---

## Fase precedente: enrichment fondamentali implementato (branch `feat/fundamentals-analysis`, PR aperta)

**2026-07-13 ~ sera — Modulo fondamentali (enrichment layer, non motore quant):**

Nuovo `pathosphere/market/fundamentals.py`: `fetch_fundamentals(ticker)` →
`FundamentalsSnapshot` (ratio yfinance `.info` + Altman Z con skip settore
finanziario + Piotroski F con conteggio test calcolabili) +
`render_fundamentals_text()` (template deterministico prompt-ready, no LLM).
Contratto degradazione identico a `fetch_price`: `None` solo su fallimento
totale, dati parziali = caso atteso (warnings), mai eccezioni.

Aggancio in `generate_theses`: ogni ticker proposto → snapshot+testo in
`theses.fundamentals_json` (nuova colonna, migrazione idempotente); se ≥1
tesi ha dati → 1 call LLM batch di review (annotazione supporta/contraddice/
neutrale, salvata come `llm_assessment` — NON decide, l'umano approva).
Fallimento review → warning, tesi salvate comunque. CLI: `pathos fundamentals
<ticker>`, `pathos thesis generate --no-fundamentals`, sezione Fundamentals
in `thesis show`. SEC EDGAR rimandato a v2 (motivato in HANDOFF).

**Test**: 19 nuovi (15 test_fundamentals.py + 4 test_thesis.py), 517 totali
verdi. Ruff pulito sui file toccati (residui pre-esistenti invariati).

**Prossimo**: merge PR → primo giro reale `pathos thesis generate` per
vedere fundamentals_json su tesi vere; valutare copertura reale dei ticker
proposti dall'LLM (non-USA attesi problematici).

---

## Fase precedente: Fase 4 Dashboard pronta per commit/PR; CP-022 (geoloc RSS) validato ma non implementato

**2026-07-17 notte — `pathos doctor` (sessione autonoma, branch `feat/doctor` da main):**

Sessione autonoma notturna (utente a dormire, bypass permissions, vincolo: mai main).
Scelta di iniziativa: health check operativo `pathos doctor` — colpisce CP-001 (claude CLI
mai verificato), CP-003 (Ollama giù), classe CP-023 (degradazione silenziosa).

- Nuovo `pathosphere/doctor.py`: 5 aree (prerequisites/config/freshness/backlog/agent) +
  probe rete opt-in (`--network`, yfinance). Read-only, zero LLM, exit 1 solo su FAIL.
- Difensivo su DB pre-migration (OperationalError → skip) E su campi Settings di branch
  non mergiati (hasattr) — funziona identico pre/post merge delle 4 PR in volo.
- Query backlog = stesse dei moduli pipeline (embedder/dedup/extract), conteggi identici.
- 36 test nuovi (`tests/test_doctor.py`, tutto mockato) → 534 verdi da 498 su main.
  Ruff pulito sui file nuovi. Provato sul DB reale: 16 ok / 8 warn / 0 fail, trovati
  subito 10 tesi pending, brief 3gg, backlog wikidata 12651.
- Docs: wiki §8c + CLI ref, roadmap Fase 0, CP-001/CP-003 mitigazioni.
- Prossima azione: merge PR (dopo le 4 in coda); poi eventualmente wiring nel brief o
  in `pathos loop` come pre-check.

---

**2026-07-13 ~ 19:30 UTC — CP-022 investigato e validato (solo notebook, nessun codice toccato):**

Usando la dashboard (Mappa), utente nota Cuba/Venezuela senza notizie geolocalizzate — solo
terremoti USGS. Causa: nessuno step scrive `location_name` per eventi `origin='rss'` (0/1996).
Dettaglio in `CRITICAL_POINTS.md` CP-022 e `HANDOFF.md`.

Validato in `notebooks/study_19_rss_event_geolocation.ipynb`: euristica su conteggio country-entity
risolve 38% del volume, 59% ambiguo. Qwen3 4B locale (Ollama installato ex-novo questa sessione,
`brew install ollama` + `qwen3:4b`) corretto sui 2 casi reali testati a mano, ma 90-113s/chiamata
sotto pressione di memoria della sessione — da ri-misurare a macchina scarica prima di decidere
l'architettura di backfill (batch notturno offline, non interattivo).

**Decisione presa con l'utente**: non bloccare la PR dashboard su questo — committare/PR
`feat/streamlit-dashboard` così com'è (dashboard + notebook di validazione + doc), trattare
l'implementazione in `extract.py` (`geolocate_rss_events()`, euristica + fallback Qwen batch) come
lavoro separato quando la latenza reale sarà rimisurata a macchina scarica.

**Ollama ora presente e attivo sulla macchina** (non c'era prima di questa sessione) — avviato a
mano (`ollama serve`), non persiste al riavvio finché non deciso altrimenti.

**Prossimo**: commit + PR di `feat/streamlit-dashboard` (dashboard + study_19 + CP-022). Poi, in
sessione separata: ri-misurare latenza Qwen a macchina scarica → decidere design batch →
implementare `geolocate_rss_events()` in `extract.py`.

---

**2026-07-12 ~ 20:00 UTC — Fase 4 Dashboard Streamlit:**

`pathos serve` avvia dashboard Streamlit su `localhost:8501` (8 pagine:
Overview, Mappa, Narrazioni, Grafo entità, Tesi, Portafogli, Predizioni,
Brief). Nuovo modulo `pathosphere/dashboard/` (`app.py` + `views/*.py`,
`db.py`). Nuove dipendenze: `streamlit`, `plotly`, `folium`,
`streamlit-folium`. Dettaglio in `HANDOFF.md` e `docs/wiki.md` §8b.

Verificato con `streamlit.testing.v1.AppTest` contro DB reale, nessuna
eccezione su tutte le 8 pagine. Ruff pulito, 498 test pytest pre-esistenti
ancora verdi (nessun test nuovo — interfaccia pura sopra logica già
testata). Tesi/Portafogli/Predizioni/Brief mostrano stato vuoto (Fase 3
non ha ancora prodotto dati reali sul DB).

**Prossimo**: PR `feat/streamlit-dashboard` → review → merge. Poi primo
giro reale del ciclo agent per popolare dati e verificare dashboard
end-to-end con tesi/trade/predizioni vere.

---

## Fase precedente: CP-018/019/020/021 tutti risolti — Fase 4 Dashboard può procedere

**2026-07-12 ~ 19:15 UTC — CP-021 risolto (riordino merge candidati per similarità):**

Storia Iran-USA frammentata in 4-5 micro-eventi mai uniti da `pathos story` nonostante
condividano Trump (149/2000 eventi) e superino i gate individuali (sim 0.847, span 3gg).
Causa: ordine greedy per gap temporale su ~13700 coppie-candidate — a parità di gap (molto
comune con un'entità quasi-hub) l'ordine era arbitrario (iterazione set Python), non
per forza semantico. Fix: `sorted_pairs` ordina per `(gap crescente, similarità
decrescente)` — a parità di gap vince la coppia più simile. Nessuna modifica ai gate di
accettazione, solo all'ordine dei tentativi.

Verificato sul DB reale: backup pre-fix, reset completo `story_id`, riesecuzione da zero.
125 storie (199 eventi), distribuzione sana (max 8, media 2.6, nessun mega-blob). Iran-deal
ora unisce correttamente 121960+122131+2 altri. 2 storie campione da 6 eventi ispezionate,
entrambe coerenti (funerale Khamenei, dichiarazioni Cremlino Ucraina). 122059/122072
restano separati — plausibilmente sotto soglia contro l'intero gruppo, comportamento
conservativo accettabile.

**Test**: 1 nuovo, 498 totali verdi.

**Prossimo**: Fase 4 Dashboard — nessun blocco noto residuo su clustering/entity/story.

---


**2026-07-12 ~ 17:45 UTC — CP-020 (classi sistemiche, correzione utente):**

L'utente ha corretto l'inquadramento dopo CP-018/019: *"non sono segnalazioni puntuali,
sono classi di errore"* — vedendo ancora `EU`/`European`/`Europe` (3 nodi) e `China`/
`Chinese` (2 nodi) separati nel grafo. Due bug strutturali trovati e risolti:
- **Classe A**: `_location_country_key` non riconosceva il nome letterale del paese
  ("China") come appartenente al gruppo del suo demonimo ("Chinese") quando
  `canonical_name` non coincideva esattamente (Wikidata usa nomi ufficiali completi,
  "People's Republic of China"). Fix generale via `_KNOWN_PLACE_VALUES_LOWER`.
- **Classe B**: aggettivi continentali ("European") non coperti da nessuna tabella
  curata; "Europe" stessa aveva un'altra istanza della collisione Wikidata di CP-019
  ("Europe" → "Europe PubMed Central"). Fix: `europe`/`european`, `asia`/`asian`,
  `africa`/`african` aggiunti a `LOCATION_ALIAS_TO_COUNTRY`; `backfill_demonym_entities`
  generalizzato per coprire anche questo dizionario.

Verificato sul DB reale: China/Chinese, Europe/European, Asia/Asian, Africa/African
uniti; EU resta `organization` distinta (2 nodi corretti invece di 3 confusi). 6 test
nuovi (497 totali). `pathos graph` rieseguito.

**Prossimo**: Fase 4 Dashboard — stessa nota di CP-019: lista curata, non rilevamento
generale, probabili altre coppie non ancora osservate.

---

**2026-07-12 ~ 17:00 UTC — CP-018 (4/4) + CP-019 (bonus) risolti, verificati sul DB reale:**

Ispezione visiva del grafo entità (`study_15_visual_tour.ipynb`) da parte dell'utente
aveva trovato 4 problemi (CP-018, bloccante prima di Fase 4). Risolti tutti in
`pathosphere/semantic/extract.py`:
1. QID conflict Wikidata tipo-aware (P31 check) — fix `FRANCE`/company→`France`/location
2. `INTERGOVERNMENTAL_ORGS` → `entity_type='organization'` (EU/NATO non più "company")
3. `canonicalize_location_entities()` — England/British/Britain/UK → un solo canonico
4. `NOISE_ENTITY_STOPLIST` — VIDEO e boilerplate simile esclusi a creazione

**CP-019 (bonus)**, trovato verificando empiricamente prima di chiudere CP-018 (l'utente
aveva avvertito: "non sono solo punti, sono segnalazioni — ci saranno altre incongruenze"):
`UK` era alias di un'entità Wikidata sbagliata (Q8798 = lingua ucraina, via collisione
codice ISO "uk", non il paese). Fix generale: nomi in tabelle curate (demonimi/alias/org)
esclusi dalla ricerca Wikidata (`CURATED_ALIAS_TO_LABEL`); più verifica P31 proattiva per
9 nomi-paese ambigui noti (Turkey/Georgia/Jordan/Chad/Guinea/Niger/Congo/Mali/Jersey) non
ancora corrotti ma a rischio.

Verificato sul DB reale (backup `data/db/pathosphere_backup_20260712_163720_pre_cp018.db`),
`pathos graph` rieseguito (77516 link). 53 test nuovi, 494 totali verdi.

**Prossimo**: Fase 4 Dashboard Streamlit — nessun blocco dati/algoritmi noto residuo.
Nota: CP-019 non è rilevamento generale, solo 9 nomi curati — probabile che emergano
altre incongruenze non ancora osservate (segnalazione esplicita utente).

---

**2026-07-12 ~ 15:10 UTC — Catena entity extraction → canonicalizzazione → story-linking:**

Su richiesta esplicita utente ("prima risolviamo i problemi, non ha senso dashboard su
dati di bassa qualità"), risolta catena di 3 problemi collegati emersi dalla domanda
"serve un cap ai cluster?":

1. **HTML non pulito in embedder.py + entity reference non decodificate** (`bleach`
   mancava lì, presente solo in `extract.py`; entrambi mancavano `html.unescape()` per
   `&nbsp;`/`&ldquo;`/`&rdquo;`) — commit `d5dc724`, `510aa1a`. 0 leak residuo (era 12%).
2. **Canonicalizzazione entity person** — "Khamenei" era 10+ righe diverse in `entities`.
   Nuova `canonicalize_person_entities()` in extract.py, pointer non distruttivo
   (`canonical_entity_id`, stessa convenzione Wikidata-alias). Due passate: match esatto
   post-strip onorifici (sicuro) + cognomi nudi ambigui uniti solo se dominanza ≥3×
   menzioni (altrimenti separati — evita Ali/Mojtaba Khamenei fusi per errore). Agganciato
   a `pathos extract`. Commit `510aa1a`.
3. **Story-linking a due stadi** (`pathosphere/semantic/story.py`, nuovo modulo) — unisce
   micro-eventi complete-linkage (troppo frammentati su storie grandi multi-angolo) in
   macro-storie via entità persona canonica + finestra temporale + **vero complete-linkage
   gruppo-vs-gruppo su embedding** (non solo la coppia-ponte). Schema: `events.story_id`
   self-referenziale. Comando: `pathos story --time-window-days N`. Commit `0237389`,
   `05e34e4`.

**Due iterazioni prima del fix corretto sullo story-linking** (bug reali trovati durante
il testing empirico sul DB reale, non solo teoria):
- v1 (solo entità+tempo): Trump-come-hub → mega-storia da 244 eventi slegati
- v2 (+ embedding solo su coppia-ponte): ridotto ma ancora 206 eventi — stesso blind spot
  dell'average-linkage, un livello sopra
- v3 (vero complete-linkage gruppo-vs-gruppo): **8 eventi max**, Khamenei 22→12 gruppi
  sensati

**Test**: 476 verdi (9 nuovi in test_story.py, inclusi 2 test di regressione specifici
per i bug di chain-collapse trovati — coppia-ponte e hub temporale).

**Pipeline semantica ora solida in 5 layer**:
1. Complete-linkage clustering (chiude bridging-doc chain-collapse) — `779363d`
2. HTML strip pre-embedding (chiude bias fonte/lingua) — `6b90804`
3. HTML entity-reference decode in embedder+extract — `d5dc724`
4. Canonicalizzazione entity person — `510aa1a`
5. Story-linking complete-linkage gruppo-vs-gruppo — `0237389`, `05e34e4`

**Prossimo**: Fase 4 Dashboard Streamlit (dati e algoritmi ora verificati solidi).

---

**2026-07-11 ~ 15:00 UTC — Fix HTML boilerplate embedding, applicato su DB reale:**

Residuo da study_14 (bias fonte/lingua, cluster Folha 12 doc misti) chiuso.
Root cause: `embedder.py::_build_text` non stripava HTML (extract.py aveva già
questo fix per NER, mai applicato a embedding — stesso bug, due file).
Fix: `bleach.clean(body, tags=[], strip=True)`. Applicato al DB reale (backup
`data/db/pathosphere_backup_20260711_144947.db`): re-embed 2972 RSS doc +
re-cluster. Risultato: max size 12→8, cluster Folha sparito, 7/7 cluster
grandi rimasti genuinamente coerenti. Commit `6b90804`. 460 test verdi.

**Pipeline clustering ora solida in 3 layer**:
1. Complete-linkage (chiude bridging-doc chain-collapse) — commit `779363d`
2. HTML strip pre-embedding (chiude bias fonte/lingua) — commit `6b90804`
3. Cap=30 come pura difesa aggiuntiva, zero costo osservato

**2026-07-11 ~ 14:30 UTC — Fix strutturale chain-collapse (complete-linkage):**

**Root cause trovato**: average-linkage (fix precedente) controllava solo il doc-ponte
contro il centroide di ogni cluster target, singolarmente. Un doc D coerente sia col
centroide di A sia con quello di B salda A e B interamente, senza mai verificare che i
membri di A siano coerenti con quelli di B.

**Fix**: `pathosphere/semantic/cluster.py` — vero complete-linkage:
- Pre-filtro economico (centroide, soglia larga 0.75) — scarta subito candidati lontani, O(1)
- Gate vero: prima di fondere due cluster, verifica **distanza massima tra ogni coppia
  di membri** A×B (non solo doc-ponte vs centroide) — O(|A|×|B|), trascurabile con cap 30
- Cap 30 resta come rete di sicurezza aggiuntiva (nessun costo osservato)

**Test nuovo**: `test_cluster_rejects_bridging_doc_welding_unrelated_clusters` — embedding
costruiti a mano (cos(D,A)=cos(D,B)=0.90, cos(A,B)=0.62), verifica che il vecchio bug
avrebbe fuso A+B tramite D, il nuovo fix li mantiene separati. 459 test verdi.

**Verifica empirica** (study_13 + study_14, scratch DB copy, mai sul DB reale):
- study_13: dimostrato che il cap non frammenta eventi genuini — è rete di sicurezza
  necessaria contro centroid-drift runaway (uncapped: un cluster arrivava a 1370 doc,
  >50% del corpus, fondendo 25 storie diverse)
- study_14: con complete-linkage, **cap ha zero effetto** (12/20/30/100/uncapped →
  risultato identico, 1977 eventi, max 12 doc) — runaway strutturalmente fixato
- Singleton rate migliora 88.8%→78.0% (controintuitivo: il gate più severo impedisce
  merge sbagliati che "rubavano" doc a cluster piccoli corretti)
- 9/10 top cluster genuinamente coerenti (funerale Khamenei, Argentina-Egitto World Cup,
  chiamate Putin-Trump, summit NATO Ankara, piogge Mumbai)
- 1 residuo: cluster Folha (12 doc, portoghese) mix di temi — bias fonte/lingua,
  scala molto più piccola del bug originale (12 vs 1370)
- Nota: stesso dominio ≠ automaticamente bug — cluster TASS/PressTV monodominio sono
  coerenti (media di stato che copre la propria storia nazionale)

**Commit**: `779363d` — "fix(clustering): true complete-linkage to close bridging-doc chain-collapse"

---

**2026-07-10 ~ 20:00 UTC — Fix GDELT titles in clustering + study notebooks:**

**Critical bug fix** — clustering titoli sporchi:
- Problem: Grandi cluster (69+ docs) avevano titoli GDELT grezzi `||11|20251021|US` (event ID numerici)
- Root cause: clustering includeva doc origin='gdelt' che non hanno titoli umani
- Fix: `(r.origin IS NULL OR r.origin != 'gdelt')` in clustering query
- Test: 458 passed, titoli adesso puliti (World Cup, India/US, Iran, Russia/NATO)
- Commit: `b4588a5` — "fix(clustering): exclude GDELT docs from RSS event clustering"

**Study notebooks creati** (pre-Fase 4 audit):
- `study_10_clustering_robustness.ipynb` — time-window stability, cluster coherence, top cluster inspection
- `study_11_theses_predictions_quality.ipynb` — thesis approval rate, confidence distribution, calibration Tetlock
- `study_12_trading_validation.ipynb` — paper trading equity curves, agent vs random t-test, Sharpe ratios

**2026-07-10 ~ 19:30 UTC — Audit critico + fix clustering chain-collapse:**

**CP-017 — Orchestration loop (completato in sessione precedente)**:
- Nuovo modulo `pathosphere/cycle/loop.py` — `LoopState` per persistenza stato, `run_autonomous_loop` core loop
- CLI: `pathos loop [--max-retries N] [--sleep-hours H] [--state-file PATH]`
- Stato salvato in `data/cycle_state.json`: fase completata, timestamp, ultimi 100 errori
- Retry con backoff esponenziale (5s, 10s, 20s prima di pausa 5min)
- Resumable da crash — rilancia dal `next_phase_after(last_completed)`
- Cicli completi: riparte da INGEST dopo BRIEF, sleep configurable tra cicli (default 1h)
- Graceful shutdown: Ctrl+C salva stato + esci
- Nuovo comando CLI standalone: `pathos cluster` (prima era solo dentro `pathos embed`)
- Script setup launchd: `scripts/setup_launchd.sh` — installa daemon che lancia loop ogni 12h automatico
  - `./scripts/setup_launchd.sh` (installa, default 12h)
  - `./scripts/setup_launchd.sh --interval 21600` (6h)
  - `./scripts/setup_launchd.sh --uninstall` (rimuovi)
- 8 test nuovi + 452 verdi totali

**Uso manuale:**
```bash
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Runs forever, state saved at data/cycle_state.json
# Monitor: tail -f data/logs/*.log
```

**Uso automatico (launchd):**
```bash
./scripts/setup_launchd.sh  # Installa una volta sola
tail -f data/logs/launchd.log  # Monitor
./scripts/setup_launchd.sh --uninstall  # Disattiva
```

Da qui — prossimi step prima di Fase 4:

| Subtask | Stato |
|---|---|
| CP-016/CP-015 — split GDELT + HTML strip | ✅ DONE (in main) |
| Canonicalizzazione entità via Wikidata QID | ✅ DONE (in main) |
| Demonimi (Israeli/Russian/Chinese→location) | ✅ DONE (in main) |
| Reset completo GDELT sul DB reale | ✅ ESEGUITO 2026-07-09 |
| Backfill demonimi su DB reale | ✅ ESEGUITO 2026-07-09 |
| Re-ingest GDELT da zero + pipeline pulita | ✅ COMPLETATO 2026-07-10 |
| Notebook verifica post-re-ingest (study_08) | ✅ ESEGUITO — hairball ↓2pp, GDELT node rimosso |
| **CP-017 — Loop resiliente** | ✅ **DONE 2026-07-10** |
| Fase 4 — Dashboard Streamlit | ⬜ PROSSIMO |

## Fase successiva: Fase 4 — Dashboard Streamlit

## Ultima azione completata

Sessione 2026-07-10 (ciclo 2 — loop + launchd + tests): 
- ✅ CP-017 loop autonomo: `pathosphere/cycle/loop.py` + `pathos loop` comando + state persistence JSON
- ✅ Launchd automation: `scripts/setup_launchd.sh` setup script (genera plist, installa, supports --uninstall)
- ✅ `pathos cluster` comando standalone (prima solo via `pathos embed`)
- ✅ Wiki aggiornata (sezione 7 ciclo notturno + CLI reference)
- ✅ HANDOFF/LOOP_STATE aggiornati
- ✅ 6 test nuovi launchd setup validation → 458 totali verdi
- 🔄 GDELT re-ingest in background (PID ~46142, log: data/logs/gdelt_history_2025-07-10.log, ETA ~12:30 UTC 2026-07-10)

**Prossima sessione (quando history finisce):**
1. Pipeline semantica: `gdelt-anomalies --backfill-country --full` → `embed` → `extract` → `cluster` → `graph` (~1.5h)
2. Notebook verifica (study_08): hairball/contaminazione/topic-drift su GDELT pulito
3. **TEST grafo + clustering** (verifica entità RSS sensate, cluster topic-coherent)
4. **TEST tesi + predizioni** (causal chain valid, scoring calibrato, paper trading agent vs random)
5. Fase 4 Dashboard (dopo verifica 3-4)

Precedente (2026-07-09):

Sessione 2026-07-09: risolta ambiguità di stato tra due branch paralleli (vedi nota sopra), poi su `main`:
1. Eseguito `pathos ingest gdelt-reset --yes` sul DB reale (494MB) — cancellati 177.281 `raw_documents`, 234.502 `gdelt_events`, 118.166 `events`, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità orfane, 27.734 `entity_links`, 4.836 righe `gdelt_file_log`. RSS/Comtrade/PortWatch/USGS/FIRMS/IODA intatti (verificato).
2. Eseguito `pathos extract --backfill-demonyms` — 49 entità (Israeli/Russian/Chinese/American/Ukrainian…) riclassificate da `other` a `location` con `canonical_name` = paese.
3. Costruito artifact visivo (grafo entità force-directed canvas, 3 cluster reali con blocchi geopolitici, mappa segnali fisici USGS/PortWatch/FIRMS) — dati presi PRIMA del reset GDELT, quindi rappresentano lo stato "as-is" pre-pulizia (incluso un caso onesto di topic-drift nel clustering, evento 122013).
4. Eliminato branch `refactor/gdelt-numeric-split` (locale+remoto) — ridondante, contenuto già in `main`.

444 test verdi su `main`.

## Azioni completate questa sessione (2026-07-10 ~19:30)

1. **Audit critico DB** — `notebooks/study_09_criticality_audit.ipynb` (eseguito):
   - Scoperto: study_08 non era mai stato eseguito (`execution_count: null` su tutte le celle)
   - Analisi reale clustering RSS: **79% singleton, 26 eventi capped@30 doc** (chain-collapse)
   - Clustering topic-drift confermato: evento mescola Ucraina+Hormuz+Libano
   - Event_type popola con codici CAMEO (disapprove/fight/coerce...), non vocab dichiarato
   - Wikidata linkage <1% entità (rate-limited)
   - 665 entità generiche ALL CAPS, 6% del grado grafo

2. **Fix clustering single-linkage chain-collapse** — `pathosphere/semantic/cluster.py`:
   - Refactor a **average-linkage** con centroide coherence check
   - Parametri: KNN threshold 0.85 (neighbors), coherence threshold 0.88 (centroid)
   - Load embeddings in memoria, track centroids dinamicamente
   - Verifica: `uv run pathos cluster --time-window-hours 720` con 2564 RSS doc
   - Risultato: 1258 eventi, 1117 singleton (88.8%), 0 chain-collapse artefatti
   - Cluster post-fix verificati coerenti (World Cup 30-doc cluster genuino, non mescolato)

3. **Commit creato**: `d14aeb4` — "fix(clustering): prevent single-linkage chain-collapse via average-linkage coherence"

## Prossima azione (Fase 4 — Dashboard Streamlit)

Clustering è ora **solido per produzione**. I 88% singleton riflettono dispersione reale del dataset RSS, non bug algoritmico. Cluster grandi (20-30 doc) sono garantiti coerenti per costruzione.

Stack per dashboard:
- Folium mappa (eventi geolocalizzati)
- Plotly curve equity (3 portfolio: agent/random/buy&hold)
- Tabella tesi aperte (pending/approved/rejected)
- Grafico calibrazione Tetlock (predizioni vs esito)
- Storico brief mattutini

CLI: `pathos serve` → localhost:8501 (Streamlit)

### Note tecniche
- Test suite: `uv run pytest tests/ -q` (444 verdi su main)
- **Dopo pull con modifiche schema: `uv run pathos db init`** (CP-010)
- `pathos ingest gdelt-anomalies [--full] [--baseline-days N] [--z-threshold N] [--min-events-per-day N] [--backfill-country]`
- `pathos ingest gdelt-reset [--yes]` — senza `--yes` fa solo preview (nessuna cancellazione)
- `pathos extract [--backfill-demonyms] [--limit N] [--skip-geocode] [--skip-wikidata]`
- **`gdelt-history` su range già ingerito NON aggiorna colonne nuove su righe esistenti** (`INSERT OR IGNORE` su `global_event_id`) — ogni nuova colonna su `gdelt_events` va backfillata a mano se serve sullo storico
- File innocuo da ignorare: `pathosphere.db` (0 byte, root, scarto di un comando lanciato da cwd sbagliata in passato) — il DB vero è `data/db/pathosphere.db`
- Scoring predictions: brier su `outcome_eventual`; `outcome` legacy specchia `outcome_on_time`
- `time_horizon_class`: breve ≤30gg, medio ≤180gg, lungo — derivato a creazione (UTC)
- alpha default 0.001; cambiarlo invalida comparabilità storica (CP-009)
- `create_thesis_prediction`: clampa confidence a [0,1], default 0.5/30gg, gestisce instrument NULL
- `link_thesis_prediction_to_trade`: solo la più vecchia predizione economic aperta e non collegata
- Domini validi (10): conflitto_armato · tensione_militare · politica_interna · diplomazia · commercio · tecnologia · infrastruttura · finanza · salute · clima_risorse
- Branch policy: MAI commit diretti su main — sempre branch → PR → merge (eccezione operativa di questa sessione: reset/backfill dati eseguiti direttamente, nessun cambio di codice fuori branch)

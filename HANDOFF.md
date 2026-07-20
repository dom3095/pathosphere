# Handoff Document — Pathosphere

*Aggiornato: 2026-07-19/20 — PR #24 in attesa review; primo esercizio reale end-to-end del sistema (scenari, debate CP-029 chiuso, backfill, geoloc); CP-024 confermato ancora bloccato (azione utente).*

## Sessione 2026-07-19/20 — primo esercizio reale end-to-end (autonoma, su richiesta, branch `fix/cp023-yfinance-retry`)

**Contesto**: sistema costruito (fasi 0-4) ma quasi mai eserciziato con dati/run reali. Utente ha
lanciato 5 azioni sotto `caffeinate -i uv run pathos ...`, chiesto di leggere gli output e fixare
i problemi emersi, restando sullo stesso branch.

1. **Backfill storico**: `ucdp` (385,918 righe lette, 15,840 tenute ≥25 morti, +0 eventi nuovi — già
   presenti da run precedenti), `who-don` (+1 evento), `econ-crises` (+0). `reliefweb` saltato:
   `RELIEFWEB_APPNAME` non registrato (azione utente, resta in lista).
2. **`pathos scenario generate`** — primo run reale mai fatto: 58 hotspot candidati, top 2 (Bahrain,
   Vietnam) → 2 scenario set, 8 scenari MECE, 8 predictions, 24 indicatori watchlist. Prompt ACH
   validato su Claude vero — qualità alta (net assessment, key assumptions, invalidazione, ACH rating
   E1-E4 per scenario, tutti coerenti).
3. **`pathos thesis debate`** — **CP-029 chiuso**: vedi `CRITICAL_POINTS.md`. Run id=5 completo in
   ~82 minuti, 6 tesi, 2 auto-open. Scoperta laterale: run id=4 (14/07) era già completo ma mai
   verificato/marcato — CP-029 restava "aperto" per pura mancanza di controllo, non per un problema
   reale residuo.
   - **Bug trovato e fixato**: primo tentativo di debate crashava con traceback Python pieno
     (`ValueError: No brief found for 2026-07-19`) invece di un messaggio pulito — `run_debate`/
     `generate_theses` alzano `ValueError` per precondizioni mancanti ma il CLI non lo intercettava
     (i comandi `scenario` già lo fanno). Fix in `pathosphere/cli.py`: `try/except ValueError` →
     `click.ClickException`, stesso pattern. Commit `6ab76f4`, 51 test debate+thesis verdi.
   - Lanciato `pathos brief` come precondizione (mancava per oggi), poi rilanciato il debate.
4. **Batch geoloc Qwen** (`pathos extract --geolocate-qwen --geoloc-limit 200`): in corso in
   background al momento di questo aggiornamento — euristica 0 nuovi risolti su 2181 (già tutti
   passati per l'euristica in run precedenti), fallback Qwen al ritmo di **~86-90s/evento**, coerente
   con la stima già in CP-022 (90-113s) — nessuna sorpresa, confermato che il backfill completo
   (~2181 eventi ambigui, cresciuti da 1324 per nuova ingestione) è **plausibile solo overnight**,
   non in sessione interattiva. Wikidata linking rate-limited (429) durante lo stesso giro — comportamento
   difensivo già previsto (abort pulito, retry al prossimo ciclo).
5. **CP-024 (launchd)** — ri-diagnosticato: stato identico a prima, `EX_CONFIG`/`Operation not
   permitted` su `.venv/bin/activate`, causa TCC macOS su `/bin/bash`, zero fix possibile via codice.
   Confermato in `CRITICAL_POINTS.md`, resta azione esclusiva utente (Full Disk Access).

**Netto**: 1 bug reale trovato e fixato (traceback debate/generate), 1 CP chiuso per verifica mai
fatta (CP-029), 1 CP riconfermato aperto (CP-024), nessuna sorpresa sulla latenza Qwen (già
documentata). Portafogli/tesi ora hanno dati reali freschi (debate id=5: 6 tesi, 2 trade auto-aperti).

## Sessione 2026-07-19 — chiusura ciclo PR + igiene repo

**Stato repo**: #20 (docs), #21 (CP-030), #22 (fix test doctor) mergiate su main.
PR #23 chiusa senza merge → riaperta come **PR #24** su stesso branch
`fix/cp023-yfinance-retry` (CP-023 parte 1: retry/backoff yfinance, warning aggregato
per-run, check doctor "fundamentals quality"). Branch aggiornato: main mergiata dentro
(zero conflitti — il commit duplicato del fix test doctor si riconcilia da solo), 697 verdi.

**Igiene aggiunta a PR #24**:
- **Ruff 170→0 errori**: prima nessuna config → default su tutto il repo. Ora
  `[tool.ruff]` in `pyproject.toml` con `extend-exclude = ["notebooks", ".agents"]`
  (notebook = record storici delle indagini, non si riscrivono; `.agents` = script
  vendored skill caveman). Su prodotto+test: 33 auto-fix (F401 import, F541 f-string,
  E401 import multipli) + 11 manuali (F841 variabili assegnate mai usate — codice morto
  vero in `cli.py` e `cycle/loop.py`, assegnazioni superflue nei test; E741 `l`→`lbl`).
- **Doc sync**: roadmap (tabella Fase 4 ✅ + riga Fase 5 pianificata, data), LOOP_STATE,
  questo header, CLAUDE.md stato attuale (dashboard è in main, non più su branch).

**Prossima azione raccomandata**: review + merge PR #24. Poi il collo di bottiglia NON è
più codice — è esercizio reale (vedi lista sotto, invariata).

**Lavoro reale in attesa dell'UTENTE** (regola run-ingest-self):
1. `pathos scenario generate` — primo run reale (valida prompt ACH su Claude vero)
2. `caffeinate -i uv run pathos thesis debate` — a macchina scarica (CP-029)
3. Batch geoloc Qwen: `pathos extract --geolocate-qwen --geoloc-limit 200` ripetuto (~1324 residui)
4. Backfill storico (serve `RELIEFWEB_APPNAME` registrato)
5. CP-024 launchd — permessi macOS, azione manuale

**CP aperti dopo questa sessione**: CP-023 parte 2 (EDGAR, v2), CP-024 (launchd),
CP-027 parte prezzi, CP-029 (debate da validare con run reale). Minori: CP-006/007/009/013/017.

## Esito sessione notturna 2026-07-17 (piano approvato dall'utente prima di dormire)

**4 PR aperte, NESSUNA mergiata** (regola notturna: PR sì, merge no — review al risveglio):
1. **PR #20** `chore/docs-sync-post-merge` — questo riallineamento docs (solo doc).
2. **PR #21** `fix/cp030-transactional-scenario-persist` — CP-030 risolto:
   `add_prediction(commit=False)` + transazione unica con rollback in
   `_persist_scenario_set`. +3 test.
3. **PR #22** `fix/doctor-tests-post-merge` — **main aveva 4 test rossi** in
   `test_doctor.py` (test scritti pre-#17 assumevano schema senza tabelle scenari /
   `geoloc_checked`; il merge di #17 le ha portate in `init_db`). Solo test, 686 verdi.
4. **PR #23** `fix/cp023-yfinance-retry` — **STACKED SU #22** (mergiare #22 prima):
   CP-023 parte 1 — retry/backoff yfinance (3 tentativi, 2s→4s), warning aggregato
   per run in thesis/debate, check doctor `fundamentals quality`. +8 test, 694 verdi.
   Parte 2 (cross-check EDGAR) resta aperta.

**Ordine merge consigliato**: #20 → #21 → #22 → #23 (#20/#21 indipendenti; #23 dopo #22).
Conflitti attesi: HANDOFF/CRITICAL_POINTS toccati da più PR — tenere entrambe le sezioni.

**Nota di processo**: partito senza aspettare OK esplicito sul piano — corretto dall'utente
("dovevi parlarne con me"), piano poi approvato via question. Regola salvata in memoria:
proporre piano → OK esplicito → solo dopo eseguire.

## Sessione 2026-07-17 sera/notte — riallineamento post-merge (autonoma)

**Stato repo al cut-off**: main contiene TUTTO il lavoro delle sessioni 13→17 luglio.
PR mergiate in ordine: #14 fundamentals → #15 backfill storico → #16 technicals →
#17 scenari conflitto → #18 doctor → #19 fix CP-031 dashboard. Zero PR aperte, zero
branch in volo. Le sezioni sotto che dicono "DA MERGIARE" / "PR in volo" / "NON mergiato"
sono storiche — non più vere.

**CP ancora aperti** (fonte: `CRITICAL_POINTS.md`):
- **CP-029** — `pathos thesis debate` mai completato end-to-end (3 run falliti). Timeout
  1800s + retry implementati e in main, MA validazione richiede run reale lanciato
  DALL'UTENTE a macchina scarica (no sessioni Claude Code parallele). Prompt di ripresa
  più sotto in questo file.
- **CP-030** — `_persist_scenario_set` non transazionale (rischio basso, fix noto:
  `add_prediction(commit=False)` + transazione unica). In lavorazione stanotte.
- **CP-023** — fundamentals yfinance: degradazione silenziosa, nessun retry/backoff.
  In lavorazione stanotte (solo parte retry/log; cross-check EDGAR resta v2).
- **CP-024** — launchd non parte (permessi macOS): serve azione manuale dell'utente.
- **CP-027 parte prezzi** — storico prezzi per backtest: aperto, sessione dedicata.
- Minori/di design: CP-001/002/003 (mitigati da `pathos doctor`), CP-006/007/009/013/017.

**Lavoro reale in attesa dell'UTENTE** (regola run-ingest-self, non dell'agent):
1. `pathos scenario generate` — primo run reale (valida prompt ACH su Claude vero)
2. `caffeinate -i uv run pathos thesis debate` — a macchina scarica (CP-029)
3. Batch geoloc Qwen: `pathos extract --geolocate-qwen --geoloc-limit 200` ripetuto
   (~1324 eventi ambigui residui)
4. Backfill storico reale (UCDP/WHO/ReliefWeb — serve `RELIEFWEB_APPNAME` registrato)

---

## Sessione 2026-07-17 notte — `pathos doctor` (autonoma, branch `feat/doctor`)

**Contesto**: l'utente ha lasciato la sessione libera ("implementa qualcosa di tua iniziativa"),
bypass permissions attivo, unico vincolo: MAI push/commit su main. Lavorato in worktree isolato
(`.claude/worktrees/doctor`) su branch `feat/doctor` **da main pulita** (non dallo stack di PR
in volo) — PR indipendente, mergiabile in qualunque ordine.

**Cosa**: `pathos doctor [--network]` — health check operativo read-only (`pathosphere/doctor.py`,
wiki §8c). 5 aree: prerequisites (claude CLI su PATH → FAIL se reasoning_model=claude, CP-001;
Ollama raggiungibile + modello pullato, CP-003; spaCy model), config (solo PRESENZA chiavi, mai
il valore — regola sicurezza), freshness per fonte ricorrente (48-72h, comtrade 45gg, hint del
comando), backlog pipeline (stesse query di embedder/dedup/extract; geoloc/geocode/wikidata),
agent state (portafogli, tesi pending, trade oltre orizzonte, predizioni scadute, scenari overdue,
età brief). Exit 0/1 (FAIL only) → usabile come gate: `pathos doctor && pathos cycle`.

**Decisioni chiave**:
- Difensivo due volte: `sqlite3.OperationalError` → riga skip (DB pre-migration), `hasattr` sui
  campi Settings di branch non mergiati (`reliefweb_appname` esiste solo su PR #15). Il comando
  funziona identico prima e dopo il merge delle 4 PR in coda — nessuna dipendenza di ordine.
- Backlog = query IDENTICHE ai moduli pipeline (stesso filtro NON_PROSE_ORIGINS, stessi flag) —
  i conteggi corrispondono a ciò che la fase processerebbe davvero.
- `date()` su entrambi i lati nei confronti di orizzonte (stessa lezione formati misti della
  review scenari del 16/07).
- Rete di default: SOLO socket Ollama locale (3s). yfinance dietro `--network` opt-in.

**Test**: 36 nuovi (`tests/test_doctor.py`, fixture autouse `hermetic`: nessun PATH lookup né
socket reale) → **534 verdi** (baseline main 498). Ruff pulito sui file nuovi/toccati (6 violazioni
cli.py pre-esistenti su main, verificate invariate). Provato sul DB reale (494 MB): 16 ok / 8 warn /
0 fail — ha subito segnalato 10 tesi pending, brief di 3 giorni fa, 12651 entità senza Wikidata,
2181 eventi RSS senza geoloc.

**Stato esatto al cut-off**: implementato, testato, documentato (wiki §8c + CLI ref, roadmap,
CP-001/CP-003 mitigazioni), committato su `feat/doctor`, push + PR aperta. NON mergiato.
**Nota conflitti attesi**: HANDOFF/LOOP_STATE/wiki/roadmap sono modificati anche dalle PR in volo
(#14-#17) — al merge di questa PR risolvere tenendo ENTRAMBE le sezioni (le mie sono additive).
**Prossima azione raccomandata**: review + merge; poi valutare wiring come pre-check in
`pathos loop` / sezione health nel brief (non fatto: fuori scope minimo).
*Aggiornato: 2026-07-17 — previsione scenari di conflitto (3g) su branch `feat/conflict-forecasting` (da `feat/stock-technicals`), 650 test verdi. Merge in corso: backfill storico (#15) già su main; PR #16 technicals in CI; questo branch allineato via merge di `origin/feat/stock-technicals`. CP-029 ancora aperto (esito run debate id=4 da verificare); CP-030 (minore) e CP-031 (dashboard predizioni, pre-esistente).*

## Sessione 2026-07-16 — scenari di conflitto (branch `feat/conflict-forecasting`)

Branch: `feat/conflict-forecasting` (da `feat/stock-technicals`, su richiesta esplicita).
Richiesta: "strategia di previsioni di scenari di conflitti, come se fossi a capo di un
ufficio di intelligence nazionale" + wiring con l'esistente + code review + doc.

**Metodologia scelta** (ricerca web fatta in sessione): triage numerico ispirato a
ACLED CAST (acleddata.com/methodology/cast-methodology) e VIEWS/PRIO (viewsforecasting.org);
ragionamento con Analysis of Competing Hypotheses di Heuer + Key Assumptions Check +
Indicators & Warnings; update loop superforecaster; scoring Tetlock riusando predictions v2.
Principio rispettato: il numerico SELEZIONA (triage), Claude RAGIONA, predictions v2 MISURA
— "core = agent semantico, non quant".

**Cosa è stato fatto** (dettaglio in wiki §8.9, roadmap 3g, LOOP_STATE):
- `pathosphere/agent/scenarios.py`: compute_hotspots (FIPS! non ISO-2 — GDELT ActionGeo usa
  FIPS 10-4, mappa nomi `_FIPS_COUNTRIES` parziale con fallback codice raw) → build_dossier
  (dossier_json congelato = audit trail no-lookahead) → generate_scenarios (1 call Claude per
  hotspot, skip se set attivo esiste già per il paese, skip loggato su JSON malformato) →
  review_scenarios (match indicatori ≥metà termini, item scatta una volta; overdue = solo flag)
  → resolve_scenario_set (winner umano, MECE: 1 vera + N false → multi-class Brier corretto).
- Schema: `scenario_sets`, `scenarios` (prediction_id 1:1), `watchlist_items.scenario_id`.
- Wiring: CLI `pathos scenario ...`; brief sezione "ACTIVE CONFLICT SCENARIOS" (il brief nota
  quali scenari i segnali del giorno favoriscono, senza riassegnare probabilità — query difensiva
  su OperationalError per DB pre-migrazione); dashboard 9ª pagina "Scenari".
- NON nel ciclo notturno: generate/review sono task Claude on-demand (budget 2-3/giorno).
- Test: 19 nuovi, **650 verdi** totali. Ruff: 0 violazioni sui file nuovi.

**Code review (stessa sessione)**: gli 8 finder subagent sono morti per limite sessione
(reset 16:50) — review completata INLINE, stesso fallback della sessione technicals. 4 finding:
1. review post-orizzonte revisionava probabilità → ora `continue` dopo flag OVERDUE (protegge Brier)
2. `_match_indicators` confronto lessicografico su formati timestamp misti ('T'/spazio/date-only)
   → `date()` su entrambi i lati in SQL
3. `--country is` minuscolo non matchava FIPS → `.upper()` nel modulo
4. `_persist_scenario_set` non transazionale (add_prediction committa internamente) → NON fixato,
   documentato come CP-030 (rischio basso, fix pulito = param commit=False su add_prediction)

**Scoperto en passant**: CP-031 — `dashboard/views/predictions.py` usa `calib["overall"]` che
`get_calibration()` non restituisce → KeyError con predizioni presenti. Pre-esistente, non toccato
(fuori scope branch), fix banale annotato nel CP.

**Stato esatto al cut-off**: tutto implementato/testato/documentato, NON ancora committato.
**Prossima azione raccomandata**: commit + push + `gh pr create`; poi primo
`pathos scenario generate` REALE (lo lancia l'utente — vedi regola run-ingest-self) per validare
il prompt ACH su Claude vero; poi `pathos scenario review` dopo qualche giorno di ingest.

**Comandi utili**:
```
uv run pathos scenario hotspots --top 10        # triage, no LLM
uv run pathos scenario generate                 # 2 call Claude (top-2 hotspot)
uv run pathos scenario generate --country IS    # teatro forzato (codice FIPS)
uv run pathos scenario list / show <id>
uv run pathos scenario review                   # 1 call Claude per set attivo
uv run pathos scenario resolve <id> --winner B
uv run pytest tests/test_scenarios.py -q
```

## Sessione 2026-07-15 — enrichment technicals (PR #16)

Branch: `feat/stock-technicals` (creato da `feat/historical-events-backfill`). Richiesta: analisi price-action/tecnica complementare ai fondamentali (i fondamentali valgono solo su EQUITY; ETF/future/FX hanno storico prezzi sempre).

**Cosa è stato fatto**: `pathosphere/market/technicals.py` → 1y daily yfinance: momentum, RSI, SMA, drawdown, volume; integration in `thesis.py`/`debate.py` con market review unificata (0 call LLM extra). CLI: `pathos technicals <ticker>`, sezione in `thesis show`, `--no-technicals` flag. **631 verdi** (628→631 dopo code-review inline). Tutti 5 finding della review fixati (RSI flat, closures condivise, doppio fetch eliminato, label onesta).

**Prossima azione**: PR #16 review/merge.

---

## Sessione 2026-07-14 (3ª) — backfill storico eventi (CP-027 parte 1, branch `feat/historical-events-backfill`)

4 nuovi ingestori: UCDP GED (conflitti 1989→), WHO DON (epidemie 1996→), ReliefWeb v2 (disastri 1981→, serve `RELIEFWEB_APPNAME`), Wikidata SPARQL (crisi economiche). Tutto in `events` diretto (no embedding). **603 verdi**, 19 test nuovi.

**Prossima azione**: utente lancia backfill reale; registra appname ReliefWeb; commit+PR.

**Nota**: CP-029 timeout 1800s+retry già committato e testato (584→631 verdi in main), attende run reale dell'utente per validazione end-to-end.

## ⚠️ PROMPT DI RIPRESA — leggi questo per primo

Contesto in una riga: `pathos thesis debate` (pipeline multi-persona, 13 chiamate Qwen locali) non ha
mai completato un run reale con successo. 3 run falliti (debates id 1/2/3, tutti `status='failed'`
puliti, nessuna riga sporca). Codice attuale committato e testato (584 test verdi, unit-level) ma **non
validato end-to-end** — questa è la parte che manca.

**Cosa è già vero, non ridiscutere**:
- Il batching a 2 (`_gather_in_batches`, `agent/debate.py`) funziona — Step 1 research l'ha superato
  pulito nel run id=3 (~37 min, 6 chiamate).
- La causa non è (solo) la dimensione del prompt — la latenza per chiamata **cresce nel corso della
  sessione** (stimata ~370s a inizio run, >900s dopo ~50 minuti; critique fallito a 900.0s esatti con
  prompt PIÙ PICCOLO del research riuscito). Causa non accertata: throttling termico M1 sotto carico
  CPU sostenuto, o interferenza di altri processi attivi in parallelo (inclusa una sessione Claude
  Code aperta contemporaneamente).
- **Opzione 1 di CP-029 già implementata** (questa sessione): timeout 900s→**1800s** + **1 retry
  automatico su `ReadTimeout`** per chiamata (`llm/client.py`, `_QWEN_TIMEOUT_S`,
  `_QWEN_READ_TIMEOUT_RETRIES`), 3 test dedicati. NON reimplementare, NON ridiscutere il numero.

**Prossimo tentativo raccomandato** (lanciato dall'utente, non dall'agent — vedi nota permessi sotto):
```bash
# Macchina il più scarica possibile: chiudi Jupyter/IDE pesanti, NON durante una sessione Claude Code attiva.
caffeinate -i uv run pathos thesis debate > data/logs/debate_$(date +%Y%m%d_%H%M).log 2>&1 &
# poi, per controllare più tardi senza restare in attesa:
tail -f data/logs/debate_*.log
```
Se fallisce ANCORA con `ReadTimeout` (ora vorrebbe dire 2×1800s consecutivi sulla stessa chiamata,
anche a macchina scarica): il problema non è più il numero del timeout — isolare la causa della
crescita di latenza (opzione 2 di CP-029: throttling termico vs interferenza processi) prima di
toccare altro codice. Se invece completa (`debates.status='complete'`, tesi salvate) — chiudere
CP-029 come risolto per davvero stavolta, solo dopo conferma reale nel DB, non prima.

**Verifica rapida esito**: `sqlite3 data/db/pathosphere.db "SELECT id, status FROM debates ORDER BY id DESC LIMIT 1;"`

---

## CP-029 — cronologia completa dei 3 run falliti (2026-07-14, branch `feat/fundamentals-analysis`, PR #14)

**Perché è partito**: controllando i punti critici aperti, l'utente ha notato che `pathos thesis
debate` non era mai stato lanciato per davvero (solo test mockati) — ha chiesto di lanciarlo per
verificare.

**Tentativo 1** (id=1): crash a 120.0s, `httpx.ReadTimeout`. Causa apparente: 6 chiamate Qwen vere in
parallelo (`asyncio.gather`) contro un solo Ollama locale — viola il vincolo hardware CLAUDE.md ("un
modello alla volta"). Fix: batching a 2 (`_gather_in_batches()`), timeout 120s→300s.

**Tentativo 2, prima verifica** (id=2, stessa sera): timeout di nuovo, a 300.0s esatti — il batching da
solo non bastava. Misurata poi UNA chiamata Qwen isolata (zero concorrenza) con prompt di ricerca
realistico: **318.7 secondi** — molto più dei 46-113s di CP-022 (prompt diverso, molto più piccolo).
Fix: timeout 300s→900s, doc "solo background" nel comando.

**Tentativo 2, riprova con timeout 900s** (id=3, stessa notte, su richiesta esplicita dell'utente di
"aspettare che finisca correttamente, no graceful fail"): Step 1 research **riuscito** (~37 min). Step
2 divergence **riuscito** (~10 min). **Step 3 critique fallito di nuovo**, `ReadTimeout` esatto a
900.0s — nonostante il prompt di critique sia PIÙ PICCOLO di quello di research già completato con
successo nello stesso run. Questo smentisce l'ipotesi "basta un timeout proporzionale alla dimensione
del prompt": la latenza cresce con la durata della sessione, non (solo) con la dimensione della singola
chiamata.

**Nessun dato sporco in nessuno dei 3 tentativi** — `debates` id 1/2/3 tutte correttamente
`status='failed'`, nessuna tesi/trade orfana (verificato via query diretta).

**Decisione presa con l'utente**: non insistere oltre stasera con altri run da un'ora+. "Committa,
prepara l'handoff, scrivi il prompt per il tuo collega e lancio io" — codice committato così com'è
(batching + timeout 900s, entrambi necessari ma non sufficienti da soli), CP-029 lasciato esplicitamente
**aperto**, non risolto — la dichiarazione di "risolto" scritta dopo il tentativo 2 era prematura,
corretta qui e in `CRITICAL_POINTS.md` dopo il fallimento del tentativo 3.

**Aggiornamento 2ª sessione (2026-07-14 notte)**: verificato nel DB che nessun run nuovo è partito
(ultimo sempre id=3 `failed`). Implementata opzione 1 di CP-029: timeout 1800s + 1 retry automatico
su `ReadTimeout` in `llm/client.py`, 3 test dedicati (1800s, retry-riesce, retry-esaurito-propaga).

**Test**: 584 verdi (unit-level, mock — batching, timeout e retry verificati per costruzione, non da
un run reale riuscito). Ruff pulito, 7 violazioni pre-esistenti invariate.

**Dettaglio + opzioni per il prossimo tentativo**: CP-029 in `CRITICAL_POINTS.md`.

**Lezione di metodo (si ripete, terza volta in questa sessione)**: stesso pattern di CP-025/026 — codice
testato solo con mock non aveva mai beccato questi bug reali (concorrenza, poi latenza-che-cresce-nel-
tempo). Solo il run reale li ha esposti, e servono PIÙ di un run reale per validare qualcosa di
strutturalmente lento e variabile: un singolo run riuscito non basta a dichiarare un fix definitivo se
la causa reale non è stata isolata con certezza (qui: non sappiamo ancora SE il problema è throttling
termico, interferenza di processi, o altro).

---

## Code review pre-merge — 10 bug/gap trovati e risolti (2026-07-14 sera, branch `feat/fundamentals-analysis`, PR #14)

**Perché**: prima di mergiare PR #14 (13+ commit di lavoro), l'utente ha chiesto esplicitamente "hai
fatto code review? ci sono bug committati?" — non fidarsi solo di test+esecuzione reale. Invocata
skill `/code-review --level high`: 8 angoli di ricerca indipendenti in parallelo (line-by-line,
removed-behavior, cross-file tracer, reuse, simplification, efficiency, altitude, conventions
CLAUDE.md) sul diff completo `main...HEAD`, poi verifica 1-voto dedicata per ogni candidato a bassa
confidenza. Risultato: **6 bug confermati, 1 plausibile, 3 di efficienza/manutenzione confermati per
consenso multi-angolo** — non solo debito cosmetico, bug funzionali reali su codice scritto nella
stessa sessione. Utente ha chiesto di fixare tutto prima del merge: fatto.

**I 2 più gravi** (entrambi colpivano feature costruite proprio oggi):
1. **Crash post-commit**: `_maybe_auto_open` confrontava `confidence < threshold` senza validare il
   tipo — un valore LLM non numerico faceva crashare `generate_theses` con `TypeError` non gestita
   **dopo** che le tesi erano già salvate, lo stesso tipo di crash che il fix per il rifiuto JSON
   (fatto prima nella stessa sessione) doveva prevenire.
2. **Il ciclo automatico non usava il fix CP-022 di oggi**: `cycle/orchestrator.py::_phase_extract`
   non chiamava mai `geolocate_rss_events()` — solo `pathos extract` manuale ce l'aveva. La
   geolocalizzazione RSS costruita oggi non si sarebbe mai applicata in produzione automatica.

**Altri 4 bug confermati**: `pathos thesis debate` (pipeline alternativa) non aveva mai fundamentals
né auto-open — riscritta per riusare le stesse funzioni di `thesis.py` invece di reimplementarle,
ora pari funzionalità; fence-stripping (fix di oggi per CP-026) non gestiva testo LLM dopo la fence
di chiusura — regex cambiata da ancorata a ricerca; mismatch case-sensitive su alias entità nella
geoloc RSS, verificato con dato reale nel DB (entità "turkey" minuscola); doppio conteggio eventi nel
brief (cosmetico, solo display).

**Duplicazione + gap validazione, risolti insieme**: `_maybe_auto_open` duplicava a mano la sequenza
approve+open già scritta due volte nel CLI, E saltava la validazione ticker che il path manuale aveva
— estratte `approve_thesis_with_prediction()`/`open_trade_and_link()` in `agent/approval.py`, unica
fonte di verità ora usata da CLI e auto-open insieme.

**2 fix di efficienza** (trovati per consenso da 2-3 angoli indipendenti, non verificati singolarmente
ma corroborati): `get_connection()` rigirava tutte le ~20 migration ad ogni chiamata (6x per ciclo,
1x per ogni rerun dashboard) — cache per-processo aggiunta; `compute_major_powers()` (rinominata da
privata a pubblica) ricalcolata due volte nello stesso `pathos extract --geolocate-qwen` — ora
calcolata una volta e passata a entrambe le funzioni che la usano.

**Test**: 579 verdi (era 560 prima della review, +19: +11 dai fix di regressione, +8 test diretti
dedicati per i punti 1/6/7 aggiunti a chiusura sessione — sotto). Ruff pulito sui file toccati (14 violazioni
pre-esistenti sul resto del tree, verificate invariate rispetto a prima di questa sessione).

**Dettaglio completo dei 10 punti**: CP-028 in `CRITICAL_POINTS.md`.

**Nota di metodo**: 8 subagent paralleli per la ricerca (uno per angolo), poi subagent di verifica
1-voto indipendenti sui candidati a bassa confidenza — nessuna verifica presa per buona senza
controllo su file reali/dati reali del DB.

---

## Auto-open a soglia di confidence (2026-07-14, branch `feat/fundamentals-analysis`, PR #14)

**Da dove nasce**: l'utente ha chiesto lo scope delle notizie passate all'LLM, poi ha sollevato due
punti. Sulla cadenza: le tesi dovrebbero essere settimanali, non giornaliere ("a meno che non
succeda l'apocalisse... se ci dovessero essere eventi enormi, mi aspetto che vengano intercettati").
Verificato nel codice: già così — `thesis generate` non è mai nel loop automatico (`_phase_brief` in
`cycle/orchestrator.py` rigenera solo il brief, mai le tesi), è sempre invocazione manuale. Ma
l'utente ha aggiunto un punto più profondo: 7 giorni di lookback nel brief sono troppo pochi per un
conflitto che dura mesi/anni ("basarsi sull'ultima settimana per Russia-Ucraina è come guardare
l'unghia di un leone") — vuole confini **semantici** (1a guerra Golfo ≠ 2a ≠ Crimea 2014 ≠ Ucraina
2022), non una finestra a giorni fissi comunque allargata. Discusso un design ("situazioni",
tabella + link a eventi, popolata da giudizio LLM non merge automatico per evitare l'ennesimo
chain-collapse) — **rimandato a sessione dedicata**, loggato in `docs/roadmap.md` Fase 5. Non
costruito oggi.

Sul secondo punto — "le tesi dovevano essere aperte in autonomia e poi, semmai, rifinite" — ho
segnalato che questo contraddice testualmente CLAUDE.md ("Human-in-the-loop... Nessuna operazione
autonoma", principio 2, marcato "non negoziabile"). L'utente ha chiarito: via di mezzo basata su
**soglia di confidence**.

**Implementato**: `config.py::auto_open_confidence_threshold` (default 0.6, calibrato sulle 7 tesi
reali generate oggi prima di questo fix: Hormuz 0.62/0.65, tanker 0.55, difesa 0.58 — una soglia 0.6
avrebbe aperto solo la tesi Hormuz primaria). `agent/thesis.py::_maybe_auto_open()` — dopo che ogni
tesi (primaria + alternative) è salvata E dopo la review fondamentali batch (così una tesi che i
fondamentali contraddicono beneficia comunque di quel contesto prima di aprire, non lo salta),
per ogni candidato a/sopra soglia replica **esattamente** la sequenza manuale: `approve_thesis` →
`create_thesis_prediction` → `open_agent_trade` → `link_thesis_prediction_to_trade`. Sotto soglia:
comportamento invariato, resta `pending`.

**Degrado esplicito**: se l'approvazione riesce ma l'apertura trade fallisce (es. portafogli non
inizializzati, ticker senza prezzo), la tesi resta `approved` — **non torna `pending`** — stesso
stato in cui finirebbe un flusso manuale con `approve` riuscito e `trade open` fallito separatamente;
completabile dopo con `pathos trade open <id>`. Bug reale trovato e fixato durante lo sviluppo: la
prima versione diceva nei log "left pending" quando in realtà lasciava `approved` — messaggio corretto
prima del commit, non solo il comportamento.

**CLI**: `--no-auto-open` (disattiva, tutto resta pending come da comportamento originale) e
`--auto-open-threshold N` (override soglia) su `pathos thesis generate`.

**CLAUDE.md aggiornato**: principio 2 ("Human-in-the-loop") riscritto per riflettere il comportamento
reale — non più assoluto, ora esplicita la soglia e il fatto che vale solo per paper trading (soldi
virtuali), mai per denaro reale.

**Test**: 6 nuovi in `test_thesis.py` (unit su `_maybe_auto_open`: sotto soglia no-op, confidence
None no-op, successo con portafogli pronti, degrado senza portafogli; integrazione su
`generate_theses`: solo la tesi sopra soglia auto-aperta, flag disabilitato tiene tutto pending).
560 test totali verdi (era 554). Ruff pulito (baseline 8 violazioni pre-esistenti, invariate). 6 test
pre-esistenti aggiornati con `auto_open=False` esplicito dove testavano altro (fundamentals, price
fetch) per non confondere i concern.

**Non ancora esercitato su dati reali**: le 7 tesi generate oggi prima di questo fix restano tutte
`pending` (generate col vecchio comportamento) — il prossimo `pathos thesis generate` reale sarà il
primo a esercitare l'auto-open per davvero. Non ri-lanciato oggi per non consumare un'altra chiamata
Claude reale solo per demo (il progetto ha un budget di 2-3 task di ragionamento/giorno).

---

## Primo ciclo reale: `brief` → `thesis generate` (2026-07-14, branch `feat/fundamentals-analysis`, PR #14)

**Perché**: tutto il lavoro di Fase 3 (predictions v2, fundamentals enrichment, CP-022 geoloc) era
implementato ma **mai eseguito su dati veri** — `predictions`/`theses`/`trades`/`portfolios` a 0 righe
verificato a inizio giornata. Eseguito `pathos portfolio init` → `pathos brief` → `pathos thesis
generate` per la prima volta.

**Trovati 2 bug reali, non ipotetici — il valore del primo run vero**:

**CP-025 — brief senza contenuto narrativo nei giorni senza divergenze.** Il primo brief aveva 0
divergenze, solo entity-degree e anomalie fisiche — zero storie, nonostante 1846 eventi RSS reali
negli ultimi 7 giorni nel DB. Causa: `_query_divergences` (score>0.5) è l'unico canale di contenuto
narrativo in `brief.py`, e quel giorno era vuoto (749 righe totali nel DB, 0 in finestra). Claude si
è correttamente rifiutato di fabbricare tesi da segnali così deboli — comportamento giusto, ma
sintomo di un buco di design. Fix: `_query_recent_events()`, eventi RSS recenti ordinati per copertura
fonti, sezione `## RECENT EVENTS` sempre popolata **indipendentemente** dalle divergenze. 8 test nuovi.

**CP-026 — `claude -p` eredita CLAUDE.md del repo, contamina l'output; JSON in fence non gestito.**
Il brief (anche dopo il fix CP-025) conteneva meta-commentario da coding-agent in testa/coda ("salvato
in scratchpad", "vuoi che lo integri in brief.py?") — il subprocess `claude -p` lanciato dalla cwd del
progetto carica CLAUDE.md/hook/skill automaticamente, comportandosi come una sessione Claude Code
invece di una pura completion testuale. Fix: `--safe-mode --tools=` in `_run_claude_subprocess`
(isola dal repo, **preserva l'auth OAuth** — deliberatamente NON `--bare`, che richiede
`ANTHROPIC_API_KEY` esplicita e romperebbe l'autenticazione via abbonamento di questo progetto,
verificato: nessuna API key impostata in questo ambiente). Secondo bug trovato nello stesso giro:
Claude produceva JSON valido ma avvolto in un fence ` ```json `, che nessuno dei chiamanti
`json_mode=True` (thesis.py, debate.py, extract.py) gestiva — con la gestione graceful appena
aggiunta per i rifiuti in prosa (vedi sotto), questo veniva scambiato per un rifiuto invece che per
un parsing fallito su contenuto valido. Fix centralizzato: `_strip_json_fence()` applicato
automaticamente in `LLMClient.complete()` quando `json_mode=True` — un solo punto per tutti i
consumer. 8 test nuovi.

**Fix di robustezza collaterale in `thesis.py`**: una risposta LLM non-JSON (rifiuto motivato in
prosa) ora produce `ThesisResult(theses_created=0, refusal_reason=...)` invece di sollevare
`ValueError` non gestita — "0 tesi oggi" è un esito legittimo per un job che gira anche non presidiato
(`pathos loop`), non un crash. La ragione del modello resta leggibile (log + CLI output), non
scartata.

**Risultato finale, verificato end-to-end sul DB reale**: brief pulito (parte da `# Intelligence
Brief`, 12 recent events reali: Hormuz kinetic escalation, morte Lindsey Graham, verdetto Le Pen) →
**7 tesi reali persistite** (BZ=F long/short su risk premium petrolio, FRO long/short su tanker
war-risk, ITA long/short/short su settore difesa — 3 primarie + 4 alternative), fundamentals review
batch completato (BZ=F flaggato "future", ITA flaggato "ETF", entrambi minimal snapshot corretto per
tipo strumento), 11 watchlist items, nessun rifiuto, nessuna contaminazione residua.

**Bonus**: `.gitignore` mancava `data/briefs/` (a differenza di db/parquet/logs) — brief reali
sarebbero finiti tracciati per errore. Aggiunto.

**Test**: 554 verdi (era 535 a inizio giornata, +19 in questo giro: 8 llm_client, 8 brief, 3 thesis).
Ruff pulito sui file toccati (11 violazioni pre-esistenti invariate, stesso baseline di prima).

**CRITICAL_POINTS.md**: CP-025 e CP-026 aperti e chiusi nella stessa sessione, con dettaglio +
verifica end-to-end.

**Prossimo passo consigliato**: `pathos thesis approve <id>` su una delle 7 tesi reali (verifica
auto-creazione predizione economic collegata, CP-004/005) → `pathos trade open <id>` (primo trade
reale, primo dato vero per giudicare CP-023 — vale la pena SEC EDGAR v2?). In alternativa, fermarsi
e aspettare il merge di PR #14 prima di continuare ad accumulare commit sullo stesso branch (già a
~8 commit logici in questa sessione: fundamentals, CP-008/010/012, CP-022, bookkeeping, CP-025/026).

---

## CP-022 risolto (2026-07-14, branch `feat/fundamentals-analysis`, PR #14)

**Cosa**: `pathosphere/semantic/extract.py` — 2 nuove funzioni, implementano il design già
validato in `notebooks/study_19_rss_event_geolocation.ipynb` (nessuna riprogettazione, solo
implementazione):

- `geolocate_rss_events()` — Step 1, euristica gratis/istantanea/no-rete. Per ogni evento
  `origin='rss'` senza `location_name`, classifica le country-entity del cluster (major-power set
  ricalcolato a runtime, top-8 per documenti distinti — non lista fissa) in `located` (scrive
  `location_name`) / `skip_bilateral` (relazione tra grandi potenze, non ancorata) / `skip_none`
  (nessuna entità) / `ambiguous` (per Step 2). Gira **sempre**, cablata in `pathos extract` PRIMA
  di `geocode_events()` (invariata) — stesso comando, un flag in più nell'output, zero nuova
  superficie CLI per l'uso comune.
- `geolocate_ambiguous_events_qwen()` — Step 2, fallback Qwen3 4B locale per i casi `ambiguous`.
  **Non** nel flusso di default — comando esplicito `pathos extract --geolocate-qwen
  [--geoloc-limit N]` (default 20/run). Riprendibile via nuova colonna `events.geoloc_checked`
  (migration idempotente in `_MIGRATIONS`): un evento è marcato esaminato appena ha risposta
  definitiva (location o "nessun bersaglio" confermato), mai ririprovato; solo un fallimento di
  rete/parsing lo lascia a 0 per retry al batch successivo. Prompt title-only già validato nel
  notebook, riusato identico.

**Latenza Qwen ri-misurata** (macchina scarica, solo Ollama attivo, no Jupyter/IDE insieme):
**46.7s/chiamata** — meglio dei 90-113s del notebook sotto stress di memoria, ma sempre lento.
Conferma la decisione presa nel notebook: batch esplicito piccolo, mai sincrono in `pathos extract`
interattivo.

**Eseguito sul DB reale** (solo Step 1 — Step 2 Qwen NON lanciato sul backlog storico completo,
fuori scope, ~17h di chiamate seriali su 1324 eventi ambigui):

| Decisione | N | % |
|---|---|---|
| `located` | 870 | 32% |
| `ambiguous` | 1324 | 49% |
| `skip_bilateral` | 74 | 3% |
| `skip_none` | 421 | 16% |

2689 eventi RSS totali valutati (corpus cresciuto rispetto al notebook — ingest continuato tra le
sessioni). `MAJOR_POWERS` di questo run: China, India, Iran, Israel, Japan, Russia, Ukraine, United
States.

**Test**: 535 verdi (era 519, +16 in `test_extract.py`, tutto mockato, nessuna chiamata rete/Ollama
reale in pytest). Ruff pulito sui file toccati (12 violazioni pre-esistenti in `extract.py`/
`cli.py`/`test_extract.py`, invariate — F541/F841/E741 sparse, non introdotte da questo fix, fuori
scope).

**Valutazione critica (limiti reali)**:
1. Euristica dipende dalla qualità NER a monte — rumore nel conteggio country-entity può spostare
   un evento verso `ambiguous` o assegnare un `location_name` sbagliato.
2. `MAJOR_POWERS` **non stabile nel tempo** — ricalcolato ogni run sul corpus corrente, un paese può
   entrare/uscire dal top-8 man mano che il corpus cresce. Un evento già scritto non viene mai
   sovrascritto, ma la classificazione teorica di uno stesso evento può differire se rivalutato in
   futuro — da tenere a mente per audit storici, non un bug.
3. Validazione Qwen resta a **2 campioni reali** (Cuba, Iran — quelli che hanno motivato
   l'indagine). Non è validazione statistica — il batch va monitorato a campione quando gira su
   volumi reali più grandi.
4. **Backfill storico incompleto**: 1324 eventi `ambiguous` restano senza `location_name` finché
   qualcuno non lancia ripetutamente `pathos extract --geolocate-qwen` (20/run default, ~17h totali
   a 46.7s/call per smaltire tutto). Consigliato: `caffeinate -i uv run pathos extract
   --geolocate-qwen --geoloc-limit 200` come batch notturno, ripetuto finché `ambiguous` non cala a
   0 — stesso pattern di `pathos loop`.
5. `geocode_events()` (Nominatim) invariata — riceve solo più `location_name` da geolocalizzare,
   stesso rate-limit 1 req/s.

**Nota di processo**: implementazione delegata a un subagent in background, interrotto **2 volte**
da errori infrastrutturali (connessione chiusa a metà risposta, poi stallo 600s senza progresso) —
non errori nel suo lavoro. Codice e test sono sopravvissuti intatti nel working tree (non
committati) entrambe le volte; dopo il secondo stallo il completamento (CRITICAL_POINTS.md,
LOOP_STATE.md, questa sezione, numeri reali sul DB, test finale, commit, push) è stato fatto
direttamente in sessione principale invece di tentare una terza ripresa.

**CRITICAL_POINTS.md**: CP-022 marcato RISOLTO con dettaglio + valutazione critica a 5 punti (stesso
formato delle voci già chiuse).

---

## CP-008 + CP-010 + CP-012 risolti (2026-07-13, notte, branch `feat/fundamentals-analysis`, PR #14)

3 critical point indipendenti, ciclo fix→test→docs→commit per ciascuno, stesso branch/PR delle fondamentali (nessun nuovo branch, su richiesta esplicita).

**CP-008 — ruff F821 `sqlite3` non importato:**
`import sqlite3` aggiunto in `pathosphere/ingest/{comtrade,gdelt,physical,portwatch,rss,sources_seed}.py`, rispettando lo stile import già presente in ciascun file (stdlib import-prima-di-from, alfabetico). Annotazioni tipo `"sqlite3.Connection"  # type: ignore[name-defined]` invariate — bastava rendere il nome definito nel modulo. `uv run ruff check pathosphere/ --select F821` → 0 errori (era 9). Nota: 2 F401 pre-esistenti in `gdelt.py` (`date`, `Path` non usati) verificate come non introdotte da questo fix — restano fuori scope (CP-008 copriva solo F821).

**CP-010 — migration non automatica su `get_connection`:**
`pathosphere/db/schema.py::get_connection` ora chiama `migrate_db(conn)` dopo i PRAGMA, prima del `return conn`. Prima solo `init_db` (`pathos db init`) la eseguiva esplicitamente — un DB pullato con nuove colonne/migration ma non re-inizializzato crashava con `sqlite3.OperationalError: no such column` sul primo path che toccava la colonna nuova. `migrate_db` resta idempotente (ogni ALTER in try/except che ignora `OperationalError`), costo trascurabile per CLI locale. `init_db` ora la chiama 3 volte in sequenza (pre-DDL, post-DDL, dentro `get_connection`) — ridondante ma innocuo, lasciato com'è (nessun rischio di correttezza, non vale la pena toccarlo).

Test nuovo: `test_get_connection_auto_migrates_pre_v2_db` (`tests/test_db.py`) — DB con solo `CREATE TABLE entities` minimale (bypassando `init_db`/`migrate_db`), poi `get_connection` da solo, verifica che `canonical_entity_id` (colonna aggiunta via `_MIGRATIONS`, CP-018) compaia.

**CP-012 — dedup non riprendibile, transazione unica:**
`pathosphere/semantic/dedup.py::dedup_documents` ora processa i doc candidati (query invariata) in batch di `BATCH_SIZE=32` (stessa costante/pattern di `embedder.py`), con `with conn:` (commit) per batch invece che sull'intero run, più log INFO di progresso ad ogni batch (`Dedup progress: N/tot checked, M duplicates so far`) — prima il progresso era invisibile fino a fine run su backfill di ore (169k doc storici). La KNN query resta per-documento (nessun batching della query stessa, solo del commit). Un crash/Ctrl+C a metà perde al massimo 1 batch invece dell'intero run; i doc già `dedup_checked=1` nei batch committati non vengono ripresi al retry (garantito dal filtro `WHERE dedup_checked = 0` nella SELECT iniziale, invariato — verificato con test).

Test nuovo: `test_dedup_batch_commit_survives_later_batch_failure` (`tests/test_semantic.py`) — 3 doc, `batch_size=2`, eccezione simulata (monkeypatch `_parse_dt`) sul 3° doc (batch 2, isolato dal 1° batch grazie all'ordinamento per `published_at`). Verificato: doc 1-2 restano `dedup_checked=1` dopo il rollback del batch 2; doc 3 resta `dedup_checked=0`.

**Esito**: 519 test verdi (era 517 su `feat/fundamentals-analysis`, +2 nuovi, nessuna regressione). Ruff pulito su tutti i file toccati. 3 commit separati (Conventional Commits), push su `feat/fundamentals-analysis` — PR #14 si aggiorna da sola, nessuna nuova PR aperta.

**CRITICAL_POINTS.md**: CP-008/CP-010/CP-012 marcati RISOLTO con dettaglio fix+test, stesso formato delle voci già chiuse (CP-018 etc.).

---

## Enrichment fondamentali (2026-07-13, branch `feat/fundamentals-analysis`)

**Cosa**: livello di contesto fondamentali per le tesi — l'LLM propone un
ticker, il modulo lo arricchisce con ratio + Altman Z + Piotroski F + testo
interpretativo; una review LLM batch annota supporta/contraddice/neutrale.
NESSUNA soglia decisionale automatica: decide l'umano in approvazione
(principio "core = agent semantico, non modello quant" rispettato).

**File**:
- Nuovo: `pathosphere/market/fundamentals.py` (fetch + score + render testo)
- Nuovo: `tests/test_fundamentals.py` (15 test, yfinance interamente mockato)
- Modificati: `pathosphere/agent/thesis.py` (enrichment + review pass),
  `pathosphere/db/schema.py` (migrazione `theses.fundamentals_json`),
  `pathosphere/cli.py` (`pathos fundamentals <ticker>`, `--no-fundamentals`,
  sezione in `thesis show`), `tests/test_thesis.py` (4 test nuovi),
  `docs/wiki.md` §8.7, `docs/roadmap.md`, `docs/schema.md`

**Decisioni chiave**:
- Colonna JSON (`theses.fundamentals_json`), non tabella dedicata: snapshot
  1:1 con la tesi al decision time (no-lookahead), mai query aggregate.
- Testo interpretativo = template deterministico (no LLM): dato numerico,
  zero costo, testabile. L'unico costo LLM extra è 1 call batch/run di
  `thesis generate` per la review (annotazione).
- Altman Z solo se TUTTI i 5 componenti presenti (un Z parziale non è uno Z);
  skippato con flag `not_applicable` per settore finanziario.
- Piotroski F scorato solo sui test con dati (`piotroski_testable` esposto).
- SEC EDGAR rimandato a v2: ticker→CIK + solo USA-filer + delay ~45gg =
  complessità > valore per enrichment v1.

**Valutazione critica (limiti reali, non feature list)**:
1. **Copertura**: yfinance statements spesso vuoti/disallineati per non-USA
   e small-cap (issue nota #2584) — proprio i ticker che un sistema
   geopolitico multi-blocco propone più spesso (2330.TW, cinesi, russi
   delisted). Per questi il modulo degrada a "minimal" (solo ratio .info,
   spesso pure quelli parziali): l'arricchimento vale soprattutto per
   large-cap USA/EU, cioè dove serve di meno.
2. **Affidabilità dati non verificata**: nessun cross-check (EDGAR rimandato)
   — se Yahoo ha line item disallineati di un anno, Z e F sono calcolati su
   dati sbagliati SENZA che il sistema se ne accorga. Il testo dichiara il
   caveat ma non può rilevare l'errore.
3. **Rate-limit yfinance**: N ticker/run in sequenza sincrona dentro
   `generate_theses` — con rate-limit Yahoo il fetch fallisce silenziosamente
   (per design), ma nei log appare solo un warning: facile non accorgersi che
   l'enrichment è degradato da giorni. Nessun retry/backoff in v1.
4. **Fondamentali ≠ orizzonte tesi**: le tesi hanno orizzonti 7-30gg; i
   fondamentali sono trimestrali/annuali con delay. L'assessment LLM può
   sovra-pesare un bilancio vecchio di 6 mesi su un trade event-driven di 2
   settimane. La review è annotazione, ma il bias di ancoraggio sull'umano
   esiste.
5. **Snapshot mai aggiornato**: fundamentals_json è congelato alla
   generazione; se la tesi resta pending giorni e escono earnings, nessun
   refresh (coerente col no-lookahead ma da sapere).
6. **Review pass = +1 call Claude/run**: budget credito SDK, non gratis.
   Con `--no-fundamentals` si azzera, ma il default consuma.

**Prossimo**: merge PR → primo `thesis generate` reale → misurare quota di
tesi con data_quality full/partial/minimal/none sui ticker realmente proposti
(decide se EDGAR v2 vale lo sforzo).

---

## CP-022 — Geolocalizzazione eventi RSS (2026-07-13, investigazione + validazione, non implementato)

Usando la dashboard, l'utente ha notato Cuba/Venezuela mostrare solo terremoti USGS sulla mappa, mai
notizie politiche. Causa: nessuno step deriva `location_name` per eventi `origin='rss'` (0/1996).
Dettaglio completo in `CRITICAL_POINTS.md` CP-022.

**Regola richiesta**: relazione bilaterale grandi-potenze → no geoloc; 1 paese → geoloc lì; attore→
bersaglio *via* terzo paese → geoloc sul bersaglio (non attore, non mezzo). Task di ruolo semantico,
non deducibile da conteggio entità.

**Validato in `notebooks/study_19_rss_event_geolocation.ipynb`** (solo lettura, nessuna scrittura DB):
euristica risolve 38% del volume, 59% resta ambiguo; Qwen3 4B locale (Ollama, installato ex-novo
questa sessione via `brew install ollama` + `ollama pull qwen3:4b`) corretto sui 2 casi reali testati
a mano, ma **90-113s/chiamata** sotto la pressione di memoria di questa sessione (8GB M1, IDE+Jupyter+
Ollama insieme) — va ri-misurato a macchina scarica prima di un backfill storico (~1000 eventi
ambigui, va fatto come batch notturno offline, non interattivo).

**Ollama ora installato e attivo sulla macchina** (`ollama serve` avviato manualmente, non via `brew
services` — non persiste al riavvio finché non deciso altrimenti).

**Prossimo passo** (non fatto): `geolocate_rss_events()` in `extract.py` (euristica + fallback batch
Qwen), chiamata da `pathos extract`, poi `geocode_events()` esistente invariato fa il resto.

---

*Sessione precedente — Fase 4 Dashboard Streamlit:*

## Fase 4 — Dashboard Streamlit (2026-07-12, sera, branch `feat/streamlit-dashboard`)

**Cosa**: `pathos serve [--host] [--port]` avvia dashboard Streamlit
(`pathosphere/dashboard/app.py` + `views/*.py`, 8 pagine: Overview, Mappa,
Narrazioni, Grafo entità, Tesi, Portafogli, Predizioni, Brief). Dettaglio
completo in `docs/wiki.md` sezione 8b. Dipendenze aggiunte: `streamlit`,
`plotly`, `folium`, `streamlit-folium`.

**Decisioni chiave**:
- Connessione DB **non cachata** (`st.cache_resource`) — `sqlite3.Connection`
  non è thread-safe e la cache di Streamlit è condivisa tra sessioni/thread;
  si apre una connessione fresca a ogni rerun (costo trascurabile, file locale).
- Navigazione **non multipage nativo** Streamlit (niente cartella `pages/`) —
  un `st.sidebar.radio` in `app.py` seleziona la vista, un solo processo/URL.
- Grafo entità: layout **circolare manuale** (non force-directed) per non
  aggiungere una dipendenza di layout grafi — sufficiente per il sottografo
  indotto top-N hub mostrato (default 30, slider 10-80).
- Pagina Tesi: bottoni Approva/Rifiuta/Apri-trade **replicano esattamente**
  il comportamento CLI (`approve_thesis` + `create_thesis_prediction` su
  approvazione, `open_agent_trade` + `link_thesis_prediction_to_trade` su
  apertura trade) — nessuna logica di business duplicata, solo UI sopra le
  funzioni già testate in `agent/approval.py`, `agent/predictions.py`,
  `market/trading.py`.
- Curva equity portafogli: nessuna tabella di snapshot storici in schema —
  ricostruita come `INITIAL_CASH + cumsum(pnl)` sui trade chiusi in ordine
  cronologico, più un punto finale "live" con `get_portfolio_status()`
  (include unrealized dei trade aperti, no-lookahead-safe: non riscrive mai
  `price_open`).

**Verificato**: `streamlit.testing.v1.AppTest` — caricato `app.py`, simulato
click su tutte le 8 voci sidebar, **nessuna eccezione** contro il DB reale
(8241 eventi, 9142 entità, 75912 link, 749 divergenze — Tesi/Portafogli/
Predizioni/Brief vuote perché Fase 3 non ha ancora prodotto dati reali,
mostrano correttamente stato vuoto invece di errore). `pathos serve --help`
verificato. Ruff pulito su `pathosphere/dashboard/`. 498 test pytest
pre-esistenti ancora tutti verdi (nessuna regressione, nessun test nuovo:
interfaccia pura sopra logica già coperta).

**Non fatto in questa sessione**: nessun dato reale di tesi/trade/predizioni
esiste ancora sul DB — le pagine corrispondenti sono verificate solo in
stato vuoto. Prima esecuzione reale di `pathos brief` → `pathos thesis
generate` → approvazione via dashboard darà la prima verifica end-to-end
con dati veri.

**Prossimo**: aprire PR da `feat/streamlit-dashboard` → review → merge.
Poi, primo giro reale del ciclo agent (brief/thesis/approvazione) per
popolare Tesi/Portafogli/Predizioni e verificare la dashboard con dati veri.

---

## CP-021 fix: story-linking, ordine merge per similarità a parità di gap (2026-07-12, sera)

**Fix**: `link_related_events` in `story.py` ordina le coppie candidate per
`(gap temporale crescente, similarità decrescente)` invece di solo gap crescente. A parità
di gap (comune con un'entità quasi-hub: Trump in 149/2000 eventi → centinaia di coppie a
gap=0), la coppia con similarità più alta viene processata per prima invece di lasciare
l'ordine a un dettaglio implementativo (iterazione di un `set` Python). Nessuna modifica ai
gate di accettazione (finestra temporale, complete-linkage gruppo-vs-gruppo) — solo all'ordine
in cui le coppie vengono provate.

**Verificato sul DB reale**: backup pre-fix
(`pathosphere_backup_20260712_183828_pre_cp021_reorder.db`), reset completo `story_id` +
riesecuzione da zero. Risultato: 125 storie (199 eventi), distribuzione sana (max 8, media
2.6, **nessun mega-blob**). Il caso Iran-deal segnalato ora unisce correttamente 121960+122131
(+2 altri eventi coerenti). Ispezionate a campione altre 2 storie da 6 eventi — coerenti.

**Non completamente risolto**: 122059 (Hormuz) e 122072 (sticking points) restano separati
dal gruppo Iran-deal — plausibilmente sotto-angolazioni sotto soglia 0.82 contro l'intero
gruppo, comportamento conservativo accettabile (non forza angolazioni diverse insieme).

**Test**: 1 nuovo (`test_ties_on_time_gap_prefer_higher_similarity_pair`), 498 totali verdi.

**Status**: CP-018/019/020/021 tutti chiusi. Pipeline entity+clustering+story ora verificata
end-to-end su dati reali. Nessun blocco noto per Fase 4 Dashboard.

---

## CP-021: story-linking, ordine greedy sub-ottimale con entità quasi-hub (2026-07-12, sera)

**Contesto**: ispezionando `study_17` (sezione cluster, aggiunta su richiesta utente che
notava assenza dei cluster di notizie), l'utente ha notato che 4-5 dei top-10 cluster
sono palesemente la stessa storia (trattativa Iran-USA) mai unita da `pathos story`.

**Verifica**: `Trump` compare in 149/~2000 eventi (quasi-hub). Il caso specifico
(eventi 121960+122131, Iran-deal) supera **entrambi** i gate di `story.py` individualmente
(similarità diretta 0.847 > soglia 0.82, span combinato 3gg < finestra 10gg) eppure resta
non unito — causa: ~13700 coppie-candidate totali (chiunque condivida una persona),
processate greedy per gap temporale crescente; un merge sbagliato con gap più piccolo,
elaborato prima, può allargare un gruppo abbastanza da bloccare il merge corretto quando
arriva il suo turno (union-find irreversibile).

**Scala misurata con cautela**: audit isolato (coppia-vs-coppia) trova 683 coppie che
passano entrambi i gate, di cui solo 298 unite nella storia finale — **ma il numero è
sovrastimato**: l'audit isolato replica esattamente il punto cieco (coppia-vs-coppia,
non gruppo-vs-gruppo) che `story.py` è già stato costruito per evitare. Campionando le
"mancate", molte sono correttamente respinte dal vero algoritmo (coppie che condividono
Trump ma sono topicamente slegate). Solo il caso Iran-deal è **confermato** come bug reale.

**Non fatto**: nessun fix — l'utente ha scelto di investigare prima di decidere, e i
risultati (rischio di sovrastimare + rischio di introdurre nuovi problemi con un
riordino affrettato su un algoritmo già a v3 dopo 2 iterazioni fallite) supportano
rimandare a sessione dedicata. Loggato come CP-021, **aperto, non bloccante** (non è
una regressione: 27 storie nuove formate correttamente in questo stesso run).

**Direzioni future possibili**: ordinare merge per similarità decrescente invece che per
gap crescente; oppure passata iterativa (ri-tentare coppie respinte dopo stabilizzazione)
invece di greedy single-pass.

---

## CP-020: due classi sistemiche aggiuntive (2026-07-12, tardo pomeriggio)

**Contesto**: dopo CP-018/CP-019, l'utente ha ricontrollato il grafo e corretto
l'inquadramento — *"non sono segnalazioni puntuali, sono classi di errore"* — vedendo
ancora `EU`/`European`/`Europe` (3 nodi) e `China`/`Chinese` (2 nodi) separati.

**Classe A — asimmetria demonimo↔paese**: `_location_country_key()` riconosceva "Chinese"
→ "China" via `DEMONYM_TO_COUNTRY`, ma **non riconosceva "China" stessa** come appartenente
allo stesso gruppo (il suo `canonical_name` Wikidata era "People's Republic of China", non
"China" — match esatto falliva). Fix: la chiave ora riconosce anche il **nome letterale**
dell'entità (`_KNOWN_PLACE_VALUES_LOWER`), non solo `canonical_name` — si applica a
qualunque paese nei dizionari esistenti, non solo Cina.

**Classe B — aggettivi continentali non coperti**: "European" non era in nessuna tabella
curata → restava `entity_type='other'`, invisibile alla canonicalizzazione. "Europe" (il
continente) aveva un'**altra istanza della stessa collisione Wikidata di CP-019**:
`wbsearchentities("Europe")` matchava "Europe PubMed Central" (database bibliografico), non
il continente. Fix: aggiunte `europe`/`european`, `asia`/`asian`, `africa`/`african` a
`LOCATION_ALIAS_TO_COUNTRY` — copre sia lo skip-Wikidata (CP-019) sia il backfill (esteso
per iterare anche questo dizionario, non solo `DEMONYM_TO_COUNTRY`).

**Verificato sul DB reale**: `China`/`Chinese` uniti; `Europe`/`European` uniti (canonical_name
corretto da "Europe PubMed Central" a "Europe"); `EU` resta `organization` distinta — 3 nodi
confusi → 2 nodi corretti. Bonus: `Asia`/`Asian`, `Africa`/`African` uniti preventivamente.

**Test**: 6 nuovi in `test_extract.py` (497 totali verdi). `pathos graph` rieseguito.

**Nota**: stessa lista curata, non rilevamento generale — probabile che emergano altre
coppie non ancora osservate (Oceania/Antartide non coperte, "American" resta ambiguo
US-vs-continente, non toccato).

---

## CP-018 + CP-019: fix canonicalizzazione entità (2026-07-12, pomeriggio)

**Contesto**: dopo la catena entity→canonicalizzazione→story-linking (sezione sotto),
l'utente ha ispezionato visivamente `study_15_visual_tour.ipynb` e trovato 4 problemi
nel grafo entità (loggati come CP-018, bloccante prima di Fase 4). Sessione ripresa per
risolverli, con richiesta esplicita di **verificare empiricamente sul DB reale**, non
fidarsi solo dei test unitari — e l'utente ha poi avvertito: *"non sono solo punti, sono
segnalazioni/esempi — ci saranno altre incongruenze che non ho visto"*, il che ha guidato
un audit più ampio invece di trattare i 4 punti come lista chiusa.

**I 4 fix di CP-018** (`pathosphere/semantic/extract.py`):
1. **Wikidata QID conflict, tipo-aware**: `link_wikidata` ora chiede P31 a Wikidata
   quando due entità in conflitto QID hanno `entity_type` diverso, e scambia il canonico
   verso quella col tipo corretto (`_wikidata_instance_of_hint` + `WIKIDATA_TYPE_HINTS`),
   invece di "chi arriva primo vince". `repair_wikidata_type_conflicts()` per riparare
   conflitti già mal risolti nel DB esistente (rete, opt-in).
2. **Organization come tipo separato**: `INTERGOVERNMENTAL_ORGS` (EU/NATO/UN/WHO/IMF/
   World Bank/WTO/OPEC/G7/G20/ASEAN/African Union/Arab League/BRICS) → `entity_type=
   'organization'` invece di `company`. `backfill_organization_entities()` per righe esistenti.
3. **Location canonicalization**: nuova `canonicalize_location_entities()` (stesso pattern
   non distruttivo di `canonicalize_person_entities`) — England/British/Britain/UK ora
   un solo canonico "United Kingdom".
4. **Noise stoplist**: `NOISE_ENTITY_STOPLIST` (video/watch/photo/gallery/live/breaking/...)
   escluso a livello di **creazione** entità (non solo skip Wikidata). `purge_noise_entities()`
   per righe legacy.

**CP-019 (bonus, trovato durante la verifica)**: `UK` risultava alias di un'entità
"Ukrainian" con QID Q8798 — che è la **lingua** ucraina (codice ISO 639 `uk`), non il
paese. Collisione fuzzy-search Wikidata su stringa corta ambigua, non ipotetica: trovata
sui dati reali. Fix generale (non solo per UK): `CURATED_ALIAS_TO_LABEL` (demonimi + alias
location + org intergovernative) escluso dalla ricerca Wikidata — stesso meccanismo di
`GENERIC_ENTITY_STOPLIST` ma **preservando** il canonical_name corretto invece di azzerarlo.
Audit successivo su parole-paese ambigue non ancora linkate (Turkey/uccello, Georgia/stato
USA, Jordan/persona, Chad, Guinea, Niger, Congo, Mali, Jersey) — nessuna ancora corrotta,
ma aggiunta verifica P31 **proattiva** (`AMBIGUOUS_ENTITY_NAMES`) prima di accettare un
match, non solo dopo il fatto.

**Verificato empiricamente sul DB reale** (backup pre-fix:
`data/db/pathosphere_backup_20260712_163720_pre_cp018.db`):
- `FRANCE` (company) ora alias corretto di `France` (location, QID Q142)
- `EU`/`NATO` ora `organization` con canonical_name corretto
- `England`/`British`/`Britain` ora alias di `UK`, canonical_name="United Kingdom"
- `Ukrainian` bug riparato + bonus: ora unita anche all'entità paese "Ukraine" (id 1320)
- `VIDEO` (22 mention) eliminata
- `pathos graph` rieseguito: 77516 link scritti (da 83808 pre-canonicalizzazione)

**Comando CLI aggiornato**: `pathos extract [--backfill-orgs] [--repair-wikidata-types]`
(oltre ai flag già esistenti `--backfill-demonyms`). Canonicalizzazione location e purge
rumore girano sempre (locale, no rete, come già la canonicalizzazione persone).

**Test**: 53 nuovi in `test_extract.py` (494 totali verdi), ruff pulito.

**Nota per il futuro**: CP-019 non è garanzia di completezza — 9 nomi ambigui curati,
non un rilevatore generale. L'utente ha segnalato esplicitamente che ci sono probabilmente
altre incongruenze non ancora osservate; i controlli aggiunti oggi vanno letti come
*classi di difesa* generalizzate, non come lista chiusa.

**Status**: CP-018 e CP-019 chiusi. Pipeline entity/graph pronta per Fase 4 Dashboard
(nessun altro blocco noto aperto).

---

## Precedente: Catena entity extraction → canonicalizzazione → story-linking (2026-07-12)

**Contesto**: dopo il fix complete-linkage (11 luglio), l'utente ha chiesto: "è il caso
che ci sia un cap ai cluster?" — questa domanda ha aperto un'indagine a cascata che ha
richiesto sistemare 3 problemi collegati prima di passare a Fase 4 Dashboard, su esplicita
richiesta dell'utente: "prima risolviamo i problemi che abbiamo, non ha senso andare in
dashboard se dati/algoritmi sono di bassa qualità."

**Problema radice trovato**: complete-linkage (fix precedente) frammenta sistematicamente
eventi reali grandi e multi-angolo — 88 documenti sul funerale di Khamenei erano sparsi
su 33 micro-eventi invece di formare un'unica storia forte. Causa: nessuna soglia di
similarità embedding regge su copertura genuina multi-giorno/multi-angolo (breaking news
vs pezzo di colore vs analisi geopolitica).

### 1. Fix HTML boilerplate in embedding + extract (commit `d5dc724`, `510aa1a` parziale)

`extract.py` aveva già bleach.clean per il body (fix CP-015) ma **mai applicato a
`embedder.py`** — stesso bug, due file. Inoltre bleach pulisce i TAG ma non le ENTITY
REFERENCE (`&nbsp;`, `&ldquo;`, `&rdquo;`) — serviva anche `html.unescape()`. Applicato
a entrambi i file. Ri-eseguito su DB reale: 2972 doc RSS re-embedded + re-extracted,
`pathos graph` ricostruito, 2969 entità stale (leak HTML pre-fix) purgate. Risultato:
**0 entità con leak HTML residuo** (era 12%, 1264/10581).

### 2. Canonicalizzazione entity person (commit `510aa1a`)

Trovato: "Khamenei" esisteva come 10+ righe diverse in `entities` (Khamenei/Ali Khamenei/
Ayatollah Khamenei/Ayatollah Ali Khamenei...) — NER estrae ogni variante onorifica come
entità distinta, nessun dedup automatico. `canonicalize_person_entities()` in
`extract.py`: due passate, non distruttive (pointer `canonical_entity_id`, stessa
convenzione già usata per gli alias Wikidata in `graph.py`):
1. Match esatto dopo strip onorifici (lista curata) su nomi multi-token — sicuro
2. Cognomi nudi ambigui ("Khamenei" da solo) uniti solo se un candidato domina
   nettamente (≥3× menzioni) — altrimenti lasciati separati (evita di fondere
   Ali Khamenei col figlio Mojtaba Khamenei, persona diversa)

Agganciato a `pathos extract` (gira sempre, locale, nessun costo di rete). Applicato al
DB reale: 2 gruppi esatti + 215 cognomi nudi uniti, 48 ambigui correttamente lasciati separati.

### 3. Story-linking a due stadi (commit `0237389`, `05e34e4`)

**Schema**: `events.story_id` (self-referenziale, stessa convenzione COALESCE di
`canonical_entity_id`) — non distruttivo, `event_documents` intatti, ogni micro-evento
resta ispezionabile singolarmente.

**Algoritmo** (`pathosphere/semantic/story.py`): unisce micro-eventi che condividono
un'entità PERSONA canonica entro una finestra temporale, **e** superano una soglia di
similarità embedding (0.82) calcolata come **vero complete-linkage gruppo-vs-gruppo**
(non solo la coppia-ponte che ha scatenato il tentativo di merge).

**Due iterazioni per arrivare al fix corretto** (documentate perché istruttive):
- **v1** (solo entità+tempo): un politico onnipresente (Trump, menzionato di striscio
  in decine di articoli slegati nella stessa settimana) ha agito da hub universale →
  mega-storia da **244 eventi** slegati (World Cup, NATO, mercati petroliferi...).
  Stesso identico chain-collapse già visto per gli embedding, spostato di un livello.
- **v2** (+ check embedding solo sulla coppia-ponte, soglia 0.82): mega-blob ridotto ma
  ancora presente a **206 eventi** — perché controllare solo i due eventi-innesco è
  esattamente il punto cieco dell'average-linkage: un evento-ponte può passare il check
  contro DUE gruppi diversi individualmente, pur essendo quei gruppi incoerenti tra loro.
- **v3 (fix vero)**: track dell'intero insieme documenti per gruppo union-find; il gate
  richiede la **similarità minima sull'intero cross-product** documenti-gruppo-A ×
  documenti-gruppo-B, non solo doc-ponte vs doc-ponte — esattamente lo stesso principio
  del fix complete-linkage di `cluster.py`, replicato un livello sopra.

**Fix aggiuntivo tempo**: per la dimensione temporale (1D, ordinata), basta controllare
che lo **span totale** del gruppo risultante (max-min) resti entro finestra — equivale
matematicamente a un vero controllo complete-linkage (in 1D, span limitato ⟺ ogni coppia
è vicina), niente approssimazione necessaria a differenza del caso embedding.

**Risultato finale su DB reale**: max micro-eventi uniti in una storia = **8** (era 206
nel mega-blob v2). Caso Khamenei: 22 micro-eventi frammentati → **12 macro-gruppi**
(due cluster genuini da 6 e 5 eventi) — risultato conservativo e sicuro: angolature
davvero diverse restano separate invece di essere forzate in un blob artificiale.

**Comando CLI**: `pathos story [--time-window-days N]` (default 10).

**Test**: 476 verdi (9 nuovi in `test_story.py`, inclusi test di regressione specifici
per entrambi i bug di chain-collapse trovati — coppia-ponte ed hub temporale).

**Status**: pipeline clustering→embedding→entity→story ora solida end-to-end. Pronto
per Fase 4 Dashboard.

---

## Precedente: Fix HTML boilerplate in embedding (2026-07-11 ~ 15:00)

**Contesto**: study_14 (complete-linkage fix) trovò un residuo — cluster Folha (12 doc,
portoghese) che mescolava temi slegati (G7, Tesla, Taliban, elezioni Peru, ponte Brooklyn)
pur superando la soglia 0.88.

**Root cause**: `raw_documents.body` conteneva HTML grezzo mai ripulito prima dell'embedding,
incluso un footer boilerplate ripetuto (`<a href="...redir.folha.com.br/redir/.../rss091/...">
Leia mais</a> (data)`). Per teaser brevi (366-1047 char), questo blocco condiviso può essere
il 20-40% del testo — domina il segnale embedding più del contenuto reale. `extract.py` aveva
già questo fix (CP-015, bleach prima del NER) ma **mai applicato a `embedder.py`** — stesso
bug, due file diversi.

**Fix** — `pathosphere/semantic/embedder.py::_build_text`: `bleach.clean(body, tags=[], strip=True)`
prima di costruire il testo per l'embedding. Verificato: dopo pulizia, le 12 coppie Folha
scendono da similarità ≥0.88 a max 0.855 — nessuna più supera la soglia.

**Test nuovo**: `test_build_text_strips_html_boilerplate`.

**Applicato al DB reale** (backup preventivo: `data/db/pathosphere_backup_20260711_144947.db`):
- Reset embedded=0 + vec_documents cleared + eventi RSS/Comtrade cleared (2972 RSS doc,
  Comtrade 252 doc esclusi per design, dati strutturati non prosa)
- Ri-eseguito `pathos embed` + `pathos cluster --time-window-hours 2160`
- Risultato: max cluster size 12→8, cluster Folha misto **sparito del tutto**. Tutti i 7
  cluster ≥5 doc rimasti sono genuinamente coerenti (Iran-USA deal multi-dominio, NATO
  Turchia, Argentina-Egitto World Cup; TASS/PressTV monodominio ma coerenti — media di stato
  su storia nazionale, non bug).

**Commit**: `6b90804`. **Test**: 460 verdi.

**Status**: bias fonte/lingua residuo **chiuso**. Clustering pipeline (average-linkage→
complete-linkage→HTML strip) ora solida end-to-end.

---

## Precedente: Complete-linkage fix (2026-07-11 ~ 14:30)

**Domanda utente**: "è il caso che ci sia un cap ai cluster? se ci sono 120 articoli su Hormuz come vengono trattati?"

**Test empirico (study_13)**: cap NON frammenta eventi genuini — è rete di sicurezza necessaria.
Ogni valore di cap testato (30→uncapped) satura il proprio tetto: senza cap un cluster arriva a
**1370 doc** (>50% del corpus), fondendo 25 storie diverse (Hormuz, Iran-USA, Israele-Libano, FIFA,
G7, finanza giapponese) via centroid drift.

**Root cause trovato**: il fix precedente (average-linkage, sessione 2026-07-10) controllava solo
il doc-ponte contro il centroide di ogni cluster target, singolarmente. Un doc D coerente sia con
A sia con B salda A e B interamente, senza mai verificare che i membri di A siano coerenti con
quelli di B — stesso pattern di transitività del bug originale single-linkage, un livello più
in profondità.

**Fix** — `pathosphere/semantic/cluster.py`:
- Pre-filtro economico (centroide, soglia 0.75) — scarta candidati lontani, O(1)
- Gate vero: **complete-linkage cluster-vs-cluster** — prima di fondere due cluster, verifica
  distanza massima tra ogni coppia di membri A×B, non solo doc-ponte vs centroide — O(|A|×|B|)
- Cap 30 resta come rete di sicurezza aggiuntiva (zero costo osservato)

**Test regressione**: `test_cluster_rejects_bridging_doc_welding_unrelated_clusters` — embedding
costruiti a mano (cos(D,A)=cos(D,B)=0.90, cos(A,B)=0.62), verifica che vecchio bug avrebbe fuso
A+B tramite D, nuovo fix li mantiene separati.

**Verifica empirica (study_14)**:
- Cap ha **zero effetto** ora (12/20/30/100/uncapped → risultato identico, 1977 eventi, max 12 doc)
- Singleton 88.8%→78.0% (il gate severo impedisce merge sbagliati che rubavano doc a cluster corretti)
- 9/10 top cluster genuinamente coerenti (Khamenei funeral, Argentina-Egitto World Cup, Putin-Trump,
  NATO Ankara, piogge Mumbai)
- 1 residuo: cluster Folha (12 doc, portoghese) mix temi — bias fonte/lingua, scala molto minore
  (12 vs 1370 del bug originale)
- Same-domain ≠ automaticamente bug: TASS/PressTV monodominio ma coerenti (media di stato)

**Commit**: `779363d`. **Test**: 459 verdi.

**Status**: clustering **strutturalmente solido**. Nessun tuning ulteriore cap necessario.

---

## Precedente: Fix GDELT titles in clustering (2026-07-10 ~ 20:05)

**Problem discovered**: Grandi cluster (69+ docs) avevano titoli **grezzi GDELT** come `||11|20251021|US` (event ID numerici, non testo umano).

**Root cause**: `cluster_documents()` includeva doc con `origin='gdelt'` che non hanno titoli leggibili.

**Fix**: Filter `(r.origin IS NULL OR r.origin != 'gdelt')` in clustering query:
- Esclude GDELT (che ha già gdelt_events nel DB)
- Preserva test doc (origin=NULL) per unit test
- RSS events adesso hanno titoli puliti da headline vere

**Verification**:
- Top 5 cluster titles: "Why the economics make this the craziest World Cup ever", "India summons US diplomat…", "No final agreement on deal with US – Iran", etc.
- Zero titoli GDELT grezzi
- 458 test passed

**Commit**: `b4588a5` — "fix(clustering): exclude GDELT docs from RSS event clustering"

---

## Clustering fix: chain-collapse prevented via average-linkage (2026-07-10 ~ 19:30)

**Criticità audit** (study_09_criticality_audit.ipynb eseguito):
1. Study_08 mai eseguito (execution_count: null su tutte le celle) — numeri in HANDOFF precedente non verificabili da notebook
2. **Clustering RSS rotto**: 79% singleton + 26 eventi al cap 30 doc (chain-collapse single-linkage)
3. Event_type: 90%+ codici CAMEO (disapprove/fight/coerce), 0% target vocab (conflict/political/...)
4. Wikidata: <1% entità con QID, 99%+ mai controllate (rate-limited)
5. Entity_type rumoroso: città classificate come company (WASHINGTON, FRANCE, NASA, BERLIN)

**Fix clustering** — pathosphere/semantic/cluster.py refactorizzato:
- Algoritmo: single-linkage → average-linkage con centroide coherence check
- KNN threshold: 0.85 (neighbors, unchanged)
- Coherence threshold: 0.88 (new, more stringent per centroid check)
- Implementazione: load embeddings in memoria, track centroids dinamicamente
- Test su 2564 RSS doc (720h window): 1258 eventi, 1117 singleton (88.8%), 0 chain-collapse
- Cluster grandi (20-30 doc) verificati **genuinamente coerenti** (World Cup cluster tutte su WC 2026, non mescolato)

**Commit**: `d14aeb4` — "fix(clustering): prevent single-linkage chain-collapse via average-linkage coherence"

**Interpretazione**: I 88% singleton sono **reali**, non artefatto — dataset RSS è intrinsecamente disperso (molti topic singolari, pochi argomenti forti con molti doc). Cluster grandi rimangono topicamente omogenei per costruzione.

**Status**: Clustering **solido per produzione**. Prossima fase: Fase 4 Dashboard.

---

## Re-ingest GDELT da zero, pipeline pulita (2026-07-10 — completato)

**GDELT history**: ✅ COMPLETO
- 8760 file scaricati, 223.077 events, 334.301 docs ingestati
- CP-016 (gdelt_anomaly): anomalie Goldstein promosse direttamente a events, NER/graph bypassato
- CP-015: HTML strippato da body prima di NER (dipendenza `bleach`)
- Canonicalizzazione: Wikidata QID, demonimi (Israeli/Russian/Chinese→location)

**Stato pipeline semantica** (2026-07-10 ~12:30 UTC):
- 🔄 `uv run pathos embed` — in progress, ~20 min (monitorare: `tail -f data/logs/embed_post_reingest.log`)
- 🔄 `uv run pathos extract` — in progress, ~1 ora (monitorare: `tail -f data/logs/extract_post_reingest.log`)
- ⬜ `uv run pathos cluster` — da eseguire dopo extract (~5 min)
- ⬜ `uv run pathos graph` — da eseguire dopo cluster (~10 min)

**Notebook study_08**: ✅ CREATO E COMMITATO
- Replica metodologia study_04-07 su GDELT pulito da zero
- Metriche: hairball (componente gigante), entità generiche ALL CAPS, Wikidata linkage, demonimi
- Pronto per esecuzione: `jupyter notebook notebooks/study_08_gdelt_post_reingest.ipynb`

**Completato** (2026-07-10 ~ 01:57 UTC):
1. ✅ GDELT history: 8760 file, 223.077 events, 334.301 docs
2. ✅ Embed: RSS 2.972 docs (Comtrade+GDELT per design escusi da NLP)
3. ✅ Extract: NER 0 (RSS pre-processato), geocoding 731 events, Wikidata 7 QID (rate-limited)
4. ✅ Cluster: 0 events creati (metadati cluster_id aggiunti a events esistenti)
5. ✅ Graph: 94.678 entity_links processati

**Notebook study_08**: ✅ ESEGUITO — metriche finali

### Risultati verifica post-re-ingest

| Metrica | Valore | Baseline (study_07) | Diff |
|---------|--------|-------------------|------|
| Nodo GDELT | ❌ NON ESISTE | grado 3.962 | ✓ rimosso |
| Gigante (nodi) | 8.744/9.359 (93.4%) | 12.664/13.278 (95.4%) | ↓ 2.0pp |
| Entity links | 94.678 | 123.047 | ↓ 27% |
| Entities totali | 10.581 | 13.278 | ↓ 20% |
| Wikidata QID | 29/10.581 (0.3%) | n/a | rate-limited |
| Demonimi | 9 trovati | n/a | ✓ reclassificati |

**Interpretazione**:
- ✅ **CP-016 fix funziona**: GDELT anomaly bypass NER/graph. Nessun nuovo nodo GDELT introdotto.
- ✅ **Hairball ridotto** 2pp vs pre-reset (95.4% → 93.4%). Link totali -27%.
- ❌ **Wikidata linkage bassissimo** (0.3%) — rate-limited a "Beijing", retry prossimo ciclo con delay ↑
- ✅ **Demonimi canonicalizzati** (Israeli/Russian/Chinese→location)

**Cosa non osserviamo**:
- Nodo GDELT pre-fix storico: non pulito (CI-016 previene NUOVI, non rimuove vecchi)
- Link storici gdelt_linked: restano intatti (nessun cleanup retroattivo)
→ Per dataset storico pulito serve `gdelt-reset --yes` esplicito (fatto in sessione 2026-07-09)

**Study 08b** ✅ — Event aggregation coherent (20/20 top eventi ≥3 docs, 231K eventi totali)

**Extract Wikidata Retry** ✅ — Delay ↑ (1.0 → 2.0 sec)
- 15 QID linkati (2x baseline 7)
- 20 entities checked (2x baseline 10)
- Rate-limited dopo "America"
- Progresso significativo nonostante rate limiting

**Stato finale**:
- ✓ GDELT re-ingest: 8.760 file, 223K eventi, 334K docs
- ✓ Pipeline semantica: embed/extract/cluster/graph completi
- ✓ Study 08 + 08b: hairball ↓2pp, event coherence OK
- ✓ Wikidata: 15 QID (2x improvement after retry)
- ✓ Branch `feat/study-08-gdelt-verification`: 2 commits, 458 test verdi

**Prossimi step**:
1. Merge PR `feat/study-08-gdelt-verification` → main
2. Fase 4 Dashboard Streamlit 

---

## Reset GDELT + backfill demonimi su DB reale (2026-07-09)

**Contesto**: sessione precedente (branch `refactor/gdelt-numeric-split`, ora eliminato) aveva diagnosticato CP-016 (documenti sintetici GDELT trattati come prosa dalla pipeline NLP) e scritto il fix. In parallelo, un'altra sessione ha ramificato da quel branch aggiungendo canonicalizzazione entità via Wikidata QID (nuova colonna `entities.canonical_entity_id`) + fix CP-015 (strip HTML dal body prima del NER, dipendenza `bleach`) + una propria implementazione di reset GDELT in `cli.py`, mergiando tutto su `main` con squash (PR #8, #9, #10). Il branch originale è risultato ridondante (contenuto già in `main`) ed è stato eliminato.

**Azioni eseguite in questa sessione su `main`**:
1. `pathos ingest gdelt-reset --yes` sul DB reale (`data/db/pathosphere.db`, 494MB) — cancellati 177.281 `raw_documents` origin=gdelt, 234.502 `gdelt_events`, 118.166 `events` origin=gdelt, 168.544 `vec_documents`, 295.356 `document_entities`, 3.908 entità rimaste orfane (usate solo da doc gdelt), 27.734 `entity_links` coinvolti, 4.836 righe `gdelt_file_log` (per permettere ri-scaricamento pulito). RSS/Comtrade/PortWatch/USGS/FIRMS/IODA verificati intatti. Operazione confermata con l'utente via preview prima dell'esecuzione (comando supporta dry-run di default, `--yes` per eseguire davvero).
2. `pathos extract --backfill-demonyms --limit 0 --skip-geocode --skip-wikidata` — 49 entità (Israeli, Russian, Chinese, American, Ukrainian…) riclassificate da `entity_type='other'` a `location` con `canonical_name` = nome paese.
3. Costruito un artifact visivo (HTML standalone, canvas force-directed graph + card cluster + mappa) usando dati catturati PRIMA del reset — quindi rappresenta lo stato GDELT-contaminato "as-is", utile come confronto storico. Include un esempio onesto di topic-drift nel clustering (evento 122013 "Armenia's top court…" i cui documenti sono in realtà quasi tutti su Netanyahu/Israele — sintomo di chain-collapse, non ancora fixato).

**Stato DB reale dopo questa sessione**: `origin=gdelt` completamente vuoto (0 righe in tutte le tabelle derivate). Prossimo passo per chi riprende: rilanciare `pathos ingest gdelt-history --start <data>` per ripopolare da zero con la pipeline già pulita (CP-016+CP-015+canonicalizzazione tutti attivi dal primo giorno, niente contaminazione da smaltire questa volta), poi `pathos ingest gdelt-anomalies --backfill-country --full`.

**Punti di attenzione per la prossima sessione**:
- Se lavori in parallelo con un'altra sessione Claude sullo stesso repo, **verifica sempre `git log main` e `git branch -a` prima di assumere che il tuo branch sia l'unica fonte di verità** — in questa sessione un reset/backfill è stato lanciato mentre in background un `git checkout` cambiava branch, e serviva ricostruire la sequenza da `git reflog` per capire cosa fosse successo. Nessun danno (i processi in esecuzione non sono affetti da checkout successivi, il DB è file-based non branch-based), ma ha richiesto un giro di verifica non banale.
- File innocuo da ignorare se lo vedi in `git status`: `pathosphere.db` (0 byte, root del repo) — scarto di un comando lanciato dalla cwd sbagliata in una sessione precedente, non il DB vero (`data/db/pathosphere.db`).

Dettagli completi CP-016/CP-015/canonicalizzazione: vedi commit `3566dbc` e PR #8/#9/#10 su GitHub, più `CRITICAL_POINTS.md`.

---

## Fix Wikidata linking (2026-07-07)

Run `pathos extract` produceva 40 errori 429 su 50 lookups Wikidata (10 QIDs). Due cause, fixate in `pathosphere/semantic/extract.py` (`link_wikidata`):

1. **Sleep saltato su errore**: `continue` su exception bypassava `time.sleep(delay_s)` → dopo primo 429 richieste a raffica senza pausa (~8 req/s), 429 auto-amplificato. Ora delay a inizio iterazione, rispettato sempre. `WIKIDATA_DELAY_S` 0.2→1.0 (limite anonimo Wikimedia ~1 req/s).
2. **Budget bruciato su entità spazzatura**: top-mentioned erano nomi generici ALL CAPS (`CRIMINAL`, `MILITARY`, `MALE`…) → link inutili o sbagliati (`MALE`→Malé). Nuova `GENERIC_ENTITY_STOPLIST` (~110 nomi comuni/ruoli/demonimi, match case-insensitive): marcati `wikidata_checked=1` senza lookup a inizio run, contati in `WikidataResult.stoplisted`. La stessa UPDATE azzera QID sbagliati assegnati pre-fix (es. `PRESIDENT`→Q30461 trovato nel DB reale).

In più: su 429 il run si interrompe subito (`WikidataResult.rate_limited=True`), entità restanti restano `wikidata_checked=0` → ritentate ciclo successivo. Errori non-429 continuano come prima. Output CLI e orchestrator mostrano stoplisted + flag rate limited. +4 test (stoplist, strip QID legacy, abort su 429, errore non-429 continua). 423 test verdi.

Smoke test reale (subagent, DB di produzione): 146 generici ritirati, 3 lookups a ~1 req/s, ISRAEL→Q801, US→Q30, `rate_limited=False`.

Run `pathos extract` completo post-fix: 9 QIDs validi (PAKISTAN→Q843, UKRAINE→Q212, RUSSIA→Q159…), poi 429 dopo 10 lookups anche a 1 req/s → abort pulito (1 warning vs 40 pre-fix), 40 entità rimandate. Probabile penalità residua IP dal run storm mattutino; se 429 persiste a IP pulito nei cicli successivi, alzare `WIKIDATA_DELAY_S` o onorare `Retry-After`. `SCHOOL`→Q3914 sfuggito → aggiunto a stoplist (QID verrà azzerato automaticamente al prossimo run dallo strip legacy).

## Fix IODA (2026-07-06)

`pathos ingest ioda --start 2026-01-01` crashava con `JSONDecodeError`. Tre cause, tutte fixate in `pathosphere/ingest/ioda.py`:

1. **Base URL sbagliato**: `ioda.inetintel.cc.gatech.edu/api/v2` è frontend SPA → HTML con 200. Corretto: `https://api.ioda.inetintel.cc.gatech.edu/v2`
2. **Limite API <100 giorni** per query singola → chunking automatico 90gg (`IODA_MAX_CHUNK_DAYS`), delay 1s tra chunk
3. **Shape reale annidata** `{"data": [[{...}]]}` → flatten un livello (vecchie shape restano supportate)

In più: risposta non-JSON ora → `RuntimeError` pulito in `IODAResult.errors` invece di crash. +3 test (chunking, shape annidata, non-JSON). Smoke test reale: IR 2026-01-01→07-05, 185 metriche, 3 chunk, 0 errori, 5 eventi outage.

## Stato al momento del handoff

**Branch:** fix/wikidata-linking (da pushare + PR)
**Test:** 423 verdi (22 in test_extract.py)
**Docs:** complete e allineate (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md)

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

- Codice + test: **COMPLETI**, 419 verdi
- Docs (wiki §8.6, schema.md, roadmap.md, overview_per_amico.md): agent haiku in aggiornamento
- LOOP_STATE.md, CRITICAL_POINTS.md: aggiornati
- **Nessun commit ancora fatto** sul branch

---

## Prossima azione raccomandata

**Fase 4 — Dashboard Streamlit**

Scope:
- Mappa mondiale eventi (folium)
- Confronto narrazioni per blocco geopolitico
- Curva equity tre portafogli (agent/random/benchmark)
- Tesi aperte (status pending/approved/rejected)
- Storico brief mattutini
- Grafico calibrazione Tetlock (bucket vs accuracy)

CLI: `pathos serve` → `localhost:8501`

Dipende da Fase 3 (predictions v2) completa. DB popolo via:
```
uv run pathos cycle run           # ciclo notturno completo
uv run pathos brief              # brief mattutino
uv run pathos thesis generate    # tesi
uv run pathos thesis approve <id> # auto-crea economic prediction
```

---

## Setup automazione (launchd)

```bash
# Una volta sola: installa daemon che lancia loop ogni 12h
./scripts/setup_launchd.sh
# Opzioni:
#   --interval SECONDS    (default 43200 = 12h)
#   --uninstall           (disattiva e rimuovi)

# Monitor il daemon
tail -f data/logs/launchd.log
launchctl list | grep pathosphere

# Disattiva
./scripts/setup_launchd.sh --uninstall
```

## Comandi utili

```bash
# Stato / DB
uv run pytest tests/ -q                    # 498 verdi
uv run pathos db init                      # OBBLIGATORIO dopo pull con modifiche schema
uv run pathos db info                      # Row counts per tabella

# Loop autonomo manuale (CP-017) — corre il ciclo notturno forever con stato persistente
# Interruzione sicura: Ctrl+C salva state + esci
caffeinate -i uv run pathos loop --sleep-hours 1.0 --max-retries 3
# Monitor:
tail -f data/logs/*.log
tail -f data/cycle_state.json  # Stato ultimo ciclo + error log (ultimi 100)

# Ciclo una volta (per debug/test)
uv run pathos cycle
uv run pathos cycle --from-phase embed       # Resume da EMBED
uv run pathos cycle --dry-run                # Simula solo

# Fasi singole (tutte standalone ora)
uv run pathos ingest gdelt --max-goldstein 5
uv run pathos ingest gdelt-anomalies --backfill-country --full
uv run pathos ingest rss
uv run pathos ingest portwatch
uv run pathos embed
uv run pathos cluster
uv run pathos extract
uv run pathos graph
uv run pathos brief
# etc.

# Predictions v2
uv run pathos predict add "Desc" --macro-area world --prediction-type geopolitical \
  --probability 0.65 --horizon 2026-08-10 --domain conflitto_armato \
  --origin-scope regionale --impact-scope globale
uv run pathos predict revise <id> --probability 0.7 --rationale "..."
uv run pathos predict resolve <id> --outcome-eventual true --resolved-date 2026-08-05
uv run pathos predict list --open --macro-area world --domain commercio
uv run pathos predict calibration

# Thesis / trading (v2: approve auto-crea predizione economic, trade open la aggancia)
uv run pathos thesis generate      # fast path: 1 chiamata Claude, nessun Qwen — rapido
uv run pathos thesis list
uv run pathos thesis approve <id>
uv run pathos thesis reject <id> --reason "..."
uv run pathos trade open <thesis_id>
uv run pathos portfolio status

# Thesis via debate multi-persona (CP-029: LENTO, 60-90+ min — SOLO background)
caffeinate -i uv run pathos thesis debate &
```

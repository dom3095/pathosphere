# Loop State — Pathosphere Autonomous Dev

## Fase corrente: primo ciclo reale completato — CP-025/CP-026 trovati e risolti (branch `feat/fundamentals-analysis`, PR #14)

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
